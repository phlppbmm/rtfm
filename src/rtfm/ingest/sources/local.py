"""Local filesystem source handler."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import ClassVar

from rtfm.ingest.sources.base import DownloadResult, ProgressCB
from rtfm.models import SourceConfig


class LocalHandler:
    name: ClassVar[str] = "local"

    def probe(
        self,
        config: SourceConfig,
        on_progress: ProgressCB | None = None,
    ) -> SourceConfig | None:
        # Local paths can't be probed from URLs.
        return None

    def check_version(
        self,
        config: SourceConfig,
        on_progress: ProgressCB | None = None,
    ) -> str | None:
        root = Path(config.path)
        if not root.exists():
            return None

        total_size = 0
        newest_mtime = 0.0
        count = 0
        for f in root.rglob("*.md"):
            if f.is_file():
                stat = f.stat()
                total_size += stat.st_size
                newest_mtime = max(newest_mtime, stat.st_mtime)
                count += 1

        key = f"{count}:{total_size}:{int(newest_mtime)}"
        return hashlib.sha256(key.encode()).hexdigest()[:12]

    def download(
        self,
        config: SourceConfig,
        work_dir: Path,
        on_progress: ProgressCB | None = None,
    ) -> DownloadResult:
        import fnmatch

        root = Path(config.path)
        if not root.exists():
            raise FileNotFoundError(f"Local source path does not exist: {config.path}")

        results: list[tuple[str, str, str]] = []
        total_size = 0
        newest_mtime = 0.0
        count = 0

        for md_file in root.rglob("*"):
            if not md_file.is_file():
                continue
            if not fnmatch.fnmatch(md_file.name, "*.md"):
                continue
            try:
                stat = md_file.stat()
                total_size += stat.st_size
                newest_mtime = max(newest_mtime, stat.st_mtime)
                count += 1
                rel = str(md_file.relative_to(root)).replace("\\", "/")
                content = md_file.read_text(encoding="utf-8")
                ctype = "rst" if md_file.suffix == ".rst" else "markdown"
                results.append((rel, content, ctype))
            except (UnicodeDecodeError, OSError):
                continue

        key = f"{count}:{total_size}:{int(newest_mtime)}"
        version_key = hashlib.sha256(key.encode()).hexdigest()[:12]
        return DownloadResult(results, version_key)
