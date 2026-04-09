"""Tests for data models."""

from rtfm.models import KnowledgeUnit, UnitType, AppConfig, SourceConfig, default_home


class TestKnowledgeUnit:
    def test_to_dict_roundtrip(self):
        unit = KnowledgeUnit(
            type=UnitType.API,
            framework="fastapi",
            module_path="fastapi.security",
            heading_hierarchy=["Security", "OAuth2"],
            content="OAuth2 password bearer.",
            related_symbols=["OAuth2PasswordBearer"],
            language="python",
            source_file="docs/security.md",
        )
        d = unit.to_dict()
        restored = KnowledgeUnit.from_dict(d)
        assert restored.id == unit.id
        assert restored.type == unit.type
        assert restored.framework == unit.framework
        assert restored.heading_hierarchy == unit.heading_hierarchy
        assert restored.content == unit.content
        assert restored.related_symbols == unit.related_symbols

    def test_id_is_deterministic(self):
        a = KnowledgeUnit(
            type=UnitType.CONCEPT, framework="test",
            module_path="test", heading_hierarchy=["H"],
            content="same content", source_file="f.md",
        )
        b = KnowledgeUnit(
            type=UnitType.CONCEPT, framework="test",
            module_path="test", heading_hierarchy=["H"],
            content="same content", source_file="f.md",
        )
        assert a.id == b.id

    def test_different_content_different_id(self):
        a = KnowledgeUnit(
            type=UnitType.CONCEPT, framework="test",
            module_path="test", heading_hierarchy=["H"],
            content="content A", source_file="f.md",
        )
        b = KnowledgeUnit(
            type=UnitType.CONCEPT, framework="test",
            module_path="test", heading_hierarchy=["H"],
            content="content B", source_file="f.md",
        )
        assert a.id != b.id

    def test_to_dict_type_is_string(self):
        unit = KnowledgeUnit(
            type=UnitType.PITFALL, framework="test",
            module_path="test", heading_hierarchy=["H"],
            content="watch out", source_file="f.md",
        )
        d = unit.to_dict()
        assert d["type"] == "pitfall"
        assert isinstance(d["type"], str)

    def test_from_dict_with_json_strings(self):
        """from_dict should handle JSON-encoded lists (as stored in SQLite)."""
        d = {
            "id": "abc123",
            "type": "api",
            "framework": "test",
            "module_path": "test.mod",
            "heading_hierarchy": '["A", "B"]',
            "content": "content",
            "related_symbols": '["foo", "bar"]',
            "language": "python",
            "source_file": "test.md",
        }
        unit = KnowledgeUnit.from_dict(d)
        assert unit.heading_hierarchy == ["A", "B"]
        assert unit.related_symbols == ["foo", "bar"]


class TestSourceConfig:
    def test_from_dict_github(self):
        cfg = SourceConfig.from_dict("fastapi", {
            "type": "github",
            "repo": "fastapi/fastapi",
            "docs_path": "docs/en/docs",
            "glob": "**/*.md",
            "language": "python",
        })
        assert cfg.name == "fastapi"
        assert cfg.type == "github"
        assert cfg.repo == "fastapi/fastapi"
        assert cfg.glob == "**/*.md"

    def test_from_dict_defaults(self):
        cfg = SourceConfig.from_dict("minimal", {"type": "local"})
        assert cfg.language == ""
        assert cfg.glob == "**/*.md"
        assert cfg.path == ""


class TestAppConfig:
    def test_from_dict_minimal(self):
        cfg = AppConfig.from_dict({})
        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8787
        assert cfg.sources == {}

    def test_from_dict_with_sources(self):
        cfg = AppConfig.from_dict({
            "server": {"host": "0.0.0.0", "port": 9000},
            "sources": {
                "test": {"type": "llms_txt", "url": "http://example.com/llms.txt", "language": "python"},
            },
        })
        assert cfg.host == "0.0.0.0"
        assert cfg.port == 9000
        assert "test" in cfg.sources
        assert cfg.sources["test"].url == "http://example.com/llms.txt"

    def test_default_data_dir(self):
        cfg = AppConfig()
        assert cfg.data_dir.endswith("data")


class TestDefaultHome:
    def test_returns_rtfm_path(self):
        home = default_home()
        assert home.endswith(".rtfm")
