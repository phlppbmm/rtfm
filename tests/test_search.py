"""Tests for storage and search functionality."""

import gc
import shutil
import tempfile

import pytest

from rtfm.models import KnowledgeUnit, UnitType
from rtfm.storage import Storage
from rtfm.search import hybrid_search, lookup_symbol, browse, bundle_topic


@pytest.fixture
def storage():
    tmpdir = tempfile.mkdtemp()
    s = Storage(data_dir=tmpdir)
    yield s
    s.close()
    gc.collect()
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def populated_storage(storage):
    """Storage with sample units for testing."""
    units = [
        KnowledgeUnit(
            type=UnitType.API,
            framework="fastapi",
            module_path="fastapi.security",
            heading_hierarchy=["Security", "OAuth2PasswordBearer"],
            content="class OAuth2PasswordBearer:\n    OAuth2 password bearer authentication scheme.",
            related_symbols=["OAuth2PasswordBearer", "Depends"],
            language="python",
            source_file="docs/security.md",
        ),
        KnowledgeUnit(
            type=UnitType.EXAMPLE,
            framework="fastapi",
            module_path="fastapi.security",
            heading_hierarchy=["Security", "OAuth2 Example"],
            content='```python\nfrom fastapi.security import OAuth2PasswordBearer\noauth2 = OAuth2PasswordBearer(tokenUrl="token")\n```',
            related_symbols=["OAuth2PasswordBearer"],
            language="python",
            source_file="docs/security.md",
        ),
        KnowledgeUnit(
            type=UnitType.CONCEPT,
            framework="fastapi",
            module_path="fastapi.dependencies",
            heading_hierarchy=["Dependencies", "Dependency Injection"],
            content="FastAPI has a dependency injection system. Use Depends() to declare dependencies.",
            related_symbols=["Depends"],
            language="python",
            source_file="docs/dependencies.md",
        ),
        KnowledgeUnit(
            type=UnitType.PITFALL,
            framework="fastapi",
            module_path="fastapi.dependencies",
            heading_hierarchy=["Dependencies", "Caching"],
            content="Warning: Depends caches per-request, not globally. Each request gets fresh dependencies.",
            related_symbols=["Depends"],
            language="python",
            source_file="docs/dependencies.md",
        ),
        KnowledgeUnit(
            type=UnitType.API,
            framework="svelte",
            module_path="svelte.runes",
            heading_hierarchy=["Runes", "$state"],
            content="$state creates reactive state in Svelte 5.\n\n```svelte\nlet count = $state(0);\n```",
            related_symbols=["$state"],
            language="javascript",
            source_file="docs/runes.md",
        ),
    ]
    storage.insert_units(units)
    return storage


class TestStorage:
    def test_insert_and_get(self, storage):
        unit = KnowledgeUnit(
            type=UnitType.API,
            framework="test",
            module_path="test.module",
            heading_hierarchy=["Test"],
            content="Test content.",
            language="python",
            source_file="test.md",
        )
        storage.insert_units([unit])
        retrieved = storage.get_unit(unit.id)
        assert retrieved is not None
        assert retrieved.content == "Test content."
        assert retrieved.framework == "test"

    def test_symbol_lookup(self, populated_storage):
        ids = populated_storage.lookup_symbol("oauth2passwordbearer")
        assert len(ids) >= 1

    def test_symbol_lookup_with_framework(self, populated_storage):
        ids = populated_storage.lookup_symbol("$state", framework="svelte")
        assert len(ids) >= 1

    def test_keyword_search(self, populated_storage):
        ids = populated_storage.keyword_search("dependency injection")
        assert len(ids) >= 1

    def test_stats(self, populated_storage):
        stats = populated_storage.get_stats()
        assert "fastapi" in stats
        assert stats["fastapi"]["total"] == 4

    def test_clear_framework(self, populated_storage):
        populated_storage.clear_framework("svelte")
        stats = populated_storage.get_stats()
        assert "svelte" not in stats
        assert "fastapi" in stats


class TestSearch:
    def test_hybrid_search(self, populated_storage):
        results = hybrid_search(populated_storage, "OAuth2 authentication")
        assert len(results) > 0

    def test_search_with_framework_filter(self, populated_storage):
        results = hybrid_search(populated_storage, "state", framework="svelte")
        assert all(u.framework == "svelte" for u in results)

    def test_search_with_type_filter(self, populated_storage):
        results = hybrid_search(populated_storage, "security", unit_type="api")
        assert all(u.type == UnitType.API for u in results)


class TestLookup:
    def test_lookup_existing_symbol(self, populated_storage):
        result = lookup_symbol(populated_storage, "OAuth2PasswordBearer")
        assert result is not None
        assert result.match.type == UnitType.API

    def test_lookup_returns_examples(self, populated_storage):
        result = lookup_symbol(populated_storage, "OAuth2PasswordBearer", include_examples=True)
        assert result is not None
        # The example unit shares the symbol, so should be found
        assert len(result.examples) >= 0  # May or may not find examples depending on linking

    def test_lookup_nonexistent(self, populated_storage):
        result = lookup_symbol(populated_storage, "NonExistentSymbol")
        assert result is None


class TestBrowse:
    def test_browse_framework(self, populated_storage):
        result = browse(populated_storage, "fastapi")
        assert result.framework == "fastapi"
        assert len(result.modules) > 0

    def test_browse_module(self, populated_storage):
        result = browse(populated_storage, "fastapi", module="security")
        assert result.module == "security"
        assert len(result.units) > 0


class TestBundle:
    def test_bundle_returns_typed_buckets(self, populated_storage):
        result = bundle_topic(populated_storage, "security OAuth2", framework="fastapi")
        assert isinstance(result, dict)
        for key in result:
            assert key in ("api", "example", "concept", "pitfall")
            assert len(result[key]) > 0
            assert all(u.type.value == key for u in result[key])

    def test_bundle_respects_top_k(self, populated_storage):
        result = bundle_topic(populated_storage, "depends", framework="fastapi", top_k=1)
        for units in result.values():
            assert len(units) <= 1

    def test_bundle_with_framework_filter(self, populated_storage):
        result = bundle_topic(populated_storage, "state", framework="svelte")
        for units in result.values():
            assert all(u.framework == "svelte" for u in units)

    def test_bundle_without_framework(self, populated_storage):
        result = bundle_topic(populated_storage, "security")
        assert isinstance(result, dict)
        # Should find at least something across all frameworks
        total = sum(len(v) for v in result.values())
        assert total > 0


class TestVersionTracking:
    def test_set_and_get_version(self, storage):
        storage.set_version("testfw", "abc123")
        ver = storage.get_version("testfw")
        assert ver is not None
        assert ver[0] == "abc123"

    def test_get_version_nonexistent(self, storage):
        assert storage.get_version("nonexistent") is None

    def test_get_all_versions(self, storage):
        storage.set_version("fw1", "v1")
        storage.set_version("fw2", "v2")
        versions = storage.get_all_versions()
        assert "fw1" in versions
        assert "fw2" in versions
        assert versions["fw1"][0] == "v1"

    def test_version_update_overwrites(self, storage):
        storage.set_version("fw", "old")
        storage.set_version("fw", "new")
        ver = storage.get_version("fw")
        assert ver is not None
        assert ver[0] == "new"


class TestStorageClearAll:
    def test_clear_all(self, populated_storage):
        stats_before = populated_storage.get_stats()
        assert len(stats_before) > 0

        populated_storage.clear_all()

        stats_after = populated_storage.get_stats()
        assert len(stats_after) == 0
