from __future__ import annotations

import sqlite3
import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StoredEdge:
    """A graph edge stored in the database."""
    source: str
    target: str
    kind: str  # calls, imports, inherits, implements, references
    source_file: str
    target_file: str | None
    line: int
    repo: str
    branch: str


class GraphStore(Protocol):
    """Protocol for graph storage backends."""

    def add_edges(self, edges: list[StoredEdge]) -> None: ...
    def remove_file(self, repo: str, branch: str, file_path: str) -> None: ...
    def rename_file(self, repo: str, branch: str, old_path: str, new_path: str) -> None: ...
    def get_callers(self, repo: str, branch: str, symbol: str) -> list[StoredEdge]: ...
    def get_callees(self, repo: str, branch: str, symbol: str) -> list[StoredEdge]: ...
    def get_dependents(self, repo: str, branch: str, file_path: str) -> list[str]: ...
    def get_dependencies(self, repo: str, branch: str, file_path: str) -> list[str]: ...


class SQLiteGraphStore:
    """SQLite-backed adjacency list graph store.

    Replaces the broken global NetworkX DiGraph pattern from the old codebase.
    Supports per-repo, per-branch isolation via WHERE clauses.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS graph_edges (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        kind TEXT NOT NULL,
        source_file TEXT NOT NULL,
        target_file TEXT,
        line INTEGER NOT NULL DEFAULT 0,
        repo TEXT NOT NULL,
        branch TEXT NOT NULL,
        metadata TEXT,
        UNIQUE(source, target, kind, repo, branch, source_file)
    );
    CREATE INDEX IF NOT EXISTS idx_edges_source ON graph_edges(repo, branch, source);
    CREATE INDEX IF NOT EXISTS idx_edges_target ON graph_edges(repo, branch, target);
    CREATE INDEX IF NOT EXISTS idx_edges_source_file ON graph_edges(repo, branch, source_file);
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()
        logger.info("GraphStore initialized at %s", db_path)

    def close(self) -> None:
        self._conn.close()

    def add_edges(self, edges: list[StoredEdge]) -> None:
        if not edges:
            return
        self._conn.executemany(
            """INSERT OR REPLACE INTO graph_edges
               (source, target, kind, source_file, target_file, line, repo, branch)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (e.source, e.target, e.kind, e.source_file, e.target_file,
                 e.line, e.repo, e.branch)
                for e in edges
            ],
        )
        self._conn.commit()

    def remove_file(self, repo: str, branch: str, file_path: str) -> None:
        self._conn.execute(
            "DELETE FROM graph_edges WHERE repo = ? AND branch = ? AND source_file = ?",
            (repo, branch, file_path),
        )
        self._conn.commit()

    def rename_file(self, repo: str, branch: str, old_path: str, new_path: str) -> None:
        self._conn.execute(
            """UPDATE graph_edges SET source_file = ?
               WHERE repo = ? AND branch = ? AND source_file = ?""",
            (new_path, repo, branch, old_path),
        )
        self._conn.execute(
            """UPDATE graph_edges SET target_file = ?
               WHERE repo = ? AND branch = ? AND target_file = ?""",
            (new_path, repo, branch, old_path),
        )
        self._conn.commit()

    def get_callers(self, repo: str, branch: str, symbol: str) -> list[StoredEdge]:
        rows = self._conn.execute(
            """SELECT source, target, kind, source_file, target_file, line, repo, branch
               FROM graph_edges
               WHERE repo = ? AND branch = ? AND target = ? AND kind = 'calls'""",
            (repo, branch, symbol),
        ).fetchall()
        return [StoredEdge(*r) for r in rows]

    def get_callees(self, repo: str, branch: str, symbol: str) -> list[StoredEdge]:
        rows = self._conn.execute(
            """SELECT source, target, kind, source_file, target_file, line, repo, branch
               FROM graph_edges
               WHERE repo = ? AND branch = ? AND source = ? AND kind = 'calls'""",
            (repo, branch, symbol),
        ).fetchall()
        return [StoredEdge(*r) for r in rows]

    def get_dependents(self, repo: str, branch: str, file_path: str) -> list[str]:
        """Get files that import/depend on the given file."""
        rows = self._conn.execute(
            """SELECT DISTINCT source_file FROM graph_edges
               WHERE repo = ? AND branch = ? AND target_file = ? AND kind = 'imports'""",
            (repo, branch, file_path),
        ).fetchall()
        return [r[0] for r in rows]

    def get_dependencies(self, repo: str, branch: str, file_path: str) -> list[str]:
        """Get files that the given file imports/depends on."""
        rows = self._conn.execute(
            """SELECT DISTINCT target_file FROM graph_edges
               WHERE repo = ? AND branch = ? AND source_file = ? AND kind = 'imports'
               AND target_file IS NOT NULL""",
            (repo, branch, file_path),
        ).fetchall()
        return [r[0] for r in rows]

    def get_subgraph(self, repo: str, branch: str, symbol: str, depth: int = 2) -> list[StoredEdge]:
        """BFS from symbol up to depth, returns all edges in the subgraph."""
        visited: set[str] = set()
        frontier = {symbol}
        all_edges: list[StoredEdge] = []

        for _ in range(depth):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            rows = self._conn.execute(
                f"""SELECT source, target, kind, source_file, target_file, line, repo, branch
                    FROM graph_edges
                    WHERE repo = ? AND branch = ?
                    AND (source IN ({placeholders}) OR target IN ({placeholders}))""",
                (repo, branch, *frontier, *frontier),
            ).fetchall()

            edges = [StoredEdge(*r) for r in rows]
            all_edges.extend(edges)
            visited.update(frontier)
            frontier = set()
            for e in edges:
                if e.source not in visited:
                    frontier.add(e.source)
                if e.target not in visited:
                    frontier.add(e.target)

        return all_edges
