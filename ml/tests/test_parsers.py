from __future__ import annotations

import pytest
from devai_ml.parsers.base import Symbol, Import, GraphEdge, ParseResult


class TestSymbol:
    def test_fully_qualified_name_with_parent(self):
        sym = Symbol(
            name="process",
            kind="method",
            language="python",
            file_path="src/handler.py",
            start_line=10,
            end_line=20,
            start_byte=100,
            end_byte=300,
            code="def process(self, data): ...",
            signature="def process(self, data)",
            parent="Handler",
        )
        assert sym.fully_qualified_name == "src/handler.py::Handler.process"

    def test_fully_qualified_name_without_parent(self):
        sym = Symbol(
            name="main",
            kind="function",
            language="python",
            file_path="app.py",
            start_line=1,
            end_line=5,
            start_byte=0,
            end_byte=50,
            code="def main(): ...",
            signature="def main()",
        )
        assert sym.fully_qualified_name == "app.py::main"

    def test_token_estimate(self):
        sym = Symbol(
            name="x",
            kind="function",
            language="python",
            file_path="f.py",
            start_line=1,
            end_line=1,
            start_byte=0,
            end_byte=100,
            code="x" * 400,
            signature="def x()",
        )
        assert sym.token_estimate == 100  # 400 chars / 4

    def test_frozen(self):
        sym = Symbol(
            name="x",
            kind="function",
            language="python",
            file_path="f.py",
            start_line=1,
            end_line=1,
            start_byte=0,
            end_byte=10,
            code="def x(): pass",
            signature="def x()",
        )
        with pytest.raises(AttributeError):
            sym.name = "y"


class TestImport:
    def test_basic_import(self):
        imp = Import(module="os.path", alias=None, names=("join", "exists"), file_path="a.py", line=1)
        assert imp.module == "os.path"
        assert imp.names == ("join", "exists")

    def test_aliased_import(self):
        imp = Import(module="numpy", alias="np")
        assert imp.alias == "np"


class TestGraphEdge:
    def test_edge(self):
        edge = GraphEdge(
            source="a.py::main",
            target="b.py::helper",
            kind="calls",
            file_path="a.py",
            line=15,
        )
        assert edge.kind == "calls"
        assert edge.source == "a.py::main"


class TestParseResult:
    def test_empty_result(self):
        result = ParseResult(file_path="empty.py", language="python")
        assert result.symbols == []
        assert result.imports == []
        assert result.exports == []
        assert result.edges == []
