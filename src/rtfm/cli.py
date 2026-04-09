"""CLI for rtfm — agent-first documentation retrieval."""


import io
import json as json_mod
import os
import sys
from pathlib import Path

import click
import httpx
import yaml
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table

from rtfm.models import AppConfig, default_home

# Force UTF-8 output to avoid cp1252 encoding errors on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

console = Console()

_DEFAULT_CONFIG = Path(default_home()) / "config.yaml"


_quiet = False


def _load_config(config_path: str | None) -> AppConfig:
    candidates: list[Path] = []
    if config_path:
        candidates.append(Path(config_path))
    candidates.append(_DEFAULT_CONFIG)

    for path in candidates:
        if path.exists():
            with open(path) as f:
                if config_path and not _quiet:
                    console.print(f"[dim]Config: {path}[/dim]")
                data = yaml.safe_load(f)
                return AppConfig.from_dict(data)

    console.print(f"[red]No config found. Expected at {_DEFAULT_CONFIG}[/red]")
    raise SystemExit(1)


def _should_json(json_flag: bool | None) -> bool:
    """Determine output format: JSON for pipes/agents, Rich for terminals."""
    if json_flag is not None:
        return json_flag
    return not sys.stdout.isatty()


# ---------------------------------------------------------------------------
# Smart CLI group: unknown subcommands → default query
# ---------------------------------------------------------------------------

class _SmartGroup(click.Group):
    """Click group that routes unknown commands to the default query handler."""

    def resolve_command(self, ctx: click.Context, args: list[str]) -> tuple[str | None, click.Command | None, list[str]]:
        # No args → show help
        if not args:
            return super().resolve_command(ctx, args)
        # Known subcommand → normal routing
        cmd_name = args[0]
        if cmd_name in self.commands or cmd_name == "--help":
            return super().resolve_command(ctx, args)
        # Unknown → treat entire args as a query
        cmd = self.commands.get("query")
        if cmd:
            return "query", cmd, args
        return super().resolve_command(ctx, args)

    def format_usage(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        formatter.write("Usage: rtfm QUERY... [-f FRAMEWORK] [-k TOP_K]\n")
        formatter.write("       rtfm COMMAND [ARGS]...\n")


@click.group(cls=_SmartGroup, invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """rtfm — documentation retrieval for agents and humans.

    \b
    Query indexed docs (requires running server: rtfm up):
      rtfm dependency injection -f fastapi
      rtfm Depends -f fastapi
      rtfm '$state' -f svelte
      rtfm WebSocket -f fastapi -k 3
    \b
    Single-word queries try symbol lookup first, then fall back
    to topic search. Multi-word queries return a topic bundle
    grouped by type (api, example, concept, pitfall).
    \b
    Output is JSON when piped, human-readable in terminal.
    Use --json or --pretty to override.
    \b
    Admin:
      rtfm ingest [-f name] [--rebuild]   Download & index docs
      rtfm status [--no-check]            Health scores & unit counts
      rtfm up / down                      Start/stop background server
      rtfm update                         Re-ingest outdated sources
      rtfm init                           Create ~/.rtfm/config.yaml
      rtfm remove <name>                  Delete a framework's data
      rtfm serve                          Foreground server (systemd)
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# Default query command (hidden — invoked via SmartGroup routing)
# ---------------------------------------------------------------------------

@cli.command("query", hidden=True)
@click.argument("query_words", nargs=-1, required=True)
@click.option("-f", "--framework", default=None, help="Filter to one framework")
@click.option("-k", "--top-k", default=5, help="Results per type (default: 5)")
@click.option("--json/--pretty", "json_flag", default=None, help="Force output format")
@click.option("--config", "config_path", default=None, hidden=True)
def query_cmd(
    query_words: tuple[str, ...],
    framework: str | None,
    top_k: int,
    json_flag: bool | None,
    config_path: str | None,
) -> None:
    """Query documentation (default command)."""
    global _quiet
    as_json = _should_json(json_flag)
    _quiet = as_json
    query = " ".join(query_words)

    # Smart dispatch: single word → try symbol lookup first
    if len(query_words) == 1:
        result = _try_lookup(config_path, query_words[0], framework, as_json)
        if result is not None:
            return

    # Fall through to bundle
    _do_bundle(config_path, query, framework, top_k, as_json)


def _try_lookup(
    config_path: str | None,
    symbol: str,
    framework: str | None,
    as_json: bool,
) -> dict[str, object] | None:
    """Try a symbol lookup. Returns the data dict if found, None otherwise."""
    params: dict[str, str | int | bool] = {
        "include_related": True,
        "include_examples": True,
    }
    if framework:
        params["framework"] = framework

    try:
        resp = _api_get(config_path, f"/lookup/{symbol}", params, as_json=as_json)
    except SystemExit:
        return None  # server not running — will be caught by bundle fallback

    if resp.status_code == 404:
        return None  # not a known symbol, fall through to bundle

    resp.raise_for_status()
    data = resp.json()

    if as_json:
        click.echo(json_mod.dumps(data, ensure_ascii=False, indent=2))
    else:
        console.print("[bold green]Match:[/bold green]")
        _render_unit(data["match"], verbose=True)
        if data.get("related"):
            console.print(f"\n[bold blue]Related ({len(data['related'])}):[/bold blue]")
            for unit in data["related"]:
                _render_unit(unit)
        if data.get("examples"):
            console.print(f"\n[bold cyan]Examples ({len(data['examples'])}):[/bold cyan]")
            for unit in data["examples"]:
                _render_unit(unit, verbose=True)

    return data


def _do_bundle(
    config_path: str | None,
    query: str,
    framework: str | None,
    top_k: int,
    as_json: bool,
) -> None:
    """Run a bundle query and output results."""
    params: dict[str, str | int | bool] = {"q": query, "top_k": top_k}
    if framework:
        params["framework"] = framework

    resp = _api_get(config_path, "/bundle", params, as_json=as_json)
    resp.raise_for_status()
    data = resp.json()

    if as_json:
        click.echo(json_mod.dumps(data, ensure_ascii=False, indent=2))
        return

    total = sum(len(units) for units in data.values())
    if total == 0:
        console.print("[yellow]No results.[/yellow]")
        return

    filter_bits: list[str] = [f"top_k={top_k}"]
    if framework:
        filter_bits.append(f"framework={framework}")
    filter_str = ", ".join(filter_bits)
    console.print(f'[bold]"{escape(query)}"[/bold] [dim]→ {total} results · {filter_str}[/dim]')

    section_labels = {
        "api": ("green", "API"),
        "example": ("cyan", "Examples"),
        "concept": ("blue", "Concepts"),
        "pitfall": ("red", "Pitfalls"),
    }
    for section, units in data.items():
        if not units:
            continue
        color, label = section_labels.get(section, ("white", section))
        console.print(f"\n[bold {color}]{label} ({len(units)}):[/bold {color}]")
        for i, unit in enumerate(units, start=1):
            _render_unit(unit, verbose=False, index=i)


# ---------------------------------------------------------------------------
# Admin commands (visible in help)
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--framework", "-f", default=None, help="Ingest only this framework")
@click.option("--rebuild", is_flag=True, help="Clear existing data before ingesting")
@click.option("--json", "as_json", is_flag=True, help="Emit reporter events as JSON")
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
def ingest(
    framework: str | None,
    rebuild: bool,
    as_json: bool,
    config_path: str | None,
) -> None:
    """Ingest documentation from configured sources."""
    global _quiet
    _quiet = as_json
    config = _load_config(config_path)

    from rtfm.ingest.pipeline import ingest_all

    ingest_all(config, framework=framework, rebuild=rebuild, as_json=as_json)


@cli.command()
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
@click.option("--no-check", is_flag=True, help="Skip remote update check")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
def status(config_path: str | None, no_check: bool, as_json: bool) -> None:
    """Show health scores, unit counts, and update status."""
    global _quiet
    _quiet = as_json
    from concurrent.futures import ThreadPoolExecutor

    from rtfm.health import compute_all_health
    from rtfm.ingest.downloaders import check_remote_version
    from rtfm.reporter import make_reporter
    from rtfm.storage import Storage

    config = _load_config(config_path)
    storage = Storage(config.data_dir, config.embedding_model)
    stats = storage.get_stats()
    versions = storage.get_all_versions()
    health = compute_all_health(storage.conn)
    storage.close()

    if not stats:
        if as_json:
            click.echo(json_mod.dumps({"sources": [], "total_units": 0}))
        else:
            console.print("[yellow]No data ingested yet.[/yellow]")
        return

    frameworks = sorted(stats.keys())
    update_state: dict[str, str] = {fw: "ok" for fw in frameworks}

    if not no_check:
        reporter = make_reporter(
            console,
            title=f"Checking {len(frameworks)} sources for updates",
            as_json=as_json,
        )

        def _check_one(fw: str) -> tuple[str, str]:
            src = config.sources.get(fw)
            local_ver = versions.get(fw, (None, None))[0]
            if not src or not local_ver:
                reporter.finish(fw, status="skipped", detail="not configured")
                return fw, "skipped"

            reporter.update(fw, status="checking")

            def _on_progress(msg: str) -> None:
                reporter.update(fw, detail=msg)

            try:
                remote_ver = check_remote_version(src, on_progress=_on_progress)
            except Exception as e:  # noqa: BLE001
                reporter.finish(fw, status="error", detail=str(e)[:60])
                return fw, "error"

            if remote_ver is None:
                reporter.finish(fw, status="skipped")
                return fw, "skipped"

            if remote_ver != local_ver:
                reporter.finish(
                    fw,
                    status="outdated",
                    detail=f"{_short_version(local_ver)} → {_short_version(remote_ver)}",
                )
                return fw, "outdated"
            reporter.finish(fw, status="ok", detail=_short_version(local_ver) if local_ver else "")
            return fw, "ok"

        with reporter:
            for fw in frameworks:
                reporter.add(fw, fw, status="pending")
            with ThreadPoolExecutor(max_workers=8) as pool:
                for fw, st in pool.map(_check_one, frameworks):
                    update_state[fw] = st

    if as_json:
        payload = {
            "sources": [
                {
                    "framework": fw,
                    "status": update_state[fw],
                    "version": (versions.get(fw) or ("", ""))[0],
                    "ingested_at": (versions.get(fw) or ("", ""))[1],
                    "counts": stats[fw],
                    "health": {
                        "score": health[fw].score,
                        "grade": health[fw].grade,
                        "signals": health[fw].signals,
                    } if fw in health else None,
                }
                for fw in frameworks
            ],
            "total_units": sum(c.get("total", 0) for c in stats.values()),
        }
        click.echo(json_mod.dumps(payload, ensure_ascii=False, indent=2))
        return

    console.print()
    console.print(_build_status_table(stats, versions, update_state, health=health))

    total_units = sum(c.get("total", 0) for c in stats.values())
    data_path = Path(config.data_dir)
    disk_bytes = sum(f.stat().st_size for f in data_path.rglob("*") if f.is_file()) if data_path.exists() else 0
    cache_path = Path(default_home()) / "cache"
    cache_bytes = sum(f.stat().st_size for f in cache_path.rglob("*") if f.is_file()) if cache_path.exists() else 0

    console.print(
        f"[dim]{len(frameworks)} sources, {total_units} units, "
        f"data {_human_size(disk_bytes)}, cache {_human_size(cache_bytes)}[/dim]"
    )
    outdated_count = sum(1 for s in update_state.values() if s == "outdated")
    if outdated_count:
        console.print(f"[yellow]Run 'rtfm update' to re-ingest {outdated_count} outdated source(s).[/yellow]")

    min_score = config.min_health_score
    below = [fw for fw, h in health.items() if h.score < min_score]
    if below:
        console.print(
            f"[red]{len(below)} source(s) below health threshold ({min_score}): "
            f"{', '.join(below)}[/red]"
        )


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Emit reporter events as JSON")
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
def update(as_json: bool, config_path: str | None) -> None:
    """Check for upstream changes and re-ingest outdated sources."""
    global _quiet
    _quiet = as_json
    config = _load_config(config_path)

    from rtfm.ingest.pipeline import update_all

    update_all(config, as_json=as_json)


@cli.command()
@click.argument("framework")
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
def remove(framework: str, config_path: str | None) -> None:
    """Remove a framework's indexed data."""
    config = _load_config(config_path)

    from rtfm.storage import Storage

    storage = Storage(config.data_dir, config.embedding_model)
    stats = storage.get_stats()

    if framework not in stats:
        console.print(f"[yellow]No data found for '{framework}'.[/yellow]")
        storage.close()
        return

    count = stats[framework]["total"]
    storage.clear_framework(framework)
    console.print(f"[green]Removed {framework} ({count} units).[/green]")
    storage.close()


@cli.command()
@click.option("--host", default=None, help="Host to bind to")
@click.option("--port", default=None, type=int, help="Port to bind to")
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
def serve(host: str | None, port: int | None, config_path: str | None) -> None:
    """Start the rtfm HTTP server (foreground)."""
    config = _load_config(config_path)

    import uvicorn

    uvicorn.run(
        "rtfm.server:app",
        host=host or config.host,
        port=port or config.port,
        reload=False,
    )


_PID_FILE = Path(default_home()) / "server.pid"


def _server_pid() -> int | None:
    if not _PID_FILE.exists():
        return None
    try:
        return int(_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        _PID_FILE.unlink(missing_ok=True)
        return None


def _port_responding(host: str, port: int) -> bool:
    try:
        httpx.get(f"http://{host}:{port}/openapi.json", timeout=1.0)
        return True
    except (httpx.ConnectError, httpx.HTTPError):
        return False


@cli.command()
@click.option("--host", default=None, help="Host to bind to")
@click.option("--port", default=None, type=int, help="Port to bind to")
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
def up(host: str | None, port: int | None, config_path: str | None) -> None:
    """Start the server in the background."""
    config = _load_config(config_path)
    bind_host = host or config.host
    bind_port = port or config.port

    if _port_responding(bind_host, bind_port):
        pid = _server_pid()
        pid_info = f" (PID {pid})" if pid else ""
        console.print(f"[yellow]Server already running on {bind_host}:{bind_port}{pid_info}.[/yellow]")
        return

    import subprocess

    python = sys.executable
    if sys.platform == "win32":
        pythonw = Path(python).with_name("pythonw.exe")
        if pythonw.exists():
            python = str(pythonw)

    cmd = [
        python, "-m", "uvicorn",
        "rtfm.server:app",
        "--host", bind_host,
        "--port", str(bind_port),
    ]

    log_file = Path(default_home()) / "server.log"
    with open(log_file, "w") as log:
        if sys.platform == "win32":
            proc = subprocess.Popen(
                cmd, stdout=log, stderr=log,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
        else:
            proc = subprocess.Popen(cmd, stdout=log, stderr=log, start_new_session=True)

    _PID_FILE.write_text(str(proc.pid))
    console.print(f"[green]Server started on http://{bind_host}:{bind_port} (PID {proc.pid})[/green]")
    console.print(f"[dim]Log: {log_file}[/dim]")


def _find_pid_by_port(port: int) -> int | None:
    import subprocess

    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["netstat", "-ano", "-p", "TCP"], text=True, stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    return int(line.strip().split()[-1])
        else:
            out = subprocess.check_output(
                ["lsof", "-ti", f":{port}"], text=True, stderr=subprocess.DEVNULL,
            )
            pids = out.strip().split()
            if pids:
                return int(pids[0])
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        pass
    return None


@cli.command()
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
def down(config_path: str | None) -> None:
    """Stop the background server."""
    pid = _server_pid()

    if pid is None:
        config = _load_config(config_path)
        if _port_responding(config.host, config.port):
            pid = _find_pid_by_port(config.port)
            if pid is None:
                console.print(f"[red]Server on port {config.port} is responding but could not find PID.[/red]")
                return
        else:
            console.print("[yellow]Server is not running.[/yellow]")
            return

    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        console.print(f"[red]Failed to stop PID {pid}: {e}[/red]")

    _PID_FILE.unlink(missing_ok=True)
    console.print(f"[green]Server stopped (PID {pid}).[/green]")


_SYSTEMD_UNIT = """\
[Unit]
Description=rtfm documentation retrieval server
After=network.target rtfm-update.service

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""

_SYSTEMD_UPDATE = """\
[Unit]
Description=Update rtfm documentation indexes
Before=rtfm.service
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart={exec_update}
TimeoutStartSec=300

[Install]
WantedBy=default.target
"""

_PACMAN_HOOK = """\
[Trigger]
Operation = Upgrade
Type = Package
Target = python-agent-rtfm
Target = python-agent-rtfm-bin
Target = python-agent-rtfm-git

[Action]
Description = Restarting rtfm server...
When = PostTransaction
Exec = /usr/bin/systemctl --global restart rtfm.service
NeedsTargets
"""


def _is_pacman_installed() -> bool:
    """Check if rtfm was installed via pacman (service file already shipped)."""
    return Path("/usr/lib/systemd/user/rtfm.service").exists()


@cli.command("systemd-setup")
@click.option("--config", "config_path", default=None, help="Path to config.yaml")
def systemd_setup(config_path: str | None) -> None:
    """Install a systemd user service and optional pacman hook."""
    import shutil

    if _is_pacman_installed():
        console.print("[green]Service files already installed by pacman.[/green]")
        console.print()
        console.print("[bold]Run:[/bold]")
        console.print("  systemctl --user daemon-reload")
        console.print("  systemctl --user enable --now rtfm")
        console.print("  systemctl --user enable rtfm-update  # auto-update on boot")
        return

    config = _load_config(config_path)
    rtfm_bin = shutil.which("rtfm") or "rtfm"

    serve_cmd = f"{rtfm_bin} serve --host {config.host} --port {config.port}"
    update_cmd = f"{rtfm_bin} update"
    if config_path:
        serve_cmd += f" --config {config_path}"
        update_cmd += f" --config {config_path}"

    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)

    unit_file = unit_dir / "rtfm.service"
    unit_file.write_text(_SYSTEMD_UNIT.format(exec_start=serve_cmd))
    console.print(f"[green]Wrote {unit_file}[/green]")

    update_file = unit_dir / "rtfm-update.service"
    update_file.write_text(_SYSTEMD_UPDATE.format(exec_update=update_cmd))
    console.print(f"[green]Wrote {update_file}[/green]")

    hook_dir = Path("/etc/pacman.d/hooks")
    if hook_dir.exists():
        hook_file = hook_dir / "rtfm-restart.hook"
        try:
            hook_file.write_text(_PACMAN_HOOK)
            console.print(f"[green]Wrote {hook_file}[/green]")
        except PermissionError:
            console.print(f"[yellow]Skipped pacman hook (need sudo for {hook_dir})[/yellow]")
            console.print(f"[dim]  sudo install -Dm644 /dev/stdin {hook_file} << 'EOF'[/dim]")
            for line in _PACMAN_HOOK.strip().splitlines():
                console.print(f"[dim]  {line}[/dim]")
            console.print("[dim]  EOF[/dim]")

    console.print()
    console.print("[bold]Next steps:[/bold]")
    console.print("  systemctl --user daemon-reload")
    console.print("  systemctl --user enable --now rtfm")
    console.print("  systemctl --user enable rtfm-update  # auto-update on boot")
    console.print()
    console.print("[dim]Check status: systemctl --user status rtfm[/dim]")
    console.print("[dim]View logs:    journalctl --user -u rtfm -f[/dim]")


@cli.command()
def init() -> None:
    """Create ~/.rtfm/ with a default config.yaml."""
    home = Path(default_home())
    home.mkdir(parents=True, exist_ok=True)
    config_file = home / "config.yaml"

    if config_file.exists():
        console.print(f"[yellow]Config already exists: {config_file}[/yellow]")
        return

    config_file.write_text("""\
# rtfm configuration
embedding_model: nomic-ai/nomic-embed-text-v1.5

server:
  host: 127.0.0.1
  port: 8787

sources:
  svelte:
    type: llms_txt
    url: https://svelte.dev/llms-full.txt
    language: javascript

  fastapi:
    type: github
    repo: fastapi/fastapi
    docs_path: docs/en/docs
    glob: "**/*.md"
    language: python
""")
    console.print(f"[green]Created {config_file}[/green]")
    console.print("Edit the config, then run: rtfm ingest")


# ---------------------------------------------------------------------------
# Legacy commands (hidden — preserved for backwards compatibility)
# ---------------------------------------------------------------------------

@cli.command(hidden=True)
@click.argument("query", nargs=-1, required=True)
@click.option("--framework", "-f", default=None, help="Filter by framework")
@click.option("--type", "-t", "unit_type", default=None, help="Filter by type")
@click.option("--top-k", "-k", default=5, help="Number of results")
@click.option("--verbose", "-v", is_flag=True, help="Show full content")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.option("--config", "config_path", default=None, hidden=True)
def search(
    query: tuple[str], framework: str | None, unit_type: str | None,
    top_k: int, verbose: bool, as_json: bool, config_path: str | None,
) -> None:
    """Search documentation (legacy — use 'rtfm <query>' instead)."""
    global _quiet
    _quiet = as_json

    q = " ".join(query)
    params: dict[str, str | int | bool] = {"q": q, "top_k": top_k}
    if framework:
        params["framework"] = framework
    if unit_type:
        params["type"] = unit_type

    resp = _api_get(config_path, "/search", params, as_json=as_json)
    resp.raise_for_status()
    results = resp.json()

    if as_json:
        click.echo(json_mod.dumps(results, ensure_ascii=False, indent=2))
        return

    if not results:
        console.print("[yellow]No results.[/yellow]")
        return

    filter_bits: list[str] = [f"top_k={top_k}"]
    if framework:
        filter_bits.append(f"framework={framework}")
    if unit_type:
        filter_bits.append(f"type={unit_type}")
    filter_str = ", ".join(filter_bits)
    console.print(f'[bold]"{escape(q)}"[/bold] [dim]→ {len(results)} results · {filter_str}[/dim]')
    console.print()
    for i, unit in enumerate(results, start=1):
        _render_unit(unit, verbose=verbose, index=i)


@cli.command(hidden=True)
@click.argument("symbol")
@click.option("--framework", "-f", default=None, help="Filter by framework")
@click.option("--no-related", is_flag=True, help="Skip related units")
@click.option("--no-examples", is_flag=True, help="Skip examples")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.option("--config", "config_path", default=None, hidden=True)
def lookup(
    symbol: str, framework: str | None, no_related: bool,
    no_examples: bool, as_json: bool, config_path: str | None,
) -> None:
    """Look up a symbol (legacy — use 'rtfm <symbol>' instead)."""
    global _quiet
    _quiet = as_json

    params: dict[str, str | int | bool] = {
        "include_related": not no_related,
        "include_examples": not no_examples,
    }
    if framework:
        params["framework"] = framework

    resp = _api_get(config_path, f"/lookup/{symbol}", params, as_json=as_json)
    if resp.status_code == 404:
        if as_json:
            click.echo(json_mod.dumps({"error": f"Symbol not found: {symbol}"}, ensure_ascii=False))
        else:
            console.print(f"[yellow]Symbol not found: {symbol}[/yellow]")
        return

    resp.raise_for_status()
    data = resp.json()

    if as_json:
        click.echo(json_mod.dumps(data, ensure_ascii=False, indent=2))
        return

    console.print("[bold green]Match:[/bold green]")
    _render_unit(data["match"], verbose=True)
    if data.get("related"):
        console.print(f"\n[bold blue]Related ({len(data['related'])}):[/bold blue]")
        for unit in data["related"]:
            _render_unit(unit)
    if data.get("examples"):
        console.print(f"\n[bold cyan]Examples ({len(data['examples'])}):[/bold cyan]")
        for unit in data["examples"]:
            _render_unit(unit, verbose=True)


@cli.command(hidden=True)
@click.argument("framework")
@click.option("--module", "-m", default=None, help="Drill down into a module")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.option("--config", "config_path", default=None, hidden=True)
def browse(framework: str, module: str | None, as_json: bool, config_path: str | None) -> None:
    """Browse framework structure (legacy)."""
    global _quiet
    _quiet = as_json

    params: dict[str, str | int | bool] = {"framework": framework}
    if module:
        params["module"] = module

    resp = _api_get(config_path, "/browse", params, as_json=as_json)
    resp.raise_for_status()
    data = resp.json()

    if as_json:
        click.echo(json_mod.dumps(data, ensure_ascii=False, indent=2))
        return

    if "units" in data and data["units"]:
        console.print(f"[bold]{framework}[/bold] > [bold]{module}[/bold] ({len(data['units'])} units)")
        console.print()
        for i, unit in enumerate(data["units"], start=1):
            _render_unit(unit, index=i)
        return

    if "modules" in data:
        console.print(f"[bold]{framework}[/bold] ({len(data['modules'])} modules)")
        console.print()
        for mod in data["modules"]:
            syms = ", ".join(mod["symbols"][:5])
            if len(mod["symbols"]) > 5:
                syms += f" (+{len(mod['symbols']) - 5})"
            console.print(f"  [bold]{mod['name']}[/bold]")
            if syms:
                console.print(f"    [dim]{syms}[/dim]")


@cli.command(hidden=True)
@click.argument("query", nargs=-1, required=True)
@click.option("--framework", "-f", default=None, help="Filter by framework")
@click.option("--top-k", "-k", default=5, help="Results per type bucket")
@click.option("--verbose", "-v", is_flag=True, help="Show full content")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.option("--config", "config_path", default=None, hidden=True)
def bundle(
    query: tuple[str], framework: str | None, top_k: int,
    verbose: bool, as_json: bool, config_path: str | None,
) -> None:
    """Get everything about a topic (legacy — use 'rtfm <query>' instead)."""
    global _quiet
    _quiet = as_json

    q = " ".join(query)
    params: dict[str, str | int | bool] = {"q": q, "top_k": top_k}
    if framework:
        params["framework"] = framework

    resp = _api_get(config_path, "/bundle", params, as_json=as_json)
    resp.raise_for_status()
    data = resp.json()

    if as_json:
        click.echo(json_mod.dumps(data, ensure_ascii=False, indent=2))
        return

    if not data:
        console.print("[yellow]No results.[/yellow]")
        return

    section_labels = {
        "api": ("green", "API"),
        "example": ("cyan", "Examples"),
        "concept": ("blue", "Concepts"),
        "pitfall": ("red", "Pitfalls"),
    }

    total = sum(len(units) for units in data.values())
    if total == 0:
        console.print("[yellow]No results.[/yellow]")
        return

    filter_bits: list[str] = [f"top_k={top_k}"]
    if framework:
        filter_bits.append(f"framework={framework}")
    filter_str = ", ".join(filter_bits)
    console.print(f'[bold]"{escape(q)}"[/bold] [dim]→ {total} results · {filter_str}[/dim]')

    for section, units in data.items():
        if not units:
            continue
        color, label = section_labels.get(section, ("white", section))
        console.print(f"\n[bold {color}]{label} ({len(units)}):[/bold {color}]")
        for i, unit in enumerate(units, start=1):
            _render_unit(unit, verbose=verbose, index=i)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _short_version(version_key: str) -> str:
    if not version_key:
        return "-"
    if version_key.startswith(("etag:", "mod:")):
        import hashlib
        return hashlib.sha256(version_key.encode()).hexdigest()[:12]
    return version_key[:12]


def _short_date(iso_ts: str) -> str:
    if not iso_ts or len(iso_ts) < 10:
        return "-"
    return iso_ts[5:10]


def _build_status_table(
    stats: dict[str, dict[str, int]],
    versions: dict[str, tuple[str, str]],
    update_state: dict[str, str],
    health: dict[str, object] | None = None,
) -> Table:
    from rtfm.health import HealthDetail

    table = Table(
        title="rtfm Status",
        title_style="bold",
        title_justify="left",
        show_header=True,
        header_style="bold",
        pad_edge=False,
    )
    table.add_column("Framework", style="bold")
    if health:
        table.add_column("Health", justify="right")
    table.add_column("Status")
    table.add_column("Version")
    table.add_column("Updated")
    table.add_column("Units", justify="right")
    table.add_column("a / e / c / p", justify="right", style="dim")

    for fw in sorted(stats.keys()):
        counts = stats[fw]
        ver_info = versions.get(fw)
        version = _short_version(ver_info[0]) if ver_info else "-"
        updated = _short_date(ver_info[1]) if ver_info else "-"
        state = update_state.get(fw, "ok")
        color = {"ok": "green", "outdated": "yellow", "error": "red", "skipped": "dim"}.get(state, "white")

        row: list[str] = [fw]
        if health:
            h = health.get(fw)
            if isinstance(h, HealthDetail):
                grade_color = {"A": "green", "B": "green", "C": "yellow", "D": "red", "F": "red"}.get(h.grade, "white")
                row.append(f"[{grade_color}]{h.score:>3} {h.grade}[/{grade_color}]")
            else:
                row.append("[dim]  - -[/dim]")
        row.extend([
            f"[{color}]{state}[/{color}]",
            version,
            updated,
            str(counts.get("total", 0)),
            f"{counts.get('api', 0)}/{counts.get('example', 0)}/{counts.get('concept', 0)}/{counts.get('pitfall', 0)}",
        ])
        table.add_row(*row)
    return table


def _base_url(config_path: str | None) -> str:
    config = _load_config(config_path)
    return f"http://{config.host}:{config.port}"


def _emit_error(as_json: bool, code: str, message: str, **extra: str) -> None:
    if as_json:
        payload = {"error": code, "message": message, **extra}
        click.echo(json_mod.dumps(payload, ensure_ascii=False))
    else:
        console.print(f"[red]{message}[/red]")


def _api_get(
    config_path: str | None,
    path: str,
    params: dict[str, str | int | bool],
    as_json: bool = False,
) -> httpx.Response:
    url = _base_url(config_path)
    try:
        resp = httpx.get(f"{url}{path}", params=params, timeout=30.0)
    except httpx.ConnectError as e:
        _emit_error(as_json, "connection_failed", f"Cannot connect to {url} -- is the server running? (rtfm up)")
        raise SystemExit(1) from e
    except httpx.HTTPError as e:
        _emit_error(as_json, "http_error", f"Request failed: {e}")
        raise SystemExit(1) from e
    return resp


_TYPE_COLORS = {"api": "green", "example": "cyan", "concept": "blue", "pitfall": "red"}


def _snippet(content: str, max_width: int) -> str:
    if max_width < 4:
        max_width = 4
    for raw in content.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if len(line) > max_width:
            return line[: max_width - 1].rstrip() + "…"
        return line
    return ""


def _render_unit(
    unit: dict[str, str | list[str]],
    verbose: bool = False,
    index: int | None = None,
) -> None:
    heading = " > ".join(str(h) for h in unit.get("heading_hierarchy", []) or [])
    unit_type = str(unit.get("type", ""))
    framework = str(unit.get("framework", ""))
    module_path = str(unit.get("module_path", ""))
    content = str(unit.get("content", ""))
    color = _TYPE_COLORS.get(unit_type, "white")

    head_parts: list[str] = []
    if index is not None:
        head_parts.append(f"[dim]{index}.[/dim]")
    if unit_type:
        head_parts.append(f"[{color}]\\[{escape(unit_type)}][/{color}]")
    if heading:
        head_parts.append(f"[bold {color}]{escape(heading)}[/bold {color}]")
    elif not head_parts:
        head_parts.append(f"[{color}]\\[{escape(unit_type or 'unit')}][/{color}]")
    console.print("  " + " ".join(head_parts))

    location_bits: list[str] = []
    if framework:
        location_bits.append(f"[dim]{escape(framework)}[/dim]")
    if module_path:
        location_bits.append(f"[dim]{escape(module_path)}[/dim]")
    if location_bits:
        console.print("     " + " [dim]·[/dim] ".join(location_bits))

    if verbose:
        console.print()
        console.print(Markdown(content, code_theme="monokai"))
        console.print("[dim]---[/dim]")
    else:
        max_width = max(20, console.size.width - 7)
        snippet = _snippet(content, max_width=max_width)
        if snippet:
            console.print(f"     [dim]{escape(snippet)}[/dim]")


if __name__ == "__main__":
    cli()
