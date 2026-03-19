from __future__ import annotations

from pathlib import Path

from .base import Import, LanguageParser, ParseResult, Symbol


class RawTextParser:
    """Simple parser for non-AST file types (HTML, CSS, JSON, etc).

    Doesn't extract symbols or build graphs — just produces a ParseResult
    with the file content so the semantic chunker can embed it as raw text.
    For HTML templates, this enables semantic search over Angular/React templates.
    """

    def __init__(self, language: str) -> None:
        self._language = language

    @property
    def language(self) -> str:
        return self._language

    @property
    def extensions(self) -> list[str]:
        from .registry import EXTENSION_MAP
        return [ext for ext, lang in EXTENSION_MAP.items() if lang == self._language]

    def parse_file(self, file_path: str) -> ParseResult:
        source = Path(file_path).read_text(errors="replace")
        return self._parse(source, file_path)

    def parse_source(self, source: str, file_path: str = "<string>") -> ParseResult:
        return self._parse(source, file_path)

    def _parse(self, source: str, file_path: str) -> ParseResult:
        """Create a minimal ParseResult — one symbol spanning the whole file.

        This lets the chunker treat it as a single file-level chunk,
        which gets embedded for semantic search.
        """
        lines = source.split("\n")
        line_count = len(lines)

        # Create a single file-level symbol so the chunker has something to work with
        symbols = []
        if source.strip():
            symbols.append(Symbol(
                name=Path(file_path).stem,
                kind="file",
                language=self._language,
                file_path=file_path,
                start_line=1,
                end_line=line_count,
                start_byte=0,
                end_byte=len(source.encode("utf-8")),
                code=source,
                signature=f"file {Path(file_path).name}",
            ))

        return ParseResult(
            file_path=file_path,
            language=self._language,
            symbols=symbols,
            imports=[],
            exports=[],
            edges=[],
        )
