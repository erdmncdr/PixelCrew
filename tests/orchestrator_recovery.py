"""Exercises the orchestrator's retry / hand-over / worktree / question paths with scripted stand-in agents.

    python3 tests/orchestrator_recovery.py
"""
import json, os, sys, tempfile, time
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
LAST = {}


class Stub:
    def __init__(self, name, script):
        self.name, self.script, self.calls, self.env_extra = name, script, [], {}
    def cancel(self): pass
    def execute(self, prompt, mode, cwd, task_id, job, resume=None, extra=None, effort=None, model=None):
        key = (job, task_id)
        self.calls.append(key)
        n = sum(1 for c in self.calls if c == key)
        LAST["cwd"], LAST["stub"] = cwd, self
        fn = self.script.get(key)
        return fn(n, prompt) if fn else AgentResult(True, OK_REVIEW if job == "review" else "done report")


def run_case(name, scripts, expect, team=("claude", "codex"), demo=True, side=None, extra_checks=None):
    hub = bus.Hub()
    settings = {"team": list(team), "approvePlan": False, "planReview": False, "crossReview": True,
                "fixRounds": 1, "quotaBalance": False, "finalCheck": True, "everyAgentTask": False, "demo": demo,
                "jobTimeoutMin": 5, "effortMode": "cli", "modelMode": "cli", "browserCheck": False, "teamQuestions": True}
    # demo=False turns the git safety net and the per-task worktrees on (stub agents still stand in for the CLIs)
    run = bus.Run(hub, "build it", tempfile.mkdtemp(), settings)
    LAST.update(run=run, workspace=run.workspace)
    hub.current = run
    health = {a: {"ready": True} for a in team}
    orch = orchestrator.Orchestrator(run, settings, health, {a: "x" for a in team})
    orch.agents = {a: Stub(a, scripts.get(a, {})) for a in team}
    if side:
        orch.spawn_agent = lambda a: Stub(a, side.get(a, {}))
    orch.run()
    tasks = {t["id"]: t for t in orch.tasks}
    notices = [e["text"] for e in run.events if e["kind"] == "run.notice"]
    decisions = [e["text"] for e in run.events if e["kind"] == "quota.decision"]
    got = {tid: (t["assignee"], t["reviewer"], t["status"]) for tid, t in tasks.items()}
    ok = all(got[k] == v for k, v in expect.items()) and run.status == "done"
    if extra_checks:
        ok = extra_checks(run, orch) and ok
    print(("PASS" if ok else "FAIL"), name, got, run.status)
    for n in notices + decisions: print("    ", n)
    return ok


fail = lambda msg: (lambda n, p: AgentResult(False, "", msg))
plan_ok = lambda n, p: AgentResult(True, "```json\n" + PLAN + "\n```")
results = []
# 1. transient failure: first attempt fails, retry succeeds, nothing moves
results.append(run_case("retry succeeds", {
    "claude": {("plan", None): plan_ok,
               ("implement", "T1"): lambda n, p: AgentResult(False, "", "boom") if n == 1 else AgentResult(True, "ok")},
}, {"T1": ("claude", "codex", "done"), "T2": ("codex", "claude", "done")}))
# 2. two failures: the task moves to the teammate, who then can't be its reviewer
results.append(run_case("hand over after two failures", {
    "claude": {("plan", None): plan_ok, ("implement", "T1"): fail("Time limit reached.")},
}, {"T1": ("codex", "claude", "done"), "T2": ("codex", "claude", "done")}))
# 3. reviewer answers in prose first, JSON on the nudge
results.append(run_case("review nudge", {
    "claude": {("plan", None): plan_ok,
               ("review", "T2"): lambda n, p: AgentResult(True, "Looks fine to me!") if n == 1
               else AgentResult(True, OK_REVIEW if "ONLY the ```json" in p else "nope")},
}, {"T1": ("claude", "codex", "done"), "T2": ("codex", "claude", "done")}))
# 4. the only possible reviewer crashes twice: no loop, the task is accepted as it is
results.append(run_case("review accepted when no reviewer is left", {
    "claude": {("plan", None): plan_ok, ("review", "T2"): fail("crash")},
}, {"T1": ("claude", "codex", "done"), "T2": ("codex", "codex", "done")}))
# 5. with a third agent the review moves to it instead
results.append(run_case("review handed to a third agent", {
    "claude": {("plan", None): plan_ok, ("review", "T2"): fail("crash")},
}, {"T1": ("claude", "codex", "done"), "T2": ("codex", "gemini", "done")}, team=("claude", "codex", "gemini")))


# 6. the git safety net with per-task worktrees: each task builds in its own copy, a failed first
#    attempt leaves nothing behind, both copies are merged into the folder, every task gets a
#    checkpoint, and a task or the whole run can be undone afterwards
def writer(path, text):
    def fn(n, p):
        open(os.path.join(LAST["cwd"], path), "w").write(text)
        return AgentResult(True, "wrote " + path, changed=[path])
    return fn
def flaky(n, p):
    assert LAST["cwd"] != LAST["workspace"], "the task should build in its own worktree"
    open(os.path.join(LAST["cwd"], "a.py"), "w").write("half\n" if n == 1 else "print('a')\n")
    return AgentResult(False, "", "crash", changed=["a.py"]) if n == 1 else AgentResult(True, "ok", changed=["a.py"])
def check6(run, orch):
    ws = run.workspace
    return sorted(os.listdir(ws)) == ["a.py", "t.py"] and not orch.wt_root.exists() \
        and open(os.path.join(ws, "a.py")).read() == "print('a')\n" and len([e for e in run.events if e["kind"] == "task.merged"]) == 2
results.append(run_case("git safety net + worktrees", {
    "claude": {("plan", None): plan_ok, ("implement", "T1"): flaky, ("integrate", "SON"): lambda n, p: AgentResult(True, INTEGRATE)},
    "codex": {("implement", "T2"): writer("t.py", "test\n")},
}, {"T1": ("claude", "codex", "done"), "T2": ("codex", "claude", "done")}, demo=False, extra_checks=check6))
from server.checkpoint import Checkpoints, undo_plan
ws, run = LAST["workspace"], LAST["run"]
cps = [e for e in run.events if e["kind"] == "checkpoint"]
stages = [(e["stage"], e["task_id"]) for e in cps if e["stage"] != "before"]
cleared = any("half-finished" in e["text"] for e in run.events if e["kind"] == "run.notice")
cp = Checkpoints(ws); cp.open()
plan = undo_plan(run.events, "T1")
undo_files = sorted(set(cp.changed_between(plan["before"], plan["done"])) & set(plan["files"]))
cp.restore(plan["before"], only=undo_files, message="undo T1")
after_undo = sorted(os.listdir(ws))
cp.reset_to(cps[0]["commit"])
after_reset = sorted(p for p in os.listdir(ws))
ok6 = (stages[0] == ("start", None) and ("task", "T1") in stages and ("task", "T2") in stages and stages[-1] == ("end", None)
       and cleared and undo_files == ["a.py"] and after_undo == ["t.py"] and after_reset == [])
print(("PASS" if ok6 else "FAIL"), "checkpoints", stages, "| cleared:", cleared, "| undo T1 ->", after_undo, "| reset ->", after_reset)
results.append(ok6)


# 7. both tasks edit the same file against the plan: the second merge conflicts, the author
#    resolves the markers in the folder in a separate process, and the task still completes
def resolve(n, p):
    assert LAST["cwd"] == LAST["workspace"] and "<<<<<<<" in open(os.path.join(LAST["cwd"], "shared.py")).read()
    open(os.path.join(LAST["cwd"], "shared.py"), "w").write("one\ntwo\n")
    return AgentResult(True, "kept both lines")
def check7(run, orch):
    text = open(os.path.join(run.workspace, "shared.py")).read()
    merges = [e for e in run.events if e["kind"] == "task.merged"]
    return text == "one\ntwo\n" and len(merges) == 2 and any(e.get("conflicts") for e in merges) \
        and any(e["job"] == "merge" for e in run.events if e["kind"] == "job.started")
results.append(run_case("merge conflict resolved", {
    "claude": {("plan", None): plan_ok, ("implement", "T1"): writer("shared.py", "one\n"),
               ("integrate", "SON"): lambda n, p: AgentResult(True, INTEGRATE)},
    "codex": {("implement", "T2"): lambda n, p: (time.sleep(0.3), writer("shared.py", "two\n")(n, p))[1]},
}, {"T1": ("claude", "codex", "done"), "T2": ("codex", "claude", "done")}, demo=False,
    side={"claude": {("merge", "T1"): resolve}, "codex": {("merge", "T2"): resolve}}, extra_checks=check7))


# 8. a question mid-task: claude's job drops a request in its mailbox (as pixelcrew-ask does),
#    codex answers in a separate read-only process, and the answer reaches claude's turn
def asker(n, p):
    box = LAST["stub"].env_extra.get("PIXELCREW_MAILBOX")
    assert box and os.path.isdir(box) and "pixelcrew-ask" in p, "the job should get a mailbox and the tool note"
    req = os.path.join(box, "q1.req.json")
    open(req + ".tmp", "w").write(json.dumps({"kind": "ask", "args": {"to": "T2", "question": "What is the helper called?"}}))
    os.replace(req + ".tmp", req)
    for _ in range(100):
        if os.path.exists(os.path.join(box, "q1.res.json")):
            reply = json.load(open(os.path.join(box, "q1.res.json")))
            return AgentResult(True, "used " + reply["text"])
        time.sleep(0.1)
    return AgentResult(False, "", "no answer")
def check8(run, orch):
    asked = [e for e in run.events if e["kind"] == "qa.asked"]
    answered = [e for e in run.events if e["kind"] == "qa.answered"]
    t1 = next(t for t in orch.tasks if t["id"] == "T1")
    return len(asked) == 1 and asked[0]["to"] == "codex" and len(answered) == 1 and answered[0]["ok"] \
        and t1["report"] == "used notes_add()" and not os.path.exists(os.path.join(run.workspace, ".pixelcrew"))
results.append(run_case("question answered mid-task", {
    "claude": {("plan", None): plan_ok, ("implement", "T1"): asker, ("integrate", "SON"): lambda n, p: AgentResult(True, INTEGRATE)},
}, {"T1": ("claude", "codex", "done"), "T2": ("codex", "claude", "done")}, demo=False,
    side={"codex": {("answer", "T1"): lambda n, p: AgentResult(True, "notes_add()" if "helper called" in p else "?")}},
    extra_checks=check8))
sys.exit(0 if all(results) else 1)
