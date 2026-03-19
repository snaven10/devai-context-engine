from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class FileStatus(Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass(frozen=True, slots=True)
class FileChange:
    """A file that changed between two commits."""
    path: str
    status: FileStatus
    old_path: str | None = None  # for renames


@dataclass(frozen=True, slots=True)
class RepoState:
    """Current state of a git repository."""
    repo_path: str
    current_commit: str
    branch: str
    dirty_files: list[str]
    is_detached: bool = False


class GitStateDetector:
    """Detect repository state and compute diffs using git CLI."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = str(Path(repo_path).resolve())
        if not Path(self.repo_path, ".git").exists():
            raise ValueError(f"Not a git repository: {self.repo_path}")

    def _run(self, *args: str) -> str:
        """Run a git command and return stdout."""
        cmd = ["git", "-C", self.repo_path, *args]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise RuntimeError(f"git command failed: {' '.join(cmd)}\n{result.stderr}")
        return result.stdout.strip()

    def get_state(self) -> RepoState:
        """Read current repository state."""
        commit = self._run("rev-parse", "HEAD")

        # Detect branch
        try:
            branch = self._run("rev-parse", "--abbrev-ref", "HEAD")
            is_detached = branch == "HEAD"
        except RuntimeError:
            branch = "HEAD"
            is_detached = True

        # Dirty files
        dirty_output = self._run("status", "--porcelain", "-u")
        dirty_files = []
        for line in dirty_output.splitlines():
            if line.strip():
                # Status is first 2 chars, then space, then path
                path = line[3:].strip()
                if " -> " in path:  # renamed
                    path = path.split(" -> ")[1]
                dirty_files.append(path)

        return RepoState(
            repo_path=self.repo_path,
            current_commit=commit,
            branch=branch,
            dirty_files=dirty_files,
            is_detached=is_detached,
        )

    def compute_diff(self, from_commit: str | None, to_commit: str) -> list[FileChange]:
        """Compute file changes between two commits.

        If from_commit is None, treats all files as ADDED (initial index).
        Uses -M flag for rename detection.
        """
        if from_commit is None:
            # Initial index: all tracked files are "added"
            output = self._run("ls-tree", "-r", "--name-only", to_commit)
            return [
                FileChange(path=path, status=FileStatus.ADDED)
                for path in output.splitlines()
                if path.strip()
            ]

        output = self._run("diff", "--name-status", "-M", f"{from_commit}..{to_commit}")
        return self._parse_name_status(output)

    def get_dirty_changes(self) -> list[FileChange]:
        """Get uncommitted changes (staged + unstaged)."""
        output = self._run("diff", "--name-status", "-M", "HEAD")
        changes = self._parse_name_status(output)

        # Also get untracked files
        untracked = self._run("ls-files", "--others", "--exclude-standard")
        for path in untracked.splitlines():
            if path.strip():
                changes.append(FileChange(path=path.strip(), status=FileStatus.ADDED))

        return changes

    def get_merge_base(self, branch_a: str, branch_b: str) -> str | None:
        """Find the common ancestor of two branches."""
        try:
            return self._run("merge-base", branch_a, branch_b)
        except RuntimeError:
            return None

    def get_branch_files(self, branch: str, base_branch: str) -> list[str]:
        """Get files that differ between a branch and its base."""
        merge_base = self.get_merge_base(branch, base_branch)
        if merge_base is None:
            return []
        output = self._run("diff", "--name-only", f"{merge_base}..{branch}")
        return [p.strip() for p in output.splitlines() if p.strip()]

    def get_file_content(self, file_path: str, commit: str | None = None) -> str | None:
        """Read file content at a specific commit, or from working tree."""
        if commit is None:
            full_path = Path(self.repo_path) / file_path
            if full_path.exists():
                return full_path.read_text(errors="replace")
            return None
        try:
            return self._run("show", f"{commit}:{file_path}")
        except RuntimeError:
            return None

    @staticmethod
    def _parse_name_status(output: str) -> list[FileChange]:
        """Parse git diff --name-status output."""
        changes: list[FileChange] = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            status_code = parts[0]

            if status_code == "A":
                changes.append(FileChange(path=parts[1], status=FileStatus.ADDED))
            elif status_code == "M":
                changes.append(FileChange(path=parts[1], status=FileStatus.MODIFIED))
            elif status_code == "D":
                changes.append(FileChange(path=parts[1], status=FileStatus.DELETED))
            elif status_code.startswith("R"):
                # Rename: R100\told_path\tnew_path
                changes.append(FileChange(
                    path=parts[2],
                    status=FileStatus.RENAMED,
                    old_path=parts[1],
                ))
            elif status_code.startswith("C"):
                # Copy: treat as added
                changes.append(FileChange(path=parts[2], status=FileStatus.ADDED))

        return changes
