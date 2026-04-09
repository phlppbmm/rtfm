"""rustdoc-aware extractor — all rustdoc sections are API definitions."""

import re

from rtfm.ingest.extractors.generic import _split_oversized
from rtfm.ingest.parsers.base import Section
from rtfm.models import KnowledgeUnit, UnitType

_CODE_BLOCK_RE = re.compile(r"```\w*\n(.*?)```", re.DOTALL)


class RustdocExtractor:
    """Extractor for rustdoc HTML content.

    Every section produced by the rustdoc parser is an API definition site.
    Sections with substantial code blocks also get an EXAMPLE unit.
    """

    name = "rustdoc"

    def extract(
        self,
        sections: list[Section],
        *,
        framework: str,
        language: str,
        source_file: str,
    ) -> list[KnowledgeUnit]:
        units: list[KnowledgeUnit] = []

        for section in sections:
            if not section.content.strip():
                continue

            # The primary symbol is the last element of the heading hierarchy
            symbol = section.heading_hierarchy[-1] if section.heading_hierarchy else ""
            module_path = section.heading_hierarchy[0] if section.heading_hierarchy else framework
            symbols = [symbol] if symbol else []

            # Every rustdoc section is an API definition
            api_unit = KnowledgeUnit(
                type=UnitType.API,
                framework=framework,
                module_path=module_path,
                heading_hierarchy=section.heading_hierarchy,
                content=section.content,
                related_symbols=symbols,
                definition_symbols=[s.lower() for s in symbols],
                relevance_decay=1.0,
                language=language or "rust",
                source_file=source_file,
            )
            units.extend(_split_oversized(api_unit))

            # If there are substantial code blocks, also create an example unit
            code_blocks = _CODE_BLOCK_RE.findall(section.content)
            total_code_lines = sum(block.count("\n") for block in code_blocks)
            if total_code_lines >= 5:
                example_unit = KnowledgeUnit(
                    type=UnitType.EXAMPLE,
                    framework=framework,
                    module_path=module_path,
                    heading_hierarchy=section.heading_hierarchy,
                    content=section.content,
                    related_symbols=symbols,
                    definition_symbols=[],
                    relevance_decay=1.0,
                    language=language or "rust",
                    source_file=source_file,
                )
                units.extend(_split_oversized(example_unit))

        return units
