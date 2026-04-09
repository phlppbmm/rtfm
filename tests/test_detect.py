"""Tests for doc system auto-detection."""

from rtfm.ingest.detect import detect_doc_system


class TestDetect:
    def test_llms_txt_source_type(self):
        files = [("llms-full.txt", "# Some heading\nContent", "markdown")]
        assert detect_doc_system(files, "llms_txt") == "llms_txt"

    def test_sphinx_rst_autodoc(self):
        files = [("api.rst", ".. autoclass:: Session\n   :members:\n", "rst")]
        assert detect_doc_system(files, "github") == "sphinx"

    def test_sphinx_rst_autofunction(self):
        files = [("ref.md", ".. autofunction:: create_engine\n", "markdown")]
        assert detect_doc_system(files, "github") == "sphinx"

    def test_mkdocs_admonition(self):
        files = [("docs.md", "!!! warning\n    Be careful\n", "markdown")]
        assert detect_doc_system(files, "github") == "mkdocs"

    def test_mkdocs_blocks(self):
        files = [("docs.md", "/// warning\nSome text\n///\n", "markdown")]
        assert detect_doc_system(files, "github") == "mkdocs"

    def test_mkdocs_mkdocstrings(self):
        files = [("api.md", "::: pydantic.BaseModel\n", "markdown")]
        assert detect_doc_system(files, "website") == "mkdocs"

    def test_rustdoc_html(self):
        files = [("struct.Client.html", '<html class="rustdoc"><body>content</body></html>', "html")]
        assert detect_doc_system(files, "website") == "rustdoc"

    def test_rustdoc_copy_item_path(self):
        files = [("struct.Client.html", "<html><body>Copy item path</body></html>", "html")]
        assert detect_doc_system(files, "website") == "rustdoc"

    def test_typedoc_html(self):
        files = [("index.html", '<html><body><section class="tsd-panel">content</section></body></html>', "html")]
        assert detect_doc_system(files, "website") == "typedoc"

    def test_generic_fallback(self):
        files = [("readme.md", "# Hello\nPlain text\n", "markdown")]
        assert detect_doc_system(files, "github") == "generic_md"

    def test_empty_files(self):
        assert detect_doc_system([], "github") == "generic_md"
