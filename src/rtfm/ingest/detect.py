"""Auto-detection of documentation systems from content samples."""

import re

from rtfm.models import SourceConfig


def pre_detect_from_config(config: SourceConfig) -> str:
    """Try to detect doc system from config alone (URL patterns, source type).

    This runs BEFORE download, so the downloader knows whether to keep raw HTML.
    Returns empty string if no confident detection can be made.
    """
    if config.doc_system:
        return config.doc_system
    if config.type == "llms_txt":
        return "llms_txt"

    url = config.url or config.sitemap or ""
    if "docs.rs/" in url:
        return "rustdoc"
    # TypeDoc sites: detect from doc_system config or known URL patterns
    if "typedoc" in url.lower():
        return "typedoc"
    return ""


def detect_doc_system(
    files: list[tuple[str, str, str]],
    source_type: str,
) -> str:
    """Detect the documentation system from file samples.

    Args:
        files: List of (rel_path, content, content_type) tuples.
        source_type: The source type from config (github, llms_txt, local, website).

    Returns:
        One of: 'sphinx', 'mkdocs', 'rustdoc', 'typedoc', 'llms_txt', 'generic_md'.
    """
    if source_type == "llms_txt":
        return "llms_txt"

    # Check HTML-based doc systems first
    html_samples = [c for _, c, t in files[:10] if t == "html"]
    if html_samples:
        sample_html = "\n".join(html_samples[:5])
        if _is_rustdoc(sample_html):
            return "rustdoc"
        if _is_typedoc(sample_html):
            return "typedoc"

    # Check text-based doc systems — sample broadly to catch directives
    # that may not appear in the first few files (e.g. sqlalchemy puts
    # autodoc in deep reference pages, not in the table of contents).
    text_samples = [c[:2000] for _, c, t in files if t in ("markdown", "rst")]
    if text_samples:
        sample_text = "\n".join(text_samples)
        if _is_sphinx(sample_text):
            return "sphinx"
        if _is_mkdocs(sample_text):
            return "mkdocs"

    return "generic_md"


def _is_rustdoc(html: str) -> bool:
    return ('class="rustdoc"' in html
            or "Copy item path" in html
            or 'name="generator" content="rustdoc"' in html)


def _is_typedoc(html: str) -> bool:
    return ('class="tsd-' in html
            or "typedoc" in html.lower()[:2000])


def _is_sphinx(text: str) -> bool:
    return bool(re.search(
        r"^\.\.\s+(?:auto)?(?:class|function|method|attribute|module)::",
        text,
        re.MULTILINE,
    ))


def _is_mkdocs(text: str) -> bool:
    if re.search(r"^:::\s+[\w.]+", text, re.MULTILINE):
        return True
    if re.search(r"^!!!?\s+(?:note|tip|warning|example|info|danger|caution)\b", text, re.MULTILINE):
        return True
    if re.search(r"^///\s+(?:note|tip|warning|info|details|check)\b", text, re.MULTILINE):
        return True
    return False
