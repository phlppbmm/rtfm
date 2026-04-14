"""Base types for source handlers."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Protocol

import httpx

from rtfm.models import SourceConfig

ProgressCB = Callable[[str], None]


class DownloadResult:
    """Result of downloading a documentation source."""

    def __init__(self, files: list[tuple[str, str, str]], version_key: str):
        self.files = files  # [(relative_path, content, content_type), ...]
        self.version_key = version_key  # git SHA, content hash, or mtime


class UnsupportedSource(Exception):
    """No registered handler can serve this URL."""


class SourceHandler(Protocol):
    """Protocol that every source handler must satisfy."""

    name: ClassVar[str]

    def probe(
        self,
        config: SourceConfig,
        on_progress: ProgressCB | None = None,
    ) -> SourceConfig | None:
        """Auto-detection: can this handler serve the given URL well?

        Returns a resolved *SourceConfig* (with concrete ``type``) if yes,
        ``None`` to pass to the next handler in probe order.
        """
        ...

    def check_version(
        self,
        config: SourceConfig,
        on_progress: ProgressCB | None = None,
    ) -> str | None:
        """Cheap remote version check.  Returns a version key or None."""
        ...

    def download(
        self,
        config: SourceConfig,
        work_dir: Path,
        on_progress: ProgressCB | None = None,
    ) -> DownloadResult:
        """Download documentation files."""
        ...


# ---------------------------------------------------------------------------
# Shared HTTP utilities
# ---------------------------------------------------------------------------

def version_key_from_http_response(resp: httpx.Response) -> str | None:
    """Extract a version key from HTTP headers, if possible."""
    etag = resp.headers.get("etag")
    if etag:
        return f"etag:{etag}"
    last_mod = resp.headers.get("last-modified")
    if last_mod:
        return f"mod:{last_mod}"
    return None


def content_hash(text: str) -> str:
    """Short SHA-256 hash of text content."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]
