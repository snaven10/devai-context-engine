from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Symbol:
    """A code symbol extracted from a parsed file."""
    name: str
    kind: str  # function, method, class, struct, interface, enum, constant, variable, type_alias
    language: str
    file_path: str
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    code: str
    signature: str
    docstring: str | None = None
    parent: str | None = None
    params: tuple[str, ...] = ()
    return_type: str | None = None
    decorators: tuple[str, ...] = ()
    visibility: str = "public"  # public, private, protected

    @property
    def fully_qualified_name(self) -> str:
        if self.parent:
            return f"{self.file_path}::{self.parent}.{self.name}"
        return f"{self.file_path}::{self.name}"

    @property
    def token_estimate(self) -> int:
        """Rough token count (~4 chars per token)."""
        return len(self.code) // 4


@dataclass(frozen=True, slots=True)
class Import:
    """An import statement."""
    module: str
    alias: str | None
    names: tuple[str, ...] = ()
    file_path: str = ""
    line: int = 0


@dataclass(frozen=True, slots=True)
class Export:
    """An exported symbol."""
    name: str
    kind: str
    file_path: str = ""
    line: int = 0


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """A relationship between symbols."""
    source: str  # fully qualified symbol
    target: str
    kind: str  # calls, imports, inherits, implements, references
    file_path: str
    line: int


@dataclass(slots=True)
class ParseResult:
    """Complete result of parsing a file."""
    file_path: str
    language: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    exports: list[Export] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    raw_tree: object | None = None


class LanguageParser(Protocol):
    """Protocol for language-specific parsers."""

    @property
    def language(self) -> str: ...

    @property
    def extensions(self) -> list[str]: ...

    def parse_file(self, file_path: str) -> ParseResult: ...

    def parse_source(self, source: str, file_path: str = "<string>") -> ParseResult: ...
