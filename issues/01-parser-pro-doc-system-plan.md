# DONE: Parser-Refactor — Umsetzungsplan

Detaillierter Umsetzungsplan zu `01-parser-pro-doc-system.md`. Das
Schwester-Issue beschreibt das *Was* und *Warum*; dieses Dokument das *Wie*.

## Festgelegte Entscheidungen

Drei Architekturpunkte sind vorab entschieden und werden im Plan ohne
weitere Diskussion vorausgesetzt:

1. **Schema-Migration via Drop+Rebuild.** Bei Schema-Versionssprung wird
   die DB gedroppt und der User muss `rtfm ingest --rebuild` laufen lassen.
   Kein ALTER-TABLE-Backfill. Begründung: User muss sowieso re-ingesten,
   damit die neuen Parser/Extractor ihre Felder befüllen.
2. **`relevance_decay: float` auf `KnowledgeUnit`.** Dedicated Datenfeld
   statt magischer Modulpfad-Suffixe. Wird im hybrid_search auf den
   RRF-Score angewendet. Default `1.0`, Sphinx-Extractor setzt `0.3` für
   release-notes/changelog/migration Pfade.
3. **`DownloadResult.files: list[tuple[str, str, str]]` mit Content-Type.**
   Drittes Tuple-Element ist `"markdown"`, `"html"` oder `"rst"`. Der
   Downloader entscheidet basierend auf `source_config.doc_system`, ob er
   HTML→MD konvertiert oder roh durchreicht. Parser failen laut wenn der
   content_type nicht zu ihren Erwartungen passt.

## Zentrale Architekturentscheidungen aus dem Code-Review

Drei Punkte aus dem Lesen des aktuellen Codes, die alles andere
strukturieren:

**A. Definition Sites werden auf der Symbol-Ebene markiert, nicht auf der
Unit-Ebene.** Aktuell hat `symbols (symbol, unit_id, framework)` keine
Wertigkeit. Erweitert um `is_definition INTEGER DEFAULT 0`. Damit kann eine
Section mehrere Symbole erwähnen, von denen z.B. nur `BaseModel` die
kanonische Definition trägt. Per-Unit-Bit wäre simpler, aber zu grob für
Sphinx-Listing-Seiten wie sqlalchemys `Class Mapping API`.

**B. Parser/Extractor sind separate ABCs, der existierende Code wird zum
`generic`-Default.** Kein Breaking Change. `parse_markdown()` und
`extract_units()` werden in `parsers/generic_md.py` + `extractors/generic.py`
umgezogen, hinter den ABCs. Der gesamte Pipeline-Pfad funktioniert weiter,
solange noch nichts neu detected wird. Erst wenn die Detection greift, wird
umgeroutet.

**C. HTML-Roh-Pfad ist opt-in pro Doc-System.** `_download_website()` macht
aktuell unbedingt `_html_to_markdown()`. Erweiterung: Wenn der detected
(oder konfigurierte) Doc-System-Parser HTML-aware ist, überspringt der
Downloader die Konvertierung. Default bleibt MD-Konvertierung — kein
Overhead für Quellen, die das nicht brauchen.

## Bonus-Bug, der nebenher mitgefixt wird

`extractor.py:137` `_clean_content` strippt MkDocs `///` Blöcke, aber **NICHT**
Sphinx RST `.. autofunction::`/`.. autoclass::`-Marker. Deshalb sieht man
im Lookup-Output von sqlalchemy rohe Sphinx-Direktiven. Wird in Phase 2
mitgefixt, weil dort der sphinx-Extractor neu geschrieben wird.

---

## Phase 0 — Scaffolding ohne Verhaltensänderung

**Ziel:** Refactor-Sicherheitsnetz. Tests grün halten, kein external
beobachtbares Verhalten ändern.

**Files:**
- `src/rtfm/ingest/parsers/__init__.py` (neu) — Registry mit `get_parser()`
- `src/rtfm/ingest/parsers/base.py` (neu) — Parser ABC + Section dataclass
- `src/rtfm/ingest/parsers/generic_md.py` (neu) — bewegt aus `parser.py`
- `src/rtfm/ingest/extractors/__init__.py` (neu) — Registry mit `get_extractor()`
- `src/rtfm/ingest/extractors/base.py` (neu) — Extractor ABC
- `src/rtfm/ingest/extractors/generic.py` (neu) — bewegt aus `extractor.py`
- `src/rtfm/ingest/parser.py` → Re-Export-Shim für Rückwärtskompatibilität
- `src/rtfm/ingest/extractor.py` → Re-Export-Shim
- `src/rtfm/ingest/pipeline.py` → ruft `get_parser()`/`get_extractor()`
- `tests/test_parser.py`, `tests/test_extractor.py` → Importe ggf. anpassen

**Interface-Skizzen:**

```python
# parsers/base.py
class Parser(Protocol):
    name: ClassVar[str]
    expected_content_type: ClassVar[str] = "markdown"  # or "html", "rst"
    def parse(self, content: str, source_file: str) -> list[Section]: ...

# extractors/base.py
class Extractor(Protocol):
    name: ClassVar[str]
    def extract(
        self,
        sections: list[Section],
        *,
        framework: str,
        language: str,
        source_file: str,
    ) -> list[KnowledgeUnit]: ...
```

`Section` bleibt unverändert (heading, level, heading_hierarchy, content,
source_file). Universelle Schnittstelle zwischen Parser und Extractor.

**Registry:**

```python
# parsers/__init__.py
_PARSERS: dict[str, type[Parser]] = {}

def register(name: str):
    def deco(cls: type[Parser]) -> type[Parser]:
        _PARSERS[name] = cls
        return cls
    return deco

def get_parser(doc_system: str) -> Parser:
    return _PARSERS.get(doc_system, _PARSERS["generic_md"])()
```

**Pipeline-Änderung** (`pipeline.py:34-43`):

```python
parser = get_parser(source_config.doc_system or "generic_md")
extractor = get_extractor(source_config.doc_system or "generic")
for rel_path, content, _ctype in result.files:
    sections = parser.parse(content, source_file=rel_path)
    units = extractor.extract(
        sections,
        framework=source_config.name,
        language=source_config.language,
        source_file=rel_path,
    )
    all_units.extend(units)
```

`SourceConfig` bekommt ein neues optionales Feld `doc_system: str = ""`. Wenn
leer, wird `generic_md` benutzt — exakt das aktuelle Verhalten.

**Done wenn:**
- `uv run pytest tests/ -v` grün
- `rtfm ingest -f svelte --rebuild` produziert dieselbe Anzahl Units wie vor
  dem Refactor
- `rtfm search '$state' -f svelte` liefert dieselben Top-Treffer wie vorher

---

## Phase 1 — `is_definition` + `relevance_decay` + Schema-Migration

**Ziel:** Fundament für alle Per-Doc-System-Verbesserungen. Das Schema kann
jetzt Definition-Sites darstellen, der Lookup nutzt es automatisch.

**Files:**
- `src/rtfm/models.py`
- `src/rtfm/storage.py`
- `tests/test_models.py`, `tests/test_search.py`

**Datenmodell-Änderungen:**

```python
# models.py — KnowledgeUnit
@dataclass
class KnowledgeUnit:
    type: UnitType
    framework: str
    module_path: str
    heading_hierarchy: list[str]
    content: str
    related_symbols: list[str] = field(default_factory=list)
    definition_symbols: list[str] = field(default_factory=list)  # NEU
    relevance_decay: float = 1.0                                  # NEU
    language: str = ""
    source_file: str = ""
    id: str = ""
```

`definition_symbols` ist die Untermenge von `related_symbols`, die in dieser
Section *definiert* werden, nicht nur erwähnt. `to_dict`/`from_dict` und
`UnitResponse`-Pydantic-Modell mitführen.

**Schema-Erweiterungen** (`storage.py:321-375`):

```sql
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS units (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    framework TEXT NOT NULL,
    module_path TEXT NOT NULL,
    heading_hierarchy TEXT,
    content TEXT NOT NULL,
    related_symbols TEXT,
    definition_symbols TEXT,        -- NEU, JSON list
    relevance_decay REAL DEFAULT 1.0,  -- NEU
    language TEXT,
    source_file TEXT
);

CREATE TABLE IF NOT EXISTS symbols (
    symbol TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    framework TEXT NOT NULL,
    is_definition INTEGER NOT NULL DEFAULT 0,  -- NEU
    FOREIGN KEY (unit_id) REFERENCES units(id)
);

CREATE INDEX IF NOT EXISTS idx_symbols_lookup
    ON symbols(symbol, framework, is_definition);
```

**Migration:**

```python
_SCHEMA_VERSION = 2

def __init__(self, ...):
    ...
    cur = self._conn.cursor()
    cur.executescript(_SCHEMA_SQL)  # idempotent CREATE IF NOT EXISTS
    row = cur.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
    if row is None:
        cur.execute("INSERT INTO schema_meta (version) VALUES (?)", (_SCHEMA_VERSION,))
    elif row["version"] < _SCHEMA_VERSION:
        self._drop_and_warn(row["version"])
        cur.execute("UPDATE schema_meta SET version = ?", (_SCHEMA_VERSION,))
    self._conn.commit()
```

`_drop_and_warn` druckt einmalig auf stderr (nicht stdout):
```
schema upgraded from v1 to v2 — existing data dropped, run
`rtfm ingest --rebuild` to repopulate
```
Plus: löscht alle ChromaDB-Daten (`delete_collection` + `get_or_create_collection`).

**`_SymbolRepository.replace_for_unit`** Signatur:

```python
def replace_for_unit(
    self,
    unit_id: str,
    symbols: list[str],
    framework: str,
    definition_symbols: set[str] | None = None,
) -> None:
    self._conn.execute("DELETE FROM symbols WHERE unit_id = ?", (unit_id,))
    defs = {s.lower().strip("()") for s in (definition_symbols or set())}
    for sym in symbols:
        normalized = sym.lower().strip("()")
        is_def = 1 if normalized in defs else 0
        self._conn.execute(
            "INSERT INTO symbols (symbol, unit_id, framework, is_definition) "
            "VALUES (?, ?, ?, ?)",
            (normalized, unit_id, framework, is_def),
        )
```

**`_SymbolRepository.lookup`** ergänzen:

```sql
SELECT DISTINCT unit_id FROM symbols
WHERE symbol = ? [AND framework = ?]
ORDER BY is_definition DESC, rowid ASC
```

Damit gewinnen Definition-Sites automatisch im Symbol-Lookup. **Kein Code in
`search.py` muss sich ändern** — `lookup_symbol()` nimmt weiterhin den
ersten Treffer, der jetzt der Definition-Site-Treffer ist.

**`Storage.insert_units`** ruft auf:

```python
self._symbols.replace_for_unit(
    unit.id,
    unit.related_symbols,
    unit.framework,
    set(unit.definition_symbols),
)
```

**`hybrid_search` mit relevance_decay** (`search.py:8-34`):

```python
def hybrid_search(...):
    semantic = storage.semantic_search(...)
    keyword  = storage.keyword_search(...)
    scores: dict[str, float] = {}
    for rank, (uid, _) in enumerate(semantic):
        scores[uid] = scores.get(uid, 0) + 1.0 / (k + rank + 1)
    for rank, uid in enumerate(keyword):
        scores[uid] = scores.get(uid, 0) + 1.0 / (k + rank + 1)
    sorted_ids = sorted(scores.keys(), key=lambda u: scores[u], reverse=True)[:top_k * 2]
    units = storage.get_units(sorted_ids)
    # Apply per-unit decay AFTER fetching, then re-rank
    for u in units:
        scores[u.id] *= u.relevance_decay
    units.sort(key=lambda u: scores[u.id], reverse=True)
    return units[:top_k]
```

(Top-k×2 als Buffer, damit der Decay nicht versehentlich gute Treffer
herauswirft, wenn ein vorderes Result aggressiv abgewertet wird.)

**Tests:**
- `test_models.py` — KnowledgeUnit-Roundtrip mit `definition_symbols` und
  `relevance_decay`
- `test_search.py` — Bei zwei Units mit gleichem Symbol, eine mit
  `definition_symbols=[sym]`: lookup gewinnt die Definition-Site
- `test_search.py` — Hybrid search mit decay=0.3 vs decay=1.0: das mit
  decay=1.0 gewinnt selbst wenn der RRF-Score knapp niedriger ist

**Done wenn:** Tests grün, Migration läuft sauber durch (manueller Test:
existierende DB, `rtfm status` triggert Upgrade, `rtfm ingest --rebuild`
funktioniert).

---

## Phase 2 — Sphinx-Extractor + Cleanup-Bugfix

**Ziel:** sqlalchemy, numpy, maturin, tokio. Kleinster Diff für maximalen
Lookup-Gewinn, weil die Klassifizierungs-Logik schon da ist.

**Files:**
- `src/rtfm/ingest/extractors/sphinx.py` (neu)
- `src/rtfm/ingest/parsers/sphinx_rst.py` (neu) — dünner Wrapper, nutzt die
  existierende RST→MD-Conversion-Logik aus `parser.py`
- `tests/test_extractors_sphinx.py` (neu)

**Sphinx-Extractor — Logik:**

Kopiert die Basis aus `extractors/generic.py` und ändert:

1. **`_classify_section` returnt jetzt zusätzlich `definition_symbols` und
   `decay`:**
   ```python
   def _classify_section(section: Section) -> ClassifyResult:
       """Returns: types, definition_symbols, relevance_decay"""
       has_rst_api = bool(_RST_API_DIRECTIVES.search(section.content))
       definition_symbols: set[str] = set()
       if has_rst_api:
           # Extract symbol names from .. autoclass:: Foo etc.
           for m in _RST_AUTODOC_SYMBOL.finditer(section.content):
               name = m.group(1).lstrip("~").rsplit(".", 1)[-1]
               definition_symbols.add(name)
       decay = 1.0
       if any(p in section.source_file.lower()
              for p in ("release", "changelog", "migration")):
           decay = 0.3
       ...
   ```

2. **`_clean_content` strippt RST-Direktiven-Marker:** Das ist der
   Bonus-Bugfix. Neue Regex:
   ```python
   _RST_DIRECTIVE_MARKER_RE = re.compile(
       r"^\.\.\s+(?:auto(?:class|function|method|attribute|module|data|exception)"
       r"|(?:py:)?(?:function|class|method|attribute|data|exception|decorator|module)"
       r"|seealso|versionchanged|versionadded|deprecated|note|warning|tip|hint"
       r")::\s*[^\n]*\n",
       re.MULTILINE,
   )
   ```
   Strippt nur die Marker-Zeile, nicht den dahinterstehenden indent-Block.
   (Kann auch in `extractors/generic.py` verschoben werden, wenn der
   Sphinx-Extractor diese Methode erbt — sauberer.)

3. **Source-File-basierter Decay** ist im Sphinx-Extractor zentralisiert,
   weil release-notes/changelog primär in Sphinx-Quellen vorkommen
   (numpy, sqlalchemy).

**Symbol-Pfad bleibt:** `_extract_symbols` aus `generic.py` reicht. Es
findet bereits Sphinx-Autodoc-Symbole. Was hinzukommt ist nur die
Markierung welche davon Definition-Sites sind.

**Test-Validierung gegen Live-DB:**

```bash
rtfm ingest -f sqlalchemy -f numpy --rebuild
rtfm lookup mapped_column -f sqlalchemy   # → Definition-Site, kein declared_attr.cascading
rtfm lookup Session -f sqlalchemy         # → orm.session, nicht orm.events
rtfm search "broadcasting rules" -f numpy # → user.quickstart, nicht release notes
rtfm bundle "session lifecycle" -f sqlalchemy  # → pitfall ≠ 1.1 changelog
```

Außerdem visuell prüfen:
```bash
rtfm lookup mapped_column -f sqlalchemy
# darf KEIN .. autofunction:: oder .. autoclass:: im Output zeigen
```

**Done wenn:** alle vier obigen Befehle das Erwartete liefern UND der
Lookup-Output keine rohen RST-Direktiven mehr enthält.

---

## Phase 3 — MkDocs-Extractor

**Ziel:** fastapi, pydantic, polars, claude-agent-sdk, claude-code, tauri.
Sechs Quellen auf einen Schlag — größter Breitengewinn.

**Files:**
- `src/rtfm/ingest/extractors/mkdocs.py` (neu)
- `src/rtfm/ingest/parsers/mkdocs_md.py` (neu) — dünner Wrapper um
  `parsers/generic_md.py`, evtl. mit besserer `:::`-Anchor-Erkennung
- `tests/test_extractors_mkdocs.py` (neu)

**MkDocs-Extractor — Definition-Site-Heuristiken:**

Drei Wege, eine Section als Definition zu erkennen:

1. **mkdocstrings-Inserts.** Pydantic nutzt das exzessiv:
   ```markdown
   ## BaseModel
   ::: pydantic.BaseModel
   ```
   Das `:::` Insert nennt den FQN. Symbol = letzter Pfadteil. Section ist
   Definition für `BaseModel`.
   ```python
   _MKDOCSTRINGS_INSERT = re.compile(r"^:::\s+([\w.]+)", re.MULTILINE)
   ```

2. **H2/H3 mit Code-Signatur in der nächsten nicht-leeren Zeile:**
   ```markdown
   ## Depends
   ```python
   def Depends(dependency: Callable, *, use_cache: bool = True) -> Any
   ```
   ```
   Pattern: erste nicht-leere Zeile nach Heading öffnet einen Codeblock,
   und der Codeblock enthält ein `def`/`class`/`fn`/etc. mit dem
   Heading-Symbol-Namen. Wenn Match → `definition_symbols.add(heading)`.

3. **Heading mit Inline-Code-Symbol-Name UND erste Zeile = Codeblock-Def:**
   ```markdown
   ## `Field()`
   ```python
   def Field(default=PydanticUndefined, ...) -> FieldInfo
   ```
   ```
   Symbol-Name aus `_INLINE_CODE_SYMBOL` (existiert schon). Definition-Match
   wenn Codeblock direkt darunter eine Definition mit demselben Namen
   enthält.

**Symbol-Extraktion ergänzt:**

```python
def _extract_definition_symbols(section: Section) -> set[str]:
    defs: set[str] = set()
    # 1. mkdocstrings inserts
    for m in _MKDOCSTRINGS_INSERT.finditer(section.content):
        defs.add(m.group(1).rsplit(".", 1)[-1])
    # 2 + 3. heading + code signature
    if section.heading:
        symbol = _symbol_from_heading(section.heading)
        if symbol and _code_defines_symbol(section.content, symbol):
            defs.add(symbol)
    return defs
```

**Klassifizierung sonst unverändert** — der bestehende Code mit MkDocs-
Admonitions-Erkennung bleibt, ergänzt um die Definition-Site-Markierung.

**Test-Validierung gegen Live-DB:**

```bash
rtfm ingest -f pydantic -f fastapi -f claude-code --rebuild
rtfm lookup BaseModel -f pydantic       # → BaseModel-Klasse, nicht Annotated wrapper
rtfm lookup Field -f pydantic           # → Field()
rtfm lookup Depends -f fastapi          # → zentrale DI-Doku, nicht WebSocket
rtfm bundle "WebSocket" -f fastapi      # → examples bucket > 0
rtfm search "PostToolUse matcher" -f claude-code  # → spezifischer Term in top
```

**Done wenn:** alle obigen Lookups die kanonische Definition treffen UND
das WebSocket-Bundle ≥ 1 Example hat.

---

## Phase 4 — rustdoc HTML-Parser

**Ziel:** reqwest. Demonstriert den HTML-Roh-Pfad. Klein, isoliert,
hoher Symbolwert ("der Refactor funktioniert").

**Architektur-Änderung im Downloader:**

`downloaders.py:21-26` `DownloadResult` umschreiben:

```python
class DownloadResult:
    def __init__(
        self,
        files: list[tuple[str, str, str]],  # (rel_path, content, content_type)
        version_key: str,
    ):
        self.files = files
        self.version_key = version_key
```

`content_type` ∈ `{"markdown", "html", "rst"}`. Alle existierenden
Downloader-Pfade müssen einen content_type setzen:
- `_download_github` → `"markdown"` (oder `"rst"` wenn `.rst` extension)
- `_download_llms_txt` → `"markdown"`
- `_read_local` → `"markdown"`
- `_download_website` → abhängig von `config.doc_system`

**`_download_website`** (`downloaders.py:584-673`) ergänzen:

```python
HTML_NATIVE_SYSTEMS = {"rustdoc", "typedoc"}

def _fetch_one(url: str) -> tuple[str, str, str, str, bool] | None:
    ...
    if config.doc_system in HTML_NATIVE_SYSTEMS:
        content = resp.text
        content_type = "html"
    else:
        content = _html_to_markdown(resp.text)
        content_type = "markdown"
        if not content.strip():
            return None
    filepath = _url_to_filepath(url, config.url_filter)
    etag = resp.headers.get("etag", "")
    return filepath, content, content_type, etag, False
```

Cache speichert content + content_type. Migration: alte Cache-Einträge ohne
content_type = `"markdown"`. Da Caches in `~/.rtfm/cache/<source>.json`
liegen und beim Upgrade sowieso reingest läuft, ist das nicht kritisch —
aber sauberkeitshalber: Cache-Schema bumpen, alte Caches verwerfen.

**rustdoc-Parser:**

`parsers/rustdoc_html.py`:

```python
from bs4 import BeautifulSoup, Tag
from rtfm.ingest.parsers import register
from rtfm.ingest.parsers.base import Parser, Section

@register("rustdoc")
class RustdocHtmlParser:
    name = "rustdoc"
    expected_content_type = "html"

    def parse(self, content: str, source_file: str) -> list[Section]:
        soup = BeautifulSoup(content, "lxml")
        main = soup.select_one("section#main-content")
        if not main:
            return []

        # Drop "Copy item path" buttons, source links, sidebar
        for junk in main.select("button.copy-path, .src, .sidebar"):
            junk.decompose()

        sections: list[Section] = []
        module_path = self._url_to_module(source_file)

        # Top-level definition (struct/enum/trait/fn)
        title = self._extract_title(main)  # ("Client", "struct") or None
        if title:
            symbol, kind = title
            description = self._docblock_to_md(main.select_one(".docblock"))
            sections.append(Section(
                heading=f"{kind} {symbol}",
                level=1,
                heading_hierarchy=[module_path, symbol],
                content=description,
                source_file=source_file,
            ))

            # Method sections
            for method in main.select("section.method, section.tymethod"):
                method_section = self._method_to_section(
                    method, parent_symbol=symbol, parent_module=module_path,
                    source_file=source_file,
                )
                if method_section:
                    sections.append(method_section)

        return sections

    def _url_to_module(self, source_file: str) -> str:
        # reqwest/blocking/struct.Client.html → reqwest::blocking
        # Strip the trailing struct.Client.html
        path = source_file.replace(".html", "")
        parts = path.split("/")
        # Drop the leaf if it starts with struct./enum./trait./fn./etc.
        if parts and re.match(r"^(struct|enum|trait|fn|type|constant|macro)\.", parts[-1]):
            parts = parts[:-1]
        return "::".join(parts)

    def _extract_title(self, main: Tag) -> tuple[str, str] | None:
        h1 = main.select_one(".main-heading h1, h1.fqn")
        if not h1:
            return None
        text = h1.get_text(strip=True)
        # "Struct reqwest::Client" → ("Client", "struct")
        m = re.match(r"^(\w+)\s+(?:[\w:]+::)?(\w+)", text)
        if m:
            return m.group(2), m.group(1).lower()
        return None
```

`_docblock_to_md` ist eine ~30-Zeilen-Methode die die `.docblock`-Subtree
in sauberes Markdown wandelt. Nutzt `markdownify` (neue dependency) oder
einen schlanken eigenen Walker. **Vorzug:** `markdownify`, weil wir nur
einen sauberen Subbaum haben (ohne Buttons/Sidebar) und die Library robust
auf Code-Blöcke + Tabellen reagiert.

**rustdoc-Extractor:**

`extractors/rustdoc.py`:

```python
@register("rustdoc")
class RustdocExtractor:
    name = "rustdoc"
    def extract(self, sections, *, framework, language, source_file):
        units: list[KnowledgeUnit] = []
        for section in sections:
            symbol = section.heading_hierarchy[-1]  # always set by parser
            # Top-level (struct/enum/trait) UND methods sind definition sites
            unit = KnowledgeUnit(
                type=UnitType.API,
                framework=framework,
                module_path=section.heading_hierarchy[0],
                heading_hierarchy=section.heading_hierarchy,
                content=section.content,
                related_symbols=[symbol],
                definition_symbols=[symbol],
                language=language,
                source_file=source_file,
            )
            units.append(unit)
            # Bonus: docblock with long code = also example
            if "```" in section.content and section.content.count("\n") > 10:
                example = replace(unit, type=UnitType.EXAMPLE, definition_symbols=[])
                units.append(example)
        return units
```

**Dependencies:**

`pyproject.toml`:
```toml
"beautifulsoup4>=4.12",
"lxml>=5.0",
"markdownify>=0.13",
```

**Test-Fixture:**

`tests/fixtures/rustdoc_client_struct.html` — gespeicherte Kopie der echten
`reqwest/blocking/struct.Client.html` (oder ein ge-trimter Subset davon).
Test prüft:
- Parser liefert ≥ 1 Section mit `heading_hierarchy[-1] == "Client"`
- Method-Sections für mindestens `new`, `get`, `post`, `execute`
- `_url_to_module("reqwest/blocking/struct.Client.html") == "reqwest::blocking"`

**Test-Validierung gegen Live-DB:**

```bash
# Erst Config updaten:
# reqwest:
#   doc_system: rustdoc

rtfm ingest -f reqwest --rebuild
rtfm lookup Client -f reqwest             # → Client struct definition
rtfm lookup ClientBuilder -f reqwest      # → ClientBuilder
rtfm search "POST json bearer" -f reqwest # → keine "Constant XYZ" Treffer
rtfm browse reqwest                       # → reqwest::blocking, reqwest::cookie, ...
rtfm status                               # → reqwest hat api/example > 0
```

**Done wenn:**
- browse zeigt logische Modul-Pfade (`reqwest::blocking`, nicht
  `reqwest.reqwest.latest.reqwest.blocking.struct.Client.html`)
- lookup Client trifft die Struct-Definition mit der richtigen Beschreibung
- status-Tabelle: api_units > 0 für reqwest

---

## Phase 5 — Auto-Detection + Persistenz

**Ziel:** User muss in `config.yaml` nichts ändern. Detection ist transparent
und cached.

**Files:**
- `src/rtfm/ingest/detect.py` (neu)
- `src/rtfm/models.py` — `SourceConfig.doc_system: str = ""`, in `from_dict`
  lesen
- `src/rtfm/storage.py` — `source_versions` Tabelle ergänzt um `doc_system TEXT`,
  `_VersionRepository.set/get` ergänzen
- `src/rtfm/ingest/pipeline.py` — Detection einschieben

**Detection:**

```python
# detect.py
DETECTORS = [
    (lambda files, src_type: src_type == "llms_txt", "llms_txt"),
    (_is_rustdoc, "rustdoc"),
    (_is_typedoc, "typedoc"),
    (_is_sphinx, "sphinx"),
    (_is_mkdocs, "mkdocs"),
]

def detect_doc_system(
    files: list[tuple[str, str, str]],
    source_type: str,
) -> str:
    for predicate, name in DETECTORS:
        if predicate(files, source_type):
            return name
    return "generic_md"

def _is_rustdoc(files, _src_type):
    sample = next((c for _, c, t in files[:5] if t == "html"), None)
    if not sample:
        return False
    return "Copy item path" in sample or 'class="rustdoc"' in sample

def _is_typedoc(files, _src_type):
    sample = next((c for _, c, t in files[:5] if t == "html"), None)
    if not sample:
        return False
    return "tsd-panel" in sample or 'class="tsd-' in sample

def _is_sphinx(files, _src_type):
    sample = "\n".join(c for _, c, t in files[:10] if t in ("markdown", "rst"))
    return bool(re.search(
        r"^\.\. (auto)?(class|function|method|attribute|module)::",
        sample, re.M,
    ))

def _is_mkdocs(files, _src_type):
    sample = "\n".join(c for _, c, t in files[:10] if t == "markdown")
    if re.search(r"^:::\s+[\w.]+", sample, re.M):
        return True
    if re.search(r"^!!!?\s+(note|tip|warning|example|info)\b", sample, re.M):
        return True
    if re.search(r"^///\s+(note|tip|warning|info)\b", sample, re.M):
        return True
    return False
```

**Pipeline-Integration:**

```python
# pipeline._download_and_parse
def _download_and_parse(source_config, on_progress=None):
    with tempfile.TemporaryDirectory() as work_dir:
        result = download_source(source_config, Path(work_dir), on_progress=on_progress)
        if not result.files:
            return [], result.version_key

        # Detection-Hierarchie:
        # 1. explicit config (source_config.doc_system)
        # 2. cached storage value (passed in from caller)
        # 3. fresh sniff
        doc_system = (
            source_config.doc_system
            or _cached_doc_system_for(source_config.name)
            or detect_doc_system(result.files, source_config.type)
        )

        parser = get_parser(doc_system)
        extractor = get_extractor(doc_system)
        ...
```

**Persistenz:** `_VersionRepository.set()` Signatur erweitern:

```python
def set(self, framework: str, version_key: str, doc_system: str = "") -> None:
    ...
```

`get_all` returnt jetzt `dict[str, tuple[str, str, str]]` — `(version_key, ingested_at, doc_system)`.

**`rtfm status`** zeigt das doc_system mit an (eine extra Spalte oder im
verbose-Mode), damit Fehldetection sofort sichtbar ist.

**Tests:**
- `test_detect.py` — pro Doc-System ein Sample, Detection muss matchen
- `test_detect.py` — leere Files → `generic_md`
- `test_detect.py` — explicit override schlägt Detection

**Done wenn:**
- Erst-Ingest aller 13 Quellen ohne Config-Änderungen detected korrekt
- `rtfm status` zeigt das doc_system pro Quelle
- Force-Override via `doc_system:` in config.yaml funktioniert

---

## Phase 6 — Validation gegen alle 13 Quellen

**Ziel:** Sicherheitsnetz, dass der Refactor nichts kaputtmacht. Liefert
auch die Snapshot-Daten für den späteren Health-Score.

**Files:**
- `tests/test_health_smoke.py` (neu) — Anchor-Queries gegen Live-DB

**Smoke-Test:**

```python
import pytest
from rtfm.search import lookup_symbol, hybrid_search
from rtfm.storage import Storage
from pathlib import Path

LIVE_DB = Path.home() / ".rtfm" / "data" / "rtfm.db"

pytestmark = pytest.mark.skipif(
    not LIVE_DB.exists(),
    reason="Live DB not available — run `rtfm ingest --rebuild` first",
)

ANCHORS = [
    # (kind, query, framework, predicate)
    ("lookup", "BaseModel", "pydantic",
     lambda r: r.match.heading_hierarchy[-1] in ("BaseModel", "`BaseModel`")),
    ("lookup", "Depends", "fastapi",
     lambda r: "websocket" not in r.match.module_path.lower()),
    ("lookup", "mapped_column", "sqlalchemy",
     lambda r: "Class Mapping API" not in (r.match.heading_hierarchy[0] if r.match.heading_hierarchy else "")),
    ("lookup", "Client", "reqwest",
     lambda r: r.match.heading_hierarchy[-1] == "Client"),
    ("lookup", "$state", "svelte",
     lambda r: "state" in r.match.heading_hierarchy[-1].lower()),
    ("search", "broadcasting rules", "numpy",
     lambda units: not any("release" in u.module_path for u in units[:3])),
    ("search", "PostToolUse matcher", "claude-code",
     lambda units: any("PostToolUse" in u.content for u in units[:3])),
    ("bundle", "WebSocket", "fastapi",
     lambda buckets: len(buckets.get("example", [])) > 0),
    ("bundle", "session lifecycle", "sqlalchemy",
     lambda buckets: not any("changelog" in p.module_path
                              for p in buckets.get("pitfall", []))),
]

@pytest.mark.parametrize("kind,query,framework,pred", ANCHORS)
def test_anchor(kind, query, framework, pred):
    storage = Storage(str(LIVE_DB.parent))
    try:
        if kind == "lookup":
            result = lookup_symbol(storage, query, framework=framework)
            assert result, f"no match for {query}"
            assert pred(result), f"unexpected match: {result.match.heading_hierarchy}"
        elif kind == "search":
            result = hybrid_search(storage, query, framework=framework)
            assert pred(result), f"top results: {[u.heading_hierarchy for u in result[:3]]}"
        elif kind == "bundle":
            from rtfm.search import bundle_topic
            result = bundle_topic(storage, query, framework=framework)
            assert pred(result), f"buckets: {list(result.keys())}"
    finally:
        storage.close()
```

**Komplett-Reingest und Vergleich:**

```bash
# Snapshot vor Phase 6 (sollte vor jeder Phase neu aufgenommen werden):
rtfm status > /tmp/before.txt

# Reingest mit allen neuen Parsern:
rtfm ingest --rebuild

# Vergleich:
rtfm status > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt
```

**Erwartete Veränderungen:**

| Quelle | Vorher (a/e/c/p) | Erwartet nachher | Wesentlich |
|---|---|---|---|
| reqwest | 0/0/678/11 | ~200/50/400/15 | rustdoc bringt API-Units |
| fastapi | 44/42/934/53 | example ↑↑ | mkdocs strenger |
| pydantic | 243/30/257/23 | ~ähnlich | aber lookup BaseModel ✓ |
| sqlalchemy | 442/168/865/342 | pitfall ↓ | changelog-decay |
| numpy | 173/158/2213/180 | ~ähnlich | aber search broadcasting ✓ |
| jira-js | 0/0/4456/96 | unverändert | TypeDoc erst Phase 7 |
| svelte | 493/131/466/27 | unverändert | bleibt llms_txt → generic |

**Done wenn:**
- Smoke-Tests grün gegen frisch reingestete DB
- Status-Tabelle zeigt die erwarteten Veränderungen
- Keine Quelle ist *schlechter* geworden (Sanity-Check gegen Snapshot)

---

## Phase 7 — TypeDoc (deferred)

**Files:**
- `src/rtfm/ingest/parsers/typedoc_html.py`
- `src/rtfm/ingest/extractors/typedoc.py`

Ähnlich rustdoc, andere DOM-Struktur. Nur lohnend wenn jira-js aktiv genutzt
wird. Sonst: detection erkennt typedoc, aber `get_parser("typedoc")` wirft
einen klaren Fehler ("typedoc parser not yet implemented — falling back to
generic_md") und die Quelle bleibt im aktuellen schlechten Zustand.

---

## Reihenfolge & Sequencing

```
Phase 0  (Scaffolding)            ── 1 Sitzung
Phase 1  (Schema + Felder)        ── 1 Sitzung
Phase 2  (Sphinx-Extractor)       ── 1 Sitzung   ◀ kritischer Pfad
Phase 3  (MkDocs-Extractor)       ── 2 Sitzungen ◀ kritischer Pfad
Phase 4  (rustdoc-Parser)         ── 2 Sitzungen ◀ parallel zu 2/3 möglich
Phase 5  (Auto-Detection)         ── 1 Sitzung
Phase 6  (Validation)             ── 1 Sitzung
Phase 7  (TypeDoc)                ── später, optional
```

Phase 4 hängt nur an Phase 0+1, nicht an Phase 2/3 — kann als Seitenstrang
parallel laufen wenn Bock auf Abwechslung ist.

Empfohlener kritischer Pfad: **0 → 1 → 2 → 3 → 5 → 6**. Phase 4 dazwischen
oder am Schluss.

## Risiken & Rollback

**Risiko 1: Schema-Drop verliert User-Daten.**
Mitigation: Klarer einmaliger Hinweis auf stderr beim ersten Storage-Init
nach dem Upgrade. Re-ingest läuft danach normal. Keine Backups, weil das
ChromaDB sowieso re-embedden muss.

**Risiko 2: Auto-Detection picked das Falsche.**
Mitigation: explicit `doc_system:` in `config.yaml` als Override. `rtfm status`
zeigt detected system, damit Fehldetection sichtbar ist.

**Risiko 3: rustdoc-Parser bricht bei docs.rs Layout-Updates.**
Mitigation: Test-Fixture (`tests/fixtures/rustdoc_client_struct.html`)
committen. Wenn der Test bricht, ist es eine Layoutänderung — fix wird
isoliert und reproduzierbar.

**Risiko 4: Definition-Site-Ranking verändert bestehende Top-Treffer in
unerwartete Richtung.**
Mitigation: Anchor-Test-Suite (Phase 6). Smoke-Test vergleicht top-1 vor
und nach jedem Phase-Schritt.

**Risiko 5: Phase 0 Refactor bricht Tests durch Import-Pfad-Änderungen.**
Mitigation: `parser.py`/`extractor.py` als Re-Export-Shims behalten bis
alle Tests durch sind. Erst danach löschen.

**Risiko 6: `markdownify` (neue Dep) hat eigene Conversion-Macken.**
Mitigation: Test-Fixture für `_docblock_to_md` Output gegen erwartete
Markdown-Ausgabe. Wenn die Dep zu viel Lärm macht, eigener schlanker
Walker als Plan B (~50 Zeilen).
