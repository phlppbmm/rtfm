# DONE: Parser pro Doc-System

Der generische Markdown+RST-Parser plus generischer Klassifizierer kommt an
seine Grenzen. Die Health-Tests gegen die aktuell 13 indexierten Quellen
zeigen drei wiederkehrende Defekte, die sich nicht durch Tuning, sondern nur
durch doc-system-spezifische Parser lösen lassen.

## Symptome (aus Health-Check der DB)

**1. Symbol-Lookup picks first match, nicht die kanonische Definition.**
- `lookup BaseModel -f pydantic` → trifft einen zufälligen "Annotated wrapper"-
  Satz aus `concepts.types`, nicht die Klasse.
- `lookup mapped_column -f sqlalchemy` → trifft die Listing-Seite "Class
  Mapping API", dumpt dann den `declared_attr.cascading`-Warning-Block. Die
  eigentliche `mapped_column`-Definition ist nicht der Match.
- `lookup Depends -f fastapi` → trifft den WebSocket-spezifischen Abschnitt
  "Using Depends and others", nicht die zentrale DI-Doku.

**2. Klassifizierer schickt Code-Tutorials in den `concept`-Bucket.**
- `bundle WebSocket -f fastapi`: 5 Concepts, **0 Examples**, obwohl das
  Tutorial voller funktionierender Code-Snippets ist.
- `bundle authentication -f fastapi`: 5 Concepts, 0 Examples, 0 API.
- Macro-Statistik bestätigt: jira-js (0/0), tokio (0/0), reqwest (0/0),
  claude-agent-sdk (0/0) — alle ohne ein einziges API-/Example-Unit.

**3. Crawler-Müll bei HTML-basierten Quellen.**
- reqwest: Heading-Suffix `Copy item path` an *jeder* Seite. Top-Treffer für
  "POST json bearer" sind alle "Constant AUTHORIZATION Copy item path" — also
  Copy-Button-DOM-Text.
- reqwest: `browse` listet 166 "Module" mit URL-Slug-Pfaden wie
  `reqwest.reqwest.latest.reqwest.blocking.struct.Client.html`.
- reqwest: Symbole werden lowercase aus Heading-Slugs extrahiert
  (`client`, `clientbuilder`) statt aus den eigentlichen Struct-Namen.
- jira-js: 4552 Concept-Units, davon ~95 % nutzlose TypeDoc-Property-Stubs
  (`### \`Optional\`description`, `### contextId`).

## Idee

Ein Parser pro **Doc-System**, nicht pro Framework und nicht pro Source-Type.
In den aktuellen 13 Quellen gibt es nur fünf Systeme:

| System | Quellen |
|---|---|
| Sphinx/RST | sqlalchemy, numpy, maturin, tokio |
| MkDocs/Material | fastapi, pydantic, polars, claude-agent-sdk, claude-code, tauri |
| rustdoc HTML | reqwest |
| TypeDoc HTML | jira-js |
| llms.txt flat | svelte |

Pro-Framework wäre Overfitting (13 Parser, 90 % Überlappung). Pro-Source-Type
ist die existierende Achse — zu grob, weil ein `website`-Crawl von docs.rs
und einer von einer MkDocs-Site komplett unterschiedlich behandelt werden
müssen. Das Doc-System ist die richtige Granularität.

## Architektur

```
ingest/
  pipeline.py
  downloaders/...
  parsers/
    base.py            # ABC: raw → list[Section]
    sphinx_rst.py
    mkdocs_md.py
    rustdoc_html.py    # consumes raw HTML, NOT pre-converted MD
    typedoc_html.py    # consumes raw HTML
    llms_txt.py
    generic_md.py      # fallback for unknown markdown
  extractors/
    base.py            # ABC: Section → list[KnowledgeUnit]
    sphinx.py          # .. autoclass:: / .. autofunction:: = definition site
    mkdocs.py          # ::: insert + admonitions = definition site
    rustdoc.py         # rustdoc DOM = definition site
    typedoc.py
    generic.py         # current heuristic, fallback
  dialects/
    fastapi.py         # /// blocks overlay (optional, on top of mkdocs)
  detect.py            # sniff doc system from raw content
```

Parser und Extractor sind getrennte ABCs, weil sie verschiedene Aufgaben
haben (Splitting vs. Klassifizierung) und in seltenen Fällen kombinierbar
sein sollen (z. B. `mkdocs_md` Parser + `generic` Extractor als sicherer
Fallback bei unsicherer Auto-Detection).

## HTML bleibt HTML für rustdoc/TypeDoc

Wichtig — und aktuell falsch: `downloaders/website.py` macht HTML→Markdown
via html2text, *bevor* der Parser etwas zu sehen kriegt. Damit gehen genau
die Informationen verloren, die rustdoc und TypeDoc brauchen:

- `<button>Copy item path</button>` wird zum Heading-Text
- `<section class="impl">` verliert die strukturelle Bedeutung
- `<a class="struct">Client</a>` verliert die "das ist ein Struct"-Annotation
- Klassen-/Modul-Pfade aus den Link-`href`s verschwinden komplett

Konsequenz: Wenn der Doc-System-Parser `rustdoc` oder `typedoc` ist, muss er
das **rohe HTML** bekommen. BeautifulSoup über die docs.rs DOM-Struktur ist
~50 Zeilen und liefert deterministisch saubere Symbole. Der generische
`website`+html2text-Pfad bleibt der Default für unbekannte Quellen.

Das heißt: der Downloader muss optional die Konvertierung überspringen
können, wenn der Parser HTML-aware ist.

## Auto-Detection mit Config-Override

```yaml
sources:
  sqlalchemy:
    type: github
    repo: ...
    # parser: sphinx     # optional override
```

Beim ersten Ingest die ersten N Files sniffen:
- `.. directive::`-Pattern dominant → Sphinx
- `!!! admonition` oder `:::`-Inserts → MkDocs
- `Copy item path` Strings → rustdoc
- TypeDoc-typische `Interface XYZ Properties`-Headings → TypeDoc
- alles andere flach → generic_md oder llms_txt

Ergebnis in der Source-Metadata cachen, damit es nicht bei jedem `ingest`
neu gesnifft wird. Explizit in `config.yaml` gesetzter `parser:` schlägt
Auto-Detection.

## Der eigentliche Hebel: Definition Sites

Jeder Parser kennt seine eigene "definition site" — die Stelle, an der ein
Symbol *kanonisch* eingeführt wird, statt nur erwähnt zu werden:

- **Sphinx**: was direkt nach `.. autoclass:: Foo` / `.. autofunction:: foo`
  steht
- **MkDocs**: was direkt unter einem `::: module.path.Foo` Insert oder einem
  H2/H3 mit Code-Signatur steht
- **rustdoc**: `<section id="main-content">` mit `<h1 class="fqn">`
- **TypeDoc**: `<section class="tsd-panel">` mit Klassen-/Interface-Header

Beim Speichern wird das Unit als `definition_site=true` markiert. Im Lookup
gewinnt ein `definition_site`-Treffer immer gegen einen Erwähnungs-Treffer
desselben Symbols.

Damit löst sich Symptom 1 ohne Heuristik. Das ist der Refactor mit dem mit
Abstand höchsten ROI — jeder Symbol-Lookup wird verlässlich statt geraten.

## Reihenfolge (Schmerz × Gewinn)

1. **rustdoc HTML-Parser** — reqwest ist aktuell *netto schädlich* (0
   nutzbarer Inhalt, mogelt sich aber in cross-framework-Suchen rein).
   Source-Type isoliert, klein, deterministisch testbar gegen die docs.rs
   DOM-Struktur. Der "Hello World" der Refactor.
2. **MkDocs-Parser mit definition-site-Awareness** — betrifft 6 von 13
   Quellen, größter Gewinn pro Zeile Code. Löst pydantic-, fastapi-,
   claude-code-Lookups auf einen Schlag.
3. **Sphinx-Extractor mit `.. auto*::` als Definition** — löst sqlalchemy-
   und numpy-Lookups, plus Down-Weighting von `release-notes`/`changelog`-
   Modulpfaden, die aktuell die Top-Ergebnisse kontaminieren.
4. **TypeDoc HTML-Parser** — nur jira-js. Niedrige Priorität, falls jira-js
   nicht aktiv genutzt wird; sonst hochziehen.
5. **llms_txt** — kein Refactor nötig. Der generische Parser tut's, weil
   svelte die einzige llms_txt-Quelle ist und gut funktioniert.

Nach Schritt 1+2 ist das System dramatisch besser, ohne den ganzen Stack
umgebaut zu haben.

## Tasks

- [ ] `parsers/base.py` ABC + `extractors/base.py` ABC definieren
- [ ] `detect.py` mit Sniffer für die fünf Doc-Systeme
- [ ] Source-Config: optionales `parser:` Feld
- [ ] Source-Metadata: Detection-Ergebnis cachen
- [ ] Downloader: HTML-Roh-Modus für HTML-aware Parser
- [ ] `parsers/rustdoc_html.py` + `extractors/rustdoc.py` (Schritt 1)
- [ ] Test gegen reqwest: `lookup Client`, `search "POST json"`,
      `browse reqwest` müssen sinnvolle Ergebnisse liefern
- [ ] `parsers/mkdocs_md.py` + `extractors/mkdocs.py` mit definition-site-
      Tracking (Schritt 2)
- [ ] `KnowledgeUnit.definition_site: bool` in `models.py` + Storage-Schema
- [ ] Lookup-Ranking: definition_site-Treffer gewinnen
- [ ] Test gegen pydantic/fastapi: `lookup BaseModel`, `lookup Depends`
      treffen die kanonische Definition
- [ ] `extractors/sphinx.py` mit `.. auto*::` als Definition + Down-Weighting
      von release-notes/changelog Pfaden (Schritt 3)
- [ ] `parsers/typedoc_html.py` + `extractors/typedoc.py` (Schritt 4)
- [ ] `dialects/fastapi.py` Overlay für `///`-Blöcke (optional, am Ende)
- [ ] `rtfm ingest --rebuild` über alle Quellen, Health-Check vergleichen

## Out of Scope

- Pro-Framework-Parser. Framework-Eigenheiten gehören in `dialects/` als
  schmale Overlays über einem Doc-System-Parser, nicht als eigener Parser.
- llms_txt-Rewrite. Funktioniert. Nicht anfassen.
- Allgemeine HTML-Quellen außerhalb von rustdoc/TypeDoc. Die laufen weiter
  über `generic_md` nach html2text-Konvertierung.
- Health-Score selbst. Eigenes Issue. Profitiert aber massiv von dieser
  Refactor: mit doc-system-bewussten Parsern werden die Score-Signale
  überhaupt erst aussagekräftig (z. B. "rustdoc-Quelle ohne extrahierte
  Symbole" als harter Fail).
