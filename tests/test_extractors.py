"""Tests for doc-system-specific extractors."""

from rtfm.ingest.parsers.base import Section
from rtfm.ingest.extractors.sphinx import SphinxExtractor, _extract_sphinx_definition_symbols
from rtfm.ingest.extractors.mkdocs import MkDocsExtractor, _extract_mkdocs_definition_symbols
from rtfm.ingest.extractors.generic import _clean_content, _compute_relevance_decay
from rtfm.models import UnitType


def _make_section(content: str, heading: str = "Test", **kwargs) -> Section:
    return Section(
        heading=heading,
        level=2,
        heading_hierarchy=kwargs.pop("heading_hierarchy", [heading]),
        content=content,
        source_file=kwargs.pop("source_file", "test.md"),
    )


class TestSphinxDefinitionSymbols:
    def test_autoclass(self):
        section = _make_section(".. autoclass:: Session\n   :members:\n")
        defs = _extract_sphinx_definition_symbols(section)
        assert "session" in defs

    def test_autofunction(self):
        section = _make_section(".. autofunction:: create_engine\n")
        defs = _extract_sphinx_definition_symbols(section)
        assert "create_engine" in defs

    def test_qualified_autoclass(self):
        section = _make_section(".. autoclass:: ~sqlalchemy.orm.Session\n   :members:\n")
        defs = _extract_sphinx_definition_symbols(section)
        assert "session" in defs

    def test_no_directives(self):
        section = _make_section("Just some text about sessions.")
        defs = _extract_sphinx_definition_symbols(section)
        assert defs == []


class TestMkDocsDefinitionSymbols:
    def test_mkdocstrings_insert(self):
        section = _make_section("::: pydantic.BaseModel\n")
        defs = _extract_mkdocs_definition_symbols(section)
        assert "basemodel" in defs

    def test_mkdocstrings_nested(self):
        section = _make_section("::: pydantic.fields.Field\n")
        defs = _extract_mkdocs_definition_symbols(section)
        assert "field" in defs

    def test_heading_with_code_definition(self):
        section = _make_section(
            '```python\ndef Depends(dependency, *, use_cache=True):\n    pass\n```\nSome docs.',
            heading="`Depends`",
            heading_hierarchy=["`Depends`"],
        )
        defs = _extract_mkdocs_definition_symbols(section)
        assert "depends" in defs

    def test_heading_without_code_definition(self):
        section = _make_section(
            "Just mentions Depends in text.",
            heading="`Depends`",
            heading_hierarchy=["`Depends`"],
        )
        defs = _extract_mkdocs_definition_symbols(section)
        assert defs == []

    def test_no_signals(self):
        section = _make_section("Regular text about stuff.")
        defs = _extract_mkdocs_definition_symbols(section)
        assert defs == []


class TestSphinxExtractor:
    def test_extract_with_autodoc(self):
        section = _make_section(
            ".. autoclass:: Session\n   :members:\n\nThe Session object.",
            heading="Session API",
            heading_hierarchy=["ORM", "Session API"],
            source_file="docs/orm/session_api.rst",
        )
        extractor = SphinxExtractor()
        units = extractor.extract(
            [section], framework="sqlalchemy", language="python", source_file="docs/orm/session_api.rst"
        )
        assert len(units) >= 1
        assert any(u.definition_symbols for u in units)

    def test_changelog_decay(self):
        section = _make_section(
            "Some release note content.",
            heading="v1.0",
            source_file="docs/changelog/1.0.md",
        )
        extractor = SphinxExtractor()
        units = extractor.extract(
            [section], framework="sqlalchemy", language="python", source_file="docs/changelog/1.0.md"
        )
        assert all(u.relevance_decay < 1.0 for u in units)


class TestMkDocsExtractor:
    def test_extract_with_mkdocstrings(self):
        section = _make_section(
            "::: pydantic.BaseModel\n\nA base class for creating Pydantic models.",
            heading="BaseModel",
            heading_hierarchy=["API", "BaseModel"],
            source_file="docs/api/main.md",
        )
        extractor = MkDocsExtractor()
        units = extractor.extract(
            [section], framework="pydantic", language="python", source_file="docs/api/main.md"
        )
        assert len(units) >= 1
        assert any("basemodel" in u.definition_symbols for u in units)


class TestCleanContent:
    def test_strips_rst_autoclass(self):
        text = ".. autoclass:: Session\n   :members:\n\nThe Session class."
        cleaned = _clean_content(text)
        assert ".. autoclass::" not in cleaned
        assert "The Session class" in cleaned

    def test_strips_rst_autofunction(self):
        text = ".. autofunction:: create_engine\n\nCreates an engine."
        cleaned = _clean_content(text)
        assert ".. autofunction::" not in cleaned
        assert "Creates an engine" in cleaned

    def test_strips_rst_field_options(self):
        text = ".. autoclass:: Session\n   :members:\n   :inherited-members:\n\nDocs."
        cleaned = _clean_content(text)
        assert ":members:" not in cleaned
        assert "Docs" in cleaned


class TestRelevanceDecay:
    def test_release_notes_decay(self):
        assert _compute_relevance_decay("docs/release-notes.md") == 0.3

    def test_changelog_decay(self):
        assert _compute_relevance_decay("docs/changelog/1.0.rst") == 0.3

    def test_migration_decay(self):
        assert _compute_relevance_decay("docs/migration_11.rst") == 0.3

    def test_normal_docs_no_decay(self):
        assert _compute_relevance_decay("docs/tutorial/auth.md") == 1.0
