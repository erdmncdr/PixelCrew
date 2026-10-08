"""Ask-the-team chat. Questions go to one or more agents in read-only mode;
each agent keeps its own CLI session so follow-ups continue the same
conversation, and each sees what its teammates answered last time.

Chat runs alongside task runs: a question asked while the team is working is
answered by a separate read-only CLI process with the run's status as
context, so it never interrupts the work."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from . import effort as effort_policy
from . import models
from . import prompts
from .agents import AgentResult, Cancelled, make_agent
from .config import DATA_DIR
from .demo import DemoAgent
from .orchestrator import team_of
from .i18n import t

CHATS_DIR = DATA_DIR / "chats"


class ChatThread:
    """Duck-types bus.Run so Hub.publish can store and broadcast its events."""

    def __init__(self, hub: Any, workspace: str) -> None:
        self.hub = hub
        self.id = "chat-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.workspace = workspace
        self.events: List[Dict[str, Any]] = []
        self.dir = CHATS_DIR / self.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._file = open(self.dir / "events.jsonl", "a", encoding="utf-8")
        self.sessions: Dict[str, str] = {}
        self.last_answer: Dict[str, str] = {}
        self.turns = 0

    def emit(self, kind: str, agent: str = "system", **data: Any) -> Dict[str, Any]:
        return self.hub.publish(self, kind, agent, data)

    def close(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass


class ChatManager:
    def __init__(self, hub: Any) -> None:
        self.hub = hub
        self.lock = threading.Lock()
        self.thread: Optional[ChatThread] = None
        self.busy = False
        self.agents: Dict[str, Any] = {}

    def reset(self) -> None:
        with self.lock:
            if self.busy:
                raise ValueError(t("chat.busy_reset"))
            if self.thread:
                self.thread.emit("chat.reset")
                self.thread.close()
            self.thread = None
            self.hub.chat = None

    def cancel(self) -> None:
        for agent in list(self.agents.values()):
            agent.cancel()

    def ask(self, question: str, targets: List[str], settings: Dict[str, Any], health: Dict[str, Any],
            binaries: Dict[str, Optional[str]], workspace: str, run_context: Optional[str], note: str = "") -> str:
        demo = bool(settings.get("demo"))
        team = team_of(settings)
        available = list(team) if demo else [a for a in team if health.get(a, {}).get("ready")]
        chosen = [a for a in (targets or team) if a in available]
        if not chosen:
            raise ValueError(t("chat.not_ready"))
        with self.lock:
            if self.busy:
                raise ValueError(t("chat.busy"))
            if self.thread is None or self.thread.workspace != workspace:
                if self.thread:
                    self.thread.close()
                self.thread = ChatThread(self.hub, workspace)
                self.hub.chat = self.thread
                self.thread.emit("chat.started", workspace=workspace)
            self.busy = True
            thread = self.thread
        qid = uuid.uuid4().hex[:8]
        thread.emit("chat.question", qid=qid, text=question, to=chosen, note=note)
        worker = threading.Thread(
            target=self._answer_all,
            args=(thread, qid, question, chosen, settings, binaries, demo, run_context),
            daemon=True,
        )
        worker.start()
        return qid

    # ---------------------------------------------------------------------
    def _answer_all(self, thread: ChatThread, qid: str, question: str, chosen: List[str], settings: Dict[str, Any],
                    binaries: Dict[str, Optional[str]], demo: bool, run_context: Optional[str]) -> None:
        first_turn = thread.turns == 0
        previous = dict(thread.last_answer)
        level = effort_policy.pick("chat", settings)
        workers = []
        for name in chosen:
            if demo:
                agent: Any = DemoAgent(name, thread, settings)
            else:
                agent = make_agent(name, thread, settings, binaries.get(name))
            self.agents[name] = agent
            mates = [a for a in dict.fromkeys(chosen + list(thread.sessions)) if a != name]
            prompt = prompts.chat(name, mates, thread.workspace, question,
                                  first_turn or name not in thread.sessions,
                                  previous, run_context)
            kind = "agy" if (binaries.get(name) or "").endswith("/agy") else ""
            model, _ = (None, None) if demo else models.pick(name, "chat", settings, None, kind)
            th = threading.Thread(target=self._answer_one, args=(thread, qid, name, agent, prompt, level, model),
                                  daemon=True)
            workers.append(th)
            th.start()
        for th in workers:
            th.join()
        thread.turns += 1
        self.agents.clear()
        with self.lock:
            self.busy = False
        thread.emit("chat.idle", qid=qid)

    def _answer_one(self, thread: ChatThread, qid: str, name: str, agent: Any, prompt: str, level: Optional[str],
                    model: Optional[str] = None) -> None:
        thread.emit("job.started", agent=name, job="chat", task_id=None, qid=qid, effort=level, model=model,
                    tier="standard" if model else None)
        resume = thread.sessions.get(name)
        try:
            result = agent.execute(prompt, "chat", thread.workspace, None, "chat", resume=resume, extra={"qid": qid},
                                   model=model,
                                   effort=level)
            if not result.ok and resume:
                # The stored session may be gone (CLI cleaned up); start fresh once.
                thread.emit("agent.stderr", agent=name, text=t("chat.resume_failed"), job="chat", qid=qid)
                result = agent.execute(prompt, "chat", thread.workspace, None, "chat", extra={"qid": qid}, effort=level,
                                       model=model)
        except Cancelled:
            result = AgentResult(False, "", "Durduruldu.")
        except Exception as exc:
            result = AgentResult(False, "", f"{type(exc).__name__}: {exc}")
        if result.ok and result.session:
            thread.sessions[name] = result.session
        if result.ok and result.text:
            thread.last_answer[name] = result.text
        thread.emit("job.finished", agent=name, job="chat", task_id=None, qid=qid, ok=result.ok,
                    text=result.text[-6000:], error=result.error, duration=round(result.duration, 1))
        thread.emit("chat.answer", agent=name, qid=qid, ok=result.ok, text=result.text, error=result.error)

