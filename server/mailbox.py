"""The server's side of a job's mailbox.

A job is one CLI process, often inside a sandbox that allows no network, so the helper commands
it can run (pixelcrew-ask, pixelcrew-browser; see server/bin) talk to PixelCrew through files:
a request is `<id>.req.json` in the job's mailbox folder, the reply `<id>.res.json`. One Mailbox
watches one job's folder while the job runs and hands every request to the orchestrator's
handler; the folder lives where that job's sandbox can write.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

TMP_ROOT = Path("/private/tmp/pixelcrew")
Handler = Callable[[Dict[str, Any]], Dict[str, Any]]


def location(agent: str, mode: str, cwd: str, run_id: str, key: str) -> Optional[Path]:
    """Where a job's mailbox can go, or None when the agent's sandbox for this mode allows no
    writes at all (Codex and Grok reviews are read-only everywhere)."""
    if mode == "work":  # every CLI may write inside its working folder
        return Path(cwd) / ".pixelcrew" / "mail" / key
    if agent in ("claude", "gemini"):  # no sandbox / a sandbox that only locks the folder
        return TMP_ROOT / run_id / key
    return None


class Mailbox:
    def __init__(self, path: Path, handler: Handler) -> None:
        self.path = path
        self.handler = handler
        self.seen: set = set()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def open(self) -> bool:
        try:
            self.path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False
        self._thread = threading.Thread(target=self._watch, daemon=True, name=f"mailbox-{self.path.name}")
        self._thread.start()
        return True

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        # the folder is only for this job; in a working folder, leave nothing behind
        shutil.rmtree(self.path, ignore_errors=True)
        parent = self.path.parent
        for _ in range(2):  # .pixelcrew/mail, then .pixelcrew
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def _watch(self) -> None:
        while not self._stop.is_set():
            try:
                names = sorted(os.listdir(self.path))
            except OSError:
                names = []
            for name in names:
                if not name.endswith(".req.json") or name in self.seen:
                    continue
                self.seen.add(name)
                threading.Thread(target=self._serve, args=(name,), daemon=True).start()
            self._stop.wait(0.5)

    def _serve(self, name: str) -> None:
        rid = name[: -len(".req.json")]
        try:
            req = json.loads((self.path / name).read_text("utf-8"))
        except (OSError, ValueError):
            req = {}
        try:
            reply = self.handler(req if isinstance(req, dict) else {})
        except Exception as exc:  # the job must get an answer, whatever broke
            reply = {"ok": False, "text": f"{type(exc).__name__}: {exc}"}
        tmp = self.path / f"{rid}.tmp"
        try:
            tmp.write_text(json.dumps(reply, ensure_ascii=False), "utf-8")
            os.replace(tmp, self.path / f"{rid}.res.json")
        except OSError:
            pass


def cleanup_tmp(run_id: str) -> None:
    shutil.rmtree(TMP_ROOT / run_id, ignore_errors=True)
