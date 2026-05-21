from __future__ import annotations

import re
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

    -- Framework-aware routes table. Populated by route extractors per
    -- framework (today: Quarkus/JAX-RS; future: Spring, Angular, Express).
    -- Lets the UI / agent answer "what handler serves POST /solicitudes?"
    -- and "given this Java method, what URL does it expose?".
    CREATE TABLE IF NOT EXISTS routes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        framework      TEXT NOT NULL,                -- 'quarkus' | 'spring' | 'angular' | ...
        http_method    TEXT NOT NULL DEFAULT '',     -- 'GET' | 'POST' | ... or '' for non-HTTP
        path           TEXT NOT NULL,                -- full URL path e.g. '/solicitudes/{id}'
        handler_class  TEXT NOT NULL DEFAULT '',     -- e.g. 'SolicitudesResource'
        handler_method TEXT NOT NULL DEFAULT '',     -- e.g. 'getById'
        handler_symbol TEXT NOT NULL DEFAULT '',     -- canonical id matching graph_edges
        file           TEXT NOT NULL,
        line           INTEGER NOT NULL DEFAULT 0,
        repo           TEXT NOT NULL DEFAULT '',
        branch         TEXT NOT NULL DEFAULT '',
        indexed_at     TEXT NOT NULL,
        UNIQUE(framework, http_method, path, repo, branch)
    );
    CREATE INDEX IF NOT EXISTS idx_routes_path    ON routes(path);
    CREATE INDEX IF NOT EXISTS idx_routes_handler ON routes(handler_symbol);
    CREATE INDEX IF NOT EXISTS idx_routes_repo    ON routes(repo, branch);

    -- FTS5 virtual table for fast symbol/file search.
    -- canonical / file / repo / branch / line are UNINDEXED metadata (no FTS cost).
    -- `label` is the searchable column: the tokenizer breaks symbol ids on
    -- non-alphanumerics so 'path/file.ts::Class.method' tokenizes to
    -- {path, file, ts, Class, method} — good for prefix matching.
    CREATE VIRTUAL TABLE IF NOT EXISTS graph_symbols_fts USING fts5(
        label,
        canonical UNINDEXED,
        repo UNINDEXED,
        branch UNINDEXED,
        source_file UNINDEXED,
        line UNINDEXED,
        tokenize="unicode61 remove_diacritics 2"
    );
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()
        self._normalize_existing_data()
        logger.debug("GraphStore initialized at %s", db_path)

    def _normalize_existing_data(self) -> None:
        """Clean up existing data: normalize repo paths and remove duplicates."""
        try:
            # Always strip trailing slashes from repo paths
            self._conn.execute(
                "UPDATE graph_edges SET repo = RTRIM(repo, '/') WHERE repo LIKE '%/'"
            )
            # Remove exact duplicates (same source, target, kind, file, line)
            self._conn.execute("""
                DELETE FROM graph_edges WHERE rowid NOT IN (
                    SELECT MIN(rowid) FROM graph_edges
                    GROUP BY source, target, kind, source_file, line, repo, branch
                )
            """)
            self._conn.commit()
        except Exception as e:
            logger.warning("Data normalization failed: %s", e)

    def close(self) -> None:
        self._conn.close()

    def add_edges(self, edges: list[StoredEdge]) -> None:
        if not edges:
            return
        normalized = [
            (e.source, e.target, e.kind, e.source_file, e.target_file,
             e.line, self.normalize_repo_path(e.repo), e.branch)
            for e in edges
        ]
        self._conn.executemany(
            """INSERT OR REPLACE INTO graph_edges
               (source, target, kind, source_file, target_file, line, repo, branch)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            normalized,
        )
        # Keep the FTS5 index in sync incrementally. We insert one row per
        # (symbol, file, line) — duplicates are filtered at populate time
        # but here we just append; rebuild on demand handles cleanup.
        fts_rows = []
        for (src, tgt, kind, src_file, tgt_file, line, repo, branch) in normalized:
            fts_rows.append((self._searchable_label(src), src, repo, branch, src_file, line))
            fts_rows.append((self._searchable_label(tgt), tgt, repo, branch, src_file, line))
        self._conn.executemany(
            """INSERT INTO graph_symbols_fts (label, canonical, repo, branch, source_file, line)
               VALUES (?, ?, ?, ?, ?, ?)""",
            fts_rows,
        )
        self._conn.commit()

    _CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

    @classmethod
    def _searchable_label(cls, symbol: str) -> str:
        """Expand a symbol into a tokenizer-friendly label.

        Adds spaces at camelCase boundaries so 'mapDtoToBirthRecord'
        becomes 'mapDtoToBirthRecord map Dto To Birth Record' and FTS5 will
        index every part as a queryable token.
        """
        if not symbol:
            return symbol
        expanded = cls._CAMEL_BOUNDARY_RE.sub(" ", symbol)
        if expanded == symbol:
            return symbol
        return f"{symbol} {expanded}"

    def add_routes(self, routes: list[dict]) -> int:
        """Upsert a batch of Route records. Idempotent via UNIQUE constraint."""
        if not routes:
            return 0
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (r["framework"], r["http_method"], r["path"],
             r["handler_class"], r["handler_method"], r["handler_symbol"],
             r["file"], r["line"], r["repo"], r["branch"], now)
            for r in routes
        ]
        self._conn.executemany(
            """INSERT OR REPLACE INTO routes
               (framework, http_method, path, handler_class, handler_method,
                handler_symbol, file, line, repo, branch, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        self._conn.commit()
        return len(rows)

    def search_routes(self, q: str = "", framework: str = "", http_method: str = "",
                      repo: str = "", branch: str = "", limit: int = 50) -> list[dict]:
        """Find routes by path substring + optional filters."""
        conds = []
        params: list[Any] = []
        if q:
            conds.append("path LIKE ?")
            params.append(f"%{q}%")
        if framework:
            conds.append("framework = ?")
            params.append(framework)
        if http_method:
            conds.append("http_method = ?")
            params.append(http_method)
        if repo:
            conds.append("repo = ?")
            params.append(repo)
        if branch:
            conds.append("branch = ?")
            params.append(branch)
        where = " WHERE " + " AND ".join(conds) if conds else ""
        params.append(limit)
        rows = self._conn.execute(
            f"""SELECT framework, http_method, path, handler_class, handler_method,
                       handler_symbol, file, line, repo, branch, indexed_at
                FROM routes{where}
                ORDER BY path LIMIT ?""",
            params,
        ).fetchall()
        cols = ["framework", "http_method", "path", "handler_class", "handler_method",
                "handler_symbol", "file", "line", "repo", "branch", "indexed_at"]
        return [dict(zip(cols, r)) for r in rows]

    def routes_for_handler(self, handler_symbol: str) -> list[dict]:
        """Reverse lookup: given a Java method, which URL(s) does it expose?"""
        rows = self._conn.execute(
            """SELECT framework, http_method, path, handler_class, handler_method,
                      handler_symbol, file, line, repo, branch
               FROM routes WHERE handler_symbol = ?
               ORDER BY http_method, path""",
            (handler_symbol,),
        ).fetchall()
        cols = ["framework", "http_method", "path", "handler_class", "handler_method",
                "handler_symbol", "file", "line", "repo", "branch"]
        return [dict(zip(cols, r)) for r in rows]

    def populate_fts(self, force: bool = False) -> dict:
        """Rebuild graph_symbols_fts from current graph_edges. Idempotent.
        Use after a fresh index, after upgrading, or when search seems stale.
        Returns row counts."""
        current = self._conn.execute("SELECT COUNT(*) FROM graph_symbols_fts").fetchone()[0]
        if current > 0 and not force:
            return {"already_populated": True, "rows": current}
        self._conn.execute("DELETE FROM graph_symbols_fts")

        # Two-step: pull distinct rows from graph_edges in Python so we can
        # apply the camelCase expansion to the label before insert.
        rows = self._conn.execute("""
            SELECT DISTINCT source AS sym, repo, branch, source_file, line FROM graph_edges
            UNION
            SELECT DISTINCT target AS sym, repo, branch, source_file, line FROM graph_edges
        """).fetchall()
        self._conn.executemany(
            """INSERT INTO graph_symbols_fts (label, canonical, repo, branch, source_file, line)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(self._searchable_label(r[0]), r[0], r[1], r[2], r[3], r[4]) for r in rows],
        )
        self._conn.commit()
        new = self._conn.execute("SELECT COUNT(*) FROM graph_symbols_fts").fetchone()[0]
        logger.info("FTS5 rebuilt: %d rows", new)
        return {"rebuilt": True, "rows": new}

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

    def find_references(self, repo: str, branch: str, symbol: str) -> list[StoredEdge]:
        """Find all references to a symbol across all edge kinds.

        Uses LIKE matching to handle partial/qualified names.
        """
        pattern = f"%{symbol}%"
        rows = self._conn.execute(
            """SELECT source, target, kind, source_file, target_file, line, repo, branch
               FROM graph_edges
               WHERE repo = ? AND branch = ?
               AND (target LIKE ? OR source LIKE ?)""",
            (repo, branch, pattern, pattern),
        ).fetchall()
        return [StoredEdge(*r) for r in rows]

    @staticmethod
    def normalize_repo_path(path: str) -> str:
        """Strip trailing slashes from a repo path."""
        return path.rstrip("/")

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

    @staticmethod
    def _resolve_symbol_variants(symbol: str) -> list[str]:
        """Return candidate symbol forms from most-specific to most-generic.

        Java/TS indexers today emit source IDs as `<file>::<unknown>` and
        target IDs as bare names (`getLogger`, not the FQN). A caller passing
        the documented `file::Class.method` form would get zero hits. We
        generate fallback aliases so impact_analysis can retry with a shorter
        form when the exact one matches nothing.

        Examples:
          'src/foo.ts::Bar.baz'  → ['src/foo.ts::Bar.baz', 'Bar.baz', 'baz']
          'Bar.baz'              → ['Bar.baz', 'baz']
          'baz'                  → ['baz']
        """
        if not symbol:
            return []
        variants = [symbol]
        if "::" in symbol:
            tail = symbol.rsplit("::", 1)[1]
            if tail and tail not in variants:
                variants.append(tail)
        for v in list(variants):
            if "." in v:
                last = v.rsplit(".", 1)[1]
                if last and last not in variants:
                    variants.append(last)
        return variants

    def impact_analysis(self, repo: str, branch: str, symbol: str,
                        depth: int = 3, kind: str = "calls") -> dict:
        """Public entry point. Tries `symbol` first, then bare-name aliases,
        until one returns non-empty. Adds `matched_as` and `match_strategy`
        fields so callers know which form was actually found."""
        if not symbol:
            return {"error": "symbol is required"}
        variants = self._resolve_symbol_variants(symbol)
        last_result = None
        for v in variants:
            result = self._impact_analysis_one(repo, branch, v, depth, kind)
            if isinstance(result, dict) and "error" not in result:
                up = result.get("upstream", {}).get("total_symbols", 0)
                down = result.get("downstream", {}).get("total_symbols", 0)
                if up > 0 or down > 0:
                    result["query_symbol"] = symbol
                    result["matched_as"] = v
                    result["match_strategy"] = "exact" if v == symbol else "bare-name-fallback"
                    return result
            last_result = result
        if last_result is None or "error" in last_result:
            last_result = {
                "symbol": symbol, "repo": repo, "branch": branch, "depth": depth,
                "upstream": {"total_symbols": 0, "symbols_by_depth": []},
                "downstream": {"total_symbols": 0, "symbols_by_depth": []},
                "blast_radius": {"total_symbols": 0, "total_edges": 0, "files_count": 0},
                "hot_files": [], "direct_callers": [], "direct_callees": [],
            }
        last_result["query_symbol"] = symbol
        last_result["matched_as"] = None
        last_result["match_strategy"] = "none"
        last_result["tried_variants"] = variants
        return last_result

    def _impact_analysis_one(self, repo: str, branch: str, symbol: str,
                             depth: int = 3, kind: str = "calls") -> dict:
        """Single-symbol BFS. Internal — use impact_analysis() instead."""
        if not symbol:
            return {"error": "symbol is required"}
        depth = max(1, min(int(depth), 8))
        kind_clause = " AND kind = ?" if kind else ""

        def bfs(direction: str) -> dict:
            """direction = 'upstream' (callers) or 'downstream' (callees)"""
            visited: set[str] = {symbol}
            frontier: set[str] = {symbol}
            by_depth: list[list[str]] = []  # symbols newly added at each depth
            edges_in_radius: list[StoredEdge] = []

            for _ in range(depth):
                if not frontier:
                    by_depth.append([])
                    continue
                placeholders = ",".join("?" * len(frontier))
                if direction == "upstream":
                    sql = (
                        f"SELECT source, target, kind, source_file, target_file, line, repo, branch "
                        f"FROM graph_edges WHERE repo = ? AND branch = ? "
                        f"AND target IN ({placeholders}){kind_clause}"
                    )
                else:
                    sql = (
                        f"SELECT source, target, kind, source_file, target_file, line, repo, branch "
                        f"FROM graph_edges WHERE repo = ? AND branch = ? "
                        f"AND source IN ({placeholders}){kind_clause}"
                    )
                args: list[Any] = [repo, branch, *frontier]
                if kind:
                    args.append(kind)
                rows = self._conn.execute(sql, args).fetchall()
                edges = [StoredEdge(*r) for r in rows]
                edges_in_radius.extend(edges)

                next_frontier: set[str] = set()
                for e in edges:
                    candidate = e.source if direction == "upstream" else e.target
                    if candidate and candidate not in visited:
                        next_frontier.add(candidate)
                by_depth.append(sorted(next_frontier))
                visited.update(next_frontier)
                frontier = next_frontier

            return {
                "symbols": sorted(visited - {symbol}),  # exclude the seed
                "by_depth": by_depth,
                "edges": edges_in_radius,
            }

        upstream = bfs("upstream")
        downstream = bfs("downstream")

        # Direct = depth 1
        def edges_at(edges: list[StoredEdge], src_or_tgt: str) -> list[dict]:
            return [
                {
                    "symbol": e.source if src_or_tgt == "source" else e.target,
                    "file": e.source_file, "line": e.line, "kind": e.kind,
                }
                for e in edges
                if (src_or_tgt == "source" and e.target == symbol) or
                   (src_or_tgt == "target" and e.source == symbol)
            ]

        direct_callers = edges_at(upstream["edges"], "source")
        direct_callees = edges_at(downstream["edges"], "target")

        # Blast radius: union of all touched symbols + files
        all_edges = upstream["edges"] + downstream["edges"]
        files_set: dict[str, int] = {}
        for e in all_edges:
            if e.source_file:
                files_set[e.source_file] = files_set.get(e.source_file, 0) + 1
        hot_files = sorted(files_set.items(), key=lambda kv: -kv[1])[:5]

        return {
            "symbol": symbol,
            "repo": repo,
            "branch": branch,
            "depth": depth,
            "kind": kind or "any",
            "direct_callers": direct_callers,
            "direct_callees": direct_callees,
            "upstream": {
                "total_symbols": len(upstream["symbols"]),
                "symbols_by_depth": upstream["by_depth"],
            },
            "downstream": {
                "total_symbols": len(downstream["symbols"]),
                "symbols_by_depth": downstream["by_depth"],
            },
            "blast_radius": {
                "total_symbols": len(upstream["symbols"]) + len(downstream["symbols"]),
                "total_edges": len(all_edges),
                "files_count": len(files_set),
            },
            "hot_files": [{"file": f, "edges": n} for f, n in hot_files],
        }
