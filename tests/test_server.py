"""Tests for the FastAPI server endpoints."""

import gc
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient

from rtfm.models import KnowledgeUnit, UnitType
from rtfm.server import app, get_storage
from rtfm.storage import Storage


@pytest.fixture
def storage():
    tmpdir = tempfile.mkdtemp()
    s = Storage(data_dir=tmpdir)
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
    ]
    s.insert_units(units)
    yield s
    s.close()
    gc.collect()
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def client(storage):
    app.dependency_overrides[get_storage] = lambda: storage
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestSearchEndpoint:
    def test_search_returns_list(self, client):
        resp = client.get("/search", params={"q": "OAuth2"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_search_unit_shape(self, client):
        resp = client.get("/search", params={"q": "security"})
        data = resp.json()
        unit = data[0]
        assert "id" in unit
        assert "type" in unit
        assert "framework" in unit
        assert "content" in unit
        assert "heading_hierarchy" in unit

    def test_search_framework_filter(self, client):
        resp = client.get("/search", params={"q": "state", "framework": "fastapi"})
        assert resp.status_code == 200
        for unit in resp.json():
            assert unit["framework"] == "fastapi"

    def test_search_type_filter(self, client):
        resp = client.get("/search", params={"q": "security", "type": "api"})
        assert resp.status_code == 200
        for unit in resp.json():
            assert unit["type"] == "api"

    def test_search_top_k(self, client):
        resp = client.get("/search", params={"q": "depends", "top_k": 2})
        assert resp.status_code == 200
        assert len(resp.json()) <= 2

    def test_search_top_k_validation(self, client):
        resp = client.get("/search", params={"q": "test", "top_k": 0})
        assert resp.status_code == 422


class TestLookupEndpoint:
    def test_lookup_found(self, client):
        resp = client.get("/lookup/OAuth2PasswordBearer")
        assert resp.status_code == 200
        data = resp.json()
        assert "match" in data
        assert "related" in data
        assert "examples" in data
        assert data["match"]["type"] == "api"

    def test_lookup_not_found(self, client):
        resp = client.get("/lookup/NonExistentSymbol")
        assert resp.status_code == 404

    def test_lookup_with_framework(self, client):
        resp = client.get("/lookup/Depends", params={"framework": "fastapi"})
        assert resp.status_code == 200


class TestBrowseEndpoint:
    def test_browse_framework_lists_modules(self, client):
        resp = client.get("/browse", params={"framework": "fastapi"})
        assert resp.status_code == 200
        data = resp.json()
        assert "framework" in data
        assert "modules" in data
        assert len(data["modules"]) > 0
        module = data["modules"][0]
        assert "name" in module
        assert "symbols" in module

    def test_browse_module_lists_units(self, client):
        resp = client.get("/browse", params={"framework": "fastapi", "module": "security"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["module"] == "security"
        assert "units" in data
        assert len(data["units"]) > 0


class TestBundleEndpoint:
    def test_bundle_returns_grouped(self, client):
        resp = client.get("/bundle", params={"q": "security OAuth2"})
        assert resp.status_code == 200
        data = resp.json()
        for key in data:
            assert key in ("api", "example", "concept", "pitfall")

    def test_bundle_with_framework(self, client):
        resp = client.get("/bundle", params={"q": "depends", "framework": "fastapi"})
        assert resp.status_code == 200

    def test_bundle_top_k(self, client):
        resp = client.get("/bundle", params={"q": "security", "top_k": 1})
        assert resp.status_code == 200
        for units in resp.json().values():
            assert len(units) <= 1
