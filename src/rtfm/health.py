"""Import health score — rates the quality and usability of ingested documentation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3


@dataclass
class HealthDetail:
    """Detailed breakdown of a framework's health score."""

    framework: str
    score: int = 0  # 0-100
    grade: str = "F"  # A/B/C/D/F

    # Raw metrics
    total_units: int = 0
    api_count: int = 0
    example_count: int = 0
    concept_count: int = 0
    pitfall_count: int = 0
    stub_count: int = 0  # units with content < 50 chars
    avg_content_len: float = 0.0
    total_symbols: int = 0
    definition_symbols: int = 0
    decayed_units: int = 0
    doc_system: str = ""

    # Derived ratios
    type_diversity: int = 0  # how many of the 4 types are present
    stub_ratio: float = 0.0
    definition_ratio: float = 0.0  # def_symbols / total_symbols
    decay_ratio: float = 0.0  # decayed / total

    # Individual score components
    signals: list[str] = field(default_factory=list)  # human-readable explanations


def _score_to_grade(score: int) -> str:
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 50:
        return "C"
    if score >= 30:
        return "D"
    return "F"


def compute_health(conn: sqlite3.Connection, framework: str) -> HealthDetail:
    """Compute the health score for a single framework."""
    h = HealthDetail(framework=framework)

    # Unit stats
    row = conn.execute(
        """SELECT
            COUNT(*) as total,
            SUM(CASE WHEN type='api' THEN 1 ELSE 0 END) as api,
            SUM(CASE WHEN type='example' THEN 1 ELSE 0 END) as example,
            SUM(CASE WHEN type='concept' THEN 1 ELSE 0 END) as concept,
            SUM(CASE WHEN type='pitfall' THEN 1 ELSE 0 END) as pitfall,
            SUM(CASE WHEN length(content) < 50 THEN 1 ELSE 0 END) as stubs,
            AVG(length(content)) as avg_len,
            SUM(CASE WHEN relevance_decay < 1.0 THEN 1 ELSE 0 END) as decayed
        FROM units WHERE framework=?""",
        (framework,),
    ).fetchone()

    if not row or row[0] == 0:
        h.score = 0
        h.grade = "F"
        h.signals.append("no units ingested")
        return h

    h.total_units = row[0]
    h.api_count = row[1]
    h.example_count = row[2]
    h.concept_count = row[3]
    h.pitfall_count = row[4]
    h.stub_count = row[5]
    h.avg_content_len = row[6] or 0
    h.decayed_units = row[7]

    # Symbol stats
    sym_row = conn.execute(
        "SELECT COUNT(*) as total, SUM(is_definition) as defs FROM symbols WHERE framework=?",
        (framework,),
    ).fetchone()
    h.total_symbols = sym_row[0] if sym_row else 0
    h.definition_symbols = sym_row[1] if sym_row and sym_row[1] else 0

    # Doc system
    ver_row = conn.execute(
        "SELECT doc_system FROM source_versions WHERE framework=?",
        (framework,),
    ).fetchone()
    h.doc_system = ver_row[0] if ver_row else ""

    # Derived ratios
    types_present = sum(1 for c in [h.api_count, h.example_count, h.concept_count, h.pitfall_count] if c > 0)
    h.type_diversity = types_present
    h.stub_ratio = h.stub_count / h.total_units if h.total_units else 0
    h.definition_ratio = h.definition_symbols / h.total_symbols if h.total_symbols else 0
    h.decay_ratio = h.decayed_units / h.total_units if h.total_units else 0

    # --- Scoring ---
    score = 100
    signals = h.signals

    # Type diversity (0-25 points deducted)
    if h.api_count == 0 and h.example_count == 0:
        score -= 25
        signals.append("no api or example units")
    elif h.api_count == 0:
        score -= 12
        signals.append("no api units")
    elif h.example_count == 0:
        score -= 8
        signals.append("no example units")

    if types_present >= 3:
        signals.append(f"{types_present}/4 type diversity")

    # Stub ratio (0-20 points deducted)
    if h.stub_ratio > 0.5:
        score -= 20
        signals.append(f"{h.stub_ratio:.0%} stub units (<50 chars)")
    elif h.stub_ratio > 0.3:
        score -= 10
        signals.append(f"{h.stub_ratio:.0%} stub units")

    # Definition coverage (0-20 points deducted)
    if h.total_symbols > 10:
        if h.definition_ratio == 0:
            score -= 20
            signals.append("no definition sites for symbol lookup")
        elif h.definition_ratio < 0.1:
            score -= 10
            signals.append(f"low definition coverage ({h.definition_ratio:.0%})")
        else:
            signals.append(f"{h.definition_ratio:.0%} definition coverage")
    elif h.total_symbols == 0:
        score -= 10
        signals.append("no symbols extracted")

    # Content quality (0-15 points deducted)
    if h.avg_content_len < 100:
        score -= 15
        signals.append(f"avg content {h.avg_content_len:.0f} chars (very short)")
    elif h.avg_content_len < 300:
        score -= 5
        signals.append(f"avg content {h.avg_content_len:.0f} chars (short)")

    # Changelog noise (0-10 points deducted)
    if h.decay_ratio > 0.5:
        score -= 10
        signals.append(f"{h.decay_ratio:.0%} units are changelog/release notes")
    elif h.decay_ratio > 0.3:
        score -= 5
        signals.append(f"{h.decay_ratio:.0%} changelog/release notes")

    # Doc system detection (0-5 points deducted)
    if h.doc_system == "generic_md":
        score -= 5
        signals.append("generic parser (no doc-system detected)")
    else:
        signals.append(f"parser: {h.doc_system}")

    h.score = max(0, min(100, score))
    h.grade = _score_to_grade(h.score)
    return h


def compute_all_health(conn: sqlite3.Connection) -> dict[str, HealthDetail]:
    """Compute health scores for all frameworks."""
    rows = conn.execute(
        "SELECT DISTINCT framework FROM units ORDER BY framework"
    ).fetchall()
    return {row[0]: compute_health(conn, row[0]) for row in rows}
