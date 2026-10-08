"""Event hub: every run is an append-only list of events, persisted as JSONL
and fanned out to SSE subscribers. The UI rebuilds its whole state from these
events, so live view and history replay use the same code path."""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .config import RUNS_DIR


class Run:
    def __init__(self, hub: "Hub", prompt: str, workspace: str, settings: Dict[str, Any]) -> None:
        self.hub = hub
        self.id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.prompt = prompt
        self.workspace = workspace
        self.settings = settings
        self.created = time.time()
        self.status = "running"
        self.events: List[Dict[str, Any]] = []
        self.dir = RUNS_DIR / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._file = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        self.orchestrator: Any = None
        self._write_meta()

    def emit(self, kind: str, agent: str = "system", **data: Any) -> Dict[str, Any]:
        return self.hub.publish(self, kind, agent, data)

    def finish(self, status: str) -> None:
        self.status = status
        self._write_meta()
        try:
            self._file.close()
        except OSError:
            pass

    def meta(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "workspace": self.workspace,
            "created": self.created,
            "status": self.status,
            "demo": bool(self.settings.get("demo")),
        }

    def _write_meta(self) -> None:
        (self.dir / "meta.json").write_text(json.dumps(self.meta(), ensure_ascii=False), "utf-8")


class Hub:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.subscribers: List["queue.Queue[Optional[Dict[str, Any]]]"] = []
        self.current: Optional[Run] = None
        self.chat: Any = None  # chat.ChatThread

    def publish(self, run: Run, kind: str, agent: str, data: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            event = {"seq": len(run.events), "ts": time.time(), "run": run.id, "kind": kind, "agent": agent}
            event.update(data)
            run.events.append(event)
            if not run._file.closed:
                run._file.write(json.dumps(event, ensure_ascii=False) + "\n")
                run._file.flush()
            for q in list(self.subscribers):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    # A stalled browser tab; drop it rather than block the agents.
                    self.subscribers.remove(q)
            return event

    def subscribe(self) -> Tuple["queue.Queue[Optional[Dict[str, Any]]]", List[Dict[str, Any]]]:
        q: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue(maxsize=20000)
        with self.lock:
            snapshot = list(self.current.events) if self.current else []
            if self.chat is not None:
                snapshot += list(self.chat.events)
            self.subscribers.append(q)
        return q, snapshot

    def unsubscribe(self, q: "queue.Queue[Optional[Dict[str, Any]]]") -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def active(self) -> Optional[Run]:
        run = self.current
        return run if run and run.status == "running" else None


def mark_interrupted() -> int:
    """Runs still marked running belong to a previous server process that died."""
    count = 0
    if not RUNS_DIR.exists():
        return count
    for meta_file in RUNS_DIR.glob("*/meta.json"):
        try:
            meta = json.loads(meta_file.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        if meta.get("status") == "running":
            meta["status"] = "interrupted"
            meta_file.write_text(json.dumps(meta, ensure_ascii=False), "utf-8")
            count += 1
    return count


def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    runs = []
    if not RUNS_DIR.exists():
        return runs
    for meta_file in sorted(RUNS_DIR.glob("*/meta.json"), reverse=True)[:limit]:
        try:
            runs.append(json.loads(meta_file.read_text("utf-8")))
        except (OSError, ValueError):
            continue
    return runs


def load_events(run_id: str) -> Optional[List[Dict[str, Any]]]:
    if not run_id.replace("-", "").isalnum():
        return None
    path = RUNS_DIR / run_id / "events.jsonl"
    if not path.exists():
        return None
    events = []
    for line in path.read_text("utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events

