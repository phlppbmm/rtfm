"""Sphinx-aware extractor — marks autodoc definitions and downweights changelogs."""

import re

from rtfm.ingest.extractors.generic import (
    _classify_section,
    _clean_content,
    _compute_relevance_decay,
    _derive_module_path,
    _extract_symbols,
    _split_oversized,
)
from rtfm.ingest.extractors.generic import _RST_AUTODOC_SYMBOL
from rtfm.ingest.parsers.base import Section
from rtfm.models import KnowledgeUnit, UnitType

# Match the full autodoc directive with its fully qualified name
_AUTODOC_WITH_FQN = re.compile(
    r"\.\.\s+(?:auto(?:class|function|method|attribute|data|exception)"
    r"|(?:py:)?(?:function|class|method|attribute|data|exception|decorator))"
    r"::\s+([\w.]+)",
    re.MULTILINE,
)


def _extract_sphinx_definition_symbols(section: Section) -> list[str]:
    """Extract symbols canonically defined via Sphinx autodoc directives.

    Sections containing ``.. autoclass:: Foo`` or ``.. autofunction:: bar``
    are authoritative definitions — not just mentions.
    """
    defs: set[str] = set()
    for m in _RST_AUTODOC_SYMBOL.finditer(section.content):
        name = m.group(1).lstrip("~").rsplit(".", 1)[-1]
        if name:
            defs.add(name.lower().strip("()"))
    return list(defs)


def _autodoc_stub_content(section: Section) -> str:
    """Generate minimal stub content for autodoc-only sections.

    When a Sphinx RST page has ``.. autofunction:: foo`` with no body text
    (the body is auto-generated at Sphinx build time from docstrings),
    _clean_content strips the directive marker and leaves an empty string.
    This function provides a meaningful fallback based on the directive info.
    """
    for m in _AUTODOC_WITH_FQN.finditer(section.content):
        fqn = m.group(1)
        short_name = fqn.rsplit(".", 1)[-1]
        directive_type = section.heading.split()[0] if section.heading else "symbol"
        return f"`{fqn}` ({directive_type})"
    return ""


class SphinxExtractor:
    """Sphinx-aware extractor with definition site tracking and changelog decay."""

    name = "sphinx"

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

            unit_types = _classify_section(section)
            module_path = _derive_module_path(framework, source_file)
            symbols = _extract_symbols(section.content, language, section.heading_hierarchy)
            definition_symbols = _extract_sphinx_definition_symbols(section)
            # Ensure definition symbols are always included in related_symbols
            existing = {s.lower().strip("()") for s in symbols}
            for ds in definition_symbols:
                if ds not in existing:
                    symbols.append(ds)
                    existing.add(ds)
            decay = _compute_relevance_decay(source_file)
            cleaned_content = _clean_content(section.content)

            # For autodoc-only sections (directive stripped, body empty),
            # generate a stub so the unit isn't silently dropped.
            if not cleaned_content and definition_symbols:
                cleaned_content = _autodoc_stub_content(section)
                if cleaned_content:
                    unit_types = [UnitType.API]

            if not cleaned_content:
                continue

            for unit_type in unit_types:
                unit = KnowledgeUnit(
                    type=unit_type,
                    framework=framework,
                    module_path=module_path,
                    heading_hierarchy=section.heading_hierarchy,
                    content=cleaned_content,
                    related_symbols=symbols,
                    definition_symbols=definition_symbols,
                    relevance_decay=decay,
                    language=language,
                    source_file=source_file,
                )
                units.extend(_split_oversized(unit))

        return units
