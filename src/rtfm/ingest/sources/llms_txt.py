"""llms.txt / llms-full.txt source handler."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

import httpx

from rtfm.ingest.sources.base import (
    DownloadResult,
    ProgressCB,
    content_hash,
    version_key_from_http_response,
)
from rtfm.models import SourceConfig

log = logging.getLogger(__name__)

# Minimum quality bar for auto-detected llms.txt files.
_MIN_UNITS = 10
_MIN_AVG_CONTENT_LEN = 200


class LlmsTxtHandler:
    name: ClassVar[str] = "llms_txt"

    def probe(
        self,
        config: SourceConfig,
        on_progress: ProgressCB | None = None,
    ) -> SourceConfig | None:
        base = (config.url or "").rstrip("/*")
        if not base:
            return None

        for candidate_url in _llms_candidates(base):
            try:
                resp = httpx.get(candidate_url, follow_redirects=True, timeout=15.0)
                if resp.status_code != 200:
                    continue
                # Reject HTML error pages served with 200 status
                ct = resp.headers.get("content-type", "")
                if "text/html" in ct:
                    continue
                if _llms_quality_ok(resp.text):
                    if on_progress is not None:
                        on_progress(f"found {candidate_url}")
                    return replace(config, type="llms_txt", url=candidate_url)
            except httpx.HTTPError:
                continue
        return None

    def check_version(
        self,
        config: SourceConfig,
        on_progress: ProgressCB | None = None,
    ) -> str | None:
        try:
            resp = httpx.head(config.url, follow_redirects=True, timeout=15.0)
            if resp.status_code == 200:
                return version_key_from_http_response(resp)
            if on_progress is not None:
                on_progress(f"HTTP {resp.status_code}")
        except httpx.HTTPError as e:
            if on_progress is not None:
                on_progress(f"network error: {type(e).__name__}")
        return None

    def download(
        self,
        config: SourceConfig,
        work_dir: Path,
        on_progress: ProgressCB | None = None,
    ) -> DownloadResult:
        response = httpx.get(config.url, follow_redirects=True, timeout=60.0)
        response.raise_for_status()
        text = response.text
        filename = config.url.rsplit("/", 1)[-1]

        version_key = version_key_from_http_response(response)
        if not version_key:
            version_key = content_hash(text)

        return DownloadResult([(filename, text, "markdown")], version_key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _llms_candidates(base_url: str) -> list[str]:
    """Generate candidate URLs for llms.txt / llms-full.txt probing."""
    candidates: list[str] = []
    # At the base path
    candidates.append(f"{base_url}/llms-full.txt")
    candidates.append(f"{base_url}/llms.txt")
    # At the domain root
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    if root.rstrip("/") != base_url.rstrip("/"):
        candidates.append(f"{root}/llms-full.txt")
        candidates.append(f"{root}/llms.txt")
    return candidates


def _llms_quality_ok(text: str) -> bool:
    """Quick quality check: parse into units and verify minimum bar."""
    from rtfm.ingest.extractors import get_extractor
    from rtfm.ingest.parsers import get_parser

    parser = get_parser("llms_txt")
    extractor = get_extractor("llms_txt")

    sections = parser.parse(text, source_file="llms.txt")
    units = extractor.extract(
        sections,
        framework="__probe__",
        language="",
        source_file="llms.txt",
    )

    if len(units) < _MIN_UNITS:
        return False

    avg_len = sum(len(u.content) for u in units) / len(units) if units else 0
    return avg_len >= _MIN_AVG_CONTENT_LEN
