from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def normalize_hash(content: str) -> str:
    """Normalize content and compute SHA256 hash for dedup.
    Lowercase and collapse whitespace before hashing."""
    normalized = " ".join(content.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()


@dataclass
class Memory:
    """A structured memory entry."""
    id: int | None = None
    title: str = ""
    content: str = ""
    memory_type: str = "note"        # insight, decision, note, bug, architecture, pattern, discovery
    scope: str = "shared"            # shared (team) or local (personal)
    project: str = ""                # project context
    topic_key: str | None = None     # stable key for upserts (e.g. "architecture/auth-model")
    tags: str = ""                   # comma-separated
    author: str = ""                 # who saved it
    repo: str = ""                   # repo path
    branch: str = ""                 # branch context
    files: str = ""                  # comma-separated file paths mentioned
    revision_count: int = 1          # how many times updated
    duplicate_count: int = 1         # how many times seen
    normalized_hash: str = ""        # for dedup detection
    vector_id: str | None = None     # link to LanceDB vector
    session_id: str | None = None    # link to session
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if not self.normalized_hash and self.content:
            self.normalized_hash = normalize_hash(self.content)


class MemoryStore:
    """SQLite-backed memory store with rich metadata and dedup.

    Features:
    - Rich metadata (title, type, scope, project, tags, files, author)
    - Topic key upserts (same topic_key updates existing memory)
    - Content hash deduplication (identical content within 15min window)
    - Revision and duplicate counting
    - Session tracking
    - Vector ID linkage (for semantic search via LanceDB)
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL DEFAULT '',
        content TEXT NOT NULL,
        memory_type TEXT NOT NULL DEFAULT 'note',
        scope TEXT NOT NULL DEFAULT 'shared',
        project TEXT NOT NULL DEFAULT '',
        topic_key TEXT,
        tags TEXT NOT NULL DEFAULT '',
        author TEXT NOT NULL DEFAULT '',
        repo TEXT NOT NULL DEFAULT '',
        branch TEXT NOT NULL DEFAULT '',
        files TEXT NOT NULL DEFAULT '',
        revision_count INTEGER NOT NULL DEFAULT 1,
        duplicate_count INTEGER NOT NULL DEFAULT 1,
        normalized_hash TEXT NOT NULL DEFAULT '',
        vector_id TEXT,
        session_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_memories_topic ON memories(project, scope, topic_key)
        WHERE topic_key IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_memories_hash ON memories(normalized_hash, project, scope);
    CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
    CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
    CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at DESC);

    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        project TEXT NOT NULL DEFAULT '',
        directory TEXT NOT NULL DEFAULT '',
        started_at TEXT NOT NULL,
        ended_at TEXT,
        summary TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project, started_at DESC);

    -- Junction table linking memories to specific code symbols / files.
    -- Populated by MemoryStore.extract_symbol_refs(...) at remember() time and
    -- via a one-shot backfill. The relation is many-to-many: one memory can
    -- mention many symbols, and one symbol can appear in many memories.
    CREATE TABLE IF NOT EXISTS memory_symbol_references (
        memory_id  INTEGER NOT NULL,
        symbol     TEXT NOT NULL,        -- full graph_edges id, e.g. "path/file.ts::Class.method"
        file       TEXT NOT NULL DEFAULT '',
        line       INTEGER NOT NULL DEFAULT 0,
        repo       TEXT NOT NULL DEFAULT '',
        branch     TEXT NOT NULL DEFAULT '',
        source     TEXT NOT NULL DEFAULT '',  -- 'files-field' | 'content-mention' | 'manual'
        created_at TEXT NOT NULL,
        PRIMARY KEY (memory_id, symbol, file),
        FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_msref_symbol ON memory_symbol_references(symbol);
    CREATE INDEX IF NOT EXISTS idx_msref_file   ON memory_symbol_references(file);
    CREATE INDEX IF NOT EXISTS idx_msref_repo   ON memory_symbol_references(repo, branch);
    """

    DEDUP_WINDOW_SECONDS = 900  # 15 minutes

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()
        logger.debug("MemoryStore initialized at %s", db_path)

    def close(self) -> None:
        self._conn.close()

    def save(self, memory: Memory) -> Memory:
        """Save a memory with dedup and topic key upsert logic.

        Priority:
        1. If topic_key exists in same project+scope → UPDATE (upsert)
        2. If content hash exists within dedup window → increment duplicate_count
        3. Otherwise → INSERT new memory
        """
        now = datetime.now(timezone.utc).isoformat()

        # Step 1: Topic key upsert
        if memory.topic_key:
            existing = self._conn.execute(
                """SELECT id, revision_count FROM memories
                   WHERE topic_key = ? AND project = ? AND scope = ?
                   AND deleted_at IS NULL
                   ORDER BY created_at DESC LIMIT 1""",
                (memory.topic_key, memory.project, memory.scope),
            ).fetchone()

            if existing:
                self._conn.execute(
                    """UPDATE memories SET
                       title = ?, content = ?, memory_type = ?, tags = ?,
                       author = ?, repo = ?, branch = ?, files = ?,
                       normalized_hash = ?, vector_id = ?,
                       revision_count = revision_count + 1,
                       updated_at = ?
                       WHERE id = ?""",
                    (memory.title, memory.content, memory.memory_type, memory.tags,
                     memory.author, memory.repo, memory.branch, memory.files,
                     memory.normalized_hash, memory.vector_id,
                     now, existing["id"]),
                )
                self._conn.commit()
                memory.id = existing["id"]
                memory.revision_count = existing["revision_count"] + 1
                logger.info("Updated memory #%d via topic_key '%s' (rev %d)",
                           memory.id, memory.topic_key, memory.revision_count)
                return memory

        # Step 2: Content hash dedup
        if memory.normalized_hash:
            cutoff = datetime.fromtimestamp(
                time.time() - self.DEDUP_WINDOW_SECONDS, tz=timezone.utc
            ).isoformat()
            existing = self._conn.execute(
                """SELECT id, duplicate_count FROM memories
                   WHERE normalized_hash = ? AND project = ? AND scope = ?
                   AND deleted_at IS NULL AND created_at > ?
                   ORDER BY created_at DESC LIMIT 1""",
                (memory.normalized_hash, memory.project, memory.scope, cutoff),
            ).fetchone()

            if existing:
                self._conn.execute(
                    """UPDATE memories SET
                       duplicate_count = duplicate_count + 1,
                       updated_at = ?
                       WHERE id = ?""",
                    (now, existing["id"]),
                )
                self._conn.commit()
                memory.id = existing["id"]
                memory.duplicate_count = existing["duplicate_count"] + 1
                logger.info("Dedup: memory #%d seen again (dup %d)",
                           memory.id, memory.duplicate_count)
                return memory

        # Step 3: Insert new
        cursor = self._conn.execute(
            """INSERT INTO memories
               (title, content, memory_type, scope, project, topic_key, tags,
                author, repo, branch, files, revision_count, duplicate_count,
                normalized_hash, vector_id, session_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?)""",
            (memory.title, memory.content, memory.memory_type, memory.scope,
             memory.project, memory.topic_key, memory.tags,
             memory.author, memory.repo, memory.branch, memory.files,
             memory.normalized_hash, memory.vector_id, memory.session_id,
             memory.created_at, memory.updated_at),
        )
        self._conn.commit()
        memory.id = cursor.lastrowid
        logger.info("Saved new memory #%d: %s", memory.id, memory.title or memory.content[:50])
        try:
            self.extract_symbol_refs(memory)
        except Exception as e:
            # Extraction is best-effort: a broken graph index must not block remember().
            logger.warning("extract_symbol_refs failed for memory #%d: %s", memory.id, e)
        return memory

    # --- Symbol linkage --------------------------------------------------------

    _IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

    @staticmethod
    def _short_label(symbol: str) -> str:
        """Mirror Go's shortLabel: tail after '::' or last '/'."""
        if "::" in symbol:
            return symbol.rsplit("::", 1)[1]
        if "/" in symbol:
            return symbol.rsplit("/", 1)[1]
        return symbol

    def extract_symbol_refs(self, memory: Memory) -> int:
        """Populate memory_symbol_references for a saved memory.

        Strategy:
          1. For each file in memory.files (CSV), insert a file-level reference (no specific symbol)
          2. For those files, look up symbols defined in them via graph_edges
          3. Text-match the short label of each symbol against memory.content; if it
             appears as a distinct identifier, link it.

        Idempotent: PRIMARY KEY (memory_id, symbol, file) makes re-runs cheap.
        Returns the number of distinct references inserted (counting both files and symbols).
        """
        if memory.id is None or not memory.content:
            return 0
        files = [f.strip() for f in (memory.files or "").split(",") if f.strip()]
        # Always-rebuild semantics: drop prior rows for this memory then re-insert
        self._conn.execute("DELETE FROM memory_symbol_references WHERE memory_id = ?", (memory.id,))

        now = datetime.now(timezone.utc).isoformat()
        inserted = 0

        # 1. File-level refs (symbol = '' means "any symbol in this file")
        for f in files:
            self._conn.execute(
                """INSERT OR IGNORE INTO memory_symbol_references
                   (memory_id, symbol, file, line, repo, branch, source, created_at)
                   VALUES (?, '', ?, 0, ?, ?, 'files-field', ?)""",
                (memory.id, f, memory.repo, memory.branch, now),
            )
            inserted += 1

        # 2. Symbol-level refs (only if the graph_edges table exists in the same DB)
        try:
            self._conn.execute("SELECT 1 FROM graph_edges LIMIT 0")
        except sqlite3.OperationalError:
            self._conn.commit()
            return inserted

        if not files:
            self._conn.commit()
            return inserted

        # Tokenize memory content into a set of candidate identifiers
        tokens = set(self._IDENT_RE.findall(memory.content))
        if not tokens:
            self._conn.commit()
            return inserted

        placeholders = ",".join(["?"] * len(files))
        # Pull all symbols (source AND target) that live in any of the memory's files
        rows = self._conn.execute(
            f"""SELECT DISTINCT source AS symbol, source_file AS file, repo, branch, line
                FROM graph_edges
                WHERE source_file IN ({placeholders})
                UNION
                SELECT DISTINCT target AS symbol, source_file AS file, repo, branch, line
                FROM graph_edges
                WHERE source_file IN ({placeholders})""",
            files + files,
        ).fetchall()

        seen = set()
        for r in rows:
            symbol = r["symbol"]
            short = self._short_label(symbol)
            if short in ("<unknown>", "<module>", "<anonymous>") or len(short) < 3:
                continue
            if short not in tokens:
                continue
            key = (symbol, r["file"])
            if key in seen:
                continue
            seen.add(key)
            self._conn.execute(
                """INSERT OR IGNORE INTO memory_symbol_references
                   (memory_id, symbol, file, line, repo, branch, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'content-mention', ?)""",
                (memory.id, symbol, r["file"], r["line"], r["repo"] or memory.repo,
                 r["branch"] or memory.branch, now),
            )
            inserted += 1

        self._conn.commit()
        return inserted

    def memories_by_symbol(self, symbol: str, repo: str = "", branch: str = "",
                           limit: int = 20) -> list[tuple[Memory, str]]:
        """Return [(memory, link_sources)] for memories referencing the symbol.

        Two-stage lookup:
          1. Exact JOIN against memory_symbol_references — returns rows whose
             link_sources reflect HOW the link was established (files-field,
             content-mention, vector-similarity).
          2. If stage 1 returns nothing, fall back to a LIKE search over
             memories.files and memories.content using the symbol's short
             label (tail after `::` or `.`). Hits get link_sources='inference'
             so callers can tell the difference from structural matches.
        """
        conds = ["msr.symbol = ?", "m.deleted_at IS NULL"]
        params: list[Any] = [symbol]
        if repo:
            conds.append("msr.repo = ?")
            params.append(repo)
        if branch:
            conds.append("msr.branch = ?")
            params.append(branch)
        params.append(limit)
        rows = self._conn.execute(
            f"""SELECT m.*, GROUP_CONCAT(DISTINCT msr.source) AS link_sources
                FROM memories m
                JOIN memory_symbol_references msr ON msr.memory_id = m.id
                WHERE {' AND '.join(conds)}
                GROUP BY m.id
                ORDER BY m.updated_at DESC LIMIT ?""",
            params,
        ).fetchall()
        if rows:
            return [(self._row_to_memory(r), r["link_sources"] or "") for r in rows]

        # Stage 2 — LIKE fallback. Derive a short, queryable label.
        short = self._short_label(symbol)
        if len(short) < 3:
            return []
        pattern = f"%{short}%"
        fb_conds = [
            "m.deleted_at IS NULL",
            "(m.files LIKE ? OR m.content LIKE ?)",
        ]
        fb_params: list[Any] = [pattern, pattern]
        # Optional repo/branch narrowing on the memory side
        if repo:
            fb_conds.append("m.repo = ?")
            fb_params.append(repo)
        if branch:
            fb_conds.append("m.branch = ?")
            fb_params.append(branch)
        fb_params.append(limit)
        fb_rows = self._conn.execute(
            f"""SELECT m.* FROM memories m
                WHERE {' AND '.join(fb_conds)}
                ORDER BY m.updated_at DESC LIMIT ?""",
            fb_params,
        ).fetchall()
        return [(self._row_to_memory(r), "inference") for r in fb_rows]

    def memories_by_file(self, file: str, limit: int = 20) -> list[tuple[Memory, str]]:
        """Return [(memory, link_sources)] for memories referencing the file."""
        rows = self._conn.execute(
            """SELECT m.*, GROUP_CONCAT(DISTINCT msr.source) AS link_sources
               FROM memories m
               JOIN memory_symbol_references msr ON msr.memory_id = m.id
               WHERE msr.file = ? AND m.deleted_at IS NULL
               GROUP BY m.id
               ORDER BY m.updated_at DESC LIMIT ?""",
            (file, limit),
        ).fetchall()
        return [(self._row_to_memory(r), r["link_sources"] or "") for r in rows]

    def memory_refs(self, memory_id: int) -> list[dict]:
        """Return the raw junction rows for a memory (symbol, file, source...).
        Used by the bidirectional UI to derive a subgraph from a memory click."""
        rows = self._conn.execute(
            """SELECT symbol, file, line, repo, branch, source
               FROM memory_symbol_references WHERE memory_id = ?""",
            (memory_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def symbol_reference_counts(self, repo: str = "", branch: str = "") -> dict[str, int]:
        """Return {symbol: memory_count} for nodes that have ≥1 linked memory.
        Used by the frontend to render the heatmap."""
        conds = ["msr.symbol != ''"]
        params: list[Any] = []
        if repo:
            conds.append("msr.repo = ?")
            params.append(repo)
        if branch:
            conds.append("msr.branch = ?")
            params.append(branch)
        rows = self._conn.execute(
            f"""SELECT msr.symbol, COUNT(DISTINCT msr.memory_id) AS n
                FROM memory_symbol_references msr
                WHERE {' AND '.join(conds)}
                GROUP BY msr.symbol""",
            params,
        ).fetchall()
        return {r["symbol"]: r["n"] for r in rows}

    def backfill_symbol_refs(self) -> dict:
        """Re-run extract_symbol_refs across every non-deleted memory.
        Use after upgrading or after a fresh index.
        Returns {memories_scanned, refs_inserted}."""
        rows = self._conn.execute(
            "SELECT id FROM memories WHERE deleted_at IS NULL"
        ).fetchall()
        scanned = 0
        inserted = 0
        for r in rows:
            mem = self.get(r["id"])
            if mem is None:
                continue
            scanned += 1
            inserted += self.extract_symbol_refs(mem)
        logger.info("backfill_symbol_refs: scanned=%d inserted=%d", scanned, inserted)
        return {"memories_scanned": scanned, "refs_inserted": inserted}

    # --- end symbol linkage ----------------------------------------------------

    def get(self, memory_id: int) -> Memory | None:
        """Get a single memory by ID."""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ? AND deleted_at IS NULL",
            (memory_id,),
        ).fetchone()
        return self._row_to_memory(row) if row else None

    def search_by_text(self, query: str, project: str = "", scope: str = "",
                       memory_type: str = "", limit: int = 20) -> list[Memory]:
        """Search memories by keyword (LIKE matching). For semantic search, use vector store."""
        conditions = ["deleted_at IS NULL"]
        params: list[Any] = []

        if query:
            conditions.append("(title LIKE ? OR content LIKE ? OR tags LIKE ?)")
            pattern = f"%{query}%"
            params.extend([pattern, pattern, pattern])
        if project:
            conditions.append("project = ?")
            params.append(project)
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)

        where = " AND ".join(conditions)
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM memories WHERE {where} ORDER BY updated_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def get_recent(self, project: str = "", scope: str = "", limit: int = 20) -> list[Memory]:
        """Get recent memories without search."""
        conditions = ["deleted_at IS NULL"]
        params: list[Any] = []
        if project:
            conditions.append("project = ?")
            params.append(project)
        if scope:
            conditions.append("scope = ?")
            params.append(scope)
        where = " AND ".join(conditions)
        params.append(limit)

        rows = self._conn.execute(
            f"SELECT * FROM memories WHERE {where} ORDER BY updated_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_memory(r) for r in rows]

    def get_by_topic(self, topic_key: str, project: str = "") -> Memory | None:
        """Get a memory by topic key."""
        conditions = ["topic_key = ?", "deleted_at IS NULL"]
        params: list[Any] = [topic_key]
        if project:
            conditions.append("project = ?")
            params.append(project)
        row = self._conn.execute(
            f"SELECT * FROM memories WHERE {' AND '.join(conditions)} ORDER BY updated_at DESC LIMIT 1",
            params,
        ).fetchone()
        return self._row_to_memory(row) if row else None

    def delete(self, memory_id: int, hard: bool = False) -> bool:
        """Soft delete (default) or hard delete a memory."""
        if hard:
            self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        else:
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "UPDATE memories SET deleted_at = ? WHERE id = ?", (now, memory_id),
            )
        self._conn.commit()
        return True

    def update(self, memory_id: int, **fields) -> Memory | None:
        """Partial update of a memory by ID."""
        allowed = {"title", "content", "memory_type", "tags", "files", "topic_key"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return self.get(memory_id)

        now = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        set_clause += ", revision_count = revision_count + 1, updated_at = ?"
        values = list(updates.values())
        values.append(now)
        values.append(memory_id)

        self._conn.execute(
            f"UPDATE memories SET {set_clause} WHERE id = ? AND deleted_at IS NULL",
            values,
        )
        self._conn.commit()
        return self.get(memory_id)

    def stats(self) -> dict:
        """Memory statistics."""
        total = self._conn.execute(
            "SELECT COUNT(*) FROM memories WHERE deleted_at IS NULL"
        ).fetchone()[0]
        by_type = dict(self._conn.execute(
            "SELECT memory_type, COUNT(*) FROM memories WHERE deleted_at IS NULL GROUP BY memory_type"
        ).fetchall())
        by_project = dict(self._conn.execute(
            "SELECT project, COUNT(*) FROM memories WHERE deleted_at IS NULL GROUP BY project"
        ).fetchall())
        return {"total": total, "by_type": by_type, "by_project": by_project}

    # --- Sessions ---

    def start_session(self, session_id: str, project: str, directory: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT OR REPLACE INTO sessions (id, project, directory, started_at)
               VALUES (?, ?, ?, ?)""",
            (session_id, project, directory, now),
        )
        self._conn.commit()

    def end_session(self, session_id: str, summary: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?",
            (now, summary, session_id),
        )
        self._conn.commit()

    def get_recent_sessions(self, project: str = "", limit: int = 10) -> list[dict]:
        conditions = []
        params: list[Any] = []
        if project:
            conditions.append("project = ?")
            params.append(project)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = self._conn.execute(
            f"SELECT * FROM sessions {where} ORDER BY started_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Helpers ---

    @staticmethod
    def _row_to_memory(row: sqlite3.Row) -> Memory:
        return Memory(
            id=row["id"],
            title=row["title"],
            content=row["content"],
            memory_type=row["memory_type"],
            scope=row["scope"],
            project=row["project"],
            topic_key=row["topic_key"],
            tags=row["tags"],
            author=row["author"],
            repo=row["repo"],
            branch=row["branch"],
            files=row["files"],
            revision_count=row["revision_count"],
            duplicate_count=row["duplicate_count"],
            normalized_hash=row["normalized_hash"],
            vector_id=row["vector_id"],
            session_id=row["session_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
