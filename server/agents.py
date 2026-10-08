"""Launch the Claude Code, Codex, Gemini and Grok CLIs headlessly and
translate their streaming output into one normalized event vocabulary:

  agent.state        what the agent is doing right now (drives the office animation)
  agent.tool         a tool call / shell command, with a short target
  agent.tool_result  its outcome
  agent.message      text the agent said
  agent.thinking     reasoning summary
  agent.todo         the agent's own checklist
  agent.usage        tokens / cost
  agent.limit        subscription rate-limit status (Claude only)
  file.changed       a file the agent reported touching

Modes: "plan" and "chat" are read-only, "review" may also run checks
(tests, builds) but never edit, "work" edits files.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import status
from .config import DATA_DIR, child_env
from .i18n import t as tr
from .quota import is_quota_error

READ_ONLY_CLAUDE_TOOLS = [
    "Read", "Grep", "Glob", "LS",
    "Bash(ls:*)", "Bash(cat:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(wc:*)", "Bash(find:*)",
    "Bash(rg:*)", "Bash(grep:*)", "Bash(tree:*)", "Bash(git status:*)", "Bash(git diff:*)",
    "Bash(git log:*)", "Bash(git show:*)",
]
CHECK_CLAUDE_TOOLS = [
    "Bash(npm test:*)", "Bash(npm run:*)", "Bash(npx:*)", "Bash(node:*)", "Bash(pnpm:*)", "Bash(yarn:*)",
    "Bash(bun:*)", "Bash(deno:*)", "Bash(tsc:*)", "Bash(python:*)", "Bash(python3:*)", "Bash(pytest:*)",
    "Bash(uv run:*)", "Bash(go test:*)", "Bash(go build:*)", "Bash(cargo test:*)", "Bash(cargo build:*)",
    "Bash(make:*)", "Bash(swift test:*)", "Bash(swift build:*)",
]
SAFE_WORK_CLAUDE_TOOLS = READ_ONLY_CLAUDE_TOOLS + [
    "Edit", "Write", "MultiEdit", "NotebookEdit", "TodoWrite",
    "Bash(mkdir:*)", "Bash(touch:*)", "Bash(cp:*)", "Bash(mv:*)",
    "Bash(npm:*)", "Bash(npx:*)", "Bash(node:*)", "Bash(pnpm:*)", "Bash(yarn:*)", "Bash(bun:*)",
    "Bash(deno:*)", "Bash(tsc:*)", "Bash(python:*)", "Bash(python3:*)", "Bash(pip:*)",
    "Bash(pip3:*)", "Bash(pytest:*)", "Bash(uv:*)", "Bash(go:*)", "Bash(cargo:*)",
    "Bash(make:*)", "Bash(swift:*)", "Bash(xcodebuild:*)", "Bash(git add:*)",
]
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# Claude Code tools that make no sense in a one-turn PixelCrew job: they reach other sessions
# on the machine or schedule work for after the process has exited.
CLAUDE_NEVER_TOOLS = ["ListAgents", "SendMessage", "Monitor", "ScheduleWakeup", "CronCreate", "CronDelete",
                      "RemoteTrigger", "PushNotification"]
READ_CMDS = {"cat", "head", "tail", "less", "nl", "sed", "awk", "wc", "file", "stat", "bat"}
SEARCH_CMDS = {"rg", "grep", "find", "ls", "tree", "fd", "git"}


class Cancelled(Exception):
    pass


@dataclass
class AgentResult:
    ok: bool
    text: str = ""
    error: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    changed: List[str] = field(default_factory=list)
    duration: float = 0.0
    session: Optional[str] = None
    quota: bool = False  # failed because the subscription limit ran out


def _short(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _structured(text: str) -> bool:
    """Plan / review replies are JSON meant for the orchestrator, not prose."""
    t = text.strip()
    return "```json" in t or (t.startswith("{") and t.endswith("}"))


def _rel(path: str, cwd: str) -> str:
    if not path:
        return ""
    try:
        return str(Path(path).resolve().relative_to(Path(cwd).resolve()))
    except ValueError:
        return path


def unwrap_shell(command: str) -> str:
    """`/bin/zsh -lc "npm test"` -> `npm test`"""
    m = re.match(r"^\S*(?:ba|z)?sh\s+-l?c\s+(.*)$", command.strip(), re.S)
    if not m:
        return command
    inner = m.group(1).strip()
    try:
        parts = shlex.split(inner)
        if len(parts) == 1:
            return parts[0]
    except ValueError:
        pass
    return inner.strip("'\"")


def _param(inp: Dict[str, Any], *keys: str) -> str:
    """First non-empty string among keys, matched loosely: file_path, filePath and FilePath
    are the same key (CLIs differ in casing; Antigravity uses PascalCase)."""
    loose = {str(k).lower().replace("_", ""): v for k, v in inp.items()}
    for key in keys:
        value = loose.get(key.replace("_", ""))
        if isinstance(value, str) and value:
            return value
    return ""


def _tool_path(inp: Dict[str, Any]) -> str:
    return _param(inp, "file_path", "absolute_path", "notebook_path", "target_file", "path", "filename",
                  "dir_path", "target_directory", "directory_path", "search_path", "search_directory")


def is_edit_tool(name: str) -> bool:
    """Edit tools of CLIs whose tool names differ from Claude's (Gemini, Grok, Antigravity)."""
    n = name.lower()
    return n in {"edit", "write", "multiedit", "replace", "write_file", "edit_file", "create_file",
                 "apply_patch", "str_replace", "str_replace_editor", "search_replace", "notebookedit"} \
        or (any(k in n for k in ("write", "edit", "patch", "replace")) and "todo" not in n)


def describe_generic_tool(name: str, inp: Dict[str, Any], cwd: str) -> Any:
    """(state, target) for a tool call by name heuristics, for non-Claude CLIs."""
    n = name.lower()
    path = _rel(_tool_path(inp), cwd)
    if "todo" in n:
        return "planning", tr("agent.todo")
    if n.startswith(("get_", "kill_")):
        return "working", name
    if n in ("bash", "shell", "run_shell_command", "run_command", "run_terminal_cmd", "exec", "terminal") \
            or "shell" in n or "command" in n:
        cmd = unwrap_shell(_param(inp, "command", "cmd", "command_line"))
        return classify_command(cmd), cmd
    if is_edit_tool(name):
        return "writing", path
    if "web" in n or "fetch" in n or "browse" in n or "url" in n:
        return "browsing", _param(inp, "url", "query", "prompt")
    if "read" in n or "view" in n or n in ("cat", "open_file"):
        return "reading", path
    if any(k in n for k in ("grep", "search", "glob", "list", "find")) or n in ("ls", "list_directory"):
        return "searching", _param(inp, "pattern", "query", "search_term") or path
    if n in ("task", "agent", "subagent", "delegate") or "agent" in n:
        return "delegating", _param(inp, "description", "prompt")
    return "working", name


def classify_command(command: str) -> str:
    first = command.strip().split(" ", 1)[0].split("/")[-1] if command.strip() else ""
    if first in READ_CMDS:
        return "reading"
    if first in SEARCH_CMDS:
        if first == "git" and not re.match(r"git\s+(status|diff|log|show|grep|ls-files)", command.strip()):
            return "running"
        return "searching"
    return "running"


class BaseAgent:
    name = "agent"

    def __init__(self, run: Any, settings: Dict[str, Any], binary: Optional[str]) -> None:
        self.run = run
        self.settings = settings
        self.binary = binary
        self.proc: Optional[subprocess.Popen] = None
        self.cancelled = False
        self.ctx: Dict[str, Any] = {}
        self.session: Optional[str] = None
        self.effort: Optional[str] = None
        self.model: Optional[str] = None
        self.timed_out = False
        self._lock = threading.Lock()

    # -- event helpers -------------------------------------------------
    def emit(self, kind: str, **data: Any) -> None:
        payload = dict(self.ctx)
        payload.update(data)
        self.run.emit(kind, agent=self.name, **payload)

    def state(self, state: str, target: str = "") -> None:
        self.emit("agent.state", state=state, target=_short(target, 160))

    # -- lifecycle -----------------------------------------------------
    def cancel(self) -> None:
        self.cancelled = True
        with self._lock:
            proc = self.proc
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

            def hard_kill() -> None:
                time.sleep(4)
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass

            threading.Thread(target=hard_kill, daemon=True).start()

    def execute(self, prompt: str, mode: str, cwd: str, task_id: Optional[str], job: str,
                resume: Optional[str] = None, extra: Optional[Dict[str, Any]] = None,
                effort: Optional[str] = None, model: Optional[str] = None) -> AgentResult:
        """mode: plan | chat | review | work (see module docstring).
        resume: a previous session id to continue (chat follow-ups, fix rounds).
        effort: low | medium | high, or None for the CLI's own default.
        model: the model id for this job (models.pick), or None for the configured / CLI default."""
        self.effort = effort
        self.model = model
        if self.cancelled:
            raise Cancelled()
        self.ctx = {"task_id": task_id, "job": job}
        if extra:
            self.ctx.update(extra)
        self.session = None
        started = time.time()
        result = self._execute(prompt, mode, cwd, resume)
        result.duration = time.time() - started
        result.session = self.session
        if self.cancelled:
            raise Cancelled()
        self.state("idle" if result.ok else "error", "" if result.ok else result.error)
        return result

    def _execute(self, prompt: str, mode: str, cwd: str, resume: Optional[str]) -> AgentResult:
        raise NotImplementedError

    # -- process plumbing ---------------------------------------------
    def _spawn_and_stream(self, cmd: List[str], prompt: Optional[str], cwd: str, on_line: Any) -> subprocess.Popen:
        """prompt=None: the prompt is already in cmd (argv or a file), stdin stays empty."""
        env = child_env(self.name, self.settings)
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if prompt is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=True,
        )
        with self._lock:
            self.proc = proc
        stderr_tail: List[str] = []

        def pump_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                line = line.rstrip()
                if line:
                    stderr_tail.append(line)
                    del stderr_tail[:-40]

        t = threading.Thread(target=pump_stderr, daemon=True)
        t.start()

        timeout_s = int(self.settings.get("jobTimeoutMin", 30)) * 60
        timer = threading.Timer(timeout_s, self._timeout)
        timer.daemon = True
        timer.start()
        try:
            if prompt is not None:
                assert proc.stdin is not None
                try:
                    proc.stdin.write(prompt)
                    proc.stdin.close()
                except BrokenPipeError:
                    pass
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue  # log noise on stdout
                try:
                    on_line(data)
                except Exception as exc:  # a parser bug must not kill the job
                    self.emit("agent.stderr", text=tr("agent.parse_failed", err=exc))
            proc.wait()
        finally:
            timer.cancel()
            t.join(timeout=2)
        proc.stderr_tail = stderr_tail  # type: ignore[attr-defined]
        return proc

    def _timeout(self) -> None:
        self.emit("agent.stderr", text=tr("agent.timeout_stopping"))
        self.timed_out = True
        with self._lock:
            proc = self.proc
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass


class ClaudeAgent(BaseAgent):
    name = "claude"

    def _command(self, mode: str, resume: Optional[str] = None) -> List[str]:
        cmd = [self.binary or "claude", "-p", "--output-format", "stream-json", "--verbose"]
        if resume:
            cmd += ["--resume", resume]
        model = self.model or self.settings.get("claudeModel")
        if model:
            cmd += ["--model", model]
        if self.effort:
            cmd += ["--effort", self.effort]
        full = self.settings.get("permissions") == "full"
        if mode != "work":
            cmd += ["--disallowedTools", "Edit", "Write", "MultiEdit", "NotebookEdit", *CLAUDE_NEVER_TOOLS]
            if full and mode == "review":
                cmd += ["--allowedTools", "Read", "Grep", "Glob", "LS", "Bash"]
            elif mode == "review":
                cmd += ["--allowedTools", *READ_ONLY_CLAUDE_TOOLS, *CHECK_CLAUDE_TOOLS]
            else:
                cmd += ["--allowedTools", *READ_ONLY_CLAUDE_TOOLS]
        elif full:
            cmd += ["--dangerously-skip-permissions", "--disallowedTools", *CLAUDE_NEVER_TOOLS]
        else:
            cmd += ["--permission-mode", "acceptEdits", "--allowedTools", *SAFE_WORK_CLAUDE_TOOLS,
                    "--disallowedTools", *CLAUDE_NEVER_TOOLS]
        return cmd

    def _execute(self, prompt: str, mode: str, cwd: str, resume: Optional[str]) -> AgentResult:
        self.timed_out = False
        self.state("booting")
        pending: Dict[str, Dict[str, Any]] = {}
        changed: List[str] = []
        out: Dict[str, Any] = {"text": "", "error": "", "usage": {}, "is_error": False, "saw_result": False}

        def on_line(ev: Dict[str, Any]) -> None:
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                self.session = ev.get("session_id")
                status.record_model("claude", ev.get("model"))
                self.emit("agent.session", session=self.session, model=ev.get("model"))
                self.state("thinking")
            elif t == "rate_limit_event":
                info = ev.get("rate_limit_info") or {}
                status.record_claude_limit(info.get("rateLimitType"), info.get("utilization"),
                                           info.get("resetsAt"), info.get("status"))
                if info.get("status") == "rejected":
                    out["rejected"] = True
                self.emit("agent.limit", status=info.get("status"), utilization=info.get("utilization"),
                          window=info.get("rateLimitType"), resets_at=info.get("resetsAt"))
            elif t == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    self._assistant_block(block, cwd, pending, changed)
            elif t == "user":
                content = (ev.get("message") or {}).get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            self._tool_result(block, pending)
            elif t == "result":
                out["saw_result"] = True
                out["text"] = ev.get("result") or ""
                out["is_error"] = bool(ev.get("is_error")) or ev.get("subtype") not in (None, "success")
                usage = ev.get("usage") or {}
                out["usage"] = {
                    "input_tokens": (usage.get("input_tokens") or 0)
                    + (usage.get("cache_read_input_tokens") or 0)
                    + (usage.get("cache_creation_input_tokens") or 0),
                    "output_tokens": usage.get("output_tokens") or 0,
                    "cost_usd": ev.get("total_cost_usd") or 0,
                    "turns": ev.get("num_turns"),
                }
                self.emit("agent.usage", **out["usage"])

        proc = self._spawn_and_stream(self._command(mode, resume), prompt, cwd, on_line)
        tail = "\n".join(getattr(proc, "stderr_tail", [])[-8:])
        status.CLAUDE_USAGE.request(force=True)  # the job just used quota; re-read /usage
        if self.timed_out:
            return AgentResult(False, out["text"], tr("agent.timeout"), out["usage"], changed)
        if out["is_error"] or (proc.returncode not in (0, None) and not out["saw_result"]):
            err = out["text"] or tail or tr("agent.exit_code", tool="claude", code=proc.returncode)
            if "not logged in" in err.lower() or "/login" in err:
                err = tr("agent.login_hint", tool="Claude Code CLI", cmd="claude auth login")
            res = AgentResult(False, out["text"], _short(err, 600), out["usage"], changed)
            res.quota = bool(out.get("rejected")) or is_quota_error(err)
            return res
        return AgentResult(True, out["text"], "", out["usage"], changed)

    def _assistant_block(self, block: Dict[str, Any], cwd: str, pending: Dict[str, Dict[str, Any]], changed: List[str]) -> None:
        kind = block.get("type")
        if kind == "text" and block.get("text", "").strip():
            text = block["text"].strip()
            structured = _structured(text)
            self.emit("agent.message", text=text, structured=structured)
            self.state("talking", "" if structured else text)
        elif kind == "thinking" and block.get("thinking", "").strip():
            self.emit("agent.thinking", text=_short(block["thinking"], 600))
            self.state("thinking")
        elif kind == "tool_use":
            name = block.get("name") or "?"
            inp = block.get("input") or {}
            state, target = self._describe_tool(name, inp, cwd)
            tid = block.get("id") or ""
            pending[tid] = {"tool": name}
            if name == "TodoWrite":
                items = [
                    {"text": i.get("content") or i.get("activeForm") or "", "done": i.get("status") == "completed",
                     "active": i.get("status") == "in_progress"}
                    for i in inp.get("todos") or []
                ]
                self.emit("agent.todo", items=items)
            self.emit("agent.tool", tid=tid, tool=name, state=state, target=target)
            self.state(state, target)
            if self._is_edit(name):
                path = _rel(_tool_path(inp), cwd)
                if path:
                    changed.append(path)
                    self.emit("file.changed", path=path, change="write" if name.lower().startswith("write") else "edit")

    @staticmethod
    def _is_edit(name: str) -> bool:
        return name in EDIT_TOOLS

    @staticmethod
    def _describe_tool(name: str, inp: Dict[str, Any], cwd: str) -> Any:
        if name == "Read":
            return "reading", _rel(inp.get("file_path", ""), cwd)
        if name in ("Grep", "Glob", "LS"):
            return "searching", inp.get("pattern") or _rel(inp.get("path", ""), cwd)
        if name in EDIT_TOOLS:
            return "writing", _rel(inp.get("file_path") or inp.get("notebook_path") or "", cwd)
        if name == "Bash":
            cmd = inp.get("command", "")
            return classify_command(cmd), cmd
        if name in ("WebFetch", "WebSearch"):
            return "browsing", inp.get("url") or inp.get("query") or ""
        if name in ("Task", "Agent"):
            return "delegating", inp.get("description") or ""
        if name == "TodoWrite":
            return "planning", tr("agent.todo")
        return "working", name

    def _tool_result(self, block: Dict[str, Any], pending: Dict[str, Dict[str, Any]]) -> None:
        content = block.get("content")
        if isinstance(content, list):
            content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        text = str(content or "")
        tid = block.get("tool_use_id") or ""
        info = pending.pop(tid, {})
        self.emit(
            "agent.tool_result", tid=tid, tool=info.get("tool"), ok=not block.get("is_error"),
            output=text[-2000:],
        )
        self.state("thinking")


class CodexAgent(BaseAgent):
    name = "codex"

    def _command(self, mode: str, cwd: str, resume: Optional[str] = None) -> List[str]:
        model = self.model or self.settings.get("codexModel")
        full = self.settings.get("permissions") == "full"
        effort = ["-c", f'model_reasoning_effort="{self.effort}"'] if self.effort else []
        if resume:
            # `exec resume` has no -s/-C; the sandbox comes from config and the
            # working directory from the process cwd.
            sandbox = "read-only" if mode != "work" else ("danger-full-access" if full else "workspace-write")
            cmd = [self.binary or "codex", "exec", "resume", resume, "--json", "--skip-git-repo-check",
                   "-c", f'sandbox_mode="{sandbox}"', *effort]
            if model:
                cmd += ["-m", model]
            cmd.append("-")
            return cmd
        cmd = [self.binary or "codex", "exec", "--json", "--skip-git-repo-check", "-C", cwd, *effort]
        if model:
            cmd += ["-m", model]
        if mode != "work":
            cmd += ["-s", "read-only"]
        elif full:
            cmd += ["-s", "danger-full-access"]
        else:
            cmd += ["-s", "workspace-write"]
        cmd.append("-")  # prompt comes from stdin
        return cmd

    def _execute(self, prompt: str, mode: str, cwd: str, resume: Optional[str]) -> AgentResult:
        self.timed_out = False
        self.state("booting")
        changed: List[str] = []
        out: Dict[str, Any] = {"text": "", "error": "", "usage": {}}
        seen: Dict[str, str] = {}

        def on_line(ev: Dict[str, Any]) -> None:
            t = ev.get("type", "")
            if t == "thread.started":
                self.session = ev.get("thread_id")
                self.emit("agent.session", session=self.session)
                self.state("thinking")
            elif t in ("item.started", "item.updated", "item.completed"):
                self._item(t, ev.get("item") or {}, cwd, changed, out, seen)
            elif t == "turn.completed":
                usage = ev.get("usage") or {}
                out["usage"] = {
                    "input_tokens": usage.get("input_tokens") or 0,
                    "output_tokens": (usage.get("output_tokens") or 0) + (usage.get("reasoning_output_tokens") or 0),
                    "cached_tokens": usage.get("cached_input_tokens") or 0,
                }
                self.emit("agent.usage", **out["usage"])
            elif t == "turn.failed":
                out["error"] = ((ev.get("error") or {}).get("message")) or tr("agent.turn_failed", tool="Codex")
            elif t == "error":
                msg = ev.get("message") or ""
                # Codex also reports transient reconnects as errors; keep the last one.
                out["error"] = msg
                self.emit("agent.stderr", text=_short(msg, 400))

        proc = self._spawn_and_stream(self._command(mode, cwd, resume), prompt, cwd, on_line)
        tail = "\n".join(getattr(proc, "stderr_tail", [])[-8:])
        try:
            info = status.codex_thread_info(self.session or "")
            if info.get("model"):
                status.record_model("codex", info["model"], info.get("effort"))
                self.emit("agent.model", model=info["model"], effort=info.get("effort"))
        except OSError:
            pass
        if self.timed_out:
            return AgentResult(False, out["text"], tr("agent.timeout"), out["usage"], changed)
        if proc.returncode != 0:
            err = out["error"] or tail or tr("agent.exit_code", tool="codex", code=proc.returncode)
            res = AgentResult(False, out["text"], _short(err, 600), out["usage"], changed)
            res.quota = is_quota_error(err)
            return res
        return AgentResult(True, out["text"], "", out["usage"], changed)

    def _item(self, phase: str, item: Dict[str, Any], cwd: str, changed: List[str], out: Dict[str, Any],
              seen: Dict[str, str]) -> None:
        kind = item.get("type") or item.get("item_type")
        iid = item.get("id") or ""
        done = phase == "item.completed"
        if kind == "agent_message" and done:
            text = (item.get("text") or "").strip()
            if text:
                out["text"] = text
                structured = _structured(text)
                self.emit("agent.message", text=text, structured=structured)
                self.state("talking", "" if structured else text)
        elif kind == "reasoning" and done:
            text = (item.get("text") or "").strip()
            if text:
                self.emit("agent.thinking", text=_short(text, 600))
            self.state("thinking")
        elif kind == "command_execution":
            command = unwrap_shell(item.get("command") or "")
            state = classify_command(command)
            if phase == "item.started":
                self.emit("agent.tool", tid=iid, tool="shell", state=state, target=command)
                self.state(state, command)
            elif done:
                code = item.get("exit_code")
                self.emit(
                    "agent.tool_result", tid=iid, tool="shell", ok=code in (0, None),
                    output=(item.get("aggregated_output") or "")[-2000:], exit_code=code,
                )
                self.state("thinking")
        elif kind == "file_change":
            if phase == "item.started" or (done and iid not in seen):
                seen[iid] = "file"
                for ch in item.get("changes") or []:
                    path = _rel(ch.get("path") or "", cwd)
                    if not path:
                        continue
                    changed.append(path)
                    self.emit("agent.tool", tid=f"{iid}:{path}", tool="apply_patch", state="writing", target=path)
                    self.emit("file.changed", path=path, change=ch.get("kind") or "update")
                    self.state("writing", path)
        elif kind == "mcp_tool_call" and phase == "item.started":
            target = f"{item.get('server', '')}.{item.get('tool', '')}"
            self.emit("agent.tool", tid=iid, tool="mcp", state="working", target=target)
            self.state("working", target)
        elif kind == "web_search" and phase == "item.started":
            self.emit("agent.tool", tid=iid, tool="web_search", state="browsing", target=item.get("query") or "")
            self.state("browsing", item.get("query") or "")
        elif kind == "todo_list":
            items = [{"text": i.get("text", ""), "done": bool(i.get("completed"))} for i in item.get("items") or []]
            sig = json.dumps(items)
            if seen.get(iid) != sig:
                seen[iid] = sig
                first_open = next((i for i in items if not i["done"]), None)
                if first_open:
                    first_open["active"] = True
                self.emit("agent.todo", items=items)
        elif kind == "error" and done:
            msg = item.get("message") or ""
            self.emit("agent.stderr", text=_short(msg, 400))


def _prompt_file(prompt: str) -> Path:
    """Grok reads long prompts from a file; keeps them out of `ps` and argv limits."""
    folder = DATA_DIR / "tmp"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"prompt-{uuid.uuid4().hex}.md"
    path.write_text(prompt, encoding="utf-8")
    return path


class GeminiAgent(BaseAgent):
    """Gemini CLI: `gemini -o stream-json` emits init / message (deltas) /
    tool_use / tool_result / error / result lines."""
    name = "gemini"

    def _command(self, mode: str, resume: Optional[str], session: str) -> List[str]:
        cmd = [self.binary or "gemini", "-o", "stream-json", "--skip-trust"]
        if mode != "work":
            approval = "plan"
        elif self.settings.get("permissions") == "full":
            approval = "yolo"
        else:
            approval = "auto_edit"
        cmd += ["--approval-mode", approval]
        model = self.settings.get("geminiModel")
        if model:
            cmd += ["-m", model]
        if resume:
            cmd += ["--resume", resume]
        else:
            cmd += ["--session-id", session]
        # The prompt arrives on stdin; -p switches to headless mode and is appended to it.
        cmd += ["-p", " "]
        return cmd

    def _execute(self, prompt: str, mode: str, cwd: str, resume: Optional[str]) -> AgentResult:
        self.timed_out = False
        self.state("booting")
        session = resume or str(uuid.uuid4())
        self.session = session
        changed: List[str] = []
        pending: Dict[str, str] = {}
        out: Dict[str, Any] = {"text": "", "error": "", "usage": {}, "buf": [], "status": None}

        def flush() -> None:
            text = "".join(out["buf"]).strip()
            out["buf"] = []
            if text:
                out["text"] = text
                structured = _structured(text)
                self.emit("agent.message", text=text, structured=structured)
                self.state("talking", "" if structured else text)

        def on_line(ev: Dict[str, Any]) -> None:
            t = ev.get("type")
            if t == "init":
                self.session = ev.get("session_id") or self.session
                model = ev.get("model")
                if model:
                    status.record_model("gemini", model)
                self.emit("agent.session", session=self.session, model=model)
                self.state("thinking")
            elif t == "message":
                if ev.get("role") == "assistant":
                    content = ev.get("content") or ""
                    if ev.get("delta"):
                        out["buf"].append(content)
                    else:
                        flush()
                        out["buf"].append(content)
                    if not out["buf"][:-1]:
                        self.state("talking")
            elif t == "tool_use":
                flush()
                name = ev.get("tool_name") or "?"
                inp = ev.get("parameters") or {}
                tid = ev.get("tool_id") or ""
                pending[tid] = name
                state, target = describe_generic_tool(name, inp, cwd)
                if "todo" in name.lower():
                    items = [{"text": i.get("description") or i.get("content") or "",
                              "done": i.get("status") == "completed", "active": i.get("status") == "in_progress"}
                             for i in inp.get("todos") or [] if isinstance(i, dict)]
                    self.emit("agent.todo", items=items)
                self.emit("agent.tool", tid=tid, tool=name, state=state, target=target)
                self.state(state, target)
                if is_edit_tool(name):
                    path = _rel(_tool_path(inp), cwd)
                    if path:
                        changed.append(path)
                        self.emit("file.changed", path=path, change="write" if "write" in name else "edit")
            elif t == "tool_result":
                tid = ev.get("tool_id") or ""
                err = ev.get("error") or {}
                text = ev.get("output") or (err.get("message") if isinstance(err, dict) else str(err)) or ""
                self.emit("agent.tool_result", tid=tid, tool=pending.pop(tid, None),
                          ok=ev.get("status") == "success", output=str(text)[-2000:])
                self.state("thinking")
            elif t == "error":
                msg = ev.get("message") or ""
                if ev.get("severity") != "warning":
                    out["error"] = msg
                self.emit("agent.stderr", text=_short(msg, 400))
            elif t == "result":
                flush()
                out["status"] = ev.get("status")
                err = ev.get("error") or {}
                if ev.get("status") not in (None, "success"):
                    out["error"] = (err.get("message") if isinstance(err, dict) else str(err)) or out["error"] \
                        or tr("agent.turn_failed", tool="Gemini")
                stats = ev.get("stats") or {}
                out["usage"] = {
                    "input_tokens": stats.get("input_tokens") or stats.get("input") or 0,
                    "output_tokens": stats.get("output_tokens") or 0,
                    "cached_tokens": stats.get("cached") or 0,
                    "turns": stats.get("tool_calls"),
                }
                self.emit("agent.usage", **out["usage"])

        proc = self._spawn_and_stream(self._command(mode, resume, session), prompt, cwd, on_line)
        flush()
        tail = "\n".join(getattr(proc, "stderr_tail", [])[-8:])
        if self.timed_out:
            return AgentResult(False, out["text"], tr("agent.timeout"), out["usage"], changed)
        if out["status"] not in (None, "success") or (proc.returncode != 0 and out["status"] is None):
            err = out["error"] or tail or tr("agent.exit_code", tool="gemini", code=proc.returncode)
            if re.search(r"auth|login|credential", err, re.I) and not is_quota_error(err):
                err = tr("agent.login_hint", tool="Gemini CLI", cmd="gemini")
            res = AgentResult(False, out["text"], _short(err, 600), out["usage"], changed)
            res.quota = is_quota_error(err)
            return res
        return AgentResult(True, out["text"], "", out["usage"], changed)


GROK_EXTRA_TOOLS = "spawn_subagent,image_gen,image_edit,image_to_video,reference_to_video"
GROK_EDIT_TOOLS = "write,search_replace"


class GrokAgent(ClaudeAgent):
    """Grok Build: `--output-format streaming-messages-json` speaks the same
    assistant / user / result line format as Claude Code, so the Claude
    parser is reused; only the command line and tool names differ.

    Headless Grok ends the whole turn as "cancelled" when a tool call is
    denied (plan / dontAsk modes), so permissions are not used for limits.
    Instead it always approves and the OS sandbox draws the line, like
    Codex: read-only for plan, chat and review (checks can still run),
    the workspace folder for work, none with full permissions. A session
    keeps the sandbox it was created with."""
    name = "grok"

    def _grok_command(self, mode: str, cwd: str, resume: Optional[str], session: str, prompt_path: Path) -> List[str]:
        cmd = [self.binary or "grok", "--output-format", "streaming-messages-json", "--cwd", cwd,
               "--prompt-file", str(prompt_path), "--no-subagents", "--always-approve"]
        if resume:
            cmd += ["--resume", resume]
        else:
            cmd += ["--session-id", session]
        model = self.model or self.settings.get("grokModel")
        if model:
            cmd += ["-m", model]
        if self.effort:
            cmd += ["--reasoning-effort", self.effort]
        if mode != "work":
            cmd += ["--sandbox", "read-only", "--disallowed-tools", f"{GROK_EDIT_TOOLS},{GROK_EXTRA_TOOLS}"]
        else:
            if self.settings.get("permissions") != "full":
                cmd += ["--sandbox", "workspace"]
            cmd += ["--disallowed-tools", GROK_EXTRA_TOOLS]
        return cmd

    def _tool_result(self, block: Dict[str, Any], pending: Dict[str, Dict[str, Any]]) -> None:
        """Grok wraps tool output in a JSON envelope; show the part meant for the model."""
        content = block.get("content")
        if isinstance(content, str) and content.startswith(("{", "[")):
            try:
                data = json.loads(content)
            except ValueError:
                data = None
            text = None
            if isinstance(data, dict):
                inner = next((v for v in data.values() if isinstance(v, dict)), {})
                text = (data.get("output_for_prompt") or inner.get("tool_output_for_prompt")
                        or inner.get("content") or inner.get("raw_output"))
            elif isinstance(data, list):
                text = "\n".join(str(((c or {}).get("content") or {}).get("text") or "")
                                 for c in data if isinstance(c, dict))
            if isinstance(text, str):
                block = dict(block, content=text)
        super()._tool_result(block, pending)

    @staticmethod
    def _describe_tool(name: str, inp: Dict[str, Any], cwd: str) -> Any:
        return describe_generic_tool(name, inp, cwd)

    @staticmethod
    def _is_edit(name: str) -> bool:
        return is_edit_tool(name)

    def _execute(self, prompt: str, mode: str, cwd: str, resume: Optional[str]) -> AgentResult:
        self.timed_out = False
        self.state("booting")
        session = resume or str(uuid.uuid4())
        self.session = session
        pending: Dict[str, Dict[str, Any]] = {}
        changed: List[str] = []
        out: Dict[str, Any] = {"text": "", "usage": {}, "is_error": False, "saw_result": False, "last": ""}

        def on_line(ev: Dict[str, Any]) -> None:
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                self.session = ev.get("session_id") or self.session
                if ev.get("model"):
                    status.record_model("grok", ev.get("model"))
                self.emit("agent.session", session=self.session, model=ev.get("model"))
                self.state("thinking")
            elif t == "assistant":
                message = ev.get("message") or {}
                if message.get("model"):
                    status.record_model("grok", message["model"])
                for block in message.get("content") or []:
                    if isinstance(block, dict):
                        if block.get("type") == "text" and block.get("text", "").strip():
                            out["last"] = block["text"].strip()
                        self._assistant_block(block, cwd, pending, changed)
            elif t == "user":
                content = (ev.get("message") or {}).get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            self._tool_result(block, pending)
            elif t == "result":
                out["saw_result"] = True
                self.session = ev.get("session_id") or self.session
                out["text"] = ev.get("result") or out["last"]
                out["is_error"] = bool(ev.get("is_error")) or ev.get("subtype") not in (None, "success")
                out["errors"] = [str(e) for e in ev.get("errors") or []]
                usage = ev.get("usage") or {}
                out["usage"] = {
                    "input_tokens": (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0),
                    "output_tokens": usage.get("output_tokens") or 0,
                    "cost_usd": ev.get("total_cost_usd") or 0,
                    "turns": ev.get("num_turns"),
                }
                self.emit("agent.usage", **out["usage"])

        prompt_path = _prompt_file(prompt)
        try:
            proc = self._spawn_and_stream(self._grok_command(mode, cwd, resume, session, prompt_path), None, cwd, on_line)
        finally:
            try:
                prompt_path.unlink()
            except OSError:
                pass
        # Grok does not report its remaining quota headlessly; keep its spend instead.
        status.record_spend("grok", float(out["usage"].get("cost_usd") or 0))
        status.GROK_USAGE.request(force=True)  # the job just used quota; re-read the allowance
        text = out["text"] or out["last"]
        tail = "\n".join(getattr(proc, "stderr_tail", [])[-8:])
        if self.timed_out:
            return AgentResult(False, text, tr("agent.timeout"), out["usage"], changed)
        if out["is_error"] or (proc.returncode not in (0, None)) or (not out["saw_result"] and not text):
            err = tail or (text if out["is_error"] else "") or "; ".join(out.get("errors") or []) \
                or tr("agent.exit_code", tool="grok", code=proc.returncode)
            if re.search(r"not (logged|signed) in|grok login|unauthori[sz]ed|401", err, re.I):
                err = tr("agent.login_hint", tool="Grok Build CLI", cmd="grok login")
            res = AgentResult(False, text, _short(err, 600), out["usage"], changed)
            res.quota = is_quota_error(err)
            return res
        return AgentResult(True, text, "", out["usage"], changed)


class AntigravityAgent(BaseAgent):
    """Gemini through Antigravity CLI (`agy`), Google's successor to Gemini CLI.
    `agy -p ... --output-format stream-json` emits one JSON object per line:
    init, step_update (agent text deltas and tool steps, ACTIVE then DONE) and a
    final result envelope with conversation_id, status, response and usage.

    Headless agy soft-denies every shell command it would ask about, and its
    plan mode answers with a plan file instead of the requested reply. So, like
    Codex and Grok, it runs with tools approved inside a macOS Seatbelt profile:
    read-only for plan, chat and review (checks still run), writes limited to
    the workspace (plus agy's own state and temp dirs) for work, and no
    sandbox with full permissions."""
    name = "gemini"

    @staticmethod
    def _profile(mode: str, cwd: str, full: bool) -> Optional[str]:
        def lit(path: str) -> str:
            return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'
        workspace = lit(os.path.realpath(cwd))
        if mode != "work":
            return f"(version 1)(allow default)(deny file-write* (subpath {workspace}))"
        if full:
            return None
        home = os.path.realpath(str(Path.home()))
        writable = [workspace] + [lit(p) for p in (
            f"{home}/.gemini", f"{home}/.cache", f"{home}/Library/Caches", f"{home}/.npm",
            "/private/tmp", "/private/var/folders", "/dev")]
        return "(version 1)(allow default)(deny file-write*)(allow file-write* " + \
            " ".join(f"(subpath {w})" for w in writable) + ")"

    def _command(self, prompt: str, mode: str, cwd: str, resume: Optional[str]) -> List[str]:
        cmd = [self.binary or "agy", "-p", prompt, "--output-format", "stream-json", "--dangerously-skip-permissions"]
        if resume:
            cmd += ["--conversation", resume]
        model = self.model or self.settings.get("geminiModel")
        if model:
            cmd += ["--model", model]
        # Antigravity model ids carry their own effort (gemini-3.8-flash-low); --effort only without one.
        if self.effort and not re.search(r"-(low|medium|high)$", model or ""):
            cmd += ["--effort", self.effort]
        profile = self._profile(mode, cwd, self.settings.get("permissions") == "full")
        return ["/usr/bin/sandbox-exec", "-p", profile, *cmd] if profile else cmd

    def _execute(self, prompt: str, mode: str, cwd: str, resume: Optional[str]) -> AgentResult:
        self.timed_out = False
        self.state("booting")
        self.session = resume
        changed: List[str] = []
        texts: Dict[Any, List[str]] = {}
        tools: Dict[str, str] = {}
        closed: set = set()
        seen_model: Dict[str, str] = {}
        out: Dict[str, Any] = {"text": "", "error": "", "usage": {}, "status": None}

        def flush(idx: Any) -> None:
            text = "".join(texts.pop(idx, [])).strip()
            if text:
                out["text"] = text
                structured = _structured(text)
                self.emit("agent.message", text=text, structured=structured)
                self.state("talking", "" if structured else text)

        def on_line(ev: Dict[str, Any]) -> None:
            kind = ev.get("event") or ev.get("type")
            if kind == "init":
                self.session = ev.get("conversation_id") or self.session
                model = (ev.get("init") or {}).get("model")
                if model:
                    seen_model["model"] = model
                    status.record_model("gemini", model)
                self.emit("agent.session", session=self.session, model=model)
                self.state("thinking")
            elif kind == "step_update":
                st = ev.get("step_update") or {}
                idx, typ, done = st.get("step_index"), st.get("step_type"), st.get("state") in ("DONE", "ERROR")
                if typ == "agent_response":
                    if st.get("text_delta"):
                        texts.setdefault(idx, []).append(st["text_delta"])
                        self.state("talking")
                    if done:
                        flush(idx)
                elif typ == "tool":
                    info = st.get("tool_info") or {}
                    name = info.get("name") or st.get("tool_name") or ""
                    tid = f"s{idx}"
                    if name and tid not in tools:
                        tools[tid] = name
                        params = info.get("parameters") or {}
                        state, target = describe_generic_tool(name, params, cwd)
                        self.emit("agent.tool", tid=tid, tool=name, state=state, target=target)
                        self.state(state, target)
                    if done and tid in tools and tid not in closed:
                        closed.add(tid)
                        error = info.get("error")
                        if isinstance(error, dict):
                            error = error.get("message") or json.dumps(error)
                        result = error or info.get("output") or ""
                        self.emit("agent.tool_result", tid=tid, tool=tools[tid], ok=not error,
                                  output=str(result)[-2000:])
                        # Only a write that went through counts (a sandbox may refuse it).
                        path = _rel(_tool_path(info.get("parameters") or {}), cwd)
                        if not error and is_edit_tool(tools[tid]) and path:
                            changed.append(path)
                            self.emit("file.changed", path=path, change="write" if "write" in tools[tid] else "edit")
                        self.state("thinking")
            elif kind == "result":
                res = ev.get("result") or {}
                for idx in list(texts):
                    flush(idx)
                self.session = res.get("conversation_id") or self.session
                out["status"] = res.get("status")
                out["text"] = res.get("response") or out["text"]
                err = res.get("error")
                if err:
                    out["error"] = err.get("message") if isinstance(err, dict) else str(err)
                usage = res.get("usage") or {}
                out["usage"] = {
                    "input_tokens": (usage.get("input_tokens") or 0) + (usage.get("cache_read_tokens") or 0),
                    "output_tokens": (usage.get("output_tokens") or 0) + (usage.get("thinking_tokens") or 0),
                    "turns": res.get("num_turns"),
                }
                self.emit("agent.usage", **out["usage"])

        proc = self._spawn_and_stream(self._command(prompt, mode, cwd, resume), None, cwd, on_line)
        status.AGY_USAGE.request(force=True)  # the job just used quota; re-read /usage
        if self.model and not seen_model.get("model"):
            status.record_model("gemini", self.model)  # agy's init omits the model unless it was set
        for idx in list(texts):
            flush(idx)
        tail = "\n".join(getattr(proc, "stderr_tail", [])[-8:])
        if self.timed_out:
            return AgentResult(False, out["text"], tr("agent.timeout"), out["usage"], changed)
        if out["status"] != "SUCCESS" or proc.returncode not in (0, None):
            err = out["error"] or tail or tr("agent.exit_code", tool="agy", code=out['status'] or proc.returncode)
            if re.search(r"authentication required|not (signed|logged) in|sign in", err, re.I):
                err = tr("agent.login_hint", tool="Antigravity CLI", cmd="agy")
            res = AgentResult(False, out["text"], _short(err, 600), out["usage"], changed)
            res.quota = is_quota_error(err)
            return res
        return AgentResult(True, out["text"], "", out["usage"], changed)


AGENT_CLASSES = {"claude": ClaudeAgent, "codex": CodexAgent, "gemini": GeminiAgent, "grok": GrokAgent}


def make_agent(name: str, run: Any, settings: Dict[str, Any], binary: Optional[str]) -> BaseAgent:
    # The Gemini seat is filled by Antigravity CLI when it is installed, else by Gemini CLI.
    if name == "gemini" and binary and Path(binary).name == "agy":
        return AntigravityAgent(run, settings, binary)
    return AGENT_CLASSES[name](run, settings, binary)
