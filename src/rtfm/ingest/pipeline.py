"""Ingestion pipeline: orchestrates download, parsing, extraction, and storage."""


import os
import stat
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console

from rtfm.ingest.detect import detect_doc_system, pre_detect_from_config
from rtfm.ingest.downloaders import check_remote_version, download_source
from rtfm.ingest.extractors import get_extractor
from rtfm.ingest.parsers import get_parser
from rtfm.models import AppConfig, KnowledgeUnit, SourceConfig
from rtfm.reporter import Reporter, make_reporter
from rtfm.storage import Storage

console = Console()


def _with_doc_system(config: SourceConfig, doc_system: str) -> SourceConfig:
    """Return a copy of config with doc_system set (SourceConfig is a frozen-ish dataclass)."""
    from dataclasses import replace
    return replace(config, doc_system=doc_system)


def _rmtree_safe(path: str | Path) -> None:
    """Remove a directory tree, handling Windows read-only files from git.

    Git creates read-only files inside .git/objects/.  On Windows,
    ``shutil.rmtree`` fails on those unless we flip the permissions first.
    We also tolerate residual WinError 32 (file locked) by retrying once
    after a short delay.
    """
    import shutil
    import time

    def _on_rm_error(func, fpath, exc_info):  # noqa: ANN001
        """onerror handler: clear read-only flag and retry."""
        try:
            os.chmod(fpath, stat.S_IWRITE)
            func(fpath)
        except OSError:
            pass

    for attempt in range(3):
        try:
            shutil.rmtree(path, onerror=_on_rm_error)
            return
        except OSError:
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
    # Final best-effort: ignore if it still fails
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def _download_and_parse(
    source_config: SourceConfig,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[list[KnowledgeUnit], str, str]:
    """Download, parse, and extract units for a single source.

    Returns (units, version_key, doc_system). No storage side effects.
    """
    # Pre-detect doc system from config/URL so the downloader can keep raw HTML
    pre_detected = pre_detect_from_config(source_config)
    if pre_detected and not source_config.doc_system:
        source_config = _with_doc_system(source_config, pre_detected)

    work_dir = tempfile.mkdtemp()
    try:
        result = download_source(source_config, Path(work_dir), on_progress=on_progress)
        if not result.files:
            return [], result.version_key, ""

        if on_progress is not None:
            on_progress(f"parsing {len(result.files)} files")

        # Detect doc system: explicit config > pre-detected > content-based auto-detect
        doc_system = source_config.doc_system or detect_doc_system(
            result.files, source_config.type
        )

        parser = get_parser(doc_system)
        extractor = get_extractor(doc_system)

        all_units: list[KnowledgeUnit] = []
        for rel_path, content, _content_type in result.files:
            sections = parser.parse(content, source_file=rel_path)
            units = extractor.extract(
                sections,
                framework=source_config.name,
                language=source_config.language,
                source_file=rel_path,
            )
            all_units.extend(units)

        # Deduplicate
        seen_ids: set[str] = set()
        unique: list[KnowledgeUnit] = []
        for unit in all_units:
            if unit.id not in seen_ids:
                seen_ids.add(unit.id)
                unique.append(unit)

        return unique, result.version_key, doc_system
    finally:
        _rmtree_safe(work_dir)


def _ingest_one(
    source_config: SourceConfig,
    storage: Storage,
    reporter: Reporter,
    task_id: str,
    rebuild: bool = True,
) -> int:
    """Ingest one framework end-to-end via the reporter. Returns unit count."""
    framework = source_config.name

    def _on_progress(msg: str) -> None:
        reporter.update(task_id, detail=msg)

    if rebuild:
        reporter.update(task_id, status="parsing", detail="clearing existing data")
        storage.clear_framework(framework)

    reporter.update(task_id, status="downloading")
    try:
        units, version_key, doc_system = _download_and_parse(source_config, on_progress=_on_progress)
    except Exception as e:  # noqa: BLE001 — surface to user
        reporter.finish(task_id, status="error", detail=str(e)[:80])
        return 0

    if not units:
        reporter.finish(task_id, status="done", detail="0 units")
        return 0

    reporter.update(task_id, status="embedding", detail=f"0/{len(units)}")

    def _embed_progress(done: int, total: int) -> None:
        reporter.update(task_id, detail=f"{done}/{total}")

    storage.insert_units(units, on_progress=_embed_progress)
    storage.set_version(framework, version_key, doc_system=doc_system)

    reporter.finish(task_id, status="done", detail=f"{len(units)} units")
    return len(units)


def update_all(config: AppConfig, *, as_json: bool = False) -> None:
    """Check for updates and re-ingest outdated sources via the reporter."""
    storage = Storage(config.data_dir, config.embedding_model)
    versions = storage.get_all_versions()

    frameworks = list(config.sources.keys())
    if not frameworks:
        console.print("[yellow]No sources configured.[/yellow]")
        storage.close()
        return

    # Phase 1: parallel update checks
    check_reporter = make_reporter(
        console,
        title=f"Checking {len(frameworks)} sources",
        as_json=as_json,
    )

    outdated: list[str] = []

    def _check_one(name: str) -> tuple[str, str]:
        src = config.sources[name]
        local_ver = versions.get(name, (None, None))[0]
        check_reporter.update(name, status="checking")

        def _on_progress(msg: str) -> None:
            check_reporter.update(name, detail=msg)

        try:
            remote_ver = check_remote_version(src, on_progress=_on_progress)
        except Exception as e:  # noqa: BLE001
            check_reporter.finish(name, status="error", detail=str(e)[:60])
            return name, "error"

        if remote_ver is None:
            if local_ver is None:
                check_reporter.finish(name, status="outdated", detail="not yet ingested")
                return name, "outdated"
            # Keep whatever detail the crawler set last (e.g. "rate limited",
            # "network error: ..."). Don't generic-ify it.
            check_reporter.finish(name, status="skipped")
            return name, "skipped"

        if local_ver != remote_ver:
            check_reporter.finish(name, status="outdated", detail=f"new: {remote_ver[:12]}")
            return name, "outdated"

        check_reporter.finish(name, status="ok", detail=local_ver[:12])
        return name, "ok"

    with check_reporter:
        for name in frameworks:
            check_reporter.add(name, name, status="pending")
        with ThreadPoolExecutor(max_workers=8) as pool:
            for name, state in pool.map(_check_one, frameworks):
                if state == "outdated":
                    outdated.append(name)

    if not outdated:
        if not as_json:
            console.print("\n[green]Everything is up to date.[/green]")
        storage.close()
        return

    if not as_json:
        console.print(f"\n[bold]Updating {len(outdated)} source(s): {', '.join(outdated)}[/bold]")

    # Phase 2: parallel download+parse, sequential embed (Chroma is single-writer)
    _ingest_many(config, storage, outdated, rebuild=True, as_json=as_json)
    storage.close()


def _ingest_many(
    config: AppConfig,
    storage: Storage,
    framework_names: list[str],
    *,
    rebuild: bool = True,
    as_json: bool = False,
) -> None:
    """Ingest a set of frameworks rendered through one reporter.

    Phase A — download + parse all sources in parallel (independent CPU/IO).
    Phase B — embed each prepared payload sequentially (Chroma is single-writer).

    *rebuild* — when True, clear each framework's existing data before
    re-inserting. Used by ``update`` and explicit ``ingest --rebuild``.
    """
    sources = {name: config.sources[name] for name in framework_names}
    prepared: dict[str, tuple[list[KnowledgeUnit], str, str]] = {}

    reporter = make_reporter(
        console,
        title=f"Ingesting {len(sources)} sources",
        as_json=as_json,
    )

    with reporter:
        for name in sources:
            reporter.add(name, name, status="pending")

        # Phase A: download + parse in parallel
        def _do_download(name: str) -> tuple[str, list[KnowledgeUnit] | None, str, str]:
            src = sources[name]

            def _on_progress(msg: str) -> None:
                reporter.update(name, detail=msg)

            reporter.update(name, status="downloading")
            try:
                units, version_key, doc_system = _download_and_parse(src, on_progress=_on_progress)
            except Exception as e:  # noqa: BLE001
                reporter.finish(name, status="error", detail=str(e)[:80])
                return name, None, "", ""
            # An empty download with an "unknown" version key means the
            # source could not be reached (e.g. 429 rate limit on the
            # initial crawl GET). Don't silently store 0 units — surface it.
            # Use status="error" but leave detail untouched so the last
            # crawler message ("rate limited", "network error", ...) stays
            # visible to the user.
            if not units and version_key in ("unknown", ""):
                reporter.finish(name, status="error")
                return name, None, "", ""
            reporter.update(name, status="parsing", detail=f"{len(units)} units ready")
            return name, units, version_key, doc_system

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_do_download, name): name for name in sources}
            for future in as_completed(futures):
                name, units, version_key, doc_system = future.result()
                if units is not None:
                    prepared[name] = (units, version_key, doc_system)

        # Phase B: embed sequentially (Chroma is single-writer, plus we get
        # one clean progress stream per source).
        total_units = 0
        for name in sources:
            if name not in prepared:
                continue
            units, version_key, doc_system = prepared[name]
            if not units:
                reporter.finish(name, status="done", detail="0 units")
                continue

            if rebuild:
                # Clear stale entries with old IDs that the new payload no
                # longer carries. insert_units itself is INSERT OR REPLACE
                # by id, so this only matters when rebuild is requested.
                storage.clear_framework(name)

            reporter.update(name, status="embedding", detail=f"0/{len(units)}")

            def _embed_progress(done: int, total: int, _name: str = name) -> None:
                reporter.update(_name, detail=f"{done}/{total}")

            storage.insert_units(units, on_progress=_embed_progress)
            storage.set_version(name, version_key, doc_system=doc_system)

            # Post-ingest health check
            min_score = config.min_health_score
            if min_score > 0:
                from rtfm.health import compute_health
                h = compute_health(storage.conn, name)
                if h.score < min_score:
                    storage.clear_framework(name)
                    reasons = ", ".join(h.signals[:3])
                    reporter.finish(
                        name,
                        status="rejected",
                        detail=f"health {h.score}/{min_score} ({reasons})",
                    )
                    continue

            reporter.finish(name, status="done", detail=f"{len(units)} units")
            total_units += len(units)

        reporter.log(f"Total: {total_units} units ingested across {len(prepared)} sources")


def ingest_all(
    config: AppConfig,
    framework: str | None = None,
    rebuild: bool = False,
    *,
    as_json: bool = False,
) -> None:
    """Run the full ingestion pipeline.

    *framework* — restrict to a single source.
    *rebuild*   — clear all existing data first (without *framework*) or just
                  the named framework.
    """
    storage = Storage(config.data_dir, config.embedding_model)

    if rebuild and framework is None:
        storage.clear_all()

    sources = config.sources
    if framework:
        if framework not in sources:
            console.print(f"[red]Unknown framework: {framework}[/red]")
            console.print(f"Available: {', '.join(sources.keys())}")
            storage.close()
            return
        names = [framework]
    else:
        names = list(sources.keys())

    if not names:
        console.print("[yellow]No sources configured.[/yellow]")
        storage.close()
        return

    _ingest_many(config, storage, names, rebuild=rebuild, as_json=as_json)
    storage.close()
