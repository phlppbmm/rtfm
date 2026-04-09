"""Concurrent task reporter for the rtfm CLI.

The Reporter abstraction lets background workers emit *state* (label, status,
detail) for named tasks. The chosen Reporter implementation decides how that
state is rendered:

* `LiveReporter` — `rich.Live` + `Table`, repaints the whole screen region on
  every update. Multiple workers can update concurrently without their output
  colliding.
* `PlainReporter` — append-only fallback for non-TTY environments (pipes, CI).
  Stays silent during work and prints one line per task on `finish`.
* `JsonReporter` — emits one JSON line per state change for `--json` mode.

The Reporter is passed into pipeline / downloader functions instead of having
them call `print` / `console.print` directly. That decouples *what* is happening
from *how* it is rendered, and is the prerequisite for fixing the print-stream
collisions described in `issues/00-render-loop-statt-print-stream.md`.
"""

from __future__ import annotations

import json
import sys
import threading
from typing import Protocol

import click
from rich.console import Console
from rich.live import Live
from rich.markup import escape
from rich.table import Table


# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------

# Known status values get colors and indicators. Unknown ones render in white.
_STATUS_STYLE: dict[str, tuple[str, str]] = {
    "pending": ("dim", "·"),
    "checking": ("yellow", "⠼"),
    "downloading": ("yellow", "⠼"),
    "crawling": ("yellow", "⠼"),
    "fetching": ("yellow", "⠼"),
    "parsing": ("blue", "⠼"),
    "embedding": ("cyan", "⠼"),
    "ok": ("green", "✓"),
    "done": ("green", "✓"),
    "outdated": ("yellow", "⚠"),
    "skipped": ("dim", "-"),
    "error": ("red", "✗"),
}


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class Reporter(Protocol):
    """A renderer for concurrent named tasks.

    Workers call `add` once when starting a task, then `update` to advance
    status/detail, then `finish` once when the task settles. Implementations
    are responsible for thread safety.
    """

    def add(self, task_id: str, label: str, status: str = "pending", detail: str = "") -> None: ...

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        detail: str | None = None,
    ) -> None: ...

    def finish(
        self,
        task_id: str,
        *,
        status: str = "done",
        detail: str | None = None,
    ) -> None: ...

    def log(self, message: str) -> None:
        """Emit a one-shot log line that is not associated with any task."""

    def __enter__(self) -> "Reporter": ...

    def __exit__(self, *args: object) -> None: ...


# ---------------------------------------------------------------------------
# LiveReporter — TTY render-loop
# ---------------------------------------------------------------------------


class LiveReporter:
    """Render all tasks in a single `rich.Live` table that repaints on update.

    Thread-safe. Use as a context manager.
    """

    def __init__(self, console: Console, title: str | None = None) -> None:
        self._console = console
        self._title = title
        self._tasks: dict[str, dict[str, str]] = {}
        self._order: list[str] = []
        self._logs: list[str] = []
        self._lock = threading.Lock()
        self._live: Live | None = None

    # -- Reporter API ----------------------------------------------------

    def add(self, task_id: str, label: str, status: str = "pending", detail: str = "") -> None:
        with self._lock:
            if task_id not in self._tasks:
                self._order.append(task_id)
            self._tasks[task_id] = {"label": label, "status": status, "detail": detail}
            self._refresh_locked()

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if status is not None:
                task["status"] = status
            if detail is not None:
                task["detail"] = detail
            self._refresh_locked()

    def finish(
        self,
        task_id: str,
        *,
        status: str = "done",
        detail: str | None = None,
    ) -> None:
        self.update(task_id, status=status, detail=detail)

    def log(self, message: str) -> None:
        with self._lock:
            self._logs.append(message)
            self._refresh_locked()

    # -- Context manager -------------------------------------------------

    def __enter__(self) -> "LiveReporter":
        self._live = Live(
            self._render(),
            console=self._console,
            refresh_per_second=8,
            transient=False,
        )
        self._live.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        if self._live is not None:
            with self._lock:
                self._live.update(self._render())
            self._live.__exit__(*args)
            self._live = None

    # -- Internals -------------------------------------------------------

    def _refresh_locked(self) -> None:
        if self._live is not None:
            self._live.update(self._render())

    def _render(self) -> Table:
        table = Table(
            show_header=False,
            box=None,
            pad_edge=False,
            padding=(0, 1),
            title=self._title,
            title_style="bold",
            title_justify="left",
        )
        table.add_column("Label", style="bold", no_wrap=True)
        table.add_column("Status", no_wrap=True)
        table.add_column("Detail", overflow="ellipsis", no_wrap=True)
        for task_id in self._order:
            task = self._tasks[task_id]
            status = task["status"]
            color, icon = _STATUS_STYLE.get(status, ("white", " "))
            table.add_row(
                escape(task["label"]),
                f"[{color}]{icon} {escape(status)}[/{color}]",
                f"[dim]{escape(task['detail'])}[/dim]",
            )
        for log_line in self._logs[-5:]:
            table.add_row("", "", f"[dim]{escape(log_line)}[/dim]")
        return table


# ---------------------------------------------------------------------------
# PlainReporter — silent during work, one line per finished task
# ---------------------------------------------------------------------------


class PlainReporter:
    """Append-only reporter for non-TTY output (pipes, CI, redirected stdout)."""

    def __init__(self, console: Console) -> None:
        self._console = console
        self._labels: dict[str, str] = {}
        self._last_detail: dict[str, str] = {}
        self._lock = threading.Lock()

    def add(self, task_id: str, label: str, status: str = "pending", detail: str = "") -> None:
        with self._lock:
            self._labels[task_id] = label
            if detail:
                self._last_detail[task_id] = detail

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        detail: str | None = None,
    ) -> None:
        # Silent during work, but remember the latest detail so `finish`
        # can echo it (e.g. "rate limited") even when the caller does not
        # explicitly pass a detail to finish().
        if detail is not None:
            with self._lock:
                self._last_detail[task_id] = detail

    def finish(
        self,
        task_id: str,
        *,
        status: str = "done",
        detail: str | None = None,
    ) -> None:
        with self._lock:
            label = self._labels.get(task_id, task_id)
            shown_detail = detail if detail is not None else self._last_detail.get(task_id, "")
        color, icon = _STATUS_STYLE.get(status, ("white", " "))
        suffix = f" [dim]({escape(shown_detail)})[/dim]" if shown_detail else ""
        self._console.print(f"  [{color}]{icon}[/{color}] [bold]{escape(label)}[/bold] {status}{suffix}")

    def log(self, message: str) -> None:
        self._console.print(f"  [dim]{escape(message)}[/dim]")

    def __enter__(self) -> "PlainReporter":
        return self

    def __exit__(self, *args: object) -> None:
        return


# ---------------------------------------------------------------------------
# JsonReporter — one JSON line per state change
# ---------------------------------------------------------------------------


class JsonReporter:
    """Emit one JSON-lines event per state change. For --json mode."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _emit(self, event: dict[str, str]) -> None:
        with self._lock:
            click.echo(json.dumps(event, ensure_ascii=False))

    def add(self, task_id: str, label: str, status: str = "pending", detail: str = "") -> None:
        self._emit({"event": "add", "task": task_id, "label": label, "status": status, "detail": detail})

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        detail: str | None = None,
    ) -> None:
        event: dict[str, str] = {"event": "update", "task": task_id}
        if status is not None:
            event["status"] = status
        if detail is not None:
            event["detail"] = detail
        self._emit(event)

    def finish(
        self,
        task_id: str,
        *,
        status: str = "done",
        detail: str | None = None,
    ) -> None:
        event: dict[str, str] = {"event": "finish", "task": task_id, "status": status}
        if detail is not None:
            event["detail"] = detail
        self._emit(event)

    def log(self, message: str) -> None:
        self._emit({"event": "log", "message": message})

    def __enter__(self) -> "JsonReporter":
        return self

    def __exit__(self, *args: object) -> None:
        return


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_reporter(
    console: Console,
    *,
    title: str | None = None,
    as_json: bool = False,
) -> Reporter:
    """Pick a reporter implementation based on context.

    * `as_json=True` → JsonReporter (one JSON line per event)
    * stdout is a TTY → LiveReporter (full Live render loop)
    * otherwise → PlainReporter (one line per finished task)
    """
    if as_json:
        return JsonReporter()
    if console.is_terminal and sys.stdout.isatty():
        return LiveReporter(console, title=title)
    return PlainReporter(console)
