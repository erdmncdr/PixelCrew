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
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import effort as effort_policy
from . import models
from . import prompts
from .agents import AgentResult, BaseAgent, Cancelled, make_agent
from .config import AGENTS
from .i18n import t as tr
from . import i18n
from .demo import DemoAgent
from .quota import NAME as AGENT_NAME
from .quota import ORDER, TO, QuotaGuard
from .snapshot import Snapshot, diff_as_prompt

TERMINAL = {"done", "failed", "blocked", "cancelled"}


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
              senior: int = 0) -> str:
        """The best teammate to take over from (or check) `agent`: the most quota
        left first, then the least loaded, then the next one in team order, so
        that with two agents Claude and Codex simply pair up. senior=N counts a
        worker as N load units busier, so seniors win unless they are swamped."""
        others = [a for a in self.available if a != agent]
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
        for agent in self.agents.values():
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
            if q == "tight":
                level = {"high": "medium", "medium": "low"}.get(level, level)
            elif q in ("critical", "empty"):
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
             effort: Optional[str] = None, resume: Optional[str] = None) -> AgentResult:
        task = next((t for t in self.tasks if t["id"] == task_id), None)
        model, tier = self.model_for(agent, job, task)
        self.emit("job.started", agent=agent, job=job, task_id=task_id, effort=effort, resumed=bool(resume),
                  model=model, tier=tier)
        try:
            result = self.agents[agent].execute(prompt, mode, self.workspace, task_id, job,
                                                resume=resume, effort=effort, model=model)
            if not result.ok and resume and not self.cancel_event.is_set():
                # The earlier session could not be continued; start a fresh one.
                self.emit("agent.stderr", agent=agent, task_id=task_id, job=job,
                          text=tr("session.resume_failed"))
                result = self.agents[agent].execute(prompt, mode, self.workspace, task_id, job, effort=effort,
                                                    model=model)
        except Cancelled:
            raise
        except Exception as exc:  # unexpected launcher failure (missing binary, etc.)
            result = AgentResult(False, "", f"{type(exc).__name__}: {exc}")
        for key in ("input_tokens", "output_tokens", "cost_usd"):
            self.usage[agent][key] += float(result.usage.get(key) or 0)
        self.emit(
            "job.finished", agent=agent, job=job, task_id=task_id, ok=result.ok,
            text=result.text[-6000:], error=result.error, duration=round(result.duration, 1),
        )
        return result

    def emit_task(self, task: Dict[str, Any]) -> None:
        self.emit("task.update", task={k: v for k, v in task.items() if not k.startswith("_")})

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
        except Cancelled:
            status = "cancelled"
            for t in self.tasks:
                if t["status"] not in TERMINAL:
                    t["status"] = "cancelled"
                    self.emit_task(t)
        except Exception as exc:
            status = "failed"
            self.notice(f"Beklenmeyen hata: {exc}", "error")
            self.emit("run.error", trace=traceback.format_exc()[-3000:])
        finally:
            self.phase(status)
            self.emit("run.finished", status=status, duration=round(time.time() - started, 1),
                      usage=self.usage, tasks=[self._task_summary(t) for t in self.tasks])
            self.run_.finish(status)

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
        planner_prompt = prompts.planner(self.run_.prompt, self.workspace, self.available, final_check, note,
                                         roles, bool(self.settings.get("everyAgentTask", True)))
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
                                                       bool(self.settings.get("everyAgentTask", True))), "plan", None,
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
            self.emit_task(t)
        finally:
            with self.cv:
                self.busy[agent] = None
                self.cv.notify_all()

    def _snapshot(self) -> Optional[Snapshot]:
        if self.settings.get("demo"):
            return None
        try:
            return Snapshot(Path(self.workspace))
        except OSError:
            return None

    def _work(self, agent: str, t: Dict[str, Any], kind: str) -> None:
        before = self._snapshot()
        if kind == "implement":
            mates = [a for a in self.available if a != agent]
            prompt = prompts.worker(agent, t["reviewer"], mates, self.run_.prompt, self.plan, t, self.tasks,
                                    self.workspace, self.settings.get("crossReview", True) and t["reviewer"] != agent)
        else:
            prompt = prompts.fixer(agent, t["reviewer"], self.run_.prompt, t, t.get("review") or {}, self.workspace)
        # A fix continues the author's own session, so it remembers what it built.
        resume = t.get("_session") if kind == "fix" and t.get("_session_agent") == agent else None
        res = self.call(agent, prompt, "work", t["id"], kind, effort=self.effort(kind, t, agent), resume=resume)
        if not res.ok and res.quota and self.hand_over(agent, t, kind):
            return
        if res.session:
            t["_session"], t["_session_agent"] = res.session, agent
        files = self._diff(agent, before, res)
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
        elif kind == "implement":
            wants_review = self.settings.get("crossReview", True) and t["reviewer"] in self.available
            t["status"] = "awaiting_review" if wants_review else "done"
        else:
            # Every fix gets a short re-review; the reviewer only checks the requested changes.
            wants_review = self.settings.get("crossReview", True) and t["reviewer"] in self.available
            t["status"] = "awaiting_review" if wants_review else "done"
        self.emit_task(t)

    def _diff(self, agent: str, before: Optional[Snapshot], res: AgentResult) -> List[Dict[str, Any]]:
        demo_agent = self.agents[agent]
        if isinstance(demo_agent, DemoAgent):
            return list(demo_agent.fake_diff)
        if before is None:
            return []
        try:
            after = Snapshot(Path(self.workspace))
        except OSError:
            return []
        # When several agents run at once, the folder diff contains the teammates'
        # edits too; narrow it to the files this agent reported when possible.
        other_busy = any(v for a, v in self.busy.items() if a != agent)
        only = res.changed if (other_busy and res.changed) else None
        return before.diff(after, only)

    def _review(self, agent: str, t: Dict[str, Any]) -> None:
        report = t.get("fix_report") or t.get("report") or ""
        diff_text = diff_as_prompt(t.get("_last_diff") or [])
        previous = t.get("review") if t.get("fixes") else None
        prompt = prompts.reviewer(agent, t["assignee"], self.run_.prompt, t, report, diff_text, previous)
        res = self.call(agent, prompt, "review", t["id"], "review", effort=self.effort("review", t, agent))
        if not res.ok and res.quota and self.hand_over(agent, t, "review"):
            return
        review = extract_json(res.text, "verdict") if res.ok else None
        if not review:
            t["review"] = {"verdict": "unknown", "summary": res.error or tr("review.unreadable"), "issues": []}
            t["verdict"] = "unknown"
            t["status"] = "done"
            self.notice(tr("review.no_result", id=t['id']), "warn")
        else:
            verdict = "approve" if str(review.get("verdict")).startswith("approv") else "changes_requested"
            issues = [i for i in review.get("issues") or [] if isinstance(i, dict)][:12]
            t["review"] = {"verdict": verdict, "summary": str(review.get("summary") or ""), "issues": issues}
            t["verdict"] = verdict
            rounds = int(self.settings.get("fixRounds", 1))
            if verdict == "approve":
                t["status"] = "done"
            elif t.get("fixes", 0) < rounds:
                t["status"] = "changes_requested"
            else:
                t["status"] = "done"
                self.notice(tr("review.open_notes", id=t['id']), "warn")
        self.emit("review.result", task_id=t["id"], reviewer=agent, author=t["assignee"], **t["review"])
        self.emit_task(t)

    # -- final integration check ------------------------------------------------
    def integrate(self) -> None:
        """Each task was reviewed on its own; this checks that the pieces work together."""
        agent = self.pick_lead(announce=False)
        self.phase("integrating")
        before = self._snapshot()
        prompt = prompts.integrator(agent, self.run_.prompt, self.plan, self.tasks, self.workspace)
        res = self.call(agent, prompt, "work", "SON", "integrate", effort=self.effort("integrate", agent=agent))
        if not res.ok and res.quota:
            self.guard.mark_exhausted(agent)
            self.guard.read()
        if not res.ok and res.quota and self.other(agent, skip_empty=True) != agent:
            old = agent
            agent = self.other(old, skip_empty=True)
            self.decide(tr("takeover.final", old=AGENT_NAME[old], new=AGENT_NAME[agent]), "takeover", old, agent, "SON")
            prompt = prompts.integrator(agent, self.run_.prompt, self.plan, self.tasks, self.workspace)
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
        alt = self.other(author, tasks, senior=weight)
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
        if reviewer not in self.available:
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

    def hand_over(self, agent: str, t: Dict[str, Any], kind: str) -> bool:
        """A job failed on the usage limit: give it, and the agent's queued work, to a teammate."""
        self.guard.mark_exhausted(agent)
        self.guard.read()
        self.publish_quota()
        alt = self.other(agent, skip_empty=True)
        if alt == agent:
            return False
        if kind == "review":
            t["reviewer"] = self.reviewer_for(t["assignee"], task=t)
            t["status"] = "awaiting_review"
            self.decide(tr("takeover.review", old=AGENT_NAME[agent], new=AGENT_NAME[t["reviewer"]], id=t["id"]),
                        "takeover", agent, t["reviewer"], t["id"])
        else:
            t["assignee"] = alt
            t["handoff"] = {"from": agent, "to": alt, "reason": tr("quota.hit", old=AGENT_NAME[agent])}
            t["status"] = "todo" if kind == "implement" else "changes_requested"
            self.decide(tr("takeover.task", old=AGENT_NAME[agent], new=AGENT_NAME[alt], id=t["id"]),
                        "takeover", agent, alt, t["id"])
        self.emit_task(t)
        self.rebalance(self.tasks)
        self.update_reviewers()
        return True
