# DONE: CLI vereinfachen — Agent-First

rtfm hat zu viele Befehle für den primären Use Case (Agent fragt Doku ab).
Ein Agent soll `rtfm <topic> -f <framework>` schreiben und sofort brauchbares
JSON zurückbekommen, ohne vorher die Help-Seite zu studieren.

## Ist-Zustand

12 Befehle, davon 4 Query-Befehle die sich überlappen:
- `search` — Hybrid-Suche, flache Liste
- `lookup` — exakter Symbol-Match
- `browse` — Strukturnavigation (kaum nutzbar für Agents)
- `bundle` — alles über ein Topic, gruppiert nach Typ (bester Agent-Befehl)

Jeder braucht `--json` Flag für maschinenlesbaren Output. Default ist
Rich-Rendering, das Agents nicht parsen können.

## Soll-Zustand

### Ein Primary-Befehl: `rtfm <query> -f <framework>`

```
rtfm dependency injection -f fastapi
rtfm '$state' -f svelte
rtfm Depends -f fastapi
rtfm WebSocket -f fastapi -k 3
```

Macht intern **smart dispatch**:
1. Query ist ein einzelnes Wort und matcht ein bekanntes Symbol → **lookup**
   (API-Definition + Related + Examples)
2. Sonst → **bundle** (Topic-Suche gruppiert nach api/example/concept/pitfall)

### JSON default, TTY-Detection für Human-Output

- `stdout` ist kein TTY (Pipeline, Agent, Redirect) → **JSON**
- `stdout` ist ein TTY (Terminal) → **Rich/Pretty** (wie bisher)
- `--json` erzwingt JSON auch im TTY
- `--pretty` erzwingt Rich auch in Pipes

Damit braucht kein Agent `--json` zu schreiben und kein Mensch `--pretty`.

### Schlanke --help

```
Usage: rtfm QUERY... [-f FRAMEWORK] [-k TOP_K]

  Search indexed documentation. Returns structured JSON (pipe) or
  formatted text (terminal).

  Examples:
    rtfm dependency injection -f fastapi
    rtfm Depends -f fastapi
    rtfm '$state' -f svelte

Options:
  -f, --framework TEXT  Filter to one framework
  -k, --top-k INTEGER  Results per type (default: 5)
  --json / --pretty     Force output format
  --help                Show this message and exit.

Admin commands:
  rtfm ingest    Download & index configured sources
  rtfm status    Show health scores and unit counts
  rtfm up/down   Start/stop background server
  rtfm update    Re-ingest outdated sources
  rtfm init      Create ~/.rtfm/config.yaml
  rtfm remove    Delete a framework's data
  rtfm serve     Foreground server (for systemd)
```

Maximal 25 Zeilen. Agent sieht sofort: `rtfm <query> -f <name>`.

### Alte Befehle bleiben als Hidden Aliases

`search`, `lookup`, `browse`, `bundle` funktionieren weiterhin, erscheinen
aber nicht in `--help`. Keine Breaking Changes für bestehende Scripts.

## Umsetzung

### Schritt 1: Default-Command mit Smart Dispatch

Click erlaubt kein echtes "default subcommand", aber der Workaround ist:
`@cli.command(hidden=True)` für den Query-Befehl, plus `invoke_without_command=True`
auf der Gruppe mit Argument-Forwarding. Oder: Click `Group.result_callback`
mit `standalone_mode=False`.

Einfacherer Ansatz: `rtfm` als Click-Gruppe mit `invoke_without_command=True`.
Wenn kein Subcommand erkannt wird, wird die Query als Default-Befehl
interpretiert.

```python
@click.group(invoke_without_command=True)
@click.argument("query", nargs=-1)
@click.option("-f", "--framework")
@click.option("-k", "--top-k", default=5)
@click.option("--json/--pretty", default=None)
@click.pass_context
def cli(ctx, query, framework, top_k, json):
    if ctx.invoked_subcommand is not None:
        return  # Subcommand handles it
    if not query:
        click.echo(ctx.get_help())
        return
    # Smart dispatch: single-word → try lookup, else → bundle
    _query_command(query, framework, top_k, json)
```

### Schritt 2: TTY-Detection

```python
def _should_json(json_flag: bool | None) -> bool:
    if json_flag is not None:
        return json_flag
    return not sys.stdout.isatty()
```

### Schritt 3: Smart Dispatch

```python
def _query_command(query_words, framework, top_k, json_flag):
    as_json = _should_json(json_flag)
    query = " ".join(query_words)

    # Try symbol lookup first for single-word queries
    if len(query_words) == 1:
        result = _try_lookup(query_words[0], framework)
        if result:
            _output_lookup(result, as_json)
            return

    # Fall through to bundle
    result = _do_bundle(query, framework, top_k)
    _output_bundle(result, as_json)
```

### Schritt 4: Alte Befehle verstecken

```python
@cli.command(hidden=True)
def search(...): ...

@cli.command(hidden=True)
def browse(...): ...

# lookup und bundle bleiben sichtbar als Aliases, aber hidden
@cli.command(hidden=True)
def lookup(...): ...

@cli.command(hidden=True)
def bundle(...): ...
```

### Schritt 5: Help-Text kürzen

Der große Help-Block in `cli.py` wird ersetzt durch die schlanke Version
oben. Admin-Befehle als einzeilige Liste statt ausführlicher Doku.

## Nicht ändern

- HTTP API Endpoints bleiben alle (search, lookup, browse, bundle)
- Server-Architektur bleibt
- Bestehende Befehle funktionieren weiterhin (hidden, nicht gelöscht)
- Ingest/Status/Up/Down Workflow bleibt
