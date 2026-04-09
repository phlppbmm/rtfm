"""rustdoc HTML parser — parses docs.rs pages directly from raw HTML."""

import re

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md

from rtfm.ingest.parsers.base import Section


def _docblock_to_md(element: Tag | None) -> str:
    """Convert a rustdoc .docblock element to clean markdown."""
    if element is None:
        return ""
    # Remove source links and implementation details
    for junk in element.select(".src, .source, .since, .stab"):
        junk.decompose()
    return md(str(element), heading_style="ATX", strip=["a"]).strip()


def _url_to_module(source_file: str) -> str:
    """Convert a docs.rs URL path to a logical Rust module path.

    Examples:
        reqwest/latest/reqwest/blocking/struct.Client.html → reqwest::blocking
        reqwest/latest/reqwest/struct.Client.html → reqwest
        reqwest/latest/reqwest/cookie/struct.Jar.html → reqwest::cookie
    """
    path = source_file.replace("\\", "/")
    # Strip .html suffix
    path = re.sub(r"\.html$", "", path)
    parts = path.split("/")

    # Drop common docs.rs URL segments
    # Pattern: crate_name/latest/crate_name/... or just crate_name/...
    # Find the last occurrence of a known struct/enum/trait/fn prefix
    cleaned: list[str] = []
    for part in parts:
        # Skip version segments and leaf items
        if part in ("latest", "all"):
            continue
        # Stop at the leaf item (struct.Foo, enum.Bar, fn.baz, etc.)
        if re.match(r"^(struct|enum|trait|fn|type|constant|macro|attr)\.", part):
            break
        # Skip "index" pages
        if part in ("index",):
            continue
        cleaned.append(part)

    if not cleaned:
        return "unknown"

    # Deduplicate consecutive identical segments (reqwest/reqwest → reqwest)
    deduped: list[str] = []
    for part in cleaned:
        if not deduped or deduped[-1] != part:
            deduped.append(part)

    return "::".join(deduped)


def _extract_title(main: Tag) -> tuple[str, str] | None:
    """Extract (symbol_name, kind) from the main heading.

    Returns e.g. ("Client", "struct") or ("get", "fn").
    """
    h1 = main.select_one(".main-heading h1, h1.fqn, h1")
    if not h1:
        return None
    text = h1.get_text(" ", strip=True)
    # "Struct reqwest :: Client" or "Function reqwest::blocking::get"
    # Also handles: "Struct Client" (short form)
    m = re.match(r"^(Struct|Enum|Trait|Function|Type\s+Alias|Constant|Macro|Module)\s+(.+)", text, re.IGNORECASE)
    if m:
        kind = m.group(1).lower().split()[0]  # "type alias" → "type"
        name = m.group(2).strip()
        # Get just the last segment: "reqwest :: blocking :: Client" → "Client"
        name = name.rsplit("::", 1)[-1].strip().strip(":")
        if name:
            return name, kind
    return None


class RustdocHtmlParser:
    """Parser for rustdoc-generated HTML (docs.rs pages)."""

    name = "rustdoc"
    expected_content_type = "html"

    def parse(self, content: str, source_file: str) -> list[Section]:
        soup = BeautifulSoup(content, "lxml")

        # Find the main content area
        main = soup.select_one("#main-content, section#main-content, .main-heading")
        if main is None:
            # Try the whole body as fallback
            main = soup.select_one("body")
        if main is None:
            return []

        # Remove all noise elements
        for junk in main.select(
            "button, .copy-path, .src-content, .sidebar, "
            "nav, .sub-heading, .out-of-band, .anchor, "
            ".toggle-wrapper summary, .notable-traits"
        ):
            junk.decompose()

        sections: list[Section] = []
        module_path = _url_to_module(source_file)
        title = _extract_title(soup)  # Use full soup, not stripped main

        if title:
            symbol, kind = title

            # Main docblock
            docblock = main.select_one(".docblock")
            description = _docblock_to_md(docblock)

            # Also capture the signature if present
            sig = main.select_one("pre.rust, .item-decl pre")
            sig_text = sig.get_text(strip=True) if sig else ""

            full_content = ""
            if sig_text:
                full_content = f"```rust\n{sig_text}\n```\n\n"
            full_content += description

            if full_content.strip():
                sections.append(Section(
                    heading=f"{kind} {symbol}",
                    level=1,
                    heading_hierarchy=[module_path, symbol],
                    content=full_content.strip(),
                    source_file=source_file,
                ))

            # Method sections
            for impl_block in main.select("details.method-toggle, section.method, section.tymethod"):
                method_section = self._method_to_section(
                    impl_block,
                    parent_module=module_path,
                    parent_symbol=symbol,
                    source_file=source_file,
                )
                if method_section:
                    sections.append(method_section)

        elif module_path:
            # Module-level page (index) — extract all items as sections
            for item in main.select(".item-table .item-name, .item-row"):
                item_link = item.select_one("a")
                if not item_link:
                    continue
                item_name = item_link.get_text(strip=True)
                desc_el = item.find_next_sibling(".desc") or item.select_one(".desc")
                desc = desc_el.get_text(strip=True) if desc_el else ""
                if item_name:
                    sections.append(Section(
                        heading=item_name,
                        level=2,
                        heading_hierarchy=[module_path, item_name],
                        content=desc,
                        source_file=source_file,
                    ))

        return sections

    def _method_to_section(
        self,
        element: Tag,
        *,
        parent_module: str,
        parent_symbol: str,
        source_file: str,
    ) -> Section | None:
        """Convert a method/function element to a Section."""
        # Try to find the method name
        heading_el = element.select_one("h4.code-header, h3.code-header, .method-signature")
        if heading_el is None:
            heading_el = element.select_one("summary h4, summary h3")
        if heading_el is None:
            return None

        sig_text = heading_el.get_text(" ", strip=True)
        # Extract method name from signature: "pub fn new() -> Client" → "new"
        m = re.search(r"\bfn\s+(\w+)", sig_text)
        method_name = m.group(1) if m else sig_text.split("(")[0].split()[-1] if "(" in sig_text else None
        if not method_name or not method_name.isidentifier():
            return None

        # Get the docblock
        docblock = element.select_one(".docblock")
        description = _docblock_to_md(docblock)

        content = f"```rust\n{sig_text}\n```\n\n{description}".strip()
        if not content:
            return None

        return Section(
            heading=f"fn {parent_symbol}::{method_name}",
            level=2,
            heading_hierarchy=[parent_module, parent_symbol, method_name],
            content=content,
            source_file=source_file,
        )
