"""TypeDoc HTML parser — parses TypeDoc-generated documentation pages."""

import re

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md

from rtfm.ingest.parsers.base import Section


def _extract_page_kind_and_name(soup: BeautifulSoup) -> tuple[str, str] | None:
    """Extract (kind, name) from the page title.

    TypeDoc titles follow: "Class AgileClient", "Interface User", etc.
    """
    title_el = soup.select_one(".tsd-page-title h1, h1")
    if not title_el:
        return None
    text = title_el.get_text(strip=True)
    m = re.match(
        r"^(Class|Interface|Enumeration|Type [Aa]lias|Namespace|Function|Variable)\s+(.+)",
        text,
    )
    if m:
        return m.group(1).lower().split()[0], m.group(2).strip()
    return None


def _extract_module_path(soup: BeautifulSoup) -> str:
    """Extract module path from breadcrumbs."""
    crumbs = soup.select(".tsd-breadcrumb li")
    parts = [li.get_text(strip=True) for li in crumbs if li.get_text(strip=True)]
    return ".".join(parts) if parts else ""


def _signature_text(el: Tag) -> str:
    """Extract a clean text signature from a .tsd-signature element."""
    if not el:
        return ""
    return el.get_text(" ", strip=True)


def _comment_to_md(el: Tag | None) -> str:
    """Convert a .tsd-comment element to markdown."""
    if not el:
        return ""
    return md(str(el), heading_style="ATX", strip=["a"]).strip()


class TypedocHtmlParser:
    """Parser for TypeDoc-generated HTML documentation."""

    name = "typedoc"
    expected_content_type = "html"

    def parse(self, content: str, source_file: str) -> list[Section]:
        soup = BeautifulSoup(content, "lxml")

        # Skip non-content pages
        page_info = _extract_page_kind_and_name(soup)
        if not page_info:
            return []

        kind, name = page_info

        # Skip namespace/module index pages (just link lists)
        if kind in ("namespace", "module"):
            return []

        module_path = _extract_module_path(soup) or name
        sections: list[Section] = []

        # Remove noise elements
        for junk in soup.select("nav, .tsd-breadcrumb, footer, .tsd-sources, .tsd-generator"):
            junk.decompose()

        # Top-level description
        top_comment = soup.select_one(".tsd-panel > .tsd-comment")
        hierarchy = soup.select_one(".tsd-panel.tsd-hierarchy")
        desc_parts: list[str] = []
        if hierarchy:
            desc_parts.append(_comment_to_md(hierarchy))
        if top_comment:
            desc_parts.append(_comment_to_md(top_comment))

        if desc_parts:
            sections.append(Section(
                heading=f"{kind} {name}",
                level=1,
                heading_hierarchy=[module_path, name],
                content="\n\n".join(desc_parts),
                source_file=source_file,
            ))

        # Member groups (Methods, Properties, Constructors, etc.)
        for group in soup.select(".tsd-member-group"):
            group_heading = group.select_one("h2")
            group_name = group_heading.get_text(strip=True) if group_heading else ""

            for member in group.select("section.tsd-member, .tsd-panel.tsd-member"):
                member_section = self._parse_member(
                    member,
                    parent_name=name,
                    group_name=group_name,
                    module_path=module_path,
                    source_file=source_file,
                )
                if member_section:
                    sections.append(member_section)

        # If no member groups found but page has content, create a single section
        if not sections:
            body = soup.select_one("#tsd-content, .container-main-content, body")
            if body:
                text = md(str(body), heading_style="ATX").strip()
                if text and len(text) > 30:
                    sections.append(Section(
                        heading=f"{kind} {name}",
                        level=1,
                        heading_hierarchy=[module_path, name],
                        content=text[:8000],
                        source_file=source_file,
                    ))

        return sections

    def _parse_member(
        self,
        element: Tag,
        *,
        parent_name: str,
        group_name: str,
        module_path: str,
        source_file: str,
    ) -> Section | None:
        """Parse a single member (method, property, constructor) into a Section."""
        # Get member name from heading
        heading_el = element.select_one("h3.tsd-anchor-link, h3")
        if not heading_el:
            return None

        # Remove tag badges from the heading text
        for tag in heading_el.select("code.tsd-tag"):
            tag.decompose()

        member_name = heading_el.get_text(strip=True).strip()
        if not member_name:
            return None

        # Build content from signatures and descriptions
        content_parts: list[str] = []

        # Collect all signatures (overloads)
        for sig in element.select(".tsd-signature"):
            sig_text = _signature_text(sig)
            if sig_text:
                content_parts.append(f"```typescript\n{sig_text}\n```")

        # Collect descriptions
        for comment in element.select(".tsd-comment"):
            text = _comment_to_md(comment)
            if text:
                content_parts.append(text)

        # Collect parameter info
        params_title = element.select_one(".tsd-parameters-title")
        if params_title:
            params_list = element.select_one("ul.tsd-parameter-list")
            if params_list:
                content_parts.append(_comment_to_md(params_list))

        # Collect return type
        returns = element.select_one("h4.tsd-returns-title")
        if returns:
            content_parts.append(f"**Returns:** {returns.get_text(strip=True)}")

        content = "\n\n".join(content_parts)
        if not content or len(content) < 10:
            return None

        return Section(
            heading=f"{parent_name}.{member_name}",
            level=2,
            heading_hierarchy=[module_path, parent_name, member_name],
            content=content,
            source_file=source_file,
        )
