from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from ..parsers.base import ParseResult, Symbol

logger = logging.getLogger(__name__)

# Chunk size thresholds (in estimated tokens, ~4 chars per token)
MAX_CHUNK_TOKENS = 512      # ~2048 chars — upper bound per chunk
MIN_CHUNK_TOKENS = 64       # ~256 chars — below this, merge with neighbors
LARGE_FUNCTION_THRESHOLD = 1024  # tokens — above this, split at block boundaries


@dataclass(slots=True)
class CodeChunk:
    """A semantically meaningful piece of code for embedding."""
    text: str
    file_path: str
    language: str
    level: str               # file, class, function, block
    symbol_name: str | None
    symbol_type: str | None
    start_line: int
    end_line: int
    context_header: str      # breadcrumb: "file > class > method"

    @property
    def token_estimate(self) -> int:
        return len(self.text) // 4

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]


class SemanticChunker:
    """Multi-level code chunker based on AST structure.

    Never splits mid-symbol. Groups small symbols. Splits large ones.

    Levels:
        1. File summary (imports + top-level symbol list)
        2. Class/module summary (signature + field list)
        3. Function/method level (one chunk per function)
        4. Block level (for large functions, split at logical boundaries)
    """

    def __init__(
        self,
        max_chunk_tokens: int = MAX_CHUNK_TOKENS,
        min_chunk_tokens: int = MIN_CHUNK_TOKENS,
        large_fn_threshold: int = LARGE_FUNCTION_THRESHOLD,
    ) -> None:
        self.max_chunk_tokens = max_chunk_tokens
        self.min_chunk_tokens = min_chunk_tokens
        self.large_fn_threshold = large_fn_threshold

    def chunk(self, parse_result: ParseResult) -> list[CodeChunk]:
        """Generate semantic chunks from a parse result."""
        chunks: list[CodeChunk] = []

        # Level 1: File summary
        chunks.append(self._make_file_summary(parse_result))

        # Separate top-level symbols and nested symbols
        top_level = [s for s in parse_result.symbols if s.parent is None]
        nested = self._group_by_parent(parse_result.symbols)

        small_buffer: list[Symbol] = []

        for symbol in top_level:
            if symbol.kind in ("class", "struct", "interface"):
                # Level 2: Class summary
                chunks.append(self._make_class_summary(
                    symbol, nested.get(symbol.name, []), parse_result
                ))

                # Level 3: Each method as a chunk
                methods = nested.get(symbol.name, [])
                method_small_buffer: list[Symbol] = []

                for method in methods:
                    if method.token_estimate > self.large_fn_threshold:
                        # Level 4: Split large method
                        chunks.extend(self._split_large_symbol(method, parent=symbol.name))
                    elif method.token_estimate < self.min_chunk_tokens:
                        method_small_buffer.append(method)
                    else:
                        chunks.append(self._symbol_to_chunk(method, parent=symbol.name))

                # Merge small methods
                if method_small_buffer:
                    chunks.append(self._merge_small_symbols(
                        method_small_buffer,
                        parse_result.file_path,
                        parse_result.language,
                        parent=symbol.name,
                    ))

            elif symbol.kind in ("function", "method"):
                if symbol.token_estimate > self.large_fn_threshold:
                    chunks.extend(self._split_large_symbol(symbol))
                elif symbol.token_estimate < self.min_chunk_tokens:
                    small_buffer.append(symbol)
                else:
                    chunks.append(self._symbol_to_chunk(symbol))
            else:
                # Constants, variables, type aliases — collect as small
                if symbol.token_estimate < self.min_chunk_tokens:
                    small_buffer.append(symbol)
                else:
                    chunks.append(self._symbol_to_chunk(symbol))

        # Merge remaining small top-level symbols
        if small_buffer:
            chunks.append(self._merge_small_symbols(
                small_buffer,
                parse_result.file_path,
                parse_result.language,
            ))

        return chunks

    def _make_file_summary(self, pr: ParseResult) -> CodeChunk:
        """Create a file-level summary chunk with imports and symbol signatures."""
        lines = [f"# File: {pr.file_path}", f"# Language: {pr.language}", ""]

        # Imports
        if pr.imports:
            for imp in pr.imports:
                if imp.names:
                    lines.append(f"from {imp.module} import {', '.join(imp.names)}")
                else:
                    alias = f" as {imp.alias}" if imp.alias else ""
                    lines.append(f"import {imp.module}{alias}")
            lines.append("")

        # Symbol table of contents
        top_level = [s for s in pr.symbols if s.parent is None]
        for sym in top_level:
            lines.append(f"  {sym.kind}: {sym.signature}")

        return CodeChunk(
            text="\n".join(lines),
            file_path=pr.file_path,
            language=pr.language,
            level="file",
            symbol_name=None,
            symbol_type=None,
            start_line=1,
            end_line=max(s.end_line for s in pr.symbols) if pr.symbols else 1,
            context_header=pr.file_path,
        )

    def _make_class_summary(
        self, symbol: Symbol, methods: list[Symbol], pr: ParseResult
    ) -> CodeChunk:
        """Create a class-level summary with signature and method list."""
        lines = [f"# {symbol.signature}", ""]

        if symbol.docstring:
            lines.append(f'"""{symbol.docstring}"""')
            lines.append("")

        # List methods
        for m in methods:
            lines.append(f"  {m.kind}: {m.signature}")

        return CodeChunk(
            text="\n".join(lines),
            file_path=symbol.file_path,
            language=symbol.language,
            level="class",
            symbol_name=symbol.name,
            symbol_type=symbol.kind,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            context_header=f"{symbol.file_path} > {symbol.name}",
        )

    def _symbol_to_chunk(self, symbol: Symbol, parent: str | None = None) -> CodeChunk:
        """Convert a single symbol to a chunk."""
        header = symbol.file_path
        if parent:
            header += f" > {parent}"
        header += f" > {symbol.name}"

        # Prepend context header to the code for better embeddings
        text = f"# {header}\n{symbol.code}"

        return CodeChunk(
            text=text,
            file_path=symbol.file_path,
            language=symbol.language,
            level="function",
            symbol_name=symbol.name,
            symbol_type=symbol.kind,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            context_header=header,
        )

    def _split_large_symbol(
        self, symbol: Symbol, parent: str | None = None
    ) -> list[CodeChunk]:
        """Split a large function/method into multiple chunks at line boundaries.

        Strategy: split at roughly equal intervals, trying to land on blank lines.
        Each sub-chunk includes the function signature as context.
        """
        lines = symbol.code.split("\n")
        if len(lines) <= 1:
            return [self._symbol_to_chunk(symbol, parent)]

        # Calculate target chunk size
        target_lines = max(10, len(lines) // max(1, symbol.token_estimate // self.max_chunk_tokens))

        chunks: list[CodeChunk] = []
        header = symbol.file_path
        if parent:
            header += f" > {parent}"
        header += f" > {symbol.name}"

        # First line is signature — include in every sub-chunk as context
        signature_line = lines[0]
        current_start = 0
        chunk_idx = 0

        while current_start < len(lines):
            current_end = min(current_start + target_lines, len(lines))

            # Try to find a blank line near the target end for a cleaner split
            if current_end < len(lines):
                best_split = current_end
                for offset in range(min(5, target_lines // 3)):
                    check = current_end + offset
                    if check < len(lines) and lines[check].strip() == "":
                        best_split = check + 1
                        break
                    check = current_end - offset
                    if check > current_start and lines[check].strip() == "":
                        best_split = check + 1
                        break
                current_end = best_split

            chunk_lines = lines[current_start:current_end]
            # Add signature context to non-first chunks
            if chunk_idx > 0:
                chunk_text = f"# {header} (part {chunk_idx + 1})\n{signature_line}\n# ...\n" + "\n".join(chunk_lines)
            else:
                chunk_text = f"# {header}\n" + "\n".join(chunk_lines)

            chunks.append(CodeChunk(
                text=chunk_text,
                file_path=symbol.file_path,
                language=symbol.language,
                level="block",
                symbol_name=f"{symbol.name}[{chunk_idx}]",
                symbol_type=symbol.kind,
                start_line=symbol.start_line + current_start,
                end_line=symbol.start_line + current_end - 1,
                context_header=f"{header} (part {chunk_idx + 1})",
            ))

            current_start = current_end
            chunk_idx += 1

        return chunks

    def _merge_small_symbols(
        self,
        symbols: list[Symbol],
        file_path: str,
        language: str,
        parent: str | None = None,
    ) -> CodeChunk:
        """Merge multiple small symbols into a single chunk."""
        header = file_path
        if parent:
            header += f" > {parent}"
        header += " > (grouped small symbols)"

        parts = [f"# {header}"]
        for sym in symbols:
            parts.append(f"\n# {sym.kind}: {sym.name}")
            parts.append(sym.code)

        return CodeChunk(
            text="\n".join(parts),
            file_path=file_path,
            language=language,
            level="function",
            symbol_name=None,
            symbol_type="grouped",
            start_line=min(s.start_line for s in symbols),
            end_line=max(s.end_line for s in symbols),
            context_header=header,
        )

    @staticmethod
    def _group_by_parent(symbols: list[Symbol]) -> dict[str, list[Symbol]]:
        """Group symbols by their parent name."""
        groups: dict[str, list[Symbol]] = {}
        for s in symbols:
            if s.parent is not None:
                groups.setdefault(s.parent, []).append(s)
        return groups
