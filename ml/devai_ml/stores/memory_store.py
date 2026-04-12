from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from dataclasses import dataclass, field
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
        return memory

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
