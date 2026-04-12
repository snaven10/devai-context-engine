from __future__ import annotations

import sqlite3
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class IndexRecord:
    """State of an index for a specific repo+branch."""
    repo_path: str
    branch: str
    last_commit: str
    model_name: str
    model_dimension: int
    file_count: int = 0
    symbol_count: int = 0
    chunk_count: int = 0
    indexed_at: str = ""

    def __post_init__(self) -> None:
        if not self.indexed_at:
            self.indexed_at = datetime.now(timezone.utc).isoformat()


@dataclass
class FileRecord:
    """Tracked state of an individual file."""
    file_path: str
    content_hash: str
    language: str
    symbol_count: int = 0
    chunk_count: int = 0


class IndexStateStore:
    """SQLite-backed store tracking what has been indexed.

    Replaces the old JSON-file based incremental_indexer.py.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS index_state (
        repo_path TEXT NOT NULL,
        branch TEXT NOT NULL,
        last_commit TEXT NOT NULL,
        model_name TEXT NOT NULL,
        model_dimension INTEGER NOT NULL,
        file_count INTEGER DEFAULT 0,
        symbol_count INTEGER DEFAULT 0,
        chunk_count INTEGER DEFAULT 0,
        indexed_at TEXT NOT NULL,
        PRIMARY KEY (repo_path, branch)
    );

    CREATE TABLE IF NOT EXISTS file_state (
        repo_path TEXT NOT NULL,
        branch TEXT NOT NULL,
        file_path TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        language TEXT NOT NULL DEFAULT '',
        symbol_count INTEGER DEFAULT 0,
        chunk_count INTEGER DEFAULT 0,
        PRIMARY KEY (repo_path, branch, file_path)
    );

    CREATE TABLE IF NOT EXISTS branch_lineage (
        repo_path TEXT NOT NULL,
        branch TEXT NOT NULL,
        base_branch TEXT NOT NULL,
        merge_base_commit TEXT,
        PRIMARY KEY (repo_path, branch)
    );
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(self.SCHEMA)
        self._conn.commit()
        self.normalize_paths()
        logger.debug("IndexStateStore initialized at %s", db_path)

    def normalize_paths(self) -> None:
        """Remove trailing slashes from all repo_path entries.

        Handles UNIQUE constraint conflicts by deleting the slash-suffixed
        duplicate first (keeping the clean version), then normalizing the rest.
        """
        # Delete duplicates where both 'path/' and 'path' exist (keep the clean one)
        self._conn.execute("""
            DELETE FROM index_state
            WHERE repo_path LIKE '%/'
            AND RTRIM(repo_path, '/') IN (SELECT repo_path FROM index_state WHERE repo_path NOT LIKE '%/')
        """)
        self._conn.execute("""
            DELETE FROM file_state
            WHERE repo_path LIKE '%/'
            AND RTRIM(repo_path, '/') IN (SELECT DISTINCT repo_path FROM file_state WHERE repo_path NOT LIKE '%/')
        """)
        self._conn.execute("""
            DELETE FROM branch_lineage
            WHERE repo_path LIKE '%/'
            AND RTRIM(repo_path, '/') IN (SELECT repo_path FROM branch_lineage WHERE repo_path NOT LIKE '%/')
        """)
        # Now safe to normalize remaining entries
        self._conn.execute(
            "UPDATE index_state SET repo_path = RTRIM(repo_path, '/') WHERE repo_path LIKE '%/'"
        )
        self._conn.execute(
            "UPDATE file_state SET repo_path = RTRIM(repo_path, '/') WHERE repo_path LIKE '%/'"
        )
        self._conn.execute(
            "UPDATE branch_lineage SET repo_path = RTRIM(repo_path, '/') WHERE repo_path LIKE '%/'"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get_last_indexed(self, repo_path: str, branch: str) -> IndexRecord | None:
        row = self._conn.execute(
            """SELECT repo_path, branch, last_commit, model_name, model_dimension,
                      file_count, symbol_count, chunk_count, indexed_at
               FROM index_state WHERE repo_path = ? AND branch = ?""",
            (repo_path, branch),
        ).fetchone()
        if row is None:
            return None
        return IndexRecord(*row)

    def save(self, record: IndexRecord) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO index_state
               (repo_path, branch, last_commit, model_name, model_dimension,
                file_count, symbol_count, chunk_count, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.repo_path, record.branch, record.last_commit,
                record.model_name, record.model_dimension,
                record.file_count, record.symbol_count, record.chunk_count,
                record.indexed_at,
            ),
        )
        self._conn.commit()

    def get_file_hash(self, repo_path: str, branch: str, file_path: str) -> str | None:
        row = self._conn.execute(
            "SELECT content_hash FROM file_state WHERE repo_path = ? AND branch = ? AND file_path = ?",
            (repo_path, branch, file_path),
        ).fetchone()
        return row[0] if row else None

    def save_file(self, repo_path: str, branch: str, record: FileRecord) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO file_state
               (repo_path, branch, file_path, content_hash, language, symbol_count, chunk_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                repo_path, branch, record.file_path, record.content_hash,
                record.language, record.symbol_count, record.chunk_count,
            ),
        )
        self._conn.commit()

    def remove_file(self, repo_path: str, branch: str, file_path: str) -> None:
        self._conn.execute(
            "DELETE FROM file_state WHERE repo_path = ? AND branch = ? AND file_path = ?",
            (repo_path, branch, file_path),
        )
        self._conn.commit()

    def rename_file(self, repo_path: str, branch: str, old_path: str, new_path: str) -> None:
        self._conn.execute(
            """UPDATE file_state SET file_path = ?
               WHERE repo_path = ? AND branch = ? AND file_path = ?""",
            (new_path, repo_path, branch, old_path),
        )
        self._conn.commit()

    def get_all_file_hashes(self, repo_path: str, branch: str) -> dict[str, str]:
        """Return {file_path: content_hash} for all tracked files."""
        rows = self._conn.execute(
            "SELECT file_path, content_hash FROM file_state WHERE repo_path = ? AND branch = ?",
            (repo_path, branch),
        ).fetchall()
        return dict(rows)

    def set_branch_lineage(self, repo_path: str, branch: str, base_branch: str,
                           merge_base_commit: str | None = None) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO branch_lineage
               (repo_path, branch, base_branch, merge_base_commit)
               VALUES (?, ?, ?, ?)""",
            (repo_path, branch, base_branch, merge_base_commit),
        )
        self._conn.commit()

    def get_branch_lineage(self, repo_path: str, branch: str) -> list[str]:
        """Get branch ancestry chain: [current, parent, grandparent, ..., main]."""
        lineage = [branch]
        current = branch
        visited: set[str] = {branch}

        while True:
            row = self._conn.execute(
                "SELECT base_branch FROM branch_lineage WHERE repo_path = ? AND branch = ?",
                (repo_path, current),
            ).fetchone()
            if row is None or row[0] in visited:
                break
            lineage.append(row[0])
            visited.add(row[0])
            current = row[0]

        return lineage

    def get_branch_files(self, repo_path: str, branch: str) -> list[str]:
        """Get all file paths indexed for a specific branch."""
        rows = self._conn.execute(
            "SELECT file_path FROM file_state WHERE repo_path = ? AND branch = ?",
            (repo_path, branch),
        ).fetchall()
        return [r[0] for r in rows]

    def get_stats(self, repo_path: str, branch: str) -> dict:
        """Get summary stats for a repo+branch."""
        record = self.get_last_indexed(repo_path, branch)
        if record is None:
            return {"indexed": False}
        return {
            "indexed": True,
            "last_commit": record.last_commit,
            "model": record.model_name,
            "files": record.file_count,
            "symbols": record.symbol_count,
            "chunks": record.chunk_count,
            "indexed_at": record.indexed_at,
        }
