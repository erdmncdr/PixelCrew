"""Exercises the orchestrator's retry / hand-over paths with scripted stand-in agents.

    python3 tests/orchestrator_recovery.py
"""
import json, os, sys, tempfile
os.environ["PIXELCREW_DATA_DIR"] = tempfile.mkdtemp()
os.environ["PIXELCREW_SYSTEM_LANGUAGE"] = "en"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import bus, orchestrator
from server.agents import AgentResult

PLAN = json.dumps({"summary": "two tasks", "rationale": "", "tasks": [
    {"id": "T1", "title": "core", "description": "d", "assignee": "claude", "depends_on": [], "files": ["a.py"], "complexity": "medium"},
    {"id": "T2", "title": "tests", "description": "d", "assignee": "codex", "depends_on": [], "files": ["t.py"], "complexity": "low"},
]})
OK_REVIEW = "```json\n" + json.dumps({"verdict": "approve", "summary": "fine", "issues": []}) + "\n```"
INTEGRATE = "```json\n" + json.dumps({"status": "ok", "summary": "all good", "checks": []}) + "\n```"


class Stub:
    def __init__(self, name, script):
        self.name, self.script, self.calls = name, script, []
    def cancel(self): pass
    def execute(self, prompt, mode, cwd, task_id, job, resume=None, extra=None, effort=None, model=None):
        key = (job, task_id)
        self.calls.append(key)
        n = sum(1 for c in self.calls if c == key)
        fn = self.script.get(key)
        res = fn(n, prompt) if fn else AgentResult(True, OK_REVIEW if job == "review" else "done report")
        return res


def run_case(name, scripts, expect, team=("claude", "codex")):
    hub = bus.Hub()
    settings = {"team": list(team), "approvePlan": False, "planReview": False, "crossReview": True,
                "fixRounds": 1, "quotaBalance": False, "finalCheck": True, "everyAgentTask": False, "demo": False,
                "jobTimeoutMin": 5, "effortMode": "cli", "modelMode": "cli"}
    run = bus.Run(hub, "build it", tempfile.mkdtemp(), settings)
    hub.current = run
    health = {a: {"ready": True} for a in team}
    orch = orchestrator.Orchestrator(run, settings, health, {a: "x" for a in team})
    orch.agents = {a: Stub(a, scripts.get(a, {})) for a in team}
    orch.run()
    tasks = {t["id"]: t for t in orch.tasks}
    notices = [e["text"] for e in run.events if e["kind"] == "run.notice"]
    decisions = [e["text"] for e in run.events if e["kind"] == "quota.decision"]
    got = {tid: (t["assignee"], t["reviewer"], t["status"]) for tid, t in tasks.items()}
    ok = all(got[k] == v for k, v in expect.items()) and run.status == "done"
    print(("PASS" if ok else "FAIL"), name, got, run.status)
    for n in notices + decisions: print("    ", n)
    return ok


fail = lambda msg: (lambda n, p: AgentResult(False, "", msg))
results = []
# 1. transient failure: first attempt fails, retry succeeds, nothing moves
results.append(run_case("retry succeeds", {
    "claude": {("plan", None): lambda n, p: AgentResult(True, "```json\n" + PLAN + "\n```"),
               ("implement", "T1"): lambda n, p: AgentResult(False, "", "boom") if n == 1 else AgentResult(True, "ok")},
}, {"T1": ("claude", "codex", "done"), "T2": ("codex", "claude", "done")}))
# 2. two failures: the task moves to the teammate, who then can't be its reviewer
results.append(run_case("hand over after two failures", {
    "claude": {("plan", None): lambda n, p: AgentResult(True, "```json\n" + PLAN + "\n```"),
               ("implement", "T1"): fail("Time limit reached.")},
}, {"T1": ("codex", "claude", "done"), "T2": ("codex", "claude", "done")}))
# 3. reviewer answers in prose first, JSON on the nudge
results.append(run_case("review nudge", {
    "claude": {("plan", None): lambda n, p: AgentResult(True, "```json\n" + PLAN + "\n```"),
               ("review", "T2"): lambda n, p: AgentResult(True, "Looks fine to me!") if n == 1
               else AgentResult(True, OK_REVIEW if "ONLY the ```json" in p else "nope")},
}, {"T1": ("claude", "codex", "done"), "T2": ("codex", "claude", "done")}))
# 4. the only possible reviewer crashes twice: no loop, the task is accepted as it is
results.append(run_case("review accepted when no reviewer is left", {
    "claude": {("plan", None): lambda n, p: AgentResult(True, "```json\n" + PLAN + "\n```"),
               ("review", "T2"): fail("crash")},
}, {"T1": ("claude", "codex", "done"), "T2": ("codex", "codex", "done")}))
# 5. with a third agent the review moves to it instead
results.append(run_case("review handed to a third agent", {
    "claude": {("plan", None): lambda n, p: AgentResult(True, "```json\n" + PLAN + "\n```"),
               ("review", "T2"): fail("crash")},
}, {"T1": ("claude", "codex", "done"), "T2": ("codex", "gemini", "done")}, team=("claude", "codex", "gemini")))
sys.exit(0 if all(results) else 1)
