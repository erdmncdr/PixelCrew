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
import shutil
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
    def _git(self, *args: str, check: bool = True, timeout: int = 120,
             worktree: Optional[str] = None) -> subprocess.CompletedProcess:
        """Run git on the folder, or (worktree=path) inside a linked worktree, which git finds
        through the .git file it holds."""
        env = dict(os.environ)
        env.update(AUTHOR)
        env.pop("GIT_INDEX_FILE", None)
        if worktree:
            env.pop("GIT_DIR", None)
            env.pop("GIT_WORK_TREE", None)
        else:
            env["GIT_DIR"] = str(self.git_dir)
            env["GIT_WORK_TREE"] = self.workspace
        proc = subprocess.run([GIT, "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                               "-c", "core.autocrlf=false", *args],
                              cwd=worktree or self.workspace, env=env, capture_output=True, text=True, timeout=timeout)
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
            # no trailing slash: in a task's worktree these names are symlinks to the folder's own dirs
            exclude.write_text("".join(f"{d}\n" for d in sorted(IGNORE_DIRS)) + ".DS_Store\n.pixelcrew*\n", "utf-8")
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

    # -- task worktrees -------------------------------------------------------------------
    # Each task can build in its own linked worktree of the shadow repository, branched from the
    # checkpoint taken when the task starts, and is merged into the folder when it is approved.
    # Dependency and build folders the snapshot ignores (node_modules, .venv, ...) are symlinked
    # from the folder, so tests run in the worktree without reinstalling anything.
    @_locked
    def worktree_add(self, path: str, base: str, branch: str) -> bool:
        if not self.ok:
            return False
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            if Path(path).exists():
                self.worktree_remove(path, branch)
            self._git("worktree", "add", "-q", "-B", branch, path, base)
            self._link_ignored(path)
            return True
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return False

    def _link_ignored(self, path: str) -> None:
        for name in sorted(IGNORE_DIRS):
            src = Path(self.workspace) / name
            dst = Path(path) / name
            if name != ".git" and src.is_dir() and not dst.exists():
                try:
                    os.symlink(str(src), str(dst))
                except OSError:
                    pass

    @_locked
    def worktree_reset(self, path: str, base: str) -> bool:
        """The worktree back to a clean copy of `base` (a retry or a teammate starts over)."""
        if not self.ok:
            return False
        try:
            self._git("reset", "-q", "--hard", base, worktree=path)
            self._git("clean", "-fdq", worktree=path)
            self._link_ignored(path)
            return True
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return False

    @_locked
    def worktree_commit(self, path: str, message: str) -> Optional[str]:
        """Commit the worktree as it is now on its branch."""
        if not self.ok:
            return None
        try:
            self._git("add", "-A", "--", ".", worktree=path)
            self._git("commit", "-q", "--allow-empty", "-m", message, worktree=path)
            return self._git("rev-parse", "HEAD", worktree=path).stdout.strip()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return None

    @_locked
    def merge(self, branch: str, message: str) -> Dict[str, object]:
        """Merge a task branch into the folder. {"ok": True} when it went in cleanly;
        {"ok": False, "conflicts": [paths]} when conflict markers were left in the folder for someone
        to resolve (merge_finish) or to give up on (merge_abort); {"ok": False, "error": ...} otherwise."""
        if not self.ok:
            return {"ok": False, "error": "no repository"}
        try:
            self._git("add", "-A", "--", ".")
            self._git("commit", "-q", "--allow-empty", "-m", f"before {message}")
            proc = self._git("merge", "--no-ff", "--no-edit", "-m", message, branch, check=False)
            if proc.returncode == 0:
                return {"ok": True}
            conflicts = [p for p in self._git("diff", "--name-only", "--diff-filter=U").stdout.splitlines() if p]
            if conflicts:
                return {"ok": False, "conflicts": conflicts}
            self._git("merge", "--abort", check=False)
            return {"ok": False, "error": (proc.stderr or proc.stdout).strip()[:300]}
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            self._git("merge", "--abort", check=False)
            return {"ok": False, "error": str(exc)[:300]}

    def unresolved(self, paths: List[str]) -> List[str]:
        """Which of these files still carry conflict markers."""
        left = []
        for p in paths:
            try:
                text = (Path(self.workspace) / p).read_text("utf-8", errors="replace")
            except OSError:
                continue
            if "<<<<<<< " in text and ">>>>>>> " in text:
                left.append(p)
        return left

    @_locked
    def merge_finish(self, message: str) -> Optional[str]:
        try:
            self._git("add", "-A", "--", ".")
            self._git("commit", "-q", "--allow-empty", "-m", message)
            return self._git("rev-parse", "HEAD").stdout.strip()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            return None

    @_locked
    def merge_abort(self) -> None:
        self._git("merge", "--abort", check=False)

    @_locked
    def prune(self) -> None:
        self._git("worktree", "prune", check=False)

    @_locked
    def worktree_remove(self, path: str, branch: str) -> None:
        self._git("worktree", "remove", "--force", path, check=False)
        self._git("branch", "-D", branch, check=False)
        self._git("worktree", "prune", check=False)
        if Path(path).exists():
            shutil.rmtree(path, ignore_errors=True)
