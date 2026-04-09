# DONE: Import Health Score

Zeigt in `rtfm status` pro Framework einen Score (0-100) + Grade (A-F) an,
der die Qualität und Nutzbarkeit des Imports bewertet.

## Implementierung

`src/rtfm/health.py` — berechnet den Score aus fünf Signalen:

| Signal | Max Penalty | Was es misst |
|---|---|---|
| Type Diversity | -25 | 0 api + 0 example = schlecht |
| Stub Ratio | -20 | % Units < 50 chars Content |
| Definition Coverage | -20 | % Symbols mit is_definition=1 |
| Content Quality | -15 | avg content length |
| Changelog Noise | -10 | % Units mit relevance_decay < 1 |
| Doc System Detection | -5 | generic_md = nicht erkannt |

Score startet bei 100, jedes Signal zieht Punkte ab. Grade-Mapping:
A (85+), B (70+), C (50+), D (30+), F (<30).

## Output

`rtfm status` zeigt eine neue "Health"-Spalte mit Score + Grade.
`rtfm status --json` liefert zusätzlich `health.signals` — eine Liste
menschenlesbarer Begründungen pro Framework.

## Ergebnis nach aktuellem Stand (13 Quellen)

```
fastapi       100 A   4/4 type diversity, 23% definition coverage
tauri         100 A   4/4 type diversity, 31% definition coverage
numpy          90 A   4/4 types, 54% changelog/release notes
pydantic       90 A   4/4 types, low definition coverage (4%)
reqwest        87 A   no examples, 100% definitions, short content
sqlalchemy     85 A   4/4 types, 35% stubs, 41% definitions
svelte         80 B   4/4 types, no definition sites
claude-code    80 B   4/4 types, no definitions
polars         80 B   4/4 types, no definitions
maturin        75 B   4/4 types, no definitions, generic parser
claude-agent   70 B   no api/examples
jira-js        50 C   no api/examples, no definitions, generic parser
tokio          50 C   no api/examples, no definitions, generic parser
```
