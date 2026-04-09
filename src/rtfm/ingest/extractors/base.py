"""Base classes for knowledge unit extractors."""

from typing import Protocol, ClassVar

from rtfm.ingest.parsers.base import Section
from rtfm.models import KnowledgeUnit


class Extractor(Protocol):
    """Protocol for knowledge unit extractors."""

    name: ClassVar[str]

    def extract(
        self,
        sections: list[Section],
        *,
        framework: str,
        language: str,
        source_file: str,
    ) -> list[KnowledgeUnit]: ...
