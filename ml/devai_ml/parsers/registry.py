from __future__ import annotations

import logging
import os
from pathlib import Path

from .base import LanguageParser, ParseResult

logger = logging.getLogger(__name__)

EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
    ".sc": "scala",
    ".dart": "dart",
    ".cs": "c_sharp",
    ".lua": "lua",
    ".zig": "zig",
    ".ex": "elixir",
    ".exs": "elixir",
    # Template/style files — indexed as raw text (no AST)
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".svg": "xml",
    ".md": "markdown",
    ".sql": "sql",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".proto": "protobuf",
}

# Languages with tree-sitter grammars (AST-based parsing)
TREE_SITTER_LANGUAGES: set[str] = {
    "python", "javascript", "typescript", "go", "java", "rust",
    "c", "cpp", "ruby", "php", "kotlin", "swift", "scala", "dart",
    "c_sharp", "lua", "zig", "elixir",
}

# Languages indexed as raw text (no AST, but still chunked + embedded)
RAW_TEXT_LANGUAGES: set[str] = {
    "html", "css", "scss", "sass", "less",
    "json", "yaml", "xml", "markdown",
    "sql", "graphql", "protobuf",
}

SUPPORTED_LANGUAGES: set[str] = TREE_SITTER_LANGUAGES | RAW_TEXT_LANGUAGES


class ParserRegistry:
    """Registry that maps file extensions to tree-sitter parsers.

    Lazily loads parsers and caches them. Uses tree-sitter-languages
    which bundles pre-compiled grammars.
    """

    def __init__(self) -> None:
        self._parsers: dict[str, LanguageParser] = {}

    def get_parser(self, file_path: str) -> LanguageParser | None:
        """Get the appropriate parser for a file based on its extension."""
        ext = os.path.splitext(file_path)[1].lower()
        lang = EXTENSION_MAP.get(ext)
        if lang is None:
            return None
        if lang not in SUPPORTED_LANGUAGES:
            return None
        if lang not in self._parsers:
            try:
                if lang in RAW_TEXT_LANGUAGES:
                    from .raw_parser import RawTextParser
                    self._parsers[lang] = RawTextParser(lang)
                else:
                    from .treesitter_parser import TreeSitterLanguageParser
                    self._parsers[lang] = TreeSitterLanguageParser(lang)
                logger.info("Loaded parser for %s", lang)
            except Exception as e:
                logger.warning("Failed to load parser for %s: %s", lang, e)
                return None
        return self._parsers[lang]

    def detect_language(self, file_path: str) -> str | None:
        """Detect language from file extension."""
        ext = os.path.splitext(file_path)[1].lower()
        return EXTENSION_MAP.get(ext)

    def supported_extensions(self) -> list[str]:
        """Return all supported file extensions."""
        return list(EXTENSION_MAP.keys())

    def supported_languages(self) -> list[str]:
        """Return all supported language names."""
        return sorted(SUPPORTED_LANGUAGES)
