"""Workspace snapshots, used to show exactly what each job changed.

Both CLIs report edits differently (Claude via tool calls, Codex via
file_change items, and either may write files through shell commands), so the
reliable source of truth is comparing the folder before and after a job."""
from __future__ import annotations

import difflib
import os
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .i18n import t

IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next",
    ".nuxt", ".cache", ".idea", ".vscode", "target", ".turbo", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "coverage", ".gradle", "Pods", "DerivedData", ".svelte-kit", ".parcel-cache",
}
MAX_FILES = 20000
MAX_TEXT_BYTES = 256 * 1024
MAX_DIFF_CHARS_PER_FILE = 12000
MAX_DIFF_CHARS_TOTAL = 60000

Meta = Tuple[int, int]  # (size, mtime_ns)

_cache_lock = threading.Lock()
_content_cache: Dict[Tuple[str, int, int], Optional[str]] = {}


def _read_text(path: Path, meta: Meta) -> Optional[str]:
    key = (str(path), meta[0], meta[1])
    with _cache_lock:
        if key in _content_cache:
            return _content_cache[key]
    text: Optional[str] = None
    if meta[0] <= MAX_TEXT_BYTES:
        try:
            raw = path.read_bytes()
            if b"\x00" not in raw[:8000]:
                text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            text = None
    with _cache_lock:
        if len(_content_cache) > 60000:
            _content_cache.clear()
        _content_cache[key] = text
    return text


class Snapshot:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.meta: Dict[str, Meta] = {}
        self.text: Dict[str, Optional[str]] = {}
        self.truncated = False
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".pixelcrew")]
            for name in filenames:
                if name == ".DS_Store":
                    continue
                full = Path(dirpath) / name
                try:
                    st = full.stat()
                except OSError:
                    continue
                rel = str(full.relative_to(root))
                m = (st.st_size, st.st_mtime_ns)
                self.meta[rel] = m
                self.text[rel] = _read_text(full, m)
                if len(self.meta) >= MAX_FILES:
                    self.truncated = True
                    return

    def diff(self, after: "Snapshot", only: Optional[Iterable[str]] = None) -> List[Dict[str, object]]:
        keep = None
        if only:
            keep = set()
            for p in only:
                try:
                    keep.add(str(Path(p).resolve().relative_to(self.root.resolve())) if os.path.isabs(p) else p)
                except ValueError:
                    continue
        changed: List[Dict[str, object]] = []
        paths = sorted(set(self.meta) | set(after.meta))
        budget = MAX_DIFF_CHARS_TOTAL
        for rel in paths:
            before_m, after_m = self.meta.get(rel), after.meta.get(rel)
            if before_m == after_m:
                continue
            if keep is not None and rel not in keep:
                continue
            status = "added" if before_m is None else "deleted" if after_m is None else "modified"
            old = self.text.get(rel) if before_m else ""
            new = after.text.get(rel) if after_m else ""
            if status == "modified" and old == new:
                continue  # touched but identical
            entry: Dict[str, object] = {"path": rel, "status": status, "added": 0, "removed": 0}
            if old is None or new is None:
                entry["binary"] = True
            else:
                lines = list(difflib.unified_diff(
                    old.splitlines(), new.splitlines(), f"a/{rel}", f"b/{rel}", lineterm="", n=2
                ))
                entry["added"] = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
                entry["removed"] = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
                text = "\n".join(lines)
                limit = min(MAX_DIFF_CHARS_PER_FILE, max(budget, 0))
                if len(text) > limit:
                    text = text[:limit] + t("diff.truncated")
                budget -= len(text)
                entry["diff"] = text
            changed.append(entry)
        return changed


def diff_as_prompt(files: List[Dict[str, object]], limit: int = 30000) -> str:
    """Render a job's changes for a reviewer prompt."""
    if not files:
        return "(No file changes were detected in the workspace.)"
    parts = []
    for f in files:
        if f.get("binary"):
            parts.append(f"# {f['status']}: {f['path']} (binary or large file, not shown)")
        else:
            parts.append(str(f.get("diff") or f"# {f['status']}: {f['path']}"))
    text = "\n".join(parts)
    if len(text) > limit:
        text = text[:limit] + "\n... (diff truncated; read the files directly for the rest)"
    return text
