"""Sphinx/RST parser — extends generic MD parser with autodoc directive splitting."""

import re

from rtfm.ingest.parsers.base import Section
from rtfm.ingest.parsers.generic_md import parse_markdown

# Autodoc directives that should act as section boundaries
_AUTODOC_DIRECTIVE_RE = re.compile(
    r"^\.\.\s+(auto(?:class|function|method|attribute|module|data|exception)"
    r"|(?:py:)?(?:function|class|method|attribute|data|exception|decorator|module))"
    r"::\s+([~]?[\w.]+)",
    re.MULTILINE,
)


def _split_autodoc_sections(sections: list[Section]) -> list[Section]:
    """Split sections that contain multiple autodoc directives into one section each.

    Sphinx API reference pages often have one heading (e.g. "Class Mapping API")
    followed by many ``.. autoclass::`` / ``.. autofunction::`` directives.
    The generic parser treats the entire page as one section because there are
    no sub-headings. This function splits at autodoc directive boundaries,
    creating a section per directive.
    """
    result: list[Section] = []

    for section in sections:
        matches = list(_AUTODOC_DIRECTIVE_RE.finditer(section.content))
        if len(matches) <= 1:
            result.append(section)
            continue

        # Split the content at each directive boundary
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(section.content)
            chunk = section.content[start:end].strip()
            if not chunk:
                continue

            # Extract the symbol name from the directive
            symbol = m.group(2).lstrip("~").rsplit(".", 1)[-1]
            directive_type = m.group(1).replace("auto", "").replace("py:", "")

            # Build a heading hierarchy: parent heading + symbol
            hierarchy = section.heading_hierarchy.copy()
            if symbol:
                hierarchy.append(symbol)

            result.append(Section(
                heading=f"{directive_type} {symbol}" if symbol else section.heading,
                level=section.level + 1,
                heading_hierarchy=hierarchy,
                content=chunk,
                source_file=section.source_file,
            ))

        # If there's content BEFORE the first directive, keep it as the original section
        preamble = section.content[:matches[0].start()].strip()
        if preamble:
            result.insert(len(result) - len(matches), Section(
                heading=section.heading,
                level=section.level,
                heading_hierarchy=section.heading_hierarchy,
                content=preamble,
                source_file=section.source_file,
            ))

    return result


class SphinxRstParser:
    """Parser for Sphinx/reStructuredText documentation.

    Extends the generic markdown parser with autodoc directive splitting,
    so Sphinx API reference pages get one section per documented symbol
    instead of one giant section.
    """

    name = "sphinx"
    expected_content_type = "markdown"

    def parse(self, content: str, source_file: str) -> list[Section]:
        sections = parse_markdown(content, source_file=source_file)
        return _split_autodoc_sections(sections)
