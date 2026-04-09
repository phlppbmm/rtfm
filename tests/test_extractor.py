"""Tests for the Knowledge Unit extractor."""

from rtfm.ingest.parser import Section
from rtfm.ingest.extractor import (
    extract_units,
    _classify_section,
    _extract_symbols,
    _derive_module_path,
    _clean_content,
)
from rtfm.models import UnitType


def _make_section(content: str, heading: str = "Test", **kwargs) -> Section:
    return Section(
        heading=heading,
        level=2,
        heading_hierarchy=[heading],
        content=content,
        **kwargs,
    )


class TestClassification:
    def test_api_with_function_def(self):
        section = _make_section(
            "Some description.\n\n```python\ndef create_user(name: str, age: int) -> User:\n    pass\n```"
        )
        assert UnitType.API in _classify_section(section)

    def test_api_with_class_def(self):
        section = _make_section(
            "A class.\n\n```python\nclass WebSocket:\n    pass\n```"
        )
        assert UnitType.API in _classify_section(section)

    def test_example_with_long_code(self):
        code = "\n".join(f"    line {i}" for i in range(10))
        section = _make_section(f"Example usage:\n\n```python\n{code}\n```")
        assert UnitType.EXAMPLE in _classify_section(section)

    def test_pitfall_with_breaking_change(self):
        section = _make_section("Breaking change in v5: the API now requires auth.")
        assert UnitType.PITFALL in _classify_section(section)

    def test_pitfall_with_deprecated(self):
        section = _make_section("The `query()` method is deprecated. Use `select()` instead.")
        assert UnitType.PITFALL in _classify_section(section)

    def test_concept_plain_text(self):
        section = _make_section("Dependency injection is a pattern where...")
        assert _classify_section(section) == [UnitType.CONCEPT]

    def test_note_is_not_pitfall(self):
        section = _make_section("**Note**: This feature is available since v3.")
        assert UnitType.PITFALL not in _classify_section(section)

    def test_warning_word_alone_is_concept(self):
        section = _make_section("The warning message will be displayed in the console.")
        assert _classify_section(section) == [UnitType.CONCEPT]

    # --- RST directive tests ---

    def test_rst_autoclass_is_api(self):
        section = _make_section(".. autoclass:: Session\n   :members:")
        assert UnitType.API in _classify_section(section)

    def test_rst_autofunction_is_api(self):
        section = _make_section(".. autofunction:: select")
        assert UnitType.API in _classify_section(section)

    def test_rst_function_directive_is_api(self):
        section = _make_section(".. function:: nullsfirst(column)\n\n   Sort NULL values first.")
        assert UnitType.API in _classify_section(section)

    def test_rst_warning_is_pitfall(self):
        section = _make_section(".. warning::\n\n   This will delete all data.")
        assert UnitType.PITFALL in _classify_section(section)

    def test_rst_deprecated_is_pitfall(self):
        section = _make_section(".. deprecated:: 2.0\n\n   Use the new API instead.")
        assert UnitType.PITFALL in _classify_section(section)

    def test_rst_note_is_not_pitfall(self):
        section = _make_section(".. note::\n\n   This is just an informational note.")
        assert UnitType.PITFALL not in _classify_section(section)

    # --- MkDocs admonition tests ---

    def test_mkdocs_warning_is_pitfall(self):
        section = _make_section('!!! warning "Be careful"\n    This can break things.')
        assert UnitType.PITFALL in _classify_section(section)

    def test_mkdocs_danger_is_pitfall(self):
        section = _make_section("!!! danger\n    Data loss possible.")
        assert UnitType.PITFALL in _classify_section(section)

    def test_mkdocs_example_is_example(self):
        section = _make_section("!!! example\n    Here is how to do it.")
        assert UnitType.EXAMPLE in _classify_section(section)

    def test_mkdocs_tip_is_not_pitfall(self):
        section = _make_section("!!! tip\n    Use this shortcut for faster results.")
        assert UnitType.PITFALL not in _classify_section(section)

    def test_mkdocs_note_is_not_pitfall(self):
        section = _make_section("!!! note\n    Available since version 3.0.")
        assert UnitType.PITFALL not in _classify_section(section)

    # --- FastAPI Blocks syntax ---

    def test_blocks_warning_is_pitfall(self):
        section = _make_section("/// warning\n    This is experimental.\n///")
        assert UnitType.PITFALL in _classify_section(section)

    def test_blocks_tip_is_not_pitfall(self):
        section = _make_section("/// tip\n    A helpful hint.\n///")
        assert UnitType.PITFALL not in _classify_section(section)

    # --- Heading-based classification ---

    def test_api_reference_heading(self):
        section = Section(
            heading="API Reference",
            level=2,
            heading_hierarchy=["Module", "API Reference"],
            content="Some text.\n\n```python\nx = 1\n```",
        )
        assert UnitType.API in _classify_section(section)

    # --- Multi-label ---

    def test_api_with_deprecation_is_both(self):
        section = _make_section(
            ".. autoclass:: OldSession\n   :members:\n\n"
            ".. deprecated:: 2.0\n\n   Use NewSession instead."
        )
        types = _classify_section(section)
        assert UnitType.API in types
        assert UnitType.PITFALL in types

    def test_api_with_long_code_is_api_and_example(self):
        code = "\n".join(f"    line {i}" for i in range(10))
        section = _make_section(
            f"```python\nclass Foo:\n    pass\n```\n\nUsage:\n\n```python\n{code}\n```"
        )
        types = _classify_section(section)
        assert UnitType.API in types
        assert UnitType.EXAMPLE in types


class TestSymbolExtraction:
    def test_python_function(self):
        symbols = _extract_symbols("def create_user(name: str):\n    pass", "python")
        assert "create_user" in symbols

    def test_python_class(self):
        symbols = _extract_symbols("class WebSocket:\n    pass", "python")
        assert "WebSocket" in symbols

    def test_python_decorator(self):
        symbols = _extract_symbols("@app.get\ndef route():\n    pass", "python")
        assert "app" in symbols or "route" in symbols

    def test_rust_function(self):
        symbols = _extract_symbols("pub async fn handle_request(req: Request) -> Response {", "rust")
        assert "handle_request" in symbols

    def test_rust_struct(self):
        symbols = _extract_symbols("pub struct Config {\n    host: String,\n}", "rust")
        assert "Config" in symbols

    def test_rust_trait(self):
        symbols = _extract_symbols("pub trait Handler {\n    fn call(&self);\n}", "rust")
        assert "Handler" in symbols

    def test_rune_from_heading(self):
        """Framework-specific symbols like Svelte runes come from headings."""
        symbols = _extract_symbols("some content", "javascript", ["`$state`"])
        assert "$state" in symbols

    # --- JavaScript / TypeScript ---

    def test_js_function(self):
        symbols = _extract_symbols("function fetchData(url) {", "javascript")
        assert "fetchData" in symbols

    def test_js_export_function(self):
        symbols = _extract_symbols("export async function loadItems() {", "js")
        assert "loadItems" in symbols

    def test_js_class(self):
        symbols = _extract_symbols("export class Router {", "javascript")
        assert "Router" in symbols

    def test_ts_interface(self):
        symbols = _extract_symbols("export interface UserProps {\n  name: string;\n}", "typescript")
        assert "UserProps" in symbols

    def test_ts_type(self):
        symbols = _extract_symbols("export type Result<T> = Success<T> | Error;", "ts")
        assert "Result" in symbols

    def test_ts_enum(self):
        symbols = _extract_symbols("export enum Status {\n  Active,\n  Inactive,\n}", "typescript")
        assert "Status" in symbols

    def test_js_const(self):
        symbols = _extract_symbols("export const DEFAULT_TIMEOUT = 5000;", "javascript")
        assert "DEFAULT_TIMEOUT" in symbols

    # --- Go ---

    def test_go_function(self):
        symbols = _extract_symbols("func HandleRequest(w http.ResponseWriter, r *http.Request) {", "go")
        assert "HandleRequest" in symbols

    def test_go_method(self):
        symbols = _extract_symbols("func (s *Server) ListenAndServe() error {", "go")
        assert "ListenAndServe" in symbols

    def test_go_type_struct(self):
        symbols = _extract_symbols("type Config struct {\n\tHost string\n}", "go")
        assert "Config" in symbols

    def test_go_type_interface(self):
        symbols = _extract_symbols("type Handler interface {\n\tServeHTTP(ResponseWriter, *Request)\n}", "go")
        assert "Handler" in symbols

    # --- C / C++ ---

    def test_c_function(self):
        symbols = _extract_symbols("int main(int argc, char *argv[]) {", "c")
        assert "main" in symbols

    def test_c_struct(self):
        symbols = _extract_symbols("struct Point {\n    int x;\n    int y;\n};", "c")
        assert "Point" in symbols

    def test_cpp_class(self):
        symbols = _extract_symbols("class HttpServer {\npublic:\n    void start();\n};", "cpp")
        assert "HttpServer" in symbols

    def test_cpp_namespace(self):
        symbols = _extract_symbols("namespace net {", "c++")
        assert "net" in symbols

    def test_c_macro(self):
        symbols = _extract_symbols("#define MAX_BUFFER_SIZE 1024", "c")
        assert "MAX_BUFFER_SIZE" in symbols

    def test_cpp_template_class(self):
        symbols = _extract_symbols("template<typename T>\nclass Vector {", "cpp")
        assert "Vector" in symbols

    # --- Java ---

    def test_java_class(self):
        symbols = _extract_symbols("public class UserService {", "java")
        assert "UserService" in symbols

    def test_java_abstract_class(self):
        symbols = _extract_symbols("public abstract class BaseController {", "java")
        assert "BaseController" in symbols

    def test_java_interface(self):
        symbols = _extract_symbols("public interface Repository {", "java")
        assert "Repository" in symbols

    def test_java_enum(self):
        symbols = _extract_symbols("public enum Status {\n    ACTIVE,\n    INACTIVE\n}", "java")
        assert "Status" in symbols

    def test_java_method(self):
        symbols = _extract_symbols("    public List<User> findAll() {", "java")
        assert "findAll" in symbols

    def test_java_annotation(self):
        symbols = _extract_symbols("@RestController\npublic class ApiController {", "java")
        assert "RestController" in symbols

    # --- C# ---

    def test_csharp_class(self):
        symbols = _extract_symbols("public class GameManager {", "csharp")
        assert "GameManager" in symbols

    def test_csharp_interface(self):
        symbols = _extract_symbols("public interface IRepository {", "c#")
        assert "IRepository" in symbols

    def test_csharp_struct(self):
        symbols = _extract_symbols("public struct Vector3 {", "csharp")
        assert "Vector3" in symbols

    def test_csharp_enum(self):
        symbols = _extract_symbols("public enum Direction {\n    North,\n    South\n}", "csharp")
        assert "Direction" in symbols

    def test_csharp_method(self):
        symbols = _extract_symbols("    public async Task<IActionResult> GetUsers() {", "csharp")
        assert "GetUsers" in symbols

    def test_csharp_attribute(self):
        symbols = _extract_symbols("[HttpGet]\npublic IActionResult Index() {", "csharp")
        assert "HttpGet" in symbols

    # --- RST autodoc symbols ---

    def test_rst_autoclass_symbol(self):
        symbols = _extract_symbols(".. autoclass:: Session\n   :members:", "python")
        assert "Session" in symbols

    def test_rst_autofunction_symbol(self):
        symbols = _extract_symbols(".. autofunction:: select", "python")
        assert "select" in symbols

    def test_rst_qualified_autoclass(self):
        """.. autoclass:: ~sqlalchemy.orm.Session should extract 'Session'."""
        symbols = _extract_symbols(".. autoclass:: ~sqlalchemy.orm.Session", "python")
        assert "Session" in symbols

    def test_rst_py_function_symbol(self):
        symbols = _extract_symbols(".. py:function:: create_engine(url)", "python")
        assert "create_engine" in symbols

    # --- Heading-based symbols ---

    def test_heading_inline_code_symbol(self):
        symbols = _extract_symbols("Some content.", "python", ["`Depends`"])
        assert "Depends" in symbols

    def test_heading_inline_code_multiple(self):
        symbols = _extract_symbols("text", "python", ["Using `BaseModel` and `Field`"])
        assert "BaseModel" in symbols
        assert "Field" in symbols


    # --- Aliases ---

    def test_golang_alias(self):
        symbols = _extract_symbols("func New() *Client {", "golang")
        assert "New" in symbols

    def test_unknown_language(self):
        symbols = _extract_symbols("whatever code here", "unknown")
        assert symbols == []

    def test_deduplication(self):
        symbols = _extract_symbols("def foo():\n    pass\ndef foo():\n    pass", "python")
        assert symbols.count("foo") == 1


class TestModulePath:
    def test_fastapi_path(self):
        result = _derive_module_path("fastapi", "docs/en/docs/tutorial/security/oauth2.md")
        assert result == "fastapi.tutorial.security.oauth2"

    def test_svelte_with_numeric_prefix(self):
        result = _derive_module_path("svelte", "documentation/docs/02-runes/01-state.md")
        assert result == "svelte.runes.state"

    def test_index_file(self):
        result = _derive_module_path("fastapi", "docs/en/docs/index.md")
        assert result == "fastapi"

    def test_simple_path(self):
        result = _derive_module_path("rust", "guide/ownership.md")
        assert result == "rust.guide.ownership"


class TestExtractUnits:
    def test_basic_extraction(self):
        sections = [_make_section("Some concept text.", source_file="docs/test.md")]
        units = extract_units(sections, framework="test", language="python", source_file="docs/test.md")
        assert len(units) == 1
        assert units[0].framework == "test"
        assert units[0].language == "python"
        assert units[0].source_file == "docs/test.md"

    def test_empty_sections_skipped(self):
        sections = [_make_section("", source_file="docs/test.md")]
        units = extract_units(sections, framework="test", language="python")
        assert len(units) == 0

    def test_id_is_deterministic(self):
        sections = [_make_section("Content.", source_file="docs/test.md")]
        units1 = extract_units(sections, framework="test", language="python", source_file="docs/test.md")
        units2 = extract_units(sections, framework="test", language="python", source_file="docs/test.md")
        assert units1[0].id == units2[0].id


class TestCleanContent:
    """`_clean_content` strips ingest-time markup from rendered output."""

    def test_strips_blocks_opening_and_closing(self):
        result = _clean_content("/// info\n\nA bearer token.\n\n///")
        assert "///" not in result
        assert "A bearer token." in result

    def test_strips_blocks_opening_with_title(self):
        result = _clean_content('/// note "Important"\n\nbody\n\n///')
        assert "///" not in result
        assert "body" in result

    def test_strips_mkdocs_snippet_include(self):
        result = _clean_content("Code:\n\n{* ../../docs_src/security/tutorial.py hl[8] *}\n\nDone.")
        assert "{*" not in result
        assert "*}" not in result
        assert "Code:" in result
        assert "Done." in result

    def test_strips_mkdocs_bang_snippet(self):
        result = _clean_content("Code:\n\n{! examples/foo.py !}\n\nDone.")
        assert "{!" not in result
        assert "!}" not in result

    def test_strips_heading_anchor(self):
        result = _clean_content("### Try the WebSockets { #try-the-websockets-with-dependencies }")
        assert "{ #" not in result
        assert "Try the WebSockets" in result

    def test_strips_inline_html_keeps_text(self):
        result = _clean_content('**FastAPI** has a powerful **<dfn title="DI">DI</dfn>** system.')
        assert "<dfn" not in result
        assert "</dfn>" not in result
        assert "DI" in result

    def test_strips_void_html(self):
        result = _clean_content("Look:\n\n<img src='/img/foo.png'>\n\nNice.")
        assert "<img" not in result
        assert "Look:" in result
        assert "Nice." in result

    def test_strips_html_comments(self):
        result = _clean_content("Hello <!-- TODO: rewrite --> world.")
        assert "<!--" not in result
        assert "TODO" not in result
        assert "Hello" in result
        assert "world." in result

    def test_collapses_multiple_blank_lines(self):
        result = _clean_content("foo\n\n\n\nbar")
        assert "\n\n\n" not in result
        assert "foo" in result and "bar" in result

    def test_preserves_classification_signal(self):
        """Cleaning runs after classification — directive must still classify as pitfall."""
        from rtfm.ingest.extractor import _classify_section

        section = Section(
            heading="Warning",
            level=2,
            heading_hierarchy=["Warning"],
            content="/// warning\n\nDangerous.\n\n///",
            source_file="x.md",
        )
        # Classification still sees the original directive
        assert UnitType.PITFALL in _classify_section(section)
        # But the cleaned content has no markers
        cleaned = _clean_content(section.content)
        assert "///" not in cleaned
        assert "Dangerous" in cleaned

    def test_extract_units_writes_cleaned_content(self):
        sections = [_make_section(
            "/// info\n\nUse `Depends` here.\n\n///\n\n{* example.py *}",
            source_file="docs/x.md",
        )]
        units = extract_units(sections, framework="t", language="python", source_file="docs/x.md")
        assert len(units) == 1
        assert "///" not in units[0].content
        assert "{*" not in units[0].content
        assert "Depends" in units[0].content
