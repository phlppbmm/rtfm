"""Tests for the import health score."""

import sqlite3
import json

from rtfm.health import compute_health, _score_to_grade


def _setup_test_db() -> sqlite3.Connection:
    """Create an in-memory DB with the rtfm schema and sample data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE units (
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
        CREATE TABLE symbols (
            symbol TEXT NOT NULL,
            unit_id TEXT NOT NULL,
            framework TEXT NOT NULL,
            is_definition INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE source_versions (
            framework TEXT PRIMARY KEY,
            version_key TEXT NOT NULL,
            ingested_at TEXT NOT NULL,
            doc_system TEXT NOT NULL DEFAULT ''
        );
    """)
    return conn


class TestScoreToGrade:
    def test_grade_a(self):
        assert _score_to_grade(90) == "A"
        assert _score_to_grade(85) == "A"

    def test_grade_b(self):
        assert _score_to_grade(70) == "B"
        assert _score_to_grade(84) == "B"

    def test_grade_c(self):
        assert _score_to_grade(50) == "C"
        assert _score_to_grade(69) == "C"

    def test_grade_d(self):
        assert _score_to_grade(30) == "D"

    def test_grade_f(self):
        assert _score_to_grade(0) == "F"
        assert _score_to_grade(29) == "F"


class TestComputeHealth:
    def test_empty_framework(self):
        conn = _setup_test_db()
        h = compute_health(conn, "nonexistent")
        assert h.score == 0
        assert h.grade == "F"

    def test_healthy_diverse_framework(self):
        conn = _setup_test_db()
        # Insert diverse units with good content
        for i, utype in enumerate(["api", "example", "concept", "pitfall"]):
            conn.execute(
                "INSERT INTO units (id, type, framework, module_path, content, "
                "related_symbols, definition_symbols, relevance_decay, source_file) "
                "VALUES (?, ?, 'test', 'test.mod', ?, '[]', '[]', 1.0, 'test.md')",
                (f"u{i}", utype, "x" * 500),
            )
        conn.execute(
            "INSERT INTO symbols (symbol, unit_id, framework, is_definition) "
            "VALUES ('foo', 'u0', 'test', 1)"
        )
        conn.execute(
            "INSERT INTO source_versions (framework, version_key, ingested_at, doc_system) "
            "VALUES ('test', 'abc', '2024-01-01', 'sphinx')"
        )
        conn.commit()

        h = compute_health(conn, "test")
        assert h.score >= 85
        assert h.grade == "A"
        assert h.type_diversity == 4

    def test_concept_only_framework(self):
        conn = _setup_test_db()
        for i in range(10):
            conn.execute(
                "INSERT INTO units (id, type, framework, module_path, content, "
                "related_symbols, definition_symbols, relevance_decay, source_file) "
                "VALUES (?, 'concept', 'test', 'test.mod', ?, '[]', '[]', 1.0, 'test.md')",
                (f"u{i}", "x" * 200),
            )
        conn.execute(
            "INSERT INTO source_versions (framework, version_key, ingested_at, doc_system) "
            "VALUES ('test', 'abc', '2024-01-01', 'generic_md')"
        )
        conn.commit()

        h = compute_health(conn, "test")
        assert h.score <= 70  # penalized for no api/examples, no symbols, generic parser
        assert "no api or example units" in h.signals

    def test_high_stub_ratio(self):
        conn = _setup_test_db()
        for i in range(10):
            content = "x" * 20 if i < 8 else "x" * 500  # 80% stubs
            conn.execute(
                "INSERT INTO units (id, type, framework, module_path, content, "
                "related_symbols, definition_symbols, relevance_decay, source_file) "
                "VALUES (?, 'api', 'test', 'test.mod', ?, '[]', '[]', 1.0, 'test.md')",
                (f"u{i}", content),
            )
        conn.execute(
            "INSERT INTO source_versions (framework, version_key, ingested_at, doc_system) "
            "VALUES ('test', 'abc', '2024-01-01', 'sphinx')"
        )
        conn.commit()

        h = compute_health(conn, "test")
        assert h.stub_ratio > 0.5
        assert any("stub" in s for s in h.signals)

    def test_changelog_heavy(self):
        conn = _setup_test_db()
        for i in range(10):
            decay = 0.3 if i < 6 else 1.0  # 60% changelog
            conn.execute(
                "INSERT INTO units (id, type, framework, module_path, content, "
                "related_symbols, definition_symbols, relevance_decay, source_file) "
                "VALUES (?, 'concept', 'test', 'test.mod', ?, '[]', '[]', ?, 'test.md')",
                (f"u{i}", "x" * 200, decay),
            )
        conn.execute(
            "INSERT INTO source_versions (framework, version_key, ingested_at, doc_system) "
            "VALUES ('test', 'abc', '2024-01-01', 'sphinx')"
        )
        conn.commit()

        h = compute_health(conn, "test")
        assert h.decay_ratio > 0.5
        assert any("changelog" in s for s in h.signals)
