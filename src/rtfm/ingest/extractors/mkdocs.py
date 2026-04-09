"""MkDocs-aware extractor — marks mkdocstrings definitions and heading+code definitions."""

import re

from rtfm.ingest.extractors.generic import (
    _INLINE_CODE_SYMBOL,
    _MKDOCSTRINGS_INSERT,
    _classify_section,
    _clean_content,
    _compute_relevance_decay,
    _derive_module_path,
    _extract_symbols,
    _split_oversized,
)
from rtfm.ingest.parsers.base import Section
from rtfm.models import KnowledgeUnit

# Pattern: first non-empty line after heading is a code fence opening
_CODE_AFTER_HEADING_RE = re.compile(r"^```\w*\n", re.MULTILINE)

# Definition patterns for matching heading symbol in code
_CODE_DEFINITION_RE = re.compile(
    r"(?:async\s+)?def\s+(\w+)\s*\("
    r"|class\s+(\w+)[\s(:{]"
    r"|(?:export\s+)?function\s+(\w+)\s*[<(]"
    r"|interface\s+(\w+)[\s{<]"
    r"|(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]"
    r"|(?:pub\s+)?struct\s+(\w+)"
    r"|(?:pub\s+)?trait\s+(\w+)"
    r"|(?:pub\s+)?enum\s+(\w+)",
)


def _symbol_from_heading(heading: str) -> str | None:
    """Extract a symbol name from a heading, handling inline code."""
    m = _INLINE_CODE_SYMBOL.search(heading)
    if m:
        return m.group(1).strip("()")
    # Plain heading that looks like a symbol (CamelCase or function_name)
    clean = heading.strip().strip("`").strip("()")
    if re.match(r"^[$\w]+$", clean) and not clean.islower():
        return clean
    return None


def _code_defines_symbol(content: str, symbol: str) -> bool:
    """Check if the first code block in content defines the named symbol."""
    # Find the first code block
    m = re.search(r"```\w*\n(.*?)```", content, re.DOTALL)
    if not m:
        return False
    code = m.group(1)
    for dm in _CODE_DEFINITION_RE.finditer(code):
        for group in dm.groups():
            if group and group.lower() == symbol.lower():
                return True
    return False


def _extract_mkdocs_definition_symbols(section: Section) -> list[str]:
    """Extract symbols canonically defined in a MkDocs section.

    Three signals:
    1. mkdocstrings inserts: ::: pydantic.BaseModel
    2. Heading with inline code symbol + code block defining it
    3. Heading matching a symbol name + code block defining it
    """
    defs: set[str] = set()

    # 1. mkdocstrings inserts
    for m in _MKDOCSTRINGS_INSERT.finditer(section.content):
        name = m.group(1).rsplit(".", 1)[-1]
        if name:
            defs.add(name.lower().strip("()"))

    # 2 + 3. Heading-based definition detection
    if section.heading:
        symbol = _symbol_from_heading(section.heading)
        if symbol and _code_defines_symbol(section.content, symbol):
            defs.add(symbol.lower().strip("()"))

    return list(defs)


class MkDocsExtractor:
    """MkDocs-aware extractor with definition site tracking."""

    name = "mkdocs"

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
            definition_symbols = _extract_mkdocs_definition_symbols(section)
            # Ensure definition symbols are always included in related_symbols
            existing = {s.lower().strip("()") for s in symbols}
            for ds in definition_symbols:
                if ds not in existing:
                    symbols.append(ds)
                    existing.add(ds)
            decay = _compute_relevance_decay(source_file)
            cleaned_content = _clean_content(section.content)
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
