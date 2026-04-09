"""MkDocs/Material parser — extends generic MD parser."""

from rtfm.ingest.parsers.base import Section
from rtfm.ingest.parsers.generic_md import parse_markdown


class MkDocsMarkdownParser:
    """Parser for MkDocs/Material documentation.

    Uses the generic markdown parser. MkDocs-specific admonition syntax
    (!!!, ///) is preserved in content for the extractor to classify.
    """

    name = "mkdocs"
    expected_content_type = "markdown"

    def parse(self, content: str, source_file: str) -> list[Section]:
        return parse_markdown(content, source_file=source_file)
