"""Re-export shim — preserves backward compatibility for existing imports.

Canonical location: rtfm.ingest.extractors.generic
"""

from rtfm.ingest.extractors.generic import (
    _classify_section,
    _clean_content,
    _derive_module_path,
    _extract_symbols,
    extract_units,
)

__all__ = [
    "extract_units",
    "_classify_section",
    "_extract_symbols",
    "_derive_module_path",
    "_clean_content",
]
