"""Generic Knowledge Unit extractor — classifies sections and extracts symbols."""


import re

from rtfm.ingest.parsers.base import Section
from rtfm.models import KnowledgeUnit, UnitType

# ---------------------------------------------------------------------------
# Symbol extraction regexes per language
# ---------------------------------------------------------------------------

_PYTHON_SYMBOLS = re.compile(
    r"(?:^|\n)\s*(?:async\s+)?def\s+(\w+)\s*\("
    r"|(?:^|\n)\s*class\s+(\w+)[\s(:]"
    r"|(?:^|\n)\s*@(\w+)",
    re.MULTILINE,
)

_RUST_SYMBOLS = re.compile(
    r"(?:^|\n)\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*[<(]"
    r"|(?:^|\n)\s*(?:pub\s+)?struct\s+(\w+)"
    r"|(?:^|\n)\s*(?:pub\s+)?trait\s+(\w+)"
    r"|(?:^|\n)\s*(?:pub\s+)?enum\s+(\w+)"
    r"|(?:^|\n)\s*impl(?:<[^>]*>)?\s+(\w+)",
    re.MULTILINE,
)


_JS_TS_SYMBOLS = re.compile(
    r"(?:^|\n)\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*[<(]"
    r"|(?:^|\n)\s*(?:export\s+)?class\s+(\w+)[\s{<]"
    r"|(?:^|\n)\s*(?:export\s+)?interface\s+(\w+)[\s{<]"
    r"|(?:^|\n)\s*(?:export\s+)?type\s+(\w+)\s*[<=]"
    r"|(?:^|\n)\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*="
    r"|(?:^|\n)\s*(?:export\s+)?enum\s+(\w+)",
    re.MULTILINE,
)

_GO_SYMBOLS = re.compile(
    r"(?:^|\n)\s*func\s+(?:\([^)]*\)\s+)?(\w+)\s*[<([]"
    r"|(?:^|\n)\s*type\s+(\w+)\s+(?:struct|interface|func)"
    r"|(?:^|\n)\s*(?:var|const)\s+(\w+)\s",
    re.MULTILINE,
)

_C_CPP_SYMBOLS = re.compile(
    r"(?:^|\n)\s*(?:static\s+|inline\s+|extern\s+|virtual\s+)*"
    r"(?:unsigned\s+|signed\s+|const\s+)*"
    r"(?:\w+[\s*&]+)+(\w+)\s*\("                        # function
    r"|(?:^|\n)\s*(?:typedef\s+)?struct\s+(\w+)"         # struct
    r"|(?:^|\n)\s*(?:typedef\s+)?enum\s+(\w+)"           # enum
    r"|(?:^|\n)\s*(?:typedef\s+)?union\s+(\w+)"          # union
    r"|(?:^|\n)\s*class\s+(\w+)[\s:{]"                   # C++ class
    r"|(?:^|\n)\s*namespace\s+(\w+)"                     # C++ namespace
    r"|(?:^|\n)\s*template\s*<[^>]*>\s*class\s+(\w+)"   # C++ template class
    r"|(?:^|\n)\s*#define\s+(\w+)",                      # macro
    re.MULTILINE,
)

_JAVA_SYMBOLS = re.compile(
    r"(?:^|\n)\s*(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:abstract\s+)?(?:final\s+)?"
    r"class\s+(\w+)"                                     # class
    r"|(?:^|\n)\s*(?:public\s+|private\s+|protected\s+)?interface\s+(\w+)"
    r"|(?:^|\n)\s*(?:public\s+|private\s+|protected\s+)?enum\s+(\w+)"
    r"|(?:^|\n)\s*(?:public\s+|private\s+|protected\s+)?(?:static\s+)?(?:abstract\s+)?(?:final\s+)?(?:synchronized\s+)?"
    r"(?:\w+(?:<[^>]*>)?[\s*&]*\s+)(\w+)\s*\("          # method
    r"|(?:^|\n)\s*@(\w+)",                               # annotation
    re.MULTILINE,
)

_CSHARP_SYMBOLS = re.compile(
    r"(?:^|\n)\s*(?:public\s+|private\s+|protected\s+|internal\s+)?(?:static\s+)?(?:abstract\s+)?(?:sealed\s+)?(?:partial\s+)?"
    r"class\s+(\w+)"                                     # class
    r"|(?:^|\n)\s*(?:public\s+|private\s+|protected\s+|internal\s+)?interface\s+(\w+)"
    r"|(?:^|\n)\s*(?:public\s+|private\s+|protected\s+|internal\s+)?enum\s+(\w+)"
    r"|(?:^|\n)\s*(?:public\s+|private\s+|protected\s+|internal\s+)?struct\s+(\w+)"
    r"|(?:^|\n)\s*(?:public\s+|private\s+|protected\s+|internal\s+)?delegate\s+\w+\s+(\w+)"
    r"|(?:^|\n)\s*(?:public\s+|private\s+|protected\s+|internal\s+)?(?:static\s+)?(?:async\s+)?(?:virtual\s+)?(?:override\s+)?"
    r"(?:\w+(?:<[^>]*>)?[\s*&]*\s+)(\w+)\s*\("          # method
    r"|(?:^|\n)\s*\[(\w+)",                              # attribute
    re.MULTILINE,
)

_SYMBOL_EXTRACTORS: dict[str, list[re.Pattern[str]]] = {
    "python": [_PYTHON_SYMBOLS],
    "rust": [_RUST_SYMBOLS],
    "javascript": [_JS_TS_SYMBOLS],
    "typescript": [_JS_TS_SYMBOLS],
    "js": [_JS_TS_SYMBOLS],
    "ts": [_JS_TS_SYMBOLS],
    "go": [_GO_SYMBOLS],
    "golang": [_GO_SYMBOLS],
    "c": [_C_CPP_SYMBOLS],
    "cpp": [_C_CPP_SYMBOLS],
    "c++": [_C_CPP_SYMBOLS],
    "java": [_JAVA_SYMBOLS],
    "csharp": [_CSHARP_SYMBOLS],
    "c#": [_CSHARP_SYMBOLS],
}

# ---------------------------------------------------------------------------
# Code block extraction (Markdown + RST)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Content cleaning: remove ingest-time syntax that leaks into rendered output
# ---------------------------------------------------------------------------

# MkDocs Blocks extension: /// type "title"  ...  ///
# Both opening and closing markers should be stripped while keeping the inner text.
_BLOCKS_OPENING_RE = re.compile(r"^///\s+\w+(?:\s+[\"'][^\"'\n]*[\"'])?\s*$", re.MULTILINE)
_BLOCKS_CLOSING_RE = re.compile(r"^///\s*$", re.MULTILINE)

# MkDocs Snippets extension: {* path/to/file.py hl[1:5] *} or {! ... !}
_MKDOCS_SNIPPET_RE = re.compile(r"\{\*[^{}]*?\*\}|\{!\s*[^{}]*?\s*!\}")

# MkDocs heading anchor: # Heading { #anchor-id }   or  ### { #foo .class }
_HEADING_ANCHOR_RE = re.compile(r"\s*\{\s*#[\w-]+(?:\s+[^{}]*)?\s*\}\s*$", re.MULTILINE)

# Inline HTML tags to neutralize. We strip the tag but keep enclosed text where present.
# Example: <dfn title="...">Dependency Injection</dfn> -> Dependency Injection
_INLINE_HTML_RE = re.compile(
    r"</?(?:dfn|span|abbr|small|sup|sub|kbd|var|cite|mark|ins|del|s|u|i|b|em|strong)(?:\s+[^>]*)?>"
)

# Self-closing HTML to drop entirely (image references, line breaks).
_VOID_HTML_RE = re.compile(r"<(?:img|br|hr|wbr|input|source|track)(?:\s+[^>]*)?/?>")

# HTML comments
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Leftover collapsing whitespace
_TRIPLE_BLANK_RE = re.compile(r"\n{3,}")

# RST directive markers to strip (Sphinx-specific cleanup)
_RST_DIRECTIVE_MARKER_RE = re.compile(
    r"^\.\.\s+(?:auto(?:class|function|method|attribute|module|data|exception)"
    r"|(?:py:)?(?:function|class|method|attribute|data|exception|decorator|module)"
    r"|seealso|versionchanged|versionadded|deprecated|note|warning|tip|hint"
    r"|todo|attention|caution|danger|important|admonition"
    r")::\s*[^\n]*$",
    re.MULTILINE,
)

# RST field-list-style options (:members:, :inherited-members:, etc.) that follow directives
_RST_FIELD_OPTION_RE = re.compile(r"^\s+:[a-z-]+:.*$", re.MULTILINE)

# mkdocstrings inserts: ::: module.path followed by optional YAML options block
_MKDOCSTRINGS_BLOCK_RE = re.compile(
    r"^:::\s+[\w.]+\s*\n(?:\s+\w+:.*\n)*",
    re.MULTILINE,
)


def _clean_content(text: str) -> str:
    """Strip ingest-time markup that should not appear in rendered output.

    Removes MkDocs Blocks markers, Snippets includes, heading anchor suffixes,
    inline HTML tags, void HTML elements, HTML comments, and RST directive markers.
    """
    text = _HTML_COMMENT_RE.sub("", text)
    text = _MKDOCS_SNIPPET_RE.sub("", text)
    text = _HEADING_ANCHOR_RE.sub("", text)
    text = _BLOCKS_OPENING_RE.sub("", text)
    text = _BLOCKS_CLOSING_RE.sub("", text)
    text = _RST_DIRECTIVE_MARKER_RE.sub("", text)
    text = _RST_FIELD_OPTION_RE.sub("", text)
    text = _MKDOCSTRINGS_BLOCK_RE.sub("", text)
    text = _VOID_HTML_RE.sub("", text)
    text = _INLINE_HTML_RE.sub("", text)
    text = _TRIPLE_BLANK_RE.sub("\n\n", text)
    return text.strip()


_CODE_BLOCK_RE = re.compile(r"```[\w]*\n(.*?)```", re.DOTALL)
_RST_CODE_BLOCK_RE = re.compile(
    r"::[ \t]*\n\n((?:[ \t]+.+\n?)+)",
    re.MULTILINE,
)
# .. code-block:: python / .. sourcecode:: python
_RST_DIRECTIVE_CODE_RE = re.compile(
    r"\.\.\s+(?:code-block|sourcecode)::\s*\w*\s*\n((?:[ \t]+.+\n?|\s*\n)+)",
    re.MULTILINE,
)
# Markdown indented code blocks (4+ spaces, preceded by blank line).
# html2text converts <pre><code> to these instead of fenced blocks.
_MD_INDENT_CODE_RE = re.compile(
    r"(?:^|\n\n)((?:    .+\n)+)",
    re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Structured directive detection
# ---------------------------------------------------------------------------

# Sphinx RST: API reference directives
_RST_API_DIRECTIVES = re.compile(
    r"\.\.\s+(?:auto(?:class|function|method|attribute|module|data|exception)"
    r"|(?:py:)?(?:function|class|method|attribute|data|exception|decorator|module)"
    r"|(?:js|rb|c|cpp):(?:function|class|method|attribute|data)"
    r")::",
    re.MULTILINE,
)

# Sphinx RST: pitfall admonitions (real warnings, not just notes)
_RST_PITFALL_DIRECTIVES = re.compile(
    r"\.\.\s+(?:warning|danger|deprecated|caution)::",
    re.MULTILINE,
)

# Sphinx RST: non-pitfall admonitions (informational, should NOT trigger pitfall)
_RST_NOTE_DIRECTIVES = re.compile(
    r"\.\.\s+(?:note|tip|hint|seealso|todo|attention|admonition)::",
    re.MULTILINE,
)

# MkDocs admonitions: !!! type or !!! type "title"
_MKDOCS_API_ADMONITIONS = re.compile(
    r"^!!!?\s+(?:api|reference)\b",
    re.MULTILINE | re.IGNORECASE,
)

_MKDOCS_PITFALL_ADMONITIONS = re.compile(
    r"^!!!?\s+(?:warning|danger|caution|failure)\b",
    re.MULTILINE | re.IGNORECASE,
)

_MKDOCS_EXAMPLE_ADMONITIONS = re.compile(
    r"^!!!?\s+(?:example|quote)\b",
    re.MULTILINE | re.IGNORECASE,
)

_MKDOCS_NOTE_ADMONITIONS = re.compile(
    r"^!!!?\s+(?:note|tip|info|hint|success|question|abstract|bug)\b",
    re.MULTILINE | re.IGNORECASE,
)

# FastAPI Blocks extension: /// type
_BLOCKS_PITFALL = re.compile(
    r"^///\s+(?:warning|danger|caution)\b",
    re.MULTILINE | re.IGNORECASE,
)

_BLOCKS_NOTE = re.compile(
    r"^///\s+(?:note|tip|info|details|check)\b",
    re.MULTILINE | re.IGNORECASE,
)

# Heading-based signals for API reference sections
_API_HEADING_WORDS = re.compile(
    r"\b(?:API\s+Reference|Parameters|Returns?|Raises?|Attributes?|Signature|Arguments?)\b",
    re.IGNORECASE,
)

# Definition patterns inside code blocks (language-agnostic check)
_DEFINITION_RE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"(?:async\s+)?def\s+\w+\s*\("          # Python function
    r"|class\s+\w+[\s(:]"                    # Python/JS class
    r"|(?:pub\s+)?(?:async\s+)?fn\s+\w+\s*[<(]"  # Rust function
    r"|(?:pub\s+)?struct\s+\w+"              # Rust struct
    r"|(?:pub\s+)?trait\s+\w+"               # Rust trait
    r"|(?:pub\s+)?enum\s+\w+"               # Rust enum
    r"|(?:export\s+)?(?:function|const|let|var)\s+\w+"  # JS/TS
    r"|interface\s+\w+"                      # TS interface
    r")",
    re.MULTILINE,
)

# Narrow pitfall words — only clear warning/deprecation language, not "note" or "important"
_PITFALL_WORDS = re.compile(
    r"\b(?:breaking\s+change|deprecated|DEPRECATED|removed\s+in|"
    r"no\s+longer\s+supported|backwards?\s*incompatible|"
    r"gotcha|pitfall|footgun)\b",
    re.IGNORECASE,
)

# mkdocstrings inserts: ::: module.path.Symbol
_MKDOCSTRINGS_INSERT = re.compile(r"^:::\s+([\w.]+)", re.MULTILINE)


def _extract_code_blocks(content: str) -> list[str]:
    """Extract code blocks from Markdown fences, RST blocks, and indented blocks."""
    blocks = _CODE_BLOCK_RE.findall(content)
    blocks.extend(_RST_CODE_BLOCK_RE.findall(content))
    blocks.extend(_RST_DIRECTIVE_CODE_RE.findall(content))
    blocks.extend(_MD_INDENT_CODE_RE.findall(content))
    return blocks


def _classify_section(section: Section) -> list[UnitType]:
    """Classify a section into one or more KnowledgeUnit types.

    A section can be e.g. both API and PITFALL (an API reference with a
    deprecation warning). Returns types ordered by relevance (primary first).
    """
    content = section.content
    heading = " ".join(section.heading_hierarchy)
    types: list[UnitType] = []

    # --- Phase 1: Structured directives (highest confidence) ---

    has_rst_api = bool(_RST_API_DIRECTIVES.search(content))
    has_rst_pitfall = bool(_RST_PITFALL_DIRECTIVES.search(content))
    has_mkdocs_pitfall = bool(_MKDOCS_PITFALL_ADMONITIONS.search(content))
    has_mkdocs_example = bool(_MKDOCS_EXAMPLE_ADMONITIONS.search(content))
    has_blocks_pitfall = bool(_BLOCKS_PITFALL.search(content))

    # --- Phase 2: Code analysis ---
    code_blocks = _extract_code_blocks(content)
    has_code = bool(code_blocks)
    code_text = "\n".join(code_blocks)

    has_definition = bool(_DEFINITION_RE.search(code_text)) if has_code else False
    has_long_code = any(block.strip().count("\n") >= 5 for block in code_blocks)

    # --- Phase 3: Text signals ---
    has_pitfall_words = bool(_PITFALL_WORDS.search(content))
    has_api_heading = bool(_API_HEADING_WORDS.search(heading))

    # --- Collect all matching types ---

    if has_rst_api or has_definition or (has_api_heading and has_code):
        types.append(UnitType.API)

    if has_rst_pitfall or has_mkdocs_pitfall or has_blocks_pitfall or has_pitfall_words:
        types.append(UnitType.PITFALL)

    if has_mkdocs_example or has_long_code:
        types.append(UnitType.EXAMPLE)

    if not types:
        types.append(UnitType.CONCEPT)

    return types


# RST autodoc directives that name a symbol directly
_RST_AUTODOC_SYMBOL = re.compile(
    r"\.\.\s+(?:auto(?:class|function|method|attribute|data|exception)"
    r"|(?:py:)?(?:function|class|method|attribute|data|exception|decorator))"
    r"::\s+([~]?[\w.]+)",
    re.MULTILINE,
)

# Inline code in headings/text that looks like a symbol: `Depends`, `BaseModel`, `$state`
_INLINE_CODE_SYMBOL = re.compile(r"`(\$?\w+)`")


def _extract_symbols(content: str, language: str, heading_hierarchy: list[str] | None = None) -> list[str]:
    """Extract programming symbols from content using language-specific regexes,
    RST autodoc directives, and heading text."""
    symbols: list[str] = []

    # 1. Language-specific patterns in content
    extractors = _SYMBOL_EXTRACTORS.get(language, [])
    for extractor in extractors:
        for match in extractor.finditer(content):
            for group in match.groups():
                if group:
                    symbols.append(group)

    # 2. RST autodoc directives: .. autoclass:: Session -> "Session"
    for m in _RST_AUTODOC_SYMBOL.finditer(content):
        name = m.group(1).lstrip("~").rsplit(".", 1)[-1]  # ~sqlalchemy.orm.Session -> Session
        if name:
            symbols.append(name)

    # 3. Inline code symbols from headings: `Depends`, `BaseModel`
    if heading_hierarchy:
        for heading in heading_hierarchy:
            for m in _INLINE_CODE_SYMBOL.finditer(heading):
                symbols.append(m.group(1))

    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for s in symbols:
        normalized = s.lower().strip("()")
        if normalized not in seen:
            seen.add(normalized)
            unique.append(s)
    return unique


def _symbol_from_heading(heading: str) -> str | None:
    """Extract a symbol name from a heading, handling inline code."""
    m = _INLINE_CODE_SYMBOL.search(heading)
    if m:
        return m.group(1).strip("()")
    # Plain heading that looks like a CLI command or function name
    clean = heading.strip().strip("`").strip("()")
    if re.match(r"^[$\w][\w.-]*$", clean) and not clean[0].isdigit():
        return clean
    return None


def _code_defines_symbol(content: str, symbol: str) -> bool:
    """Check if the first code block in content defines the named symbol."""
    code_blocks = _extract_code_blocks(content)
    if not code_blocks:
        return False
    code = code_blocks[0]
    return bool(_DEFINITION_RE.search(code) and symbol.lower() in code.lower())


def _extract_definition_symbols(section: Section) -> list[str]:
    """Extract symbols that are canonically *defined* in this section.

    Looks for RST autodoc directives, mkdocstrings inserts, and
    heading+code-definition patterns.
    """
    defs: set[str] = set()

    # RST autodoc directives: .. autoclass:: Session -> definition of Session
    for m in _RST_AUTODOC_SYMBOL.finditer(section.content):
        name = m.group(1).lstrip("~").rsplit(".", 1)[-1]
        if name:
            defs.add(name.lower().strip("()"))

    # mkdocstrings inserts: ::: pydantic.BaseModel -> definition of BaseModel
    for m in _MKDOCSTRINGS_INSERT.finditer(section.content):
        name = m.group(1).rsplit(".", 1)[-1]
        if name:
            defs.add(name.lower().strip("()"))

    # Heading with code definition: ## `Depends` + code block defining Depends
    if section.heading:
        symbol = _symbol_from_heading(section.heading)
        if symbol and _code_defines_symbol(section.content, symbol):
            defs.add(symbol.lower().strip("()"))

    return list(defs)


def _compute_relevance_decay(source_file: str) -> float:
    """Compute relevance decay based on source file path.

    Release notes, changelogs, and migration guides are downweighted so they
    don't dominate search results over actual documentation.
    """
    lower = source_file.lower()
    if any(p in lower for p in ("release", "changelog", "migration", "whatsnew", "changes")):
        return 0.3
    return 1.0


def _derive_module_path(framework: str, source_file: str) -> str:
    """Derive a module path from the source file path.

    Examples:
        fastapi, docs/en/docs/tutorial/security/oauth2.md -> fastapi.tutorial.security.oauth2
        svelte, documentation/docs/02-runes/01-state.md -> svelte.runes.state
    """
    # Strip common prefixes and the .md extension
    path = source_file
    for prefix in ("docs/en/docs/", "documentation/docs/", "doc/build/", "docs/source/", "docs/", "doc/", "content/"):
        if path.startswith(prefix):
            path = path[len(prefix):]
            break

    # Remove file extension
    for ext in (".md", ".rst"):
        if path.endswith(ext):
            path = path[:-len(ext)]
            break

    # Remove numeric prefixes like "02-" from path segments
    parts = path.replace("\\", "/").split("/")
    cleaned: list[str] = []
    for part in parts:
        # Strip leading digits and hyphens (e.g., "02-runes" -> "runes")
        clean = re.sub(r"^\d+-", "", part)
        # Skip index files
        if clean.lower() in ("index", "readme", ""):
            continue
        cleaned.append(clean)

    return framework + "." + ".".join(cleaned) if cleaned else framework


MAX_UNIT_TOKENS_APPROX = 2000
# Rough approximation: 1 token ≈ 4 chars
MAX_UNIT_CHARS = MAX_UNIT_TOKENS_APPROX * 4


def _split_oversized(unit: KnowledgeUnit) -> list[KnowledgeUnit]:
    """Split a unit that exceeds the size limit, keeping code blocks intact."""
    if len(unit.content) <= MAX_UNIT_CHARS:
        return [unit]

    # Split on paragraph boundaries (double newline), never inside code blocks
    parts: list[str] = []
    current: list[str] = []
    in_code_block = False
    current_len = 0

    for line in unit.content.split("\n"):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block

        in_rst_indent = not in_code_block and (line.startswith("    ") or line.startswith("\t"))

        current.append(line)
        current_len += len(line) + 1

        # Only split at paragraph boundaries outside code blocks
        if not in_code_block and not in_rst_indent and line.strip() == "" and current_len > MAX_UNIT_CHARS:
            parts.append("\n".join(current))
            current = []
            current_len = 0

    if current:
        parts.append("\n".join(current))

    if len(parts) <= 1:
        return [unit]

    units: list[KnowledgeUnit] = []
    for i, part in enumerate(parts):
        hierarchy = unit.heading_hierarchy.copy()
        if len(parts) > 1:
            hierarchy.append(f"(part {i + 1})")
        units.append(KnowledgeUnit(
            type=unit.type,
            framework=unit.framework,
            module_path=unit.module_path,
            heading_hierarchy=hierarchy,
            content=part.strip(),
            related_symbols=unit.related_symbols if i == 0 else [],
            definition_symbols=unit.definition_symbols if i == 0 else [],
            relevance_decay=unit.relevance_decay,
            language=unit.language,
            source_file=unit.source_file,
        ))
    return units


def extract_units(
    sections: list[Section],
    framework: str,
    language: str,
    source_file: str = "",
) -> list[KnowledgeUnit]:
    """Convert parsed sections into Knowledge Units."""
    units: list[KnowledgeUnit] = []

    for section in sections:
        if not section.content.strip():
            continue

        # Classify and extract symbols using the original content (directive
        # markers carry classification signal). Then strip those markers
        # before persisting so rendered output is clean.
        unit_types = _classify_section(section)
        module_path = _derive_module_path(framework, source_file)
        symbols = _extract_symbols(section.content, language, section.heading_hierarchy)
        definition_symbols = _extract_definition_symbols(section)
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


class GenericExtractor:
    """Generic extractor. Default fallback for unknown doc systems."""

    name = "generic"

    def extract(
        self,
        sections: list[Section],
        *,
        framework: str,
        language: str,
        source_file: str,
    ) -> list[KnowledgeUnit]:
        return extract_units(sections, framework, language, source_file)
