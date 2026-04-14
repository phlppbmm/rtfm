"""Downloaders for documentation sources — thin facade over source handlers."""

from __future__ import annotations

from pathlib import Path

from rtfm.ingest.sources import (
    DownloadResult,
    ProgressCB,
    UnsupportedSource,
    get_handler,
    resolve_source_type,
)
from rtfm.models import SourceConfig

# Re-export for backward compatibility
__all__ = ["DownloadResult", "ProgressCB", "UnsupportedSource", "download_source", "check_remote_version"]


def download_source(
    config: SourceConfig,
    work_dir: Path,
    on_progress: ProgressCB | None = None,
) -> DownloadResult:
    """Download documentation files. Returns files and a version key."""
    if config.type == "auto":
        config = resolve_source_type(config, on_progress)
    return get_handler(config.type).download(config, work_dir, on_progress=on_progress)


def check_remote_version(
    config: SourceConfig,
    on_progress: ProgressCB | None = None,
) -> str | None:
    """Check the remote version without downloading full content."""
    if config.type == "auto":
        config = resolve_source_type(config, on_progress)
    return get_handler(config.type).check_version(config, on_progress=on_progress)
