"""A safety net under every run: snapshots of the working folder in a shadow git repository.

The user's own folder is never touched (no .git is created there and an existing one is left
alone): the shadow repository lives under the data dir, keyed by the folder's path, and uses the
folder as its work tree. A checkpoint is taken when a run starts, after every task and after
the final check, so a task, or the whole run, can be undone from the UI, and the half-finished
edits of a job that crashed are cleared before a teammate takes the task over.

Ignored: whatever the folder's .gitignore ignores, plus the usual dependency and build dirs.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional

from .config import DATA_DIR
from .snapshot import IGNORE_DIRS

GIT = "/usr/bin/git"
_LOCKS: Dict[str, threading.RLock] = {}
AUTHOR = {"GIT_AUTHOR_NAME": "PixelCrew", "GIT_AUTHOR_EMAIL": "pixelcrew@localhost",
          "GIT_COMMITTER_NAME": "PixelCrew", "GIT_COMMITTER_EMAIL": "pixelcrew@localhost"}


def available() -> bool:
    return os.path.exists(GIT)


def undo_plan(events: List[Dict[str, object]], task_id: str) -> Optional[Dict[str, object]]:
    """From a run's events, what undoing one task means: the checkpoint taken right before its
    (last) job and the files to put back, limited to the files that task itself owned or touched,
    so a teammate's work done in parallel during the same window is left alone."""
    before = done = None
    files: set = set()
    for e in events:
        if e.get("kind") == "checkpoint" and e.get("task_id") == task_id:
            if e.get("stage") == "before" and done is None:
                before = str(e["commit"])
            elif e.get("stage") == "task":
                done = str(e["commit"])
        elif e.get("kind") == "task.update" and (e.get("task") or {}).get("id") == task_id:
            t = e["task"]
            files |= set(t.get("files") or []) | set(t.get("files_changed") or [])
    if not before or not done:
        return None
    return {"before": before, "done": done, "files": sorted(files)}


def _locked(fn):
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    wrapper.__doc__ = fn.__doc__
    return wrapper


class Checkpoints:
    def __init__(self, workspace: str) -> None:
        self.workspace = str(Path(workspace).resolve())
        key = hashlib.sha1(self.workspace.encode("utf-8")).hexdigest()[:16]
        self.git_dir = DATA_DIR / "git" / key
        self.ok = False
        # jobs run in parallel threads; git holds one index lock per repository
        self.lock = _LOCKS.setdefault(key, threading.RLock())

    # -- plumbing --------------------------------------------------------------
    def _git(self, *args: str, check: bool = True, timeout: int = 120) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update(AUTHOR)
        env["GIT_DIR"] = str(self.git_dir)
        env["GIT_WORK_TREE"] = self.workspace
        env.pop("GIT_INDEX_FILE", None)
        proc = subprocess.run([GIT, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                               "-c", "core.autocrlf=false", *args],
                              cwd=self.workspace, env=env, capture_output=True, text=True, timeout=timeout)
        if check and proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout).strip()[:300])
        return proc

    @_locked
    def open(self) -> bool:
        """Create the shadow repository if needed. False when git is missing or the folder can't be used."""
        if not available():
            return False
        try:
            if not (self.git_dir / "HEAD").exists():
                self.git_dir.mkdir(parents=True, exist_ok=True)
                self._git("init", "-q", "--initial-branch=main")
                exclude = self.git_dir / "info" / "exclude"
                exclude.parent.mkdir(parents=True, exist_ok=True)
                exclude.write_text("".join(f"{d}/\n" for d in sorted(IGNORE_DIRS)) + ".DS_Store\n.pixelcrew*\n", "utf-8")
            self.ok = True
        except (OSError, RuntimeError, subprocess.SubprocessError):
            self.ok = False
        return self.ok

    # -- checkpoints -----------------------------------------------------------------
    @_locked
    def take(self, message: str) -> Optional[str]:
        """Commit the folder as it is now and return the commit id (or None when disabled/failed)."""
        if not self.ok:
            return None
        try:
            self._git("add", "-A", "--", ".")
            self._git("commit", "-q", "--allow-empty", "-m", message)
            return self._git("rev-parse", "HEAD").stdout.strip()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return None

    @_locked
    def changed_since(self, commit: str) -> List[Dict[str, str]]:
        """Files that differ between a checkpoint and the folder now: [{status, path}]."""
        if not self.ok:
            return []
        try:
            self._git("add", "-A", "--", ".")
            out = self._git("diff", "--cached", "--name-status", "-M", commit).stdout
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return []
        rows = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                rows.append({"status": parts[0][0], "path": parts[-1]})
        return rows

    @_locked
    def changed_between(self, a: str, b: str) -> List[str]:
        """Paths that differ between two checkpoints."""
        if not self.ok:
            return []
        try:
            return [p for p in self._git("diff", "--name-only", a, b).stdout.splitlines() if p]
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return []

    @_locked
    def restore(self, commit: str, only: Optional[List[str]] = None, message: str = "") -> List[str]:
        """Put files back as they were at `commit`: modified and deleted ones are checked out, files
        that did not exist then are removed. `only` limits it to those paths. Returns the paths touched."""
        if not self.ok:
            return []
        touched: List[str] = []
        wanted = set(only or [])
        for row in self.changed_since(commit):
            path = row["path"]
            if wanted and path not in wanted:
                continue
            try:
                if row["status"] == "A":
                    full = Path(self.workspace) / path
                    if full.is_file():
                        full.unlink()
                else:
                    self._git("checkout", commit, "--", path)
                touched.append(path)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                continue
        if touched:
            self.take(message or f"restore {commit[:8]}")
        return touched

    @_locked
    def reset_to(self, commit: str) -> bool:
        """The whole folder back to a checkpoint (ignored files stay)."""
        if not self.ok:
            return False
        try:
            self._git("add", "-A", "--", ".")
            self._git("commit", "-q", "--allow-empty", "-m", "before undo")
            self._git("reset", "-q", "--hard", commit)
            self.take(f"undo to {commit[:8]}")
            return True
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return False
