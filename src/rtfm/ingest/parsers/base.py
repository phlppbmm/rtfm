"""Base classes for document parsers."""

import re
from dataclasses import dataclass
from typing import Protocol, ClassVar


@dataclass
class Section:
    """A section extracted from a document."""

    heading: str
    level: int
    heading_hierarchy: list[str]
    content: str  # Everything between this heading and the next same-or-higher-level heading
    source_file: str = ""


class Parser(Protocol):
    """Protocol for document parsers."""

    name: ClassVar[str]
    expected_content_type: ClassVar[str]

    def parse(self, content: str, source_file: str) -> list[Section]: ...
