"""TypeDoc-aware extractor — all TypeDoc sections are API definitions."""

import re

from rtfm.ingest.extractors.generic import _split_oversized
from rtfm.ingest.parsers.base import Section
from rtfm.models import KnowledgeUnit, UnitType

_CODE_BLOCK_RE = re.compile(r"```\w*\n(.*?)```", re.DOTALL)


class TypedocExtractor:
    """Extractor for TypeDoc HTML content.

    Every section produced by the TypeDoc parser is an API definition site.
    Sections with substantial code (signatures + examples) also get EXAMPLE units.
    """

    name = "typedoc"

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

            symbol = section.heading_hierarchy[-1] if section.heading_hierarchy else ""
            module_path = section.heading_hierarchy[0] if section.heading_hierarchy else framework
            symbols = [symbol] if symbol else []

            api_unit = KnowledgeUnit(
                type=UnitType.API,
                framework=framework,
                module_path=module_path,
                heading_hierarchy=section.heading_hierarchy,
                content=section.content,
                related_symbols=symbols,
                definition_symbols=[s.lower() for s in symbols],
                relevance_decay=1.0,
                language=language or "typescript",
                source_file=source_file,
            )
            units.extend(_split_oversized(api_unit))

        return units
