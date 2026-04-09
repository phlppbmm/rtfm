"""Dual-index storage: ChromaDB (semantic) + SQLite/FTS5 (keyword + structure)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rtfm.models import default_home

# Library log suppression — must run *before* importing chromadb (which pulls
# in onnxruntime via the embedding model). On WSL onnxruntime emits a warning
# with raw ANSI escape codes about missing GPU device files; that warning
# previously leaked into stdout. Python-level logging is not enough because
# the message comes from native code via fd 2 directly.
os.environ.setdefault("ORT_LOGGING_LEVEL", "3")  # 3 = ERROR
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
for _name in ("onnxruntime", "chromadb", "chromadb.telemetry", "httpx", "httpcore", "sentence_transformers"):
    logging.getLogger(_name).setLevel(logging.ERROR)


_LIBRARY_LOG = Path(default_home()) / "library.log"


@contextlib.contextmanager
def _route_native_stderr() -> Iterator[None]:
    """Redirect file descriptor 2 to ``~/.rtfm/library.log`` for the duration.

    Catches messages from native libraries (onnxruntime GPU probes, etc.)
    that bypass Python's logging system. Errors are not lost — they land in
    the library log where the user can inspect them.
    """
    _LIBRARY_LOG.parent.mkdir(parents=True, exist_ok=True)
    sys.stderr.flush()
    saved_fd = os.dup(2)
    log_fd = os.open(str(_LIBRARY_LOG), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.dup2(log_fd, 2)
        os.close(log_fd)
        yield
    finally:
        sys.stderr.flush()
        os.dup2(saved_fd, 2)
        os.close(saved_fd)


with _route_native_stderr():
    import chromadb  # noqa: E402 — must be imported after env vars above

from rtfm.models import KnowledgeUnit, UnitType  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

    from chromadb.api.types import Metadata, Where


def _placeholders(items: list[str]) -> str:
    """Generate comma-separated '?' placeholders for SQL IN clauses."""
    return ",".join("?" for _ in items)


# ---------------------------------------------------------------------------
# SQLite repositories
# ---------------------------------------------------------------------------


class _UnitRepository:
    """All SQLite operations on the `units` table (+ FTS5)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert(self, unit: KnowledgeUnit) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO units
               (id, type, framework, module_path, heading_hierarchy,
                content, related_symbols, definition_symbols,
                relevance_decay, language, source_file)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                unit.id,
                unit.type.value,
                unit.framework,
                unit.module_path,
                json.dumps(unit.heading_hierarchy),
                unit.content,
                json.dumps(unit.related_symbols),
                json.dumps(unit.definition_symbols),
                unit.relevance_decay,
                unit.language,
                unit.source_file,
            ),
        )

    def get(self, unit_id: str) -> KnowledgeUnit | None:
        row = self._conn.execute(
            "SELECT * FROM units WHERE id = ?", (unit_id,)
        ).fetchone()
        if not row:
            return None
        return KnowledgeUnit.from_dict(dict(row))

    def get_many(self, unit_ids: list[str]) -> list[KnowledgeUnit]:
        """Fetch multiple units, preserving the order of *unit_ids*."""
        if not unit_ids:
            return []
        rows = self._conn.execute(
            f"SELECT * FROM units WHERE id IN ({_placeholders(unit_ids)})",
            unit_ids,
        ).fetchall()
        by_id = {row["id"]: KnowledgeUnit.from_dict(dict(row)) for row in rows}
        return [by_id[uid] for uid in unit_ids if uid in by_id]

    def ids_for_framework(self, framework: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT id FROM units WHERE framework = ?", (framework,)
        ).fetchall()
        return [row["id"] for row in rows]

    def keyword_search(
        self,
        query: str,
        top_k: int = 20,
        framework: str | None = None,
        unit_type: str | None = None,
    ) -> list[str]:
        """FTS5 keyword search. Returns unit_ids ordered by BM25 rank."""
        safe_query = query.replace('"', '""')
        fts_query = f'"{safe_query}"'

        clauses = ["units_fts MATCH ?"]
        params: list[str | int] = [fts_query]

        if framework:
            clauses.append("u.framework = ?")
            params.append(framework)
        if unit_type:
            clauses.append("u.type = ?")
            params.append(unit_type)

        where = " AND ".join(clauses)
        params.append(top_k)

        rows = self._conn.execute(
            f"""SELECT u.id
                FROM units u
                JOIN units_fts ON units_fts.rowid = u.rowid
                WHERE {where}
                ORDER BY rank LIMIT ?""",
            params,
        ).fetchall()
        return [row["id"] for row in rows]

    def stats(self) -> dict[str, dict[str, int]]:
        rows = self._conn.execute(
            """SELECT framework, type, COUNT(*) as count
               FROM units GROUP BY framework, type ORDER BY framework, type"""
        ).fetchall()
        stats: dict[str, dict[str, int]] = {}
        for row in rows:
            fw = row["framework"]
            if fw not in stats:
                stats[fw] = {"total": 0}
            stats[fw][row["type"]] = row["count"]
            stats[fw]["total"] += row["count"]
        return stats

    def browse_frameworks(self, framework: str) -> list[dict[str, str | list[str]]]:
        rows = self._conn.execute(
            """SELECT module_path, COUNT(*) as count
               FROM units WHERE framework = ?
               GROUP BY module_path ORDER BY module_path""",
            (framework,),
        ).fetchall()

        modules: list[dict[str, str | list[str]]] = []
        for row in rows:
            module_path = row["module_path"]
            syms = self._conn.execute(
                """SELECT DISTINCT s.symbol FROM symbols s
                   JOIN units u ON u.id = s.unit_id
                   WHERE u.module_path = ? AND u.framework = ?
                   LIMIT 20""",
                (module_path, framework),
            ).fetchall()

            first_unit = self._conn.execute(
                "SELECT content FROM units WHERE module_path = ? AND framework = ? LIMIT 1",
                (module_path, framework),
            ).fetchone()
            desc = ""
            if first_unit:
                desc = first_unit["content"].split("\n")[0][:120]

            modules.append({
                "name": module_path,
                "description": desc,
                "symbols": [s["symbol"] for s in syms],
            })
        return modules

    def browse_module(self, framework: str, module: str) -> list[KnowledgeUnit]:
        rows = self._conn.execute(
            "SELECT * FROM units WHERE framework = ? AND module_path LIKE ?",
            (framework, f"%{module}%"),
        ).fetchall()
        return [KnowledgeUnit.from_dict(dict(row)) for row in rows]

    def delete_framework(self, framework: str) -> None:
        self._conn.execute("DELETE FROM units WHERE framework = ?", (framework,))

    def delete_all(self) -> None:
        self._conn.execute("DELETE FROM units")


class _SymbolRepository:
    """All SQLite operations on the `symbols` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def replace_for_unit(
        self,
        unit_id: str,
        symbols: list[str],
        framework: str,
        definition_symbols: set[str] | None = None,
    ) -> None:
        """Delete existing symbols for *unit_id* and insert new ones."""
        self._conn.execute("DELETE FROM symbols WHERE unit_id = ?", (unit_id,))
        defs = {s.lower().strip("()") for s in (definition_symbols or set())}
        for sym in symbols:
            normalized = sym.lower().strip("()")
            is_def = 1 if normalized in defs else 0
            self._conn.execute(
                "INSERT INTO symbols (symbol, unit_id, framework, is_definition) VALUES (?, ?, ?, ?)",
                (normalized, unit_id, framework, is_def),
            )

    def lookup(self, symbol: str, framework: str | None = None) -> list[str]:
        """Exact symbol lookup. Returns matching unit_ids, definition sites first."""
        normalized = symbol.lower().strip("()")
        if framework:
            rows = self._conn.execute(
                "SELECT unit_id, MAX(is_definition) as is_def "
                "FROM symbols WHERE symbol = ? AND framework = ? "
                "GROUP BY unit_id ORDER BY is_def DESC, MIN(rowid) ASC",
                (normalized, framework),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT unit_id, MAX(is_definition) as is_def "
                "FROM symbols WHERE symbol = ? "
                "GROUP BY unit_id ORDER BY is_def DESC, MIN(rowid) ASC",
                (normalized,),
            ).fetchall()
        return [row["unit_id"] for row in rows]

    def symbols_for_unit(self, unit_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT symbol FROM symbols WHERE unit_id = ?", (unit_id,)
        ).fetchall()
        return [row["symbol"] for row in rows]

    def related_unit_ids(
        self, unit_id: str, framework: str | None = None
    ) -> list[str]:
        """Find unit_ids that share symbols with *unit_id* (excluding itself).

        When *framework* is provided, only return units from that framework.
        """
        symbols = self.symbols_for_unit(unit_id)
        if not symbols:
            return []
        params: list[str] = symbols + [unit_id]
        sql = (
            f"SELECT DISTINCT s.unit_id FROM symbols s "
            f"WHERE s.symbol IN ({_placeholders(symbols)}) AND s.unit_id != ?"
        )
        if framework is not None:
            sql += " AND s.framework = ?"
            params.append(framework)
        rows = self._conn.execute(sql, params).fetchall()
        return [row["unit_id"] for row in rows]

    def delete_framework(self, framework: str) -> None:
        self._conn.execute("DELETE FROM symbols WHERE framework = ?", (framework,))

    def delete_all(self) -> None:
        self._conn.execute("DELETE FROM symbols")


class _VersionRepository:
    """All SQLite operations on the `source_versions` table."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def set(self, framework: str, version_key: str, doc_system: str = "") -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO source_versions (framework, version_key, ingested_at, doc_system)
               VALUES (?, ?, ?, ?)""",
            (framework, version_key, datetime.now(UTC).isoformat(), doc_system),
        )
        self._conn.commit()

    def get(self, framework: str) -> tuple[str, str] | None:
        row = self._conn.execute(
            "SELECT version_key, ingested_at FROM source_versions WHERE framework = ?",
            (framework,),
        ).fetchone()
        if row:
            return (row["version_key"], row["ingested_at"])
        return None

    def get_doc_system(self, framework: str) -> str:
        """Return the cached doc_system for a framework, or empty string."""
        row = self._conn.execute(
            "SELECT doc_system FROM source_versions WHERE framework = ?",
            (framework,),
        ).fetchone()
        return row["doc_system"] if row else ""

    def get_all(self) -> dict[str, tuple[str, str]]:
        rows = self._conn.execute(
            "SELECT framework, version_key, ingested_at FROM source_versions"
        ).fetchall()
        return {row["framework"]: (row["version_key"], row["ingested_at"]) for row in rows}


# ---------------------------------------------------------------------------
# Schema initialisation (FTS5 + triggers = inherently raw SQL)
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = 2

_SCHEMA_SQL = """\
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
    definition_symbols TEXT,
    relevance_decay REAL NOT NULL DEFAULT 1.0,
    language TEXT,
    source_file TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS units_fts USING fts5(
    content,
    module_path,
    related_symbols,
    heading_hierarchy,
    content=units,
    content_rowid=rowid
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS units_ai AFTER INSERT ON units BEGIN
    INSERT INTO units_fts(rowid, content, module_path, related_symbols, heading_hierarchy)
    VALUES (new.rowid, new.content, new.module_path, new.related_symbols, new.heading_hierarchy);
END;

CREATE TRIGGER IF NOT EXISTS units_ad AFTER DELETE ON units BEGIN
    INSERT INTO units_fts(units_fts, rowid, content, module_path, related_symbols, heading_hierarchy)
    VALUES ('delete', old.rowid, old.content, old.module_path, old.related_symbols, old.heading_hierarchy);
END;

CREATE TRIGGER IF NOT EXISTS units_au AFTER UPDATE ON units BEGIN
    INSERT INTO units_fts(units_fts, rowid, content, module_path, related_symbols, heading_hierarchy)
    VALUES ('delete', old.rowid, old.content, old.module_path, old.related_symbols, old.heading_hierarchy);
    INSERT INTO units_fts(rowid, content, module_path, related_symbols, heading_hierarchy)
    VALUES (new.rowid, new.content, new.module_path, new.related_symbols, new.heading_hierarchy);
END;

CREATE TABLE IF NOT EXISTS source_versions (
    framework TEXT PRIMARY KEY,
    version_key TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    doc_system TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS symbols (
    symbol TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    framework TEXT NOT NULL,
    is_definition INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (unit_id) REFERENCES units(id)
);

CREATE INDEX IF NOT EXISTS idx_symbols_lookup ON symbols(symbol, framework, is_definition);
"""


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------


class Storage:
    """Manages the dual ChromaDB + SQLite storage layer."""

    def __init__(self, data_dir: str, embedding_model: str = "nomic-ai/nomic-embed-text-v1.5"):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._embedding_model = embedding_model

        # SQLite
        self._db_path = self._data_dir / "rtfm.db"
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._check_schema_version()
        self._conn.cursor().executescript(_SCHEMA_SQL)
        # Ensure schema version is recorded
        row = self._conn.execute("SELECT version FROM schema_meta LIMIT 1").fetchone()
        if row is None:
            self._conn.execute("INSERT INTO schema_meta (version) VALUES (?)", (_SCHEMA_VERSION,))
        self._conn.commit()

        # Repositories
        self._units = _UnitRepository(self._conn)
        self._symbols = _SymbolRepository(self._conn)
        self._versions = _VersionRepository(self._conn)

        # ChromaDB — first call may load the embedding model and trigger
        # native stderr output (onnxruntime GPU probe). Route fd 2 to the
        # library log for the duration so the user-facing stream stays clean.
        with _route_native_stderr():
            self._chroma_client = chromadb.PersistentClient(path=str(self._data_dir / "chroma"))
            self._collection = self._chroma_client.get_or_create_collection(
                name="knowledge_units",
                metadata={"hnsw:space": "cosine"},
            )

    def _check_schema_version(self) -> None:
        """Check schema version and drop data if outdated.

        When the schema version is older than the current version, all tables
        are dropped and recreated. The user must run `rtfm ingest --rebuild`
        to repopulate.
        """
        cur = self._conn.cursor()
        try:
            row = cur.execute(
                "SELECT version FROM schema_meta LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            # Table doesn't exist yet — fresh DB or pre-versioned DB
            row = None

        if row is None:
            # First run or pre-versioned DB — drop everything and start fresh
            old_tables = cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name != 'schema_meta'"
            ).fetchall()
            if old_tables:
                logging.getLogger(__name__).warning(
                    "schema upgraded to v%d — existing data dropped, "
                    "run `rtfm ingest --rebuild` to repopulate",
                    _SCHEMA_VERSION,
                )
                # Drop in correct order: triggers first (implicit with tables),
                # then FTS, then regular tables
                for suffix in ("units_fts", "units", "symbols", "source_versions"):
                    cur.execute(f"DROP TABLE IF EXISTS {suffix}")
                self._conn.commit()
            return

        if row["version"] < _SCHEMA_VERSION:
            logging.getLogger(__name__).warning(
                "schema upgraded from v%d to v%d — existing data dropped, "
                "run `rtfm ingest --rebuild` to repopulate",
                row["version"],
                _SCHEMA_VERSION,
            )
            for suffix in ("units_fts", "units", "symbols", "source_versions", "schema_meta"):
                cur.execute(f"DROP TABLE IF EXISTS {suffix}")
            self._conn.commit()

    # -- Mutation --------------------------------------------------------

    def insert_units(
        self,
        units: list[KnowledgeUnit],
        on_progress: "Callable[[int, int], None] | None" = None,
    ) -> None:
        """Insert or replace units into both SQLite and ChromaDB."""
        if not units:
            return

        for unit in units:
            self._units.upsert(unit)
            self._symbols.replace_for_unit(
                unit.id,
                unit.related_symbols,
                unit.framework,
                set(unit.definition_symbols),
            )
        self._conn.commit()

        self._chroma_upsert(units, on_progress=on_progress)

    def clear_framework(self, framework: str) -> None:
        """Remove all data for a framework."""
        ids = self._units.ids_for_framework(framework)
        self._symbols.delete_framework(framework)
        self._units.delete_framework(framework)
        self._conn.commit()
        self._chroma_delete(ids)

    def clear_all(self) -> None:
        """Remove all data."""
        self._symbols.delete_all()
        self._units.delete_all()
        self._conn.commit()

        self._chroma_client.delete_collection("knowledge_units")
        self._collection = self._chroma_client.get_or_create_collection(
            name="knowledge_units",
            metadata={"hnsw:space": "cosine"},
        )

    # -- Search ----------------------------------------------------------

    def semantic_search(
        self,
        query: str,
        top_k: int = 20,
        framework: str | None = None,
        unit_type: str | None = None,
    ) -> list[tuple[str, float]]:
        """Semantic search via ChromaDB. Returns list of (unit_id, distance)."""
        where_filter: Where | None = None
        conditions: list[Where] = []
        if framework:
            conditions.append({"framework": framework})
        if unit_type:
            conditions.append({"type": unit_type})

        if len(conditions) == 1:
            where_filter = conditions[0]
        elif len(conditions) > 1:
            where_filter = {"$and": conditions}  # pyright: ignore[reportArgumentType]

        with _route_native_stderr():
            results = self._collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where_filter,
            )

        ids = results["ids"][0] if results["ids"] else []
        distances = results["distances"][0] if results["distances"] else []
        return list(zip(ids, distances, strict=False))

    def keyword_search(
        self,
        query: str,
        top_k: int = 20,
        framework: str | None = None,
        unit_type: str | None = None,
    ) -> list[str]:
        """FTS5 keyword search. Returns list of unit_ids ordered by BM25 rank."""
        return self._units.keyword_search(query, top_k, framework, unit_type)

    def lookup_symbol(
        self,
        symbol: str,
        framework: str | None = None,
    ) -> list[str]:
        """Exact symbol lookup. Returns unit_ids matching the symbol."""
        return self._symbols.lookup(symbol, framework)

    # -- Read ------------------------------------------------------------

    def get_unit(self, unit_id: str) -> KnowledgeUnit | None:
        return self._units.get(unit_id)

    def get_units(self, unit_ids: list[str]) -> list[KnowledgeUnit]:
        return self._units.get_many(unit_ids)

    def get_related_units(
        self, unit_id: str, framework: str | None = None
    ) -> list[KnowledgeUnit]:
        """Get units related to a given unit via shared symbols.

        When *framework* is provided, restrict matches to that framework.
        """
        related_ids = self._symbols.related_unit_ids(unit_id, framework=framework)
        return self._units.get_many(related_ids)

    def get_examples_for_unit(
        self, unit_id: str, framework: str | None = None
    ) -> list[KnowledgeUnit]:
        related = self.get_related_units(unit_id, framework=framework)
        return [u for u in related if u.type == UnitType.EXAMPLE]

    # -- Browse ----------------------------------------------------------

    def browse_frameworks(self, framework: str) -> list[dict[str, str | list[str]]]:
        return self._units.browse_frameworks(framework)

    def browse_module(self, framework: str, module: str) -> list[KnowledgeUnit]:
        return self._units.browse_module(framework, module)

    # -- Stats / versions ------------------------------------------------

    def get_stats(self) -> dict[str, dict[str, int]]:
        return self._units.stats()

    def set_version(self, framework: str, version_key: str, doc_system: str = "") -> None:
        self._versions.set(framework, version_key, doc_system=doc_system)

    def get_version(self, framework: str) -> tuple[str, str] | None:
        return self._versions.get(framework)

    def get_all_versions(self) -> dict[str, tuple[str, str]]:
        return self._versions.get_all()

    @property
    def conn(self) -> sqlite3.Connection:
        """Raw SQLite connection for read-only queries (e.g. health score)."""
        return self._conn

    # -- Lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._conn.close()
        del self._collection
        del self._chroma_client

    # -- ChromaDB helpers ------------------------------------------------

    def _chroma_upsert(
        self,
        units: list[KnowledgeUnit],
        on_progress: "Callable[[int, int], None] | None" = None,
    ) -> None:
        ids = [u.id for u in units]
        documents = [
            " > ".join(u.heading_hierarchy) + "\n\n" + u.content
            for u in units
        ]
        metadatas: list[Metadata] = [
            {
                "type": u.type.value,
                "framework": u.framework,
                "module_path": u.module_path,
                "language": u.language,
            }
            for u in units
        ]
        batch_size = 100
        total = len(ids)
        with _route_native_stderr():
            for i in range(0, total, batch_size):
                self._collection.upsert(
                    ids=ids[i : i + batch_size],
                    documents=documents[i : i + batch_size],
                    metadatas=metadatas[i : i + batch_size],
                )
                done = min(i + batch_size, total)
                if on_progress is not None:
                    on_progress(done, total)
        # No fallback print: if the caller wants progress, they pass a callback.
        # Library code never writes to stdout directly — that is the CLI's job.

    def _chroma_delete(self, ids: list[str]) -> None:
        if not ids:
            return
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            self._collection.delete(ids=ids[i : i + batch_size])
