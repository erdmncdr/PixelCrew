"""A scripted stand-in for the agent CLIs. It emits the same events the real
agents produce, so the whole pipeline and UI can be tried without spending
tokens or having either CLI logged in."""
from __future__ import annotations

import json
import os
import random
import time
from typing import Any, Dict, List, Optional

from .agents import AgentResult, BaseAgent, Cancelled
from .i18n import lang
from .quota import NAME


def L(en: str, tr: str) -> str:
    return tr if lang() == "tr" else en

# Tool names as each CLI reports them, so the demo feed looks like the real one.
TOOLS = {
    "claude": {"shell": "Bash", "write": "Write", "edit": "Edit", "read": "Read", "search": "Glob"},
    "codex": {"shell": "shell", "write": "apply_patch", "edit": "apply_patch", "read": "shell", "search": "shell"},
    "gemini": {"shell": "run_shell_command", "write": "write_file", "edit": "replace", "read": "read_file",
               "search": "glob"},
    "grok": {"shell": "bash", "write": "write_file", "edit": "edit_file", "read": "read_file", "search": "grep"},
}
# Who takes which demo task, in order of preference.
ROLES = [
    ("T1", ["claude", "gemini", "grok", "codex"]),
    ("T2", ["codex", "grok", "claude", "gemini"]),
    ("T3", ["grok", "codex", "gemini", "claude"]),
    ("T4", ["gemini", "claude", "codex", "grok"]),
]


def _team_in(prompt: str) -> List[str]:
    team = [a for a in TOOLS if f'- "{a}":' in prompt]
    return team or ["claude", "codex"]


FAKE_CODE = {
    "js": ["export function {n}(state, action) {{", "  if (!action) return state;", "  const next = {{ ...state }};",
           "  next.items = [...state.items, action.item];", "  return next;", "}}"],
    "css": [":root {{ --gap: 12px; }}", ".{n} {{", "  display: grid;", "  gap: var(--gap);", "}}"],
    "html": ["<main id=\"{n}\">", "  <h1>To-do</h1>", "  <ul class=\"list\"></ul>", "</main>"],
    "md": ["# {n}", "", "Install: `npm install`", "Run: `npm start`"],
    "py": ["def {n}(items):", "    return [i for i in items if i]"],
}


def _fake_diff(path: str) -> Dict[str, Any]:
    ext = path.rsplit(".", 1)[-1] if "." in path else "js"
    ext = "js" if ext in ("ts", "mjs", "jsx", "tsx") or ext not in FAKE_CODE else ext
    name = path.rsplit("/", 1)[-1].split(".")[0].replace("-", "_") or "main"
    lines = [l.format(n=name) for l in FAKE_CODE[ext]]
    body = "\n".join("+" + l for l in lines)
    diff = f"--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}"
    return {"path": path, "status": "added", "added": len(lines), "removed": 0, "diff": diff}


class DemoAgent(BaseAgent):
    def __init__(self, name: str, run: Any, settings: Dict[str, Any]) -> None:
        super().__init__(run, settings, None)
        self.name = name
        self.fake_diff: List[Dict[str, Any]] = []
        self.reviews_done = 0
        self.fail_on_limit = os.environ.get("PIXELCREW_DEMO_QUOTA_FAIL") == name

    def _sleep(self, lo: float, hi: float) -> None:
        end = time.time() + random.uniform(lo, hi)
        while time.time() < end:
            if self.cancelled:
                raise Cancelled()
            time.sleep(0.1)

    def execute(self, prompt: str, mode: str, cwd: str, task_id: Optional[str], job: str,
                resume: Optional[str] = None, extra: Optional[Dict[str, Any]] = None,
                effort: Optional[str] = None, model: Optional[str] = None) -> AgentResult:
        self.ctx = {"task_id": task_id, "job": job}
        if extra:
            self.ctx.update(extra)
        self.fake_diff = []
        started = time.time()
        self.state("booting")
        self._sleep(0.6, 1.0)
        self.emit("agent.session", session=f"demo-{self.name}-{int(started)}", model=model or "demo")
        if self.fail_on_limit and job == "implement":
            self.fail_on_limit = False
            self._think(L("Starting the task.", "Göreve başlıyorum."))
            self.emit("agent.stderr", text=f"{NAME.get(self.name, self.name)} usage limit reached. Your limit resets at 18:20.")
            self.state("error", L("limit reached", "limit doldu"))
            return AgentResult(False, "", f"{NAME.get(self.name, self.name)} usage limit reached", {}, [],
                               time.time() - started, quota=True)
        handler = getattr(self, f"_{job}")
        text = handler(prompt)
        self.emit("agent.usage", input_tokens=random.randint(8000, 30000), output_tokens=random.randint(600, 3000),
                  cost_usd=round(random.uniform(0.02, 0.12), 3) if self.name == "claude" else 0)
        self.state("idle")
        return AgentResult(True, text, "", {}, [f["path"] for f in self.fake_diff], time.time() - started,
                           session=resume or f"demo-{self.name}-{int(started)}")

    # -- scripted steps --------------------------------------------------
    def tool(self, kind: str) -> str:
        return TOOLS.get(self.name, TOOLS["codex"])[kind]

    def _think(self, text: str) -> None:
        self.state("thinking")
        self.emit("agent.thinking", text=text)
        self._sleep(1.2, 2.2)

    def _tool(self, state: str, tool: str, target: str, output: str = "", ok: bool = True) -> None:
        tid = f"demo{random.randint(1, 10**9)}"
        self.emit("agent.tool", tid=tid, tool=tool, state=state, target=target)
        self.state(state, target)
        self._sleep(1.0, 2.4)
        self.emit("agent.tool_result", tid=tid, tool=tool, ok=ok, output=output)

    def _reply(self, text: str) -> str:
        self.emit("agent.message", text=text, structured=True)
        return text

    def _say(self, text: str) -> None:
        self.emit("agent.message", text=text)
        self.state("talking", text)
        self._sleep(0.8, 1.4)

    def _integrate(self, prompt: str) -> str:
        self._think(L("Checking that the pieces work together.", "Parçaların birlikte çalıştığını kontrol ediyorum."))
        self._tool("running", self.tool("shell"), "npm test", "PASS  3 suites, 13 tests")
        self._tool("running", self.tool("shell"), "node src/app.js --smoke", "ok")
        self._say(L("Tests and the end-to-end run passed; no fix needed.", "Testler ve uçtan uca deneme geçti; düzeltme gerekmedi."))
        result = {"status": "ok", "summary": L("All tests pass and the app works end to end.", "Tüm testler geçti, uygulama uçtan uca çalışıyor."),
                  "checks": [{"command": "npm test", "result": "passed", "note": L("13 tests", "13 test")},
                             {"command": "node src/app.js --smoke", "result": "passed", "note": ""}]}
        return self._reply("```json\n" + json.dumps(result, ensure_ascii=False) + "\n```")

    def _chat(self, prompt: str) -> str:
        question = prompt.split("<message>")[-1].split("</message>")[0].strip()
        self._think(L("Working out the question.", "Soruyu anlamaya çalışıyorum."))
        self._tool("searching", self.tool("search"), "**/*" if self.name != "codex" else "rg --files", "hello.py")
        text = {
            "claude": L(f"You asked “{question[:80]}”. This is Demo mode, so I don't write a real answer; "
                        "in real mode I'd look at the files in the folder and answer here. To get something built, "
                        "switch to **Build** mode.",
                        f"“{question[:80]}” diye sordun. Demo modundayım, gerçek bir cevap üretmiyorum; "
                        "ama gerçek modda klasördeki dosyalara bakıp buraya cevap yazarım. Bir şey yaptırmak istersen "
                        "**İş yaptır** moduna geç."),
            "codex": L("In real mode I'd scan the folder and write a second opinion backed by command output. "
                       "Ask a follow-up and we'll remember the conversation.",
                       "Gerçek modda klasörü tarayıp komut çıktılarıyla desteklenmiş bir ikinci görüş yazarım. "
                       "Takip sorusu sorarsan konuşmayı hatırlarız."),
            "gemini": L("Agreed. I can read large files and documents in one go, so in real mode I'd sketch "
                        "the big picture of the project.",
                        "Ben de katılıyorum. Büyük dosyaları ve belgeleri bir kerede okuyabildiğim için gerçek modda "
                        "projenin genel resmini çıkarırım."),
            "grok": L("A quick addition: in real mode I'd try the idea right away with a fast prototype or script.",
                      "Kısa bir ek: gerçek modda hızlı bir prototip ya da betikle fikri hemen denerim."),
        }.get(self.name, L("Demo answer.", "Demo cevabı."))
        self._say(text)
        return text

    def _plan(self, prompt: str) -> str:
        request = prompt.split("<request>")[-1].split("</request>")[0].strip()
        if len(request.split()) <= 2 and not any(w in request.lower() for w in ("yap", "kur", "yaz", "ekle", "düzelt", "build", "make", "fix")):
            self._think(L("This isn't a work request; it looks like a greeting.", "Bu bir iş isteği değil, selamlaşma gibi."))
            answer = L("Hi! What should we build? Describe a task, or switch to **Ask** mode to ask something.",
                       "Merhaba! Ne yapmamı istersin? Bir iş yaz ya da **Soru sor** moduna geçip bir şey sor.")
            return self._reply("```json\n" + json.dumps({"answer": answer, "tasks": []}, ensure_ascii=False) + "\n```")
        short = request if len(request) < 60 else request[:57] + "…"
        self._think(L("Scanning the folder and splitting the request into parts.", "Klasörü tarayıp isteği parçalara ayırıyorum."))
        self._tool("searching", self.tool("search"), "**/*", "package.json\nsrc/\nREADME.md")
        self._tool("reading", self.tool("read"), "package.json", '{ "name": "demo" }')
        self._think(L("Drawing file boundaries so the agents can work in parallel.", "Ajanların paralel çalışabileceği dosya sınırlarını belirliyorum."))
        team = _team_in(prompt)
        who = {tid: next(a for a in prefs if a in team) for tid, prefs in ROLES}
        rationale = "; ".join(f"{tid} {NAME[a]}" for tid, a in who.items())
        plan = {
            "summary": L(f"Split “{short}” into four parts: UI, core logic, tests and wiring.",
                         f"“{short}” isteğini dört parçaya böldük: arayüz, çekirdek mantık, testler ve bağlama."),
            "rationale": L(f"Split: {rationale}. T1 and T2 touch different files, so they run at the same time.",
                           f"Dağılım: {rationale}. T1 ve T2 farklı dosyalara dokunduğu için aynı anda yürür."),
            "tasks": [
                {"id": "T1", "title": L("UI skeleton", "Arayüz iskeleti"), "assignee": who["T1"], "depends_on": [],
                 "description": L("Create index.html and the stylesheet. Done when: the page opens with an empty list.",
                                 "index.html ve stil dosyasını oluştur. Kabul: sayfa açılınca boş liste görünür."),
                 "files": ["index.html", "src/style.css"], "complexity": "medium"},
                {"id": "T2", "title": L("Core logic", "Çekirdek mantık"), "assignee": who["T2"], "depends_on": [],
                 "description": L("Write add, remove and complete for the list. Done when: pure functions, no side effects.",
                                 "Liste ekleme, silme ve tamamlama fonksiyonlarını yaz. Kabul: saf fonksiyonlar, yan etki yok."),
                 "files": ["src/store.js"], "complexity": "high"},
                {"id": "T3", "title": L("Unit tests", "Birim testleri"), "assignee": who["T3"], "depends_on": ["T2"],
                 "description": L("Write and run tests for store.js. Done when: all tests pass.",
                                 "store.js için testleri yaz ve çalıştır. Kabul: tüm testler geçer."),
                 "files": ["tests/store.test.js"], "complexity": "low"},
                {"id": "T4", "title": L("Wire the UI to the logic, and README", "Arayüzü mantığa bağla ve README"), "assignee": who["T4"], "depends_on": ["T1", "T2"],
                 "description": L("Connect events to the store functions; document setup in the README.",
                                 "Olayları store fonksiyonlarına bağla, README'ye kurulumu yaz."),
                 "files": ["src/app.js", "README.md"], "complexity": "medium"},
            ],
        }
        return self._reply("```json\n" + json.dumps(plan, ensure_ascii=False) + "\n```")

    def _plan_review(self, prompt: str) -> str:
        self._think(L("Checking the plan for dependencies and file conflicts.", "Planı bağımlılıklar ve dosya çakışmaları açısından kontrol ediyorum."))
        self._tool("reading", self.tool("read"), "package.json", '{ "name": "demo" }')
        self._say(L("The plan makes sense. T1 and T2 are in different files and can run in parallel.", "Plan mantıklı. T1 ve T2 farklı dosyalarda, paralel yürüyebilir."))
        review = {"verdict": "approve", "comments": [L("T3's tests depend on T2 correctly.", "T3 testleri T2'ye doğru şekilde bağlı."),
                                                     L("T4's done-when could be a little sharper, but it's not a blocker.", "T4 için kabul ölçütü biraz daha net olabilir ama engel değil.")], "revised_plan": None}
        return self._reply("```json\n" + json.dumps(review, ensure_ascii=False) + "\n```")

    def _task_files(self, prompt: str) -> List[str]:
        for line in prompt.splitlines():
            if line.startswith("Files you are expected to touch:"):
                return [f.strip() for f in line.split(":", 1)[1].split(",") if f.strip() and "(" not in f]
        return ["src/main.js"]

    def _implement(self, prompt: str) -> str:
        files = self._task_files(prompt)
        self._think(L("Reading the task and looking at the existing files.", "Görevi okuyup mevcut dosyalara bakıyorum."))
        self._tool("searching", self.tool("search"), "export function", "src/store.js:1")
        write, verify = L("Write {f}", "{f} dosyasını yaz"), L("Verify", "Doğrula")
        self.emit("agent.todo", items=[{"text": write.format(f=f), "done": False, "active": i == 0} for i, f in enumerate(files)]
                  + [{"text": verify, "done": False}])
        for i, path in enumerate(files):
            self._tool("writing", self.tool("write"), path)
            self.emit("file.changed", path=path, change="add")
            self.fake_diff.append(_fake_diff(path))
            self.emit("agent.todo", items=[{"text": write.format(f=f), "done": j <= i, "active": j == i + 1} for j, f in enumerate(files)]
                      + [{"text": verify, "done": False, "active": i + 1 == len(files)}])
        self._tool("running", self.tool("shell"), "npm test", "PASS tests/store.test.js\n  ✓ 12 passed (0.4s)")
        report = L(f"Wrote {', '.join(files)}. Verified with `npm test`: 12 tests passed.",
                   f"{', '.join(files)} yazıldı. `npm test` ile doğrulandı, 12 test geçti.")
        self._say(report)
        return report

    def _review(self, prompt: str) -> str:
        self.reviews_done += 1
        self._think(L("Reviewing the changes against the done-when criteria.", "Değişiklikleri kabul ölçütlerine göre inceliyorum."))
        self._tool("reading", self.tool("read"), "src/store.js", "export function add…")
        self._tool("running", self.tool("shell"), "npm test -- --silent", "✓ 12 passed")
        # The core-logic task gets one round of changes so the fix flow shows up in every team.
        if "implemented: T2 " in prompt and "RE-REVIEW" not in prompt:
            review = {"verdict": "changes_requested", "summary": L("remove() throws when it gets an id that doesn't exist.", "Silme fonksiyonu olmayan bir id gelince hata fırlatıyor."),
                      "issues": [{"severity": "medium", "file": "src/store.js", "message": L("remove() should return the list unchanged for an unknown id.", "remove() bilinmeyen id için listeyi olduğu gibi döndürmeli.")}]}
            self._say(L("Found a problem: remove() crashes on an unknown id.", "Bir sorun buldum: remove() bilinmeyen id'de patlıyor."))
        else:
            review = {"verdict": "approve", "summary": L("Criteria met, tests pass.", "Kabul ölçütleri karşılanmış, testler geçiyor."), "issues": []}
            self._say(L("Approved, no problems found.", "Onaylıyorum, sorun görmedim."))
        return self._reply("```json\n" + json.dumps(review, ensure_ascii=False) + "\n```")

    def _fix(self, prompt: str) -> str:
        self._think(L("Applying the review notes.", "İnceleme notlarını uyguluyorum."))
        self._tool("writing", self.tool("edit"), "src/store.js")
        self.emit("file.changed", path="src/store.js", change="update")
        self.fake_diff.append({"path": "src/store.js", "status": "modified", "added": 1, "removed": 1,
                               "diff": "--- a/src/store.js\n+++ b/src/store.js\n@@ -8,3 +8,3 @@\n export function remove(state, id) {\n-  if (!state.byId[id]) throw new Error('missing');\n+  if (!state.byId[id]) return state;\n }"})
        self._tool("running", self.tool("shell"), "npm test", "✓ 13 passed")
        report = L("remove() now returns the list unchanged for an unknown id; added a test for it.",
                   "remove() artık bilinmeyen id'de listeyi değiştirmeden döndürüyor; bunun için bir test eklendi.")
        self._say(report)
        return report
