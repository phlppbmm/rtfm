"""FastAPI server exposing rtfm search, lookup, browse, and bundle endpoints."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query

from rtfm.models import (
    AppConfig,
    BrowseModulesResponse,
    BrowseUnitsResponse,
    BundleResponse,
    KnowledgeUnit,
    LookupResponse,
    ModuleResponse,
    UnitResponse,
    default_home,
)
from rtfm.search import browse, bundle_topic, hybrid_search, lookup_symbol
from rtfm.storage import Storage


def _load_config() -> AppConfig:
    config_path = Path(default_home()) / "config.yaml"
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f)
            return AppConfig.from_dict(data)
    return AppConfig()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    config = _load_config()
    app.state.storage = Storage(config.data_dir, config.embedding_model)
    yield
    app.state.storage.close()


app = FastAPI(
    title="rtfm",
    description="Local documentation retrieval service for agent-assisted development. "
    "Indexes framework docs into typed Knowledge Units (api, example, concept, pitfall) "
    "and exposes them via hybrid semantic + keyword search.",
    version="0.1.0",
    lifespan=lifespan,
)


def get_storage() -> Storage:
    """Dependency that provides the shared Storage instance."""
    storage: Storage = app.state.storage
    return storage


StorageDep = Annotated[Storage, Depends(get_storage)]


def _unit_response(unit: KnowledgeUnit) -> UnitResponse:
    """Convert a KnowledgeUnit dataclass to a Pydantic response model."""
    return UnitResponse(
        id=unit.id,
        type=unit.type,
        framework=unit.framework,
        module_path=unit.module_path,
        heading_hierarchy=unit.heading_hierarchy,
        content=unit.content,
        related_symbols=unit.related_symbols,
        language=unit.language,
        source_file=unit.source_file,
    )


@app.get("/search", response_model=list[UnitResponse])
def search_endpoint(
    storage: StorageDep,
    q: Annotated[str, Query(description="Search query")],
    framework: Annotated[str | None, Query(description="Filter by framework name")] = None,
    type: Annotated[str | None, Query(description="Filter by unit type: api, example, concept, pitfall")] = None,
    top_k: Annotated[int, Query(ge=1, le=100, description="Number of results to return")] = 10,
) -> list[UnitResponse]:
    """Hybrid semantic + keyword search across all indexed documentation.

    Uses Reciprocal Rank Fusion (RRF) to merge results from ChromaDB vector
    search and SQLite FTS5 keyword search. Returns ranked knowledge units.
    """
    units = hybrid_search(storage, q, framework=framework, unit_type=type, top_k=top_k)
    return [_unit_response(u) for u in units]


@app.get("/lookup/{symbol}", response_model=LookupResponse)
def lookup_endpoint(
    storage: StorageDep,
    symbol: str,
    framework: Annotated[
        str | None, Query(description="Disambiguate when symbol exists in multiple frameworks")
    ] = None,
    include_related: Annotated[bool, Query(description="Include units sharing the same symbols")] = True,
    include_examples: Annotated[bool, Query(description="Include example-type units for this symbol")] = True,
) -> LookupResponse:
    """Exact symbol lookup by function, class, or component name.

    Returns the primary matching unit plus optionally related units and examples.
    Use the framework parameter to disambiguate when a symbol exists in multiple
    indexed frameworks (e.g. 'WebSocket' in both fastapi and svelte).
    """
    result = lookup_symbol(
        storage, symbol,
        framework=framework,
        include_related=include_related,
        include_examples=include_examples,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")

    return LookupResponse(
        match=_unit_response(result.match),
        related=[_unit_response(u) for u in result.related],
        examples=[_unit_response(u) for u in result.examples],
    )


@app.get("/browse", response_model=BrowseModulesResponse | BrowseUnitsResponse)
def browse_endpoint(
    storage: StorageDep,
    framework: Annotated[str, Query(description="Framework to browse")],
    module: Annotated[str | None, Query(description="Drill down into a specific module")] = None,
) -> BrowseModulesResponse | BrowseUnitsResponse:
    """Structural navigation of indexed documentation.

    Without the module parameter: returns a list of all modules and their
    top symbols for the given framework.
    With module: returns all knowledge units inside that module.
    """
    result = browse(storage, framework, module=module)

    if result.module:
        return BrowseUnitsResponse(
            framework=result.framework,
            module=result.module,
            units=[_unit_response(u) for u in result.units],
        )

    return BrowseModulesResponse(
        framework=result.framework,
        modules=[
            ModuleResponse(
                name=m.name,
                description=m.description,
                symbols=m.symbols,
            )
            for m in result.modules
        ],
    )


@app.get("/bundle", response_model=BundleResponse)
def bundle_endpoint(
    storage: StorageDep,
    q: Annotated[str, Query(description="Topic query")],
    framework: Annotated[str | None, Query(description="Filter by framework name")] = None,
    top_k: Annotated[int, Query(ge=1, le=50, description="Max results per type bucket")] = 5,
) -> BundleResponse:
    """Get everything about a topic in one call.

    Returns knowledge units grouped by type (api, example, concept, pitfall).
    Ideal for building comprehensive issue context without multiple queries.
    Internally runs a broad hybrid search and distributes results into typed buckets.
    """
    result = bundle_topic(storage, q, framework=framework, top_k=top_k)
    return BundleResponse(**{
        section: [_unit_response(u) for u in units]
        for section, units in result.items()
    })
