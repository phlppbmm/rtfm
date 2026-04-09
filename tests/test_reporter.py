"""Tests for the Reporter abstraction."""

import json
import threading
import time

import pytest
from rich.console import Console

from rtfm.reporter import (
    JsonReporter,
    LiveReporter,
    PlainReporter,
    make_reporter,
)


@pytest.fixture
def force_tty_console():
    """Console that *thinks* it is a TTY, so LiveReporter renders."""
    return Console(force_terminal=True, force_interactive=True, width=120)


@pytest.fixture
def non_tty_console():
    """Console that explicitly does not pretend to be a TTY."""
    return Console(force_terminal=False, force_interactive=False, width=120)


class TestPlainReporter:
    def test_silent_during_work(self, capsys):
        console = Console(file=None, force_terminal=False)
        reporter = PlainReporter(console)
        with reporter:
            reporter.add("a", "task A")
            reporter.update("a", status="downloading", detail="50%")
        # PlainReporter writes nothing until finish() is called.
        captured = capsys.readouterr()
        assert "task A" not in captured.out

    def test_finish_emits_one_line(self, capsys):
        console = Console(force_terminal=False, width=120)
        reporter = PlainReporter(console)
        with reporter:
            reporter.add("a", "task A")
            reporter.finish("a", status="ok", detail="abc123")
        captured = capsys.readouterr()
        # The label should appear once on a finish line.
        assert "task A" in captured.out
        assert "ok" in captured.out

    def test_finish_unknown_task_uses_id(self, capsys):
        console = Console(force_terminal=False, width=120)
        reporter = PlainReporter(console)
        with reporter:
            reporter.finish("orphan", status="error")
        captured = capsys.readouterr()
        assert "orphan" in captured.out
        assert "error" in captured.out


class TestLiveReporter:
    def test_add_and_update_are_thread_safe(self, force_tty_console):
        reporter = LiveReporter(force_tty_console)

        with reporter:
            def worker(i: int) -> None:
                tid = f"t{i}"
                reporter.add(tid, f"task {i}")
                for j in range(5):
                    reporter.update(tid, status="downloading", detail=f"{j}/5")
                    time.sleep(0.001)
                reporter.finish(tid, status="ok", detail="done")

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        # All 8 tasks should be present and finished
        assert len(reporter._tasks) == 8
        assert all(t["status"] == "ok" for t in reporter._tasks.values())

    def test_update_unknown_task_is_noop(self, force_tty_console):
        reporter = LiveReporter(force_tty_console)
        with reporter:
            # Must not raise
            reporter.update("nonexistent", status="downloading")

    def test_log_is_buffered(self, force_tty_console):
        reporter = LiveReporter(force_tty_console)
        with reporter:
            reporter.log("hello")
            reporter.log("world")
        assert reporter._logs == ["hello", "world"]


class TestJsonReporter:
    def test_emits_events(self, capsys):
        reporter = JsonReporter()
        with reporter:
            reporter.add("a", "task A", status="pending")
            reporter.update("a", status="downloading", detail="50%")
            reporter.finish("a", status="ok")

        captured = capsys.readouterr()
        lines = [line for line in captured.out.strip().split("\n") if line]
        events = [json.loads(line) for line in lines]
        assert len(events) == 3
        assert events[0]["event"] == "add"
        assert events[0]["task"] == "a"
        assert events[1]["event"] == "update"
        assert events[1]["status"] == "downloading"
        assert events[2]["event"] == "finish"
        assert events[2]["status"] == "ok"

    def test_log_event(self, capsys):
        reporter = JsonReporter()
        with reporter:
            reporter.log("hello")
        captured = capsys.readouterr()
        event = json.loads(captured.out.strip())
        assert event["event"] == "log"
        assert event["message"] == "hello"


class TestFactory:
    def test_json_mode_returns_json_reporter(self):
        console = Console(force_terminal=True)
        reporter = make_reporter(console, as_json=True)
        assert isinstance(reporter, JsonReporter)

    def test_tty_returns_live_reporter(self, force_tty_console, monkeypatch):
        # Force stdout.isatty() to True
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)
        reporter = make_reporter(force_tty_console)
        assert isinstance(reporter, LiveReporter)

    def test_non_tty_returns_plain_reporter(self, non_tty_console, monkeypatch):
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)
        reporter = make_reporter(non_tty_console)
        assert isinstance(reporter, PlainReporter)
