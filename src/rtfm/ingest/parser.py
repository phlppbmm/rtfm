"""Re-export shim — preserves backward compatibility for existing imports.

Canonical location: rtfm.ingest.parsers.base (Section) and
                    rtfm.ingest.parsers.generic_md (parse_markdown)
"""

from rtfm.ingest.parsers.base import Section
from rtfm.ingest.parsers.generic_md import parse_markdown

__all__ = ["Section", "parse_markdown"]
