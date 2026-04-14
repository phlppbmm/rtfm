"""Source handler registry — download, version-check, and auto-detection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rtfm.ingest.sources.base import (
    DownloadResult,
    ProgressCB,
    SourceHandler,
    UnsupportedSource,
)
from rtfm.ingest.sources.github import GithubHandler
from rtfm.ingest.sources.llms_txt import LlmsTxtHandler
from rtfm.ingest.sources.local import LocalHandler
from rtfm.ingest.sources.website import WebsiteHandler

if TYPE_CHECKING:
    from rtfm.models import SourceConfig

__all__ = [
    "DownloadResult",
    "ProgressCB",
    "SourceHandler",
    "UnsupportedSource",
    "get_handler",
    "resolve_source_type",
]

_HANDLERS: dict[str, SourceHandler] = {
    "github": GithubHandler(),
    "llms_txt": LlmsTxtHandler(),
    "local": LocalHandler(),
    "website": WebsiteHandler(),
}

# Probe order for auto-detection. "local" is excluded — can't probe URLs.
_PROBE_ORDER = ["github", "llms_txt", "website"]


def get_handler(source_type: str) -> SourceHandler:
    """Return the handler for a concrete (non-auto) source type."""
    handler = _HANDLERS.get(source_type)
    if handler is None:
        raise ValueError(f"Unknown source type: {source_type}")
    return handler


def resolve_source_type(
    config: SourceConfig,
    on_progress: ProgressCB | None = None,
) -> SourceConfig:
    """Probe handlers in order and return a config with a concrete type.

    Raises *UnsupportedSource* if no handler can serve the URL.
    """
    from dataclasses import replace

    # Website-specific fields imply website type — skip probing.
    if config.url_filter or config.sitemap or config.urls:
        url = config.url
        if url and not url.endswith("/*"):
            url = url.rstrip("/") + "/*"
        return replace(config, type="website", url=url)

    # Local path implies local type.
    if config.path:
        return replace(config, type="local")

    for name in _PROBE_ORDER:
        result = _HANDLERS[name].probe(config, on_progress)
        if result is not None:
            return result
    raise UnsupportedSource(
        f"No plugin can handle '{config.url}'. "
        f"Supported: GitHub repos, llms.txt sites, crawlable doc sites. "
        f"A handler for this documentation format needs to be added."
    )
