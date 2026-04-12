from __future__ import annotations

from devai_ml.parsers.base import Symbol, Import, ParseResult
from devai_ml.chunking.semantic_chunker import SemanticChunker, CodeChunk


def make_symbol(name: str, kind: str, code: str, start: int = 1, parent: str | None = None) -> Symbol:
    lines = code.count("\n") + 1
    return Symbol(
        name=name,
        kind=kind,
        language="python",
        file_path="test.py",
        start_line=start,
        end_line=start + lines - 1,
        start_byte=0,
        end_byte=len(code),
        code=code,
        signature=f"def {name}()" if kind == "function" else f"class {name}",
        parent=parent,
    )


class TestSemanticChunker:
    def setup_method(self):
        self.chunker = SemanticChunker(
            max_chunk_tokens=128,
            min_chunk_tokens=16,
            large_fn_threshold=256,
        )

    def test_file_summary_always_created(self):
        result = ParseResult(
            file_path="test.py",
            language="python",
            symbols=[make_symbol("main", "function", "def main():\n    pass")],
        )
        chunks = self.chunker.chunk(result)
        file_chunks = [c for c in chunks if c.level == "file"]
        assert len(file_chunks) == 1
        assert "test.py" in file_chunks[0].text

    def test_function_creates_chunk(self):
        code = "def process(data):\n" + "    result = transform(data)\n" * 10 + "    return result"
        result = ParseResult(
            file_path="test.py",
            language="python",
            symbols=[make_symbol("process", "function", code)],
        )
        chunks = self.chunker.chunk(result)
        fn_chunks = [c for c in chunks if c.level == "function" and c.symbol_name == "process"]
        assert len(fn_chunks) == 1

    def test_class_creates_summary(self):
        class_sym = make_symbol("Handler", "class", "class Handler:\n    pass")
        method_sym = make_symbol(
            "handle", "method",
            "def handle(self, req):\n" + "    x = 1\n" * 10 + "    return x",
            start=2, parent="Handler",
        )
        result = ParseResult(
            file_path="test.py",
            language="python",
            symbols=[class_sym, method_sym],
        )
        chunks = self.chunker.chunk(result)
        class_chunks = [c for c in chunks if c.level == "class"]
        assert len(class_chunks) == 1
        assert "Handler" in class_chunks[0].text

    def test_small_symbols_merged(self):
        symbols = [
            make_symbol(f"const_{i}", "constant", f"CONST_{i} = {i}", start=i)
            for i in range(5)
        ]
        result = ParseResult(file_path="test.py", language="python", symbols=symbols)
        chunks = self.chunker.chunk(result)
        # Should have file summary + one merged chunk
        non_file = [c for c in chunks if c.level != "file"]
        assert len(non_file) == 1
        assert non_file[0].symbol_type == "grouped"

    def test_large_function_split(self):
        # Create a function larger than large_fn_threshold (256 tokens = ~1024 chars)
        code = "def big_function():\n" + "    line = 'x' * 50\n" * 100
        result = ParseResult(
            file_path="test.py",
            language="python",
            symbols=[make_symbol("big_function", "function", code)],
        )
        chunks = self.chunker.chunk(result)
        block_chunks = [c for c in chunks if c.level == "block"]
        assert len(block_chunks) >= 2  # Should be split into multiple blocks

    def test_content_hash(self):
        chunk = CodeChunk(
            text="hello world",
            file_path="a.py",
            language="python",
            level="function",
            symbol_name="test",
            symbol_type="function",
            start_line=1,
            end_line=1,
            context_header="a.py > test",
        )
        assert len(chunk.content_hash) == 16
        assert chunk.content_hash == chunk.content_hash  # deterministic

    def test_imports_in_file_summary(self):
        result = ParseResult(
            file_path="test.py",
            language="python",
            symbols=[make_symbol("main", "function", "def main():\n    pass")],
            imports=[Import(module="os", alias=None), Import(module="sys", alias=None)],
        )
        chunks = self.chunker.chunk(result)
        file_chunk = [c for c in chunks if c.level == "file"][0]
        assert "import os" in file_chunk.text
        assert "import sys" in file_chunk.text
