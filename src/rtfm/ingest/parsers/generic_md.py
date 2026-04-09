"""Generic Markdown + RST parser — splits documents into sections preserving heading hierarchy."""


import re

from rtfm.ingest.parsers.base import Section


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+\{.*\})?\s*$", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)

# RST underline characters mapped to heading levels
_RST_ADORNMENT_CHARS = "=-~^\"'`"
_RST_HEADING_RE = re.compile(
    r"^(?P<overline>[=\-~^\"'`]+\n)?(?P<title>[^\n]+)\n(?P<underline>[=\-~^\"'`]+)$",
    re.MULTILINE,
)


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter from the start of a markdown document."""
    return _FRONTMATTER_RE.sub("", text)


def _rst_to_md_headings(text: str) -> str:
    """Convert RST-style underline headings to Markdown #-headings.

    RST uses underline characters (=, -, ~, etc.) to denote heading levels.
    The level is determined by order of first appearance in the document.
    """
    # Discover adornment character order (first seen = highest level)
    char_order: list[str] = []

    for m in _RST_HEADING_RE.finditer(text):
        underline = m.group("underline")
        title = m.group("title").strip()
        if not underline or not title:
            continue
        char = underline[0]
        # Underline must be at least as long as the title and all same char
        if len(underline) >= len(title) and underline == char * len(underline) and char not in char_order:
            char_order.append(char)

    if not char_order:
        return text

    def _replace(m: re.Match[str]) -> str:
        underline = m.group("underline")
        title = m.group("title").strip()
        m.group("overline")
        char = underline[0]
        if len(underline) < len(title) or underline != char * len(underline):
            return m.group(0)
        if char not in char_order:
            return m.group(0)
        level = char_order.index(char) + 1
        level = min(level, 6)
        prefix = "#" * level
        return f"{prefix} {title}"

    return _RST_HEADING_RE.sub(_replace, text)


def parse_markdown(text: str, source_file: str = "", split_level: int = 2) -> list[Section]:
    """Parse markdown into sections split at headings of `split_level` or above.

    Headings below `split_level` are included in their parent section's content.
    The heading hierarchy is tracked across all levels for context.
    """
    text = _strip_frontmatter(text)

    # Convert RST headings if this looks like an RST file
    if source_file.endswith(".rst") or (not _HEADING_RE.search(text) and _RST_HEADING_RE.search(text)):
        text = _rst_to_md_headings(text)

    # Find all headings
    headings: list[tuple[int, str, int]] = []  # (level, title, char_offset)
    for m in _HEADING_RE.finditer(text):
        level = len(m.group(1))
        title = m.group(2).strip()
        headings.append((level, title, m.start()))

    if not headings:
        # No headings — entire document is one section
        content = text.strip()
        if content:
            return [Section(
                heading="",
                level=0,
                heading_hierarchy=[],
                content=content,
                source_file=source_file,
            )]
        return []

    sections: list[Section] = []
    # Content before the first heading
    preamble = text[: headings[0][2]].strip()
    if preamble:
        sections.append(Section(
            heading="",
            level=0,
            heading_hierarchy=[],
            content=preamble,
            source_file=source_file,
        ))

    # Track heading hierarchy as a stack: list of (level, title)
    hierarchy_stack: list[tuple[int, str]] = []

    for i, (level, title, offset) in enumerate(headings):
        # Determine content end
        content_end = headings[i + 1][2] if i + 1 < len(headings) else len(text)

        # Update hierarchy stack: pop everything at this level or deeper
        hierarchy_stack = [(lvl, t) for lvl, t in hierarchy_stack if lvl < level]
        hierarchy_stack.append((level, title))
        full_hierarchy = [t for _, t in hierarchy_stack]

        # Only create a new section for headings at or above split_level
        if level <= split_level:
            # Extract content (everything after the heading line itself)
            heading_line_end = text.index("\n", offset) + 1 if "\n" in text[offset:] else len(text)
            content = text[heading_line_end:content_end].strip()
            sections.append(Section(
                heading=title,
                level=level,
                heading_hierarchy=full_hierarchy.copy(),
                content=content,
                source_file=source_file,
            ))
        else:
            # Append to the last section's content (sub-heading content belongs to parent)
            if sections:
                sub_content = text[offset:content_end].strip()
                sections[-1].content = sections[-1].content + "\n\n" + sub_content
                # Ensure the sub-heading's hierarchy is not lost — it's part of content

    return sections


class GenericMarkdownParser:
    """Generic Markdown + RST parser. Default fallback for unknown doc systems."""

    name = "generic_md"
    expected_content_type = "markdown"

    def parse(self, content: str, source_file: str) -> list[Section]:
        return parse_markdown(content, source_file=source_file)
