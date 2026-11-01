"""The team workflow:

  planning     the lead agent inspects the workspace and splits the request into tasks
  plan_review  another agent critiques (and may revise) the plan
  approval     optional pause until the user approves / reassigns tasks in the UI
  executing    tasks run in dependency order; the team's agents work in parallel,
               each finished task is reviewed by a different agent, and requested
               changes go back to the author for a fix round
  done         summary

Task status flow:
  todo -> working -> awaiting_review -> reviewing -> (changes_requested -> fixing ->)* done
  any step can end in failed; tasks whose dependency failed become blocked.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import browser
from . import effort as effort_policy
from . import mailbox
from . import models
from . import prompts
from .agents import AgentResult, BaseAgent, Cancelled, make_agent
from .config import AGENTS, DATA_DIR
from .i18n import t as tr
from . import i18n
from .demo import DemoAgent
from .quota import NAME as AGENT_NAME
from .quota import ORDER, TO, QuotaGuard
from .snapshot import Snapshot, diff_as_prompt
from .bus import recent_runs
from .checkpoint import Checkpoints

TERMINAL = {"done", "failed", "blocked", "cancelled"}
MAX_ASKS = 3  # questions one task may put to teammates (per job chain)


def _short(text: Any, limit: int = 160) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def team_of(settings: Dict[str, Any]) -> List[str]:
    """The agents the user put on the team, in registry order."""
    chosen = settings.get("team") or ["claude", "codex"]
    return [a for a in AGENTS if a in chosen] or ["claude", "codex"]


def names(agents: List[str]) -> str:
    """Claude, Codex and Gemini"""
    return i18n.names([AGENT_NAME[a] for a in agents])


def extract_json(text: str, required_key: str) -> Optional[Dict[str, Any]]:
    """Find the first JSON object in an agent reply that contains required_key."""
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text or "", re.S)
    for c in candidates:
        try:
            data = json.loads(c)
            if isinstance(data, dict) and required_key in data:
                return data
        except ValueError:
            continue
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text or ""):
        try:
            data, _ = decoder.raw_decode(text[m.start():])
        except ValueError:
            continue
        if isinstance(data, dict) and required_key in data:
            return data
    return None


class Answered(Exception):
    """The request was conversation, not work; the planner replied directly."""


class Orchestrator(threading.Thread):
    def __init__(self, run: Any, settings: Dict[str, Any], health: Dict[str, Any], binaries: Dict[str, Optional[str]]) -> None:
        super().__init__(daemon=True, name=f"orchestrator-{run.id}")
        self.run_ = run
        self.settings = settings
        self.workspace = run.workspace
        self.cancel_event = threading.Event()
        self.approval_event = threading.Event()
        self.overrides: Dict[str, str] = {}
        self.effort_overrides: Dict[str, str] = {}
        self.cv = threading.Condition()
        self.team = team_of(settings)
        self.busy: Dict[str, Optional[str]] = {a: None for a in self.team}
        self.tasks: List[Dict[str, Any]] = []
        self.plan: Dict[str, Any] = {}
        self.usage: Dict[str, Dict[str, float]] = {a: {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0} for a in self.team}
        self.guard = QuotaGuard(settings)
        self._quota_checked = 0.0
        self.cp: Optional[Checkpoints] = None  # the git safety net; None in demo or when git is missing
        # tasks build in their own git worktrees of the safety net and are merged when approved
        self.isolate = bool(settings.get("isolateTasks", True))
        self.wt_root = DATA_DIR / "worktrees" / run.id
        self.merge_lock = threading.RLock()  # one merge (or checkpoint) of the folder at a time
        self._wt_warned = False
        # agents may ask each other questions and try web UIs in a headless browser (server/bin)
        self.questions = bool(settings.get("teamQuestions", True))
        self.browser_ok = bool(settings.get("browserCheck", True)) and not settings.get("demo") and browser.available()
        self.side_agents: List[BaseAgent] = []  # extra CLI processes: answers and merges
        self.answer_locks: Dict[str, threading.Lock] = {a: threading.Lock() for a in self.team}

        if settings.get("demo"):
            self.available = list(self.team)
            self.agents: Dict[str, BaseAgent] = {a: DemoAgent(a, run, settings) for a in self.team}
        else:
            self.available = [a for a in self.team if health.get(a, {}).get("ready")]
            self.agents = {a: make_agent(a, run, settings, binaries.get(a)) for a in self.team}
        self.health = health
        self.binaries = {} if settings.get("demo") else dict(binaries)

    # -- helpers -----------------------------------------------------------
    def emit(self, kind: str, **data: Any) -> None:
        self.run_.emit(kind, **data)

    def phase(self, name: str) -> None:
        self.emit("run.phase", phase=name)

    def notice(self, text: str, level: str = "info") -> None:
        self.emit("run.notice", level=level, text=text)

    def load(self, agent: str, tasks: Optional[List[Dict[str, Any]]] = None) -> int:
        """Unfinished work on an agent's plate: tasks it builds plus tasks it reviews."""
        open_tasks = [t for t in (self.tasks if tasks is None else tasks) if t.get("status") not in TERMINAL]
        return sum(2 for t in open_tasks if t.get("assignee") == agent) + \
            sum(1 for t in open_tasks if t.get("reviewer") == agent)

    def role(self, agent: str) -> str:
        return models.role(agent, self.settings)

    def other(self, agent: str, tasks: Optional[List[Dict[str, Any]]] = None, skip_empty: bool = False,
              senior: int = 0, exclude: Optional[set] = None) -> str:
        """The best teammate to take over from (or check) `agent`: the most quota
        left first, then the least loaded, then the next one in team order, so
        that with two agents Claude and Codex simply pair up. senior=N counts a
        worker as N load units busier, so seniors win unless they are swamped."""
        others = [a for a in self.available if a != agent and a not in (exclude or ())]
        if skip_empty:
            others = [a for a in others if self.guard.level(a) != "empty"]
        if not others:
            return agent
        order = self.team + [a for a in self.available if a not in self.team]
        start = order.index(agent) if agent in order else -1

        def rank(a: str) -> Any:
            quota = ORDER[self.guard.level(a)] if self.guard.enabled else 0
            penalty = senior if self.role(a) != "senior" else 0
            return quota, self.load(a, tasks) + penalty, (order.index(a) - start) % len(order)

        return min(others, key=rank)

    def check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled()

    def cancel(self) -> None:
        self.cancel_event.set()
        self.approval_event.set()
        for agent in list(self.agents.values()) + list(self.side_agents):
            agent.cancel()
        with self.cv:
            self.cv.notify_all()

    def approve(self, overrides: Dict[str, str], efforts: Optional[Dict[str, str]] = None) -> None:
        if self.approval_event.is_set():
            raise ValueError(tr("plan.already_approved"))
        self.overrides = {k: v for k, v in overrides.items() if v in self.available}
        self.effort_overrides = {k: v for k, v in (efforts or {}).items() if v in effort_policy.LEVELS}
        self.approval_event.set()

    def effort(self, job: str, task: Optional[Dict[str, Any]] = None, agent: Optional[str] = None) -> Optional[str]:
        level = effort_policy.pick(job, self.settings, task)
        user_chose = job in ("implement", "fix") and (task or {}).get("effort")
        if level and agent and self.guard.enabled and not user_chose:
            q = self.guard.level(agent)
            # "tight" already costs the agent a model tier (models.pick); taking the effort down as
            # well left medium tasks on the fast model at low effort. Only "critical" does both.
            if q in ("critical", "empty"):
                level = "low"
        return level

    def model_for(self, agent: str, job: str, task: Optional[Dict[str, Any]] = None) -> Any:
        """(model, tier) for a job: models.py picks by job and task difficulty, one tier lower
        when the agent is running out of quota."""
        binary = self.binaries.get(agent) or ""
        kind = "agy" if Path(binary).name == "agy" else ""
        level = self.guard.level(agent) if self.guard.enabled else "ok"
        return models.pick(agent, job, self.settings, task, kind, level)

    def call(self, agent: str, prompt: str, mode: str, task_id: Optional[str], job: str,
             effort: Optional[str] = None, resume: Optional[str] = None, cwd: Optional[str] = None,
             instance: Optional[BaseAgent] = None) -> AgentResult:
        """One CLI job. cwd: the task's worktree when it has one; instance: a separate agent process
        for side jobs (answers, merges) while the agent's main process may be busy."""
        task = next((t for t in self.tasks if t["id"] == task_id), None)
        model, tier = self.model_for(agent, job, task)
        inst = instance or self.agents[agent]
        cwd = cwd or self.workspace
        self.emit("job.started", agent=agent, job=job, task_id=task_id, effort=effort, resumed=bool(resume),
                  model=model, tier=tier)
        box = self.mailbox_open(agent, mode, job, task_id, cwd, inst)
        try:
            result = inst.execute(prompt, mode, cwd, task_id, job, resume=resume, effort=effort, model=model)
            if not result.ok and resume and not self.cancel_event.is_set():
                # The earlier session could not be continued; start a fresh one.
                self.emit("agent.stderr", agent=agent, task_id=task_id, job=job,
                          text=tr("session.resume_failed"))
                result = inst.execute(prompt, mode, cwd, task_id, job, effort=effort, model=model)
        except Cancelled:
            raise
        except Exception as exc:  # unexpected launcher failure (missing binary, etc.)
            result = AgentResult(False, "", f"{type(exc).__name__}: {exc}")
        finally:
            self.mailbox_close(box)
        for key in ("input_tokens", "output_tokens", "cost_usd"):
            self.usage[agent][key] += float(result.usage.get(key) or 0)
        self.emit(
            "job.finished", agent=agent, job=job, task_id=task_id, ok=result.ok,
            text=result.text[-6000:], error=result.error, duration=round(result.duration, 1),
        )
        return result

    def emit_task(self, task: Dict[str, Any]) -> None:
        self.emit("task.update", task={k: v for k, v in task.items() if not k.startswith("_")})

    def spawn_agent(self, name: str) -> BaseAgent:
        """A second process for an agent, for a side job while its main process is busy."""
        inst = DemoAgent(name, self.run_, self.settings) if self.settings.get("demo") \
            else make_agent(name, self.run_, self.settings, self.binaries.get(name))
        self.side_agents.append(inst)
        return inst

    # -- team tools: questions between agents and the headless browser --------------------------
    def tools_for(self, agent: str, mode: str, job: str, cwd: str, task_id: Optional[str] = None) -> Dict[str, bool]:
        """Which helper commands this job gets (they need a mailbox its sandbox can write)."""
        if self.settings.get("demo") or mailbox.location(agent, mode, cwd, self.run_.id, "x") is None:
            return {"ask": False, "browser": False}
        mates = [a for a in self.available if a != agent]
        return {"ask": self.questions and job in ("implement", "fix") and bool(mates),
                "browser": self.browser_ok and job in ("implement", "fix", "review", "integrate", "merge")}

    def mailbox_open(self, agent: str, mode: str, job: str, task_id: Optional[str], cwd: str,
                     inst: BaseAgent) -> Optional[mailbox.Mailbox]:
        inst.env_extra = {}
        tools = self.tools_for(agent, mode, job, cwd, task_id)
        if not any(tools.values()):
            return None
        key = f"{job}-{task_id or 'run'}-{uuid.uuid4().hex[:6]}"
        loc = mailbox.location(agent, mode, cwd, self.run_.id, key)
        if loc is None:
            return None
        svc = browser.BrowserService(loc / "shots") if tools["browser"] else None
        box = mailbox.Mailbox(loc, lambda req: self.mail_request(agent, task_id, tools, svc, req))
        if not box.open():
            return None
        box.svc = svc  # type: ignore[attr-defined]
        inst.env_extra = {"PIXELCREW_MAILBOX": str(loc), "PIXELCREW_AGENT": agent, "PIXELCREW_TASK": task_id or "",
                          "PIXELCREW_MATES": ",".join(a for a in self.available if a != agent)}
        return box

    @staticmethod
    def mailbox_close(box: Optional[mailbox.Mailbox]) -> None:
        if box is None:
            return
        svc = getattr(box, "svc", None)
        if svc:
            svc.close()
        box.close()

    def mail_request(self, agent: str, task_id: Optional[str], tools: Dict[str, bool],
                     svc: Optional[browser.BrowserService], req: Dict[str, Any]) -> Dict[str, Any]:
        kind, args = req.get("kind"), req.get("args") or {}
        if kind == "ask" and tools.get("ask"):
            return self.answer(agent, task_id, args if isinstance(args, dict) else {})
        if kind == "browser" and tools.get("browser") and svc:
            return svc.handle(args if isinstance(args, dict) else {})
        return {"ok": False, "text": "This tool is not available in this job."}

    def answer(self, asker: str, task_id: Optional[str], args: Dict[str, Any]) -> Dict[str, Any]:
        """A teammate answers one question from a running job, in a separate read-only process."""
        t = next((x for x in self.tasks if x["id"] == task_id), None)
        if t is not None and t.get("_asks", 0) >= MAX_ASKS:
            return {"ok": False, "text": tr("qa.budget", n=MAX_ASKS)}
        question = " ".join(str(args.get("question") or "").split())[:4000]
        if not question:
            return {"ok": False, "text": "Empty question."}
        to = str(args.get("to") or "").strip().lower()
        by_task = next((x["assignee"] for x in self.tasks if x["id"].lower() == to), None)
        target = to if to in self.available else by_task if by_task in self.available else self.pick_lead(announce=False)
        if target == asker:
            target = self.other(asker)
        if target == asker or target not in self.available:
            return {"ok": False, "text": tr("qa.nobody")}
        if t is not None:
            t["_asks"] = t.get("_asks", 0) + 1
        qid = uuid.uuid4().hex[:8]
        self.emit("qa.asked", agent=asker, task_id=task_id, to=target, question=question, qid=qid)
        cwd = self.cwd_of(target)
        prompt = prompts.answer(target, asker, question, self.run_.prompt, self.plan, self.tasks, cwd, task_id)
        with self.answer_locks.get(target, threading.Lock()):
            res = self.call(target, prompt, "chat", task_id, "answer", effort=self.effort("answer", agent=target),
                            cwd=cwd, instance=self.spawn_agent(target))
        text = res.text.strip() if res.ok else ""
        self.emit("qa.answered", agent=target, task_id=task_id, to=asker, qid=qid, ok=bool(text),
                  text=text[-6000:] if text else res.error, duration=round(res.duration, 1))
        if res.quota:
            self.guard.mark_exhausted(target)
            self.guard.read()
            self.publish_quota()
        return {"ok": bool(text), "text": text or tr("qa.failed", name=AGENT_NAME[target], err=_short(res.error))}

    def cwd_of(self, agent: str) -> str:
        """Where an agent's work is right now: its task's worktree while it builds, else the folder."""
        tid = self.busy.get(agent)
        t = next((x for x in self.tasks if x["id"] == tid), None) if tid else None
        return (t or {}).get("_wt") or self.workspace

    # -- main ----------------------------------------------------------------
    def run(self) -> None:
        status = "done"
        started = time.time()
        try:
            self.emit(
                "run.started", prompt=self.run_.prompt, workspace=self.workspace,
                settings=self.settings, available=self.available,
                health={a: {"ready": h.get("ready"), "problem": h.get("problem"), "fix": h.get("fix")}
                        for a, h in self.health.items()},
            )
            if not self.available:
                self.notice(tr("run.no_agents"), "error")
                status = "failed"
                return
            missing = [a for a in self.team if a not in self.available]
            if missing:
                rest = (tr("run.alone", name=AGENT_NAME[self.available[0]]) if len(self.available) == 1
                        else tr("run.shared", names=names(self.available)))
                self.notice(tr("run.missing", names=names(missing), rest=rest), "warn")
            self.quota_start()
            self.checkpoint_start()
            try:
                self.make_plan()
            except Answered:
                status = "answered"
                return
            self.check_cancel()
            if self.settings.get("approvePlan"):
                self.phase("approval")
                self.emit("approval.waiting")
                self.approval_event.wait()
                self.check_cancel()
                for t in self.tasks:
                    if t["id"] in self.overrides:
                        t["assignee"] = self.overrides[t["id"]]
                        t["reviewer"] = self.reviewer_for(t["assignee"], task=t)
                    if t["id"] in self.effort_overrides:
                        t["effort"] = self.effort_overrides[t["id"]]
            self.emit("plan.final", plan=self.public_plan())
            for t in self.tasks:
                self.emit_task(t)
            self.phase("executing")
            self.execute()
            failed = [t for t in self.tasks if t["status"] in ("failed", "blocked")]
            status = "failed" if failed and len(failed) == len(self.tasks) else "done"
            if status == "done" and self.settings.get("finalCheck", True) and len(self.tasks) > 1:
                self.check_cancel()
                self.integrate()
            self.checkpoint("end", None, "run finished")
        except Cancelled:
            status = "cancelled"
            for t in self.tasks:
                if t["status"] not in TERMINAL:
                    t["status"] = "cancelled"
                    self.emit_task(t)
        except Exception as exc:
            status = "failed"
            self.notice(tr("run.unexpected", err=exc), "error")
            self.emit("run.error", trace=traceback.format_exc()[-3000:])
        finally:
            self.cleanup()
            self.phase(status)
            self.emit("run.finished", status=status, duration=round(time.time() - started, 1),
                      usage=self.usage, tasks=[self._task_summary(t) for t in self.tasks])
            self.run_.finish(status)

    def cleanup(self) -> None:
        """Worktrees of tasks that never finished, and the mailboxes under /tmp."""
        for t in self.tasks:
            if t.get("_wt") and self.cp:
                self.cp.worktree_remove(t.pop("_wt"), t.pop("_branch", ""))
        if self.cp and self.isolate:
            self.cp.prune()
        if self.wt_root.exists():
            shutil.rmtree(self.wt_root, ignore_errors=True)
        mailbox.cleanup_tmp(self.run_.id)

    # -- git safety net -------------------------------------------------------------
    def checkpoint_start(self) -> None:
        if self.settings.get("demo"):
            return
        cp = Checkpoints(self.workspace)
        if not cp.open():
            self.notice(tr("cp.unavailable"))
            return
        self.cp = cp
        self.checkpoint("start", None, f"run start: {self.run_.prompt[:60]}")

    def checkpoint(self, stage: str, task_id: Optional[str], message: str) -> Optional[str]:
        """Snapshot the folder now; the commit id goes into the run's events for the undo buttons."""
        if not self.cp:
            return None
        with self.merge_lock:
            commit = self.cp.take(message)
        if commit:
            self.emit("checkpoint", stage=stage, task_id=task_id, commit=commit)
        return commit

    def clear_partial_edits(self, t: Dict[str, Any], res: AgentResult) -> None:
        """A job failed halfway: put the task's files back as they were before it started, so the
        retry or the teammate starts from a clean state instead of someone's half-written code."""
        if not self.cp:
            return
        if t.get("_wt"):
            if t.get("_wt_base") and self.cp.worktree_reset(t["_wt"], t["_wt_base"]):
                self.notice(tr("cp.cleared_wt", id=t["id"]))
            return
        before = t.get("_cp")
        if not before:
            return
        paths = sorted(set(t.get("files") or []) | set(res.changed or []))
        touched = self.cp.restore(before, only=paths or None, message=f"clear {t['id']} after a failed job")
        if touched:
            self.notice(tr("cp.cleared", id=t["id"], n=len(touched)))

    # -- task worktrees ------------------------------------------------------------------
    def task_worktree(self, t: Dict[str, Any], kind: str) -> Optional[str]:
        """The task's own copy of the folder: created (or reset) for an implement job, kept for a
        fix. None when isolation is off or the worktree could not be made (the task then shares
        the folder like before)."""
        if not (self.cp and self.isolate and t.get("_cp")):
            return None
        path, branch = str(self.wt_root / t["id"]), f"task/{self.run_.id}/{t['id']}"
        if kind == "implement":
            ok = self.cp.worktree_reset(path, t["_cp"]) if t.get("_wt") else self.cp.worktree_add(path, t["_cp"], branch)
            if not ok:
                t.pop("_wt", None)
                if not self._wt_warned:
                    self._wt_warned = True
                    self.notice(tr("wt.unavailable"), "warn")
                return None
            t["_wt"], t["_branch"], t["_wt_base"] = path, branch, t["_cp"]
            return path
        if t.get("_wt"):
            t["_wt_base"] = self.cp.worktree_commit(t["_wt"], f"before {t['id']} ({kind})") or t.get("_wt_base")
        return t.get("_wt")

    def complete(self, t: Dict[str, Any]) -> None:
        """A task is accepted: its worktree is merged into the folder first, so a dependent task
        that starts next sees the result; then the task checkpoint is taken."""
        if t.get("_wt"):
            self.merge_task(t)
            if t["status"] == "failed":
                return
        t["status"] = "done"
        self.checkpoint("task", t["id"], f"{t['id']} done: {t['title'][:60]}")

    def merge_task(self, t: Dict[str, Any]) -> None:
        assert self.cp is not None
        path, branch = t.pop("_wt"), t.pop("_branch", "")
        t.pop("_wt_base", None)
        with self.merge_lock:
            self.cp.worktree_commit(path, f"{t['id']}: {t['title'][:60]}")
            result = self.cp.merge(branch, f"merge {t['id']}")
            conflicts = [str(p) for p in (result.get("conflicts") or [])]
            try:
                if result.get("ok"):
                    self.emit("task.merged", task_id=t["id"], agent=t["assignee"])
                elif conflicts:
                    self.resolve_conflicts(t, conflicts)
                else:
                    t["status"], t["error"] = "failed", tr("wt.merge_failed", id=t["id"], err=str(result.get("error") or ""))
                    self.notice(t["error"], "error")
            except Cancelled:
                self.cp.merge_abort()  # stopped mid-resolution: the folder must not keep conflict markers
                raise
            finally:
                self.cp.worktree_remove(path, branch)

    def resolve_conflicts(self, t: Dict[str, Any], conflicts: List[str]) -> None:
        """Merging the task left conflict markers in the folder: its author (or the lead) resolves
        them in a separate process; if that fails, the merge is undone and the task fails."""
        assert self.cp is not None
        agent = t["assignee"] if t["assignee"] in self.available else self.pick_lead(announce=False)
        self.notice(tr("wt.conflict", id=t["id"], files=", ".join(conflicts[:6]), name=AGENT_NAME[agent]), "warn")
        tools = self.tools_for(agent, "work", "merge", self.workspace, t["id"])
        prompt = prompts.merger(agent, self.run_.prompt, t, conflicts, self.workspace, tools)
        res = self.call(agent, prompt, "work", t["id"], "merge", effort=self.effort("merge", t, agent),
                        instance=self.spawn_agent(agent))
        left = self.cp.unresolved(conflicts)
        if res.ok and not left:
            self.cp.merge_finish(f"merge {t['id']} (conflicts resolved by {agent})")
            t["merge_report"] = res.text[-3000:]
            self.emit("task.merged", task_id=t["id"], agent=agent, conflicts=conflicts)
            return
        self.cp.merge_abort()
        t["status"] = "failed"
        t["error"] = tr("wt.conflict_failed", id=t["id"], files=", ".join(left or conflicts))
        self.notice(t["error"], "error")

    def _task_summary(self, t: Dict[str, Any]) -> Dict[str, Any]:
        return {"id": t["id"], "title": t["title"], "assignee": t["assignee"], "status": t["status"],
                "verdict": t.get("verdict"), "fixes": t.get("fixes", 0)}

    # -- planning ----------------------------------------------------------------
    def make_plan(self) -> None:
        lead = self.pick_lead()
        self.phase("planning")
        final_check = bool(self.settings.get("finalCheck", True))
        note = self.guard.prompt_note(self.available) if self.guard.enabled and len(self.available) > 1 else ""
        roles = {a: self.role(a) for a in self.available}
        try:
            history = recent_runs(self.workspace, demo=bool(self.settings.get("demo")), skip_id=self.run_.id)
        except OSError:
            history = []
        planner_prompt = prompts.planner(self.run_.prompt, self.workspace, self.available, final_check, note,
                                         roles, bool(self.settings.get("everyAgentTask", True)), history)
        res = self.call(lead, planner_prompt, "plan", None, "plan", effort=self.effort("plan", agent=lead))
        if not res.ok and res.quota:
            self.guard.mark_exhausted(lead)
            self.guard.read()
        if not res.ok and res.quota and self.other(lead, skip_empty=True) != lead:
            old = lead
            lead = self.other(old, skip_empty=True)
            self.decide(tr("takeover.plan", old=AGENT_NAME[old], new=AGENT_NAME[lead]), "takeover", old, lead)
            res = self.call(lead, planner_prompt, "plan", None, "plan", effort=self.effort("plan", agent=lead))
        self.check_cancel()
        plan = extract_json(res.text, "tasks") if res.ok else None
        if plan and plan.get("answer") and not plan.get("tasks"):
            self.emit("run.answer", by=lead, text=str(plan["answer"]))
            raise Answered()
        if not plan:
            reason = res.error or tr("plan.invalid_json")
            self.notice(tr("plan.failed", reason=reason), "warn")
            plan = {"summary": tr("plan.single_summary"), "rationale": "",
                    "tasks": [{"id": "T1", "title": self.run_.prompt[:80], "description": self.run_.prompt,
                               "assignee": lead, "depends_on": [], "files": []}]}
        plan = self.normalize_plan(plan, lead)
        self.emit("plan.proposed", by=lead, plan=plan)

        reviewer = self.reviewer_for(lead, plan["tasks"])
        if self.settings.get("planReview") and reviewer != lead and self.guard.enabled \
                and self.guard.level(reviewer) in ("critical", "empty"):
            self.decide(tr("plan_review.skipped", why=self.guard.describe(reviewer)), "skip", frm=reviewer)
            reviewer = lead
        if self.settings.get("planReview") and reviewer != lead:
            self.phase("plan_review")
            rres = self.call(reviewer, prompts.plan_reviewer(self.run_.prompt, plan, self.available, roles,
                                                       bool(self.settings.get("everyAgentTask", True)),
                                                       final_check, note), "plan", None,
                             "plan_review", effort=self.effort("plan_review", agent=reviewer))
            self.check_cancel()
            review = extract_json(rres.text, "verdict") if rres.ok else None
            if review:
                revised = review.get("revised_plan")
                use_revision = review.get("verdict") == "revise" and isinstance(revised, dict) and revised.get("tasks")
                if use_revision:
                    plan = self.normalize_plan(revised, lead)
                self.emit("plan.reviewed", by=reviewer, verdict=review.get("verdict"),
                          comments=[str(c) for c in review.get("comments") or []][:8], revised=bool(use_revision),
                          plan=plan)
            else:
                self.notice(tr("plan_review.no_result", reason=rres.error or tr("plan_review.invalid")), "warn")
        self.tasks = [dict(t) for t in plan["tasks"]]
        self.rebalance(self.tasks)
        covered = self.ensure_coverage(self.tasks)
        self.update_reviewers()
        plan["tasks"] = self.tasks
        if covered or any(t.get("handoff") for t in self.tasks) or any(t["reviewer"] == t["assignee"] for t in self.tasks if len(self.available) > 1):
            self.emit("plan.adjusted", plan=plan)  # the approval screen shows the balanced plan
        self.plan = plan

    def normalize_plan(self, plan: Dict[str, Any], lead: str) -> Dict[str, Any]:
        raw = [t for t in plan.get("tasks") or [] if isinstance(t, dict)][:8]
        tasks: List[Dict[str, Any]] = []
        id_map: Dict[str, str] = {}
        for i, t in enumerate(raw, 1):
            new_id = f"T{i}"
            id_map[str(t.get("id") or new_id)] = new_id
        for i, t in enumerate(raw, 1):
            tid = f"T{i}"
            assignee = str(t.get("assignee") or "").lower()
            if assignee not in self.available:
                assignee = lead if lead in self.available else self.available[0]
            earlier = {f"T{j}" for j in range(1, i)}
            deps = [id_map.get(str(d)) for d in t.get("depends_on") or []]
            tasks.append({
                "id": tid,
                "title": str(t.get("title") or tr("task.default_title", i=i))[:120],
                "description": str(t.get("description") or ""),
                "assignee": assignee,
                "reviewer": None,
                "depends_on": sorted({d for d in deps if d in earlier}),
                "files": [str(f) for f in t.get("files") or []][:20],
                "complexity": str(t.get("complexity") or "medium").lower() if str(t.get("complexity") or "").lower() in effort_policy.LEVELS else "medium",
                "status": "todo",
                "fixes": 0,
            })
        if not tasks:
            tasks.append({"id": "T1", "title": self.run_.prompt[:80], "description": self.run_.prompt,
                          "assignee": lead, "reviewer": None, "depends_on": [], "files": [],
                          "complexity": "medium", "status": "todo", "fixes": 0})
        for t in tasks:
            t["reviewer"] = self.reviewer_for(t["assignee"], tasks, t)
        return {"summary": str(plan.get("summary") or ""), "rationale": str(plan.get("rationale") or ""), "tasks": tasks}

    def context_summary(self) -> str:
        """Plain-text status for side questions asked while the run is going."""
        lines = [f"Request: {self.run_.prompt}"]
        if self.plan.get("summary"):
            lines.append(f"Plan: {self.plan['summary']}")
        for t in self.tasks:
            lines.append(f"- {t['id']} [{t['assignee']}] {t['title']}: {t['status']}")
        busy = [f"{a} is working on {tid}" for a, tid in self.busy.items() if tid]
        if busy:
            lines.append("; ".join(busy))
        return "\n".join(lines)

    def public_plan(self) -> Dict[str, Any]:
        return {"summary": self.plan.get("summary", ""), "rationale": self.plan.get("rationale", ""),
                "tasks": [{k: v for k, v in t.items() if not k.startswith("_")} for t in self.tasks]}

    # -- execution -------------------------------------------------------------
    def task(self, tid: str) -> Dict[str, Any]:
        return next(t for t in self.tasks if t["id"] == tid)

    def next_job(self, agent: str) -> Optional[Dict[str, Any]]:
        by_id = {t["id"]: t for t in self.tasks}
        cross = self.settings.get("crossReview", True)
        for t in self.tasks:  # reviews first: they unblock dependents
            if t["status"] == "awaiting_review" and t["reviewer"] == agent:
                return {"kind": "review", "task": t}
        for t in self.tasks:
            if t["status"] == "changes_requested" and t["assignee"] == agent:
                return {"kind": "fix", "task": t}
        for t in self.tasks:
            if t["status"] == "todo" and t["assignee"] == agent and all(by_id[d]["status"] == "done" for d in t["depends_on"]):
                return {"kind": "implement", "task": t, "review": cross}
        return None

    def execute(self) -> None:
        threads: List[threading.Thread] = []
        with self.cv:
            while True:
                self.check_cancel()
                if time.time() - self._quota_checked > 20:
                    self.quota_runtime()
                by_id = {t["id"]: t for t in self.tasks}
                for t in self.tasks:
                    if t["status"] == "todo" and any(by_id[d]["status"] in ("failed", "blocked") for d in t["depends_on"]):
                        t["status"] = "blocked"
                        self.emit_task(t)
                        self.notice(tr("task.blocked", id=t['id']), "warn")
                started_any = False
                for agent in self.available:
                    if self.busy[agent]:
                        continue
                    if self.guard.level(agent) == "empty" and self.other(agent, skip_empty=True) != agent:
                        continue  # out of quota: its work has been handed over
                    job = self.next_job(agent)
                    if not job:
                        continue
                    t = job["task"]
                    t["status"] = {"implement": "working", "review": "reviewing", "fix": "fixing"}[job["kind"]]
                    self.emit_task(t)
                    self.busy[agent] = t["id"]
                    th = threading.Thread(target=self._job_thread, args=(agent, job), daemon=True)
                    threads.append(th)
                    th.start()
                    started_any = True
                if all(t["status"] in TERMINAL for t in self.tasks) and not any(self.busy.values()):
                    break
                if not started_any and not any(self.busy.values()):
                    # Nothing runnable and nobody working: unreachable dependencies.
                    for t in self.tasks:
                        if t["status"] not in TERMINAL:
                            t["status"] = "blocked"
                            self.emit_task(t)
                    break
                self.cv.wait(timeout=1.0)
        for th in threads:
            th.join(timeout=5)

    def _job_thread(self, agent: str, job: Dict[str, Any]) -> None:
        t = job["task"]
        try:
            if job["kind"] == "review":
                self._review(agent, t)
            else:
                self._work(agent, t, job["kind"])
        except Cancelled:
            pass
        except Exception as exc:
            t["status"] = "failed"
            t["error"] = str(exc)
            self.notice(tr("task.error", id=t['id'], err=exc), "error")
            self.drop_worktree(t)
            self.emit_task(t)
        finally:
            with self.cv:
                self.busy[agent] = None
                self.cv.notify_all()

    def _snapshot(self, cwd: Optional[str] = None) -> Optional[Snapshot]:
        if self.settings.get("demo"):
            return None
        try:
            return Snapshot(Path(cwd or self.workspace))
        except OSError:
            return None

    def _work(self, agent: str, t: Dict[str, Any], kind: str) -> None:
        if self.cp:
            t["_cp"] = self.checkpoint("before", t["id"], f"before {t['id']} ({kind})")
        cwd = self.task_worktree(t, kind) or self.workspace
        isolated = cwd != self.workspace
        before = self._snapshot(cwd)
        tools = self.tools_for(agent, "work", kind, cwd, t["id"])
        if kind == "implement":
            mates = [a for a in self.available if a != agent]
            prompt = prompts.worker(agent, t["reviewer"], mates, self.run_.prompt, self.plan, t, self.tasks,
                                    cwd, self.settings.get("crossReview", True) and t["reviewer"] != agent,
                                    tools, isolated)
        else:
            prompt = prompts.fixer(agent, t["reviewer"], self.run_.prompt, t, t.get("review") or {}, cwd, tools, isolated)
        # A fix continues the author's own session, so it remembers what it built.
        resume = t.get("_session") if kind == "fix" and t.get("_session_agent") == agent else None
        overlap = not isolated and self.others_busy(agent)  # a teammate's edits may land in this job's window
        res = self.call(agent, prompt, "work", t["id"], kind, effort=self.effort(kind, t, agent), resume=resume, cwd=cwd)
        if not res.ok:
            self.clear_partial_edits(t, res)
        if not res.ok and res.quota and self.hand_over(agent, t, kind):
            return
        if not res.ok and not res.quota:
            # A crash, timeout or CLI hiccup: one more try in a fresh session, then a teammate.
            self.check_cancel()
            self.notice(tr("task.retry", name=AGENT_NAME[agent], id=t["id"], err=_short(res.error)), "warn")
            res = self.call(agent, prompt, "work", t["id"], kind, effort=self.effort(kind, t, agent), cwd=cwd)
            if not res.ok:
                self.clear_partial_edits(t, res)
            if not res.ok and res.quota and self.hand_over(agent, t, kind):
                return
            if not res.ok and not res.quota and self.hand_over(agent, t, kind, error=_short(res.error)):
                return
        if res.session:
            t["_session"], t["_session_agent"] = res.session, agent
        files = self._diff(agent, before, res, overlap or (not isolated and self.others_busy(agent)), cwd)
        self.emit("task.diff", task_id=t["id"], agent=agent, job=kind, files=files)
        t["_last_diff"] = files
        if kind == "implement":
            t["report"] = res.text[-4000:]
        else:
            t["fix_report"] = res.text[-4000:]
            t["fixes"] = t.get("fixes", 0) + 1
        t["files_changed"] = sorted(set(t.get("files_changed", [])) | {f["path"] for f in files})
        if not res.ok:
            t["status"] = "failed"
            t["error"] = res.error
            self.notice(tr("task.failed", id=t["id"], err=_short(res.error)), "error")
            self.drop_worktree(t)
        elif self.settings.get("crossReview", True) and t["reviewer"] in self.available:
            # Every fix gets a short re-review; the reviewer only checks the requested changes.
            t["status"] = "awaiting_review"
        else:
            self.complete(t)
        self.emit_task(t)

    def drop_worktree(self, t: Dict[str, Any]) -> None:
        """A failed task's copy is discarded; the folder never saw its edits."""
        if t.get("_wt") and self.cp:
            self.cp.worktree_remove(t.pop("_wt"), t.pop("_branch", ""))
            t.pop("_wt_base", None)

    def others_busy(self, agent: str) -> bool:
        return any(v for a, v in self.busy.items() if a != agent)

    def _diff(self, agent: str, before: Optional[Snapshot], res: AgentResult, overlap: bool = False,
              cwd: Optional[str] = None) -> List[Dict[str, Any]]:
        demo_agent = self.agents[agent]
        if isinstance(demo_agent, DemoAgent):
            return list(demo_agent.fake_diff)
        if before is None:
            return []
        try:
            after = Snapshot(Path(cwd or self.workspace))
        except OSError:
            return []
        # When another agent worked during this job, the folder diff contains its edits
        # too; narrow it to the files this agent reported when it reported any.
        only = res.changed if (overlap and res.changed) else None
        return before.diff(after, only)

    def _review(self, agent: str, t: Dict[str, Any]) -> None:
        report = t.get("fix_report") or t.get("report") or ""
        diff_text = diff_as_prompt(t.get("_last_diff") or [])
        previous = t.get("review") if t.get("fixes") else None
        cwd = t.get("_wt") or self.workspace
        tools = self.tools_for(agent, "review", "review", cwd, t["id"])
        prompt = prompts.reviewer(agent, t["assignee"], self.run_.prompt, t, report, diff_text, previous, cwd, tools,
                                  cwd != self.workspace)
        res = self.call(agent, prompt, "review", t["id"], "review", effort=self.effort("review", t, agent), cwd=cwd)
        if not res.ok and res.quota and self.hand_over(agent, t, "review"):
            return
        review = extract_json(res.text, "verdict") if res.ok else None
        if not review and not res.quota:
            # No verdict (crash, timeout or prose without the JSON block): ask once more, with a nudge.
            self.check_cancel()
            if res.ok:
                self.notice(tr("review.retry", name=AGENT_NAME[agent], id=t["id"]), "warn")
            else:
                self.notice(tr("task.retry", name=AGENT_NAME[agent], id=t["id"], err=_short(res.error)), "warn")
            nudge = prompt + "\n\nYour previous reply contained no JSON verdict. Reply with ONLY the ```json block."
            res = self.call(agent, nudge if res.ok else prompt, "review", t["id"], "review",
                            effort=self.effort("review", t, agent), cwd=cwd)
            if not res.ok and res.quota and self.hand_over(agent, t, "review"):
                return
            review = extract_json(res.text, "verdict") if res.ok else None
            if not review and not res.ok and not res.quota and self.hand_over(agent, t, "review", error=_short(res.error)):
                return
        if not review:
            t["review"] = {"verdict": "unknown", "summary": res.error or tr("review.unreadable"), "issues": []}
            t["verdict"] = "unknown"
            self.complete(t)
            self.notice(tr("review.no_result", id=t['id']), "warn")
        else:
            verdict = "approve" if str(review.get("verdict")).startswith("approv") else "changes_requested"
            issues = [i for i in review.get("issues") or [] if isinstance(i, dict)][:12]
            t["review"] = {"verdict": verdict, "summary": str(review.get("summary") or ""), "issues": issues}
            t["verdict"] = verdict
            rounds = int(self.settings.get("fixRounds", 1))
            if verdict == "approve":
                self.complete(t)
            elif t.get("fixes", 0) < rounds:
                t["status"] = "changes_requested"
            else:
                self.complete(t)
                self.notice(tr("review.open_notes", id=t['id']), "warn")
        self.emit("review.result", task_id=t["id"], reviewer=agent, author=t["assignee"], **t["review"])
        self.emit_task(t)

    # -- final integration check ------------------------------------------------
    def integrate(self) -> None:
        """Each task was reviewed on its own; this checks that the pieces work together."""
        agent = self.pick_lead(announce=False)
        self.phase("integrating")
        before = self._snapshot()
        tools = self.tools_for(agent, "work", "integrate", self.workspace)
        prompt = prompts.integrator(agent, self.run_.prompt, self.plan, self.tasks, self.workspace, tools)
        res = self.call(agent, prompt, "work", "SON", "integrate", effort=self.effort("integrate", agent=agent))
        if not res.ok and res.quota:
            self.guard.mark_exhausted(agent)
            self.guard.read()
        if not res.ok and res.quota and self.other(agent, skip_empty=True) != agent:
            old = agent
            agent = self.other(old, skip_empty=True)
            self.decide(tr("takeover.final", old=AGENT_NAME[old], new=AGENT_NAME[agent]), "takeover", old, agent, "SON")
            tools = self.tools_for(agent, "work", "integrate", self.workspace)
            prompt = prompts.integrator(agent, self.run_.prompt, self.plan, self.tasks, self.workspace, tools)
            res = self.call(agent, prompt, "work", "SON", "integrate", effort=self.effort("integrate", agent=agent))
        files = self._diff(agent, before, res)
        if files:
            self.emit("task.diff", task_id="SON", agent=agent, job="integrate", files=files)
        data = extract_json(res.text, "status") if res.ok else None
        if data:
            status = str(data.get("status") or "ok")
            status = status if status in ("ok", "fixed", "problems") else "ok"
            summary = str(data.get("summary") or "")
            checks = [c for c in data.get("checks") or [] if isinstance(c, dict)][:10]
        else:
            status, summary, checks = ("problems" if not res.ok else "ok"), (res.error or res.text[-600:]), []
        self.emit("final.result", agent=agent, status=status, summary=summary, checks=checks,
                  files=[f["path"] for f in files])
        if status == "problems":
            self.notice(tr("final.problems"), "warn")

    # -- quota balancing --------------------------------------------------------------
    def decide(self, text: str, action: str, frm: Optional[str] = None, to: Optional[str] = None,
               task_id: Optional[str] = None) -> None:
        """A visible, explained decision taken because of usage limits."""
        self.emit("quota.decision", action=action, text=text, frm=frm, to=to, task_id=task_id)

    def quota_start(self) -> None:
        if not self.guard.enabled:
            return
        self.guard.refresh()
        self.guard.read()
        info = self.guard.last
        self._quota_checked = time.time()
        self.publish_quota()
        if len(self.available) < 2:
            return
        for agent in list(self.available):
            if info[agent]["level"] == "empty" and len(self.available) > 1:
                self.available.remove(agent)
                rest = (tr("quota.exclude.alone", name=AGENT_NAME[self.available[0]]) if len(self.available) == 1
                        else tr("quota.exclude.shared", names=names(self.available)))
                self.decide(tr("quota.exclude", why=self.guard.describe(agent), rest=rest),
                            "exclude", frm=agent, to=self.available[0] if len(self.available) == 1 else None)
        for agent in self.available:
            mate = self.other(agent)
            if mate != agent and info[agent]["level"] == "tight" and info[mate]["level"] in ("ok", "unknown"):
                self.decide(tr("quota.tight", why=self.guard.describe(agent), mate=AGENT_NAME[mate], to=TO[mate],
                               name=AGENT_NAME[agent]), "level", frm=agent)

    def publish_quota(self) -> None:
        self.emit("quota.status", agents=self.guard.last, critical=self.guard.critical, tight=self.guard.tight)

    def pick_lead(self, announce: bool = True) -> str:
        """The first senior of the team leads (Claude by default); another agent
        leads when that one is nearly out of quota."""
        seniors = [a for a in self.available if self.role(a) == "senior"]
        lead = seniors[0] if seniors else self.available[0]
        alt = self.other(lead, [], senior=100)
        if self.guard.enabled and alt != lead and self.guard.level(lead) in ("critical", "empty") \
                and self.guard.worse(lead, alt):
            if announce:
                self.decide(tr("quota.lead", new=AGENT_NAME[alt], why=self.guard.describe(lead)), "lead", lead, alt)
            return alt
        return lead

    def reviewer_for(self, author: str, tasks: Optional[List[Dict[str, Any]]] = None,
                     task: Optional[Dict[str, Any]] = None) -> str:
        """Seniors check plans and hard tasks (a worker only when no senior is free at all),
        and preferably workers' tasks; anyone may check a senior's easy task."""
        complexity = (task or {}).get("complexity")
        if task is None or complexity == "high":
            weight = 100
        elif self.role(author) != "senior" or complexity == "medium":
            weight = 3
        else:
            weight = 0
        # an agent whose review of this task already failed twice is not asked again
        alt = self.other(author, tasks, senior=weight, exclude=set((task or {}).get("_review_failed") or ()))
        if alt != author and self.guard.enabled and self.guard.level(alt) in ("critical", "empty") \
                and self.guard.worse(alt, author):
            return author  # self-review in a fresh session beats a reviewer that runs dry mid-review
        return alt

    def rebalance(self, tasks: List[Dict[str, Any]]) -> None:
        """Move unstarted work away from an agent that is running out of quota."""
        if not self.guard.enabled or len(self.available) < 2:
            return
        for t in tasks:
            if t["status"] not in ("todo", "changes_requested"):
                continue
            a = t["assignee"]
            b = self.other(a, tasks)
            if b == a:
                continue
            la, lb = self.guard.level(a), self.guard.level(b)
            out_of_quota = la in ("critical", "empty") and ORDER[lb] < ORDER[la]
            too_heavy = la == "tight" and t.get("complexity") == "high" and lb in ("ok", "unknown")
            if not (out_of_quota or too_heavy):
                continue
            t["assignee"] = b
            t["handoff"] = {"from": a, "to": b, "reason": self.guard.describe(a)}
            key = "quota.reassign.heavy" if too_heavy and not out_of_quota else "quota.reassign"
            self.decide(tr(key, id=t["id"], mate=AGENT_NAME[b], to=TO[b], why=self.guard.describe(a)), "reassign", a, b, t["id"])
            if self.plan:  # during planning the adjusted plan is published as a whole instead
                self.emit_task(t)

    def ensure_coverage(self, tasks: List[Dict[str, Any]]) -> bool:
        """Give every available agent at least one task (the everyAgentTask setting): an idle
        agent takes a task from whoever has the most, a worker the easiest one, a senior the
        hardest. Agents that are out of quota are left out."""
        if not self.settings.get("everyAgentTask", True) or len(self.available) < 2:
            return False
        usable = [a for a in self.available
                  if not (self.guard.enabled and self.guard.level(a) in ("critical", "empty"))]
        rank = {"low": 0, "medium": 1, "high": 2}
        moved: List[str] = []
        for agent in usable:
            if any(t["assignee"] == agent for t in tasks):
                continue
            owned: Dict[str, List[Dict[str, Any]]] = {}
            for t in tasks:
                if t["status"] == "todo":
                    owned.setdefault(t["assignee"], []).append(t)
            donors = [a for a, ts in owned.items() if len(ts) > 1]
            if not donors:
                break
            donor = max(donors, key=lambda a: len(owned[a]))
            pick = min if self.role(agent) == "worker" else max
            t = pick(owned[donor], key=lambda x: rank.get(x.get("complexity"), 1))
            t["assignee"] = agent
            moved.append(tr("coverage.item", id=t["id"], name=AGENT_NAME[agent], to=TO[agent]))
        idle = [a for a in usable if not any(t["assignee"] == a for t in tasks)]
        if moved:
            for t in tasks:  # reviewers again, now that the work is spread differently
                t["reviewer"] = None
            for t in tasks:
                t["reviewer"] = self.reviewer_for(t["assignee"], tasks, t)
            self.notice(tr("coverage.moved", list=", ".join(moved)))
        if idle:
            self.notice(tr("coverage.idle", n=len(tasks), names=names(idle)))
        return bool(moved)

    def reviewer_ok(self, reviewer: str, author: str, task: Optional[Dict[str, Any]] = None) -> bool:
        if reviewer not in self.available or reviewer in ((task or {}).get("_review_failed") or ()):
            return False
        if reviewer == author:  # self-review is only a fallback
            return self.reviewer_for(author, task=task) == author
        return not (self.guard.enabled and self.guard.level(reviewer) in ("critical", "empty")
                    and self.guard.worse(reviewer, author))

    def update_reviewers(self) -> None:
        """Replace reviewers that cannot do the check any more (quota, or they now own the task)."""
        moved: Dict[str, List[Any]] = {}
        for t in self.tasks:
            if t["status"] in ("done", "failed", "blocked", "cancelled", "reviewing"):
                continue
            cur, author = t.get("reviewer"), t["assignee"]
            if cur and self.reviewer_ok(cur, author, t):
                continue
            new = self.reviewer_for(author, task=t)
            if new == cur:
                continue
            if cur and cur != author and cur in self.available:
                moved.setdefault(cur, []).append((new, author))
            t["reviewer"] = new
            if self.plan:
                self.emit_task(t)
        for agent, pairs in moved.items():
            takers = sorted({n for n, a in pairs if n != a}, key=self.team.index)
            then = (tr("review.moved.others", names=names(takers)) if takers and all(n != a for n, a in pairs)
                    else tr("review.moved.self") if not takers
                    else tr("review.moved.mixed", names=names(takers)))
            self.decide(tr("review.moved", name=AGENT_NAME[agent], why=self.guard.describe(agent), then=then),
                        "review", frm=agent)

    def quota_runtime(self) -> None:
        """Re-check limits during the run and shift work if an agent got worse."""
        self._quota_checked = time.time()
        if not self.guard.enabled or len(self.available) < 2:
            return
        before = {a: self.guard.level(a) for a in self.available}
        self.guard.read()
        worse = [a for a in self.available if ORDER[self.guard.level(a)] > ORDER.get(before.get(a, "ok"), 0)
                 and self.guard.level(a) in ("tight", "critical", "empty")]
        if not worse:
            return
        self.publish_quota()
        for a in worse:
            self.decide(tr("quota.dropped", why=self.guard.describe(a)), "level", frm=a)
        self.rebalance(self.tasks)
        self.update_reviewers()

    def hand_over(self, agent: str, t: Dict[str, Any], kind: str, error: str = "") -> bool:
        """A job failed: give it, and (on a usage limit) the agent's queued work, to a teammate.
        error="" means the usage limit; otherwise it is the failure to explain."""
        if not error:
            self.guard.mark_exhausted(agent)
            self.guard.read()
            self.publish_quota()
        alt = self.other(agent, skip_empty=True)
        if alt == agent:
            return False
        if kind == "review":
            if error:
                t.setdefault("_review_failed", []).append(agent)
            t["reviewer"] = self.reviewer_for(t["assignee"], task=t)
            if t["reviewer"] == t["assignee"]:
                return False  # nobody else can check it: the caller accepts the task as it is
            t["status"] = "awaiting_review"
            text = (tr("review.handoff", old=AGENT_NAME[agent], new=AGENT_NAME[t["reviewer"]], id=t["id"], err=error)
                    if error else tr("takeover.review", old=AGENT_NAME[agent], new=AGENT_NAME[t["reviewer"]], id=t["id"]))
            self.decide(text, "takeover", agent, t["reviewer"], t["id"])
        else:
            t["assignee"] = alt
            reason = tr("quota.hit", old=AGENT_NAME[agent]) if not error else error
            t["handoff"] = {"from": agent, "to": alt, "reason": reason}
            t["status"] = "todo" if kind == "implement" else "changes_requested"
            if t["reviewer"] == alt:  # the new author can't review its own work
                t["reviewer"] = self.reviewer_for(alt, task=t)
            text = (tr("task.handoff", old=AGENT_NAME[agent], new=AGENT_NAME[alt], to=TO[alt], id=t["id"], err=error)
                    if error else tr("takeover.task", old=AGENT_NAME[agent], new=AGENT_NAME[alt], id=t["id"]))
            self.decide(text, "takeover", agent, alt, t["id"])
        self.emit_task(t)
        if not error:
            self.rebalance(self.tasks)
        self.update_reviewers()
        return True
