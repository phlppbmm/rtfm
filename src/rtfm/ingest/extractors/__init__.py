"""Extractor registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rtfm.ingest.extractors.base import Extractor
from rtfm.ingest.extractors.generic import GenericExtractor, extract_units
from rtfm.ingest.extractors.mkdocs import MkDocsExtractor
from rtfm.ingest.extractors.rustdoc import RustdocExtractor
from rtfm.ingest.extractors.sphinx import SphinxExtractor
from rtfm.ingest.extractors.typedoc import TypedocExtractor

if TYPE_CHECKING:
    pass

_EXTRACTORS: dict[str, type[Extractor]] = {
    "generic": GenericExtractor,  # type: ignore[dict-item]
    "generic_md": GenericExtractor,  # type: ignore[dict-item]
    "sphinx": SphinxExtractor,  # type: ignore[dict-item]
    "mkdocs": MkDocsExtractor,  # type: ignore[dict-item]
    "rustdoc": RustdocExtractor,  # type: ignore[dict-item]
    "typedoc": TypedocExtractor,  # type: ignore[dict-item]
    "llms_txt": GenericExtractor,  # type: ignore[dict-item]
}


def register_extractor(name: str, cls: type[Extractor]) -> None:
    """Register an extractor class by doc-system name."""
    _EXTRACTORS[name] = cls


def get_extractor(doc_system: str) -> Extractor:
    """Get an extractor instance for the given doc system."""
    cls = _EXTRACTORS.get(doc_system, _EXTRACTORS["generic"])
    return cls()  # type: ignore[misc]


__all__ = ["Extractor", "get_extractor", "register_extractor", "extract_units"]
