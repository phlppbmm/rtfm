"""Parser registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rtfm.ingest.parsers.base import Parser, Section
from rtfm.ingest.parsers.generic_md import GenericMarkdownParser, parse_markdown
from rtfm.ingest.parsers.mkdocs_md import MkDocsMarkdownParser
from rtfm.ingest.parsers.rustdoc_html import RustdocHtmlParser
from rtfm.ingest.parsers.sphinx_rst import SphinxRstParser
from rtfm.ingest.parsers.typedoc_html import TypedocHtmlParser

if TYPE_CHECKING:
    pass

_PARSERS: dict[str, type[Parser]] = {
    "generic_md": GenericMarkdownParser,  # type: ignore[dict-item]
    "sphinx": SphinxRstParser,  # type: ignore[dict-item]
    "mkdocs": MkDocsMarkdownParser,  # type: ignore[dict-item]
    "rustdoc": RustdocHtmlParser,  # type: ignore[dict-item]
    "typedoc": TypedocHtmlParser,  # type: ignore[dict-item]
    "llms_txt": GenericMarkdownParser,  # type: ignore[dict-item]
}


def register_parser(name: str, cls: type[Parser]) -> None:
    """Register a parser class by doc-system name."""
    _PARSERS[name] = cls


def get_parser(doc_system: str) -> Parser:
    """Get a parser instance for the given doc system."""
    cls = _PARSERS.get(doc_system, _PARSERS["generic_md"])
    return cls()  # type: ignore[misc]


__all__ = ["Parser", "Section", "get_parser", "register_parser", "parse_markdown"]
