"""Hybrid search: combines semantic (ChromaDB) and keyword (FTS5) results via RRF."""


from rtfm.models import BrowseResult, KnowledgeUnit, LookupResult, ModuleOverview
from rtfm.storage import Storage


def hybrid_search(
    storage: Storage,
    query: str,
    framework: str | None = None,
    unit_type: str | None = None,
    top_k: int = 10,
) -> list[KnowledgeUnit]:
    """Hybrid search combining semantic and keyword results with Reciprocal Rank Fusion."""
    # Get candidates from both indices — fetch extra to compensate for decay
    semantic_results = storage.semantic_search(query, top_k=top_k * 2, framework=framework, unit_type=unit_type)
    keyword_results = storage.keyword_search(query, top_k=top_k * 2, framework=framework, unit_type=unit_type)

    # Reciprocal Rank Fusion
    k = 60  # RRF constant
    scores: dict[str, float] = {}

    for rank, (uid, _distance) in enumerate(semantic_results):
        scores[uid] = scores.get(uid, 0) + 1.0 / (k + rank + 1)

    for rank, uid in enumerate(keyword_results):
        scores[uid] = scores.get(uid, 0) + 1.0 / (k + rank + 1)

    # Fetch more than top_k so decay doesn't accidentally drop good results
    sorted_ids = sorted(scores.keys(), key=lambda uid: scores[uid], reverse=True)[:top_k * 2]
    units = storage.get_units(sorted_ids)

    # Apply per-unit relevance decay and re-rank
    for unit in units:
        scores[unit.id] *= unit.relevance_decay

    units.sort(key=lambda u: scores[u.id], reverse=True)
    return units[:top_k]


def lookup_symbol(
    storage: Storage,
    symbol: str,
    framework: str | None = None,
    include_related: bool = True,
    include_examples: bool = True,
) -> LookupResult | None:
    """Exact symbol lookup with optional related units and examples."""
    unit_ids = storage.lookup_symbol(symbol, framework=framework)
    if not unit_ids:
        return None

    # Primary match is the first result
    primary = storage.get_unit(unit_ids[0])
    if not primary:
        return None

    related: list[KnowledgeUnit] = []
    examples: list[KnowledgeUnit] = []

    if include_related:
        related = storage.get_related_units(primary.id, framework=framework)
        # Filter out examples from related (they go in their own list)
        related = [u for u in related if u.type.value != "example"]

    if include_examples:
        examples = storage.get_examples_for_unit(primary.id, framework=framework)
        # Also include examples from other matching units
        for uid in unit_ids[1:]:
            examples.extend(storage.get_examples_for_unit(uid, framework=framework))

    # Deduplicate examples
    seen: set[str] = set()
    unique_examples: list[KnowledgeUnit] = []
    for ex in examples:
        if ex.id not in seen:
            seen.add(ex.id)
            unique_examples.append(ex)

    return LookupResult(match=primary, related=related, examples=unique_examples)


def browse(
    storage: Storage,
    framework: str,
    module: str | None = None,
) -> BrowseResult:
    """Structural navigation of the documentation."""
    if module:
        units = storage.browse_module(framework, module)
        return BrowseResult(framework=framework, module=module, units=units)

    modules_data = storage.browse_frameworks(framework)
    modules = [
        ModuleOverview(
            name=str(m["name"]),
            description=str(m["description"]),
            symbols=list(m.get("symbols", [])),
        )
        for m in modules_data
    ]
    return BrowseResult(framework=framework, modules=modules)


def bundle_topic(
    storage: Storage,
    query: str,
    framework: str | None = None,
    top_k: int = 5,
) -> dict[str, list[KnowledgeUnit]]:
    """Get everything about a topic, grouped by unit type.

    Runs one broad search, then sorts results into api/example/concept/pitfall buckets.
    Each bucket gets up to top_k results.
    """
    # Cast a wide net
    all_results = hybrid_search(storage, query, framework=framework, top_k=top_k * 4)

    buckets: dict[str, list[KnowledgeUnit]] = {
        "api": [],
        "example": [],
        "concept": [],
        "pitfall": [],
    }

    for unit in all_results:
        type_key = unit.type.value if hasattr(unit.type, "value") else unit.type
        if type_key in buckets and len(buckets[type_key]) < top_k:
            buckets[type_key].append(unit)

    # Drop empty buckets
    return {k: v for k, v in buckets.items() if v}
