"""Data models for rtfm Knowledge Units and API responses."""


import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel


class UnitType(StrEnum):
    API = "api"
    EXAMPLE = "example"
    CONCEPT = "concept"
    PITFALL = "pitfall"


@dataclass
class KnowledgeUnit:
    """Atomic knowledge unit — the central data object in rtfm."""

    type: UnitType
    framework: str
    module_path: str
    heading_hierarchy: list[str]
    content: str
    related_symbols: list[str] = field(default_factory=list)
    definition_symbols: list[str] = field(default_factory=list)
    relevance_decay: float = 1.0
    language: str = ""
    source_file: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = self._compute_id()

    def _compute_id(self) -> str:
        # Include content hash to avoid collisions when headings repeat within a file
        content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:8]
        key = f"{self.framework}:{self.source_file}:{json.dumps(self.heading_hierarchy)}:{content_hash}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, str | float | list[str]]:
        return {
            "id": self.id,
            "type": self.type.value,
            "framework": self.framework,
            "module_path": self.module_path,
            "heading_hierarchy": self.heading_hierarchy,
            "content": self.content,
            "related_symbols": self.related_symbols,
            "definition_symbols": self.definition_symbols,
            "relevance_decay": self.relevance_decay,
            "language": self.language,
            "source_file": self.source_file,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        return cls(
            id=d["id"],
            type=UnitType(d["type"]),
            framework=d["framework"],
            module_path=d["module_path"],
            heading_hierarchy=_parse_json_list(d.get("heading_hierarchy", "[]")),
            content=d["content"],
            related_symbols=_parse_json_list(d.get("related_symbols", "[]")),
            definition_symbols=_parse_json_list(d.get("definition_symbols", "[]")),
            relevance_decay=float(d.get("relevance_decay", 1.0)),
            language=d.get("language", ""),
            source_file=d.get("source_file", ""),
        )


def _parse_json_list(val: Any) -> list[str]:
    """Parse a value that may be a JSON string or already a list."""
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


@dataclass
class LookupResult:
    """Response for /lookup/{symbol}."""

    match: KnowledgeUnit
    related: list[KnowledgeUnit] = field(default_factory=list)
    examples: list[KnowledgeUnit] = field(default_factory=list)


@dataclass
class ModuleOverview:
    """Module summary for /browse response."""

    name: str
    description: str
    symbols: list[str] = field(default_factory=list)


@dataclass
class BrowseResult:
    """Response for /browse."""

    framework: str
    module: str | None = None
    modules: list[ModuleOverview] = field(default_factory=list)
    units: list[KnowledgeUnit] = field(default_factory=list)


@dataclass
class SourceConfig:
    """Configuration for a single documentation source."""

    name: str
    type: str  # "github", "local", "llms_txt", "website"
    language: str
    repo: str = ""
    docs_path: str = ""
    glob: str = "**/*.md"
    path: str = ""
    url: str = ""
    sitemap: str = ""
    url_filter: str = ""
    urls: list[str] | None = None
    doc_system: str = ""  # detected or explicit: sphinx, mkdocs, rustdoc, typedoc, llms_txt, generic_md

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> Self:
        return cls(
            name=name,
            type=d["type"],
            language=d.get("language", ""),
            repo=d.get("repo", ""),
            docs_path=d.get("docs_path", ""),
            glob=d.get("glob", "**/*.md"),
            path=d.get("path", ""),
            url=d.get("url", ""),
            sitemap=d.get("sitemap", ""),
            url_filter=d.get("url_filter", ""),
            urls=d.get("urls"),
            doc_system=d.get("doc_system", ""),
        )


def default_home() -> str:
    """Return ~/.rtfm as the default home directory."""
    return str(Path.home() / ".rtfm")


@dataclass
class AppConfig:
    """Top-level application configuration."""

    embedding_model: str = "nomic-ai/nomic-embed-text-v1.5"
    data_dir: str = ""
    host: str = "127.0.0.1"
    port: int = 8787
    min_health_score: int = 80
    sources: dict[str, SourceConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.data_dir:
            self.data_dir = str(Path(default_home()) / "data")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        server: dict[str, str | int] = d.get("server", {})
        sources: dict[str, SourceConfig] = {}
        for name, src in d.get("sources", {}).items():
            sources[name] = SourceConfig.from_dict(name, src)
        return cls(
            embedding_model=d.get("embedding_model", "nomic-ai/nomic-embed-text-v1.5"),
            data_dir=d.get("data_dir", ""),
            host=str(server.get("host", "127.0.0.1")),
            port=int(server.get("port", 8787)),
            min_health_score=int(d.get("min_health_score", 80)),
            sources=sources,
        )


# ---------------------------------------------------------------------------
# Pydantic response models for the HTTP API
# ---------------------------------------------------------------------------

class UnitResponse(BaseModel):
    """A single knowledge unit as returned by the API."""
    id: str
    type: UnitType
    framework: str
    module_path: str
    heading_hierarchy: list[str]
    content: str
    related_symbols: list[str]
    definition_symbols: list[str] = []
    relevance_decay: float = 1.0
    language: str
    source_file: str


class LookupResponse(BaseModel):
    """Response for GET /lookup/{symbol}."""
    match: UnitResponse
    related: list[UnitResponse]
    examples: list[UnitResponse]


class ModuleResponse(BaseModel):
    """A module summary within a framework."""
    name: str
    description: str
    symbols: list[str]


class BrowseModulesResponse(BaseModel):
    """Response for GET /browse when listing modules."""
    framework: str
    modules: list[ModuleResponse]


class BrowseUnitsResponse(BaseModel):
    """Response for GET /browse when drilling into a module."""
    framework: str
    module: str
    units: list[UnitResponse]


class BundleResponse(BaseModel):
    """Response for GET /bundle — grouped by unit type."""
    api: list[UnitResponse] = []
    example: list[UnitResponse] = []
    concept: list[UnitResponse] = []
    pitfall: list[UnitResponse] = []
