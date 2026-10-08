"""Local HTTP server: static UI, JSON API and a Server-Sent Events stream.

Bound to 127.0.0.1 only. Because this server can start agents that edit files
and run commands, every request's Host header is checked (defeats DNS
rebinding) and state-changing requests must be same-origin JSON (defeats
cross-site form posts from other pages open in the browser)."""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import subprocess
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

from .bus import Hub, Run, list_runs, load_events
from .config import (AGENTS, DATA_DIR, DEFAULT_WORKSPACE, HEALTH, TOOL, WEB_DIR, install_command, load_settings,
                     login_command, resolve_workspace, save_settings)
from . import i18n, keys, models, status
from .chat import ChatManager
from .orchestrator import Orchestrator, names, team_of
from .quota import QuotaGuard
from .i18n import t

HUB = Hub()
CHAT = ChatManager(HUB)
_start_lock = threading.Lock()
_pick_lock = threading.Lock()


def _sh(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _applescript_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Handler(BaseHTTPRequestHandler):
    server_version = "PixelCrew/1.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing ------------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # keep the terminal quiet
        pass

    @property
    def allowed_hosts(self) -> set:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self.allowed_hosts

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        return origin in {f"http://{h}" for h in self.allowed_hosts}

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._send_json({"error": message}, status)

    def _body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            raise ValueError(t("req.too_big"))
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError(t("req.object"))
        return data

    # -- routing ---------------------------------------------------------------
    def do_GET(self) -> None:
        if not self._host_ok():
            return self._error(t("req.bad_host"), 403)
        url = urlparse(self.path)
        path = url.path
        if path == "/api/stream":
            return self._stream()
        if path == "/api/state":
            return self._state(refresh="refresh" in parse_qs(url.query))
        if path == "/api/health":
            return self._send_json({"health": HEALTH.get()})
        if path == "/api/status":
            return self._send_json(status.snapshot(load_settings()))
        if path == "/api/runs":
            return self._send_json({"runs": list_runs()})
        if path.startswith("/api/runs/") and path.endswith("/events"):
            run_id = path[len("/api/runs/"):-len("/events")]
            events = load_events(run_id)
            if events is None:
                return self._error(t("req.no_record"), 404)
            return self._send_json({"events": events})
        return self._static(path)

    def do_POST(self) -> None:
        if not self._host_ok() or not self._origin_ok():
            return self._error(t("req.origin"), 403)
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._error(t("req.json"), 415)
        try:
            body = self._body()
        except ValueError as exc:
            return self._error(str(exc))
        path = urlparse(self.path).path
        if path == "/api/settings":
            return self._settings(body)
        if path == "/api/run":
            return self._start_run(body)
        if path == "/api/approve":
            return self._approve(body)
        if path == "/api/cancel":
            return self._cancel(body)
        if path == "/api/ask":
            return self._ask(body)
        if path == "/api/workspace/pick":
            return self._pick_folder()
        if path == "/api/chat/reset":
            return self._chat_reset()
        if path == "/api/chat/cancel":
            CHAT.cancel()
            return self._send_json({"ok": True})
        if path == "/api/keys":
            return self._key(body)
        if path == "/api/terminal":
            return self._terminal(body)
        return self._error(t("req.not_found"), 404)

    # -- handlers --------------------------------------------------------------
    def _state(self, refresh: bool) -> None:
        run = HUB.current
        settings = load_settings()
        self._send_json({
            "agents": {a: {k: info[k] for k in ("name", "vendor", "color")} for a, info in AGENTS.items()},
            "team": team_of(settings),
            "modelTiers": models.tiers(),
            "settings": settings,
            "language": i18n.lang(),
            "keychain": keys.supported(),
            "health": HEALTH.get(refresh=True) if refresh else HEALTH.peek(),
            "run": run.meta() if run else None,
            "chatBusy": CHAT.busy,
        })

    def _settings(self, body: Dict[str, Any]) -> None:
        body.pop("recentWorkspaces", None)  # maintained here, not by the client
        if "workspace" in body:
            try:
                body["workspace"] = str(resolve_workspace(str(body["workspace"]), create=bool(body.get("createWorkspace"))))
            except ValueError as exc:
                return self._error(str(exc))
            current = load_settings()
            ordered = [body["workspace"], current["workspace"]] + list(current.get("recentWorkspaces") or [])
            recents: list = []
            for p in ordered:
                if p and p not in recents and os.path.isdir(p):
                    recents.append(p)
            body["recentWorkspaces"] = recents[:8]
        before = load_settings()
        settings = save_settings(body)
        if settings["auth"] != before["auth"]:
            HEALTH.invalidate()
        self._send_json({"settings": settings, "language": i18n.lang()})

    def _key(self, body: Dict[str, Any]) -> None:
        """Store (or with an empty key, remove) an agent's API key in the keychain and switch
        the agent to the matching sign-in mode."""
        agent = str(body.get("agent") or "")
        if agent not in AGENTS:
            return self._error(t("key.unknown"))
        key = str(body.get("key") or "").strip()
        try:
            if key:
                keys.save(agent, key)
            else:
                keys.delete(agent)
        except ValueError as exc:
            reason = t("key.format") if str(exc) == "format" else str(exc)
            return self._error(t("key.failed", err=reason))
        auth = dict(load_settings()["auth"])
        auth[agent] = "key" if key else "account"
        settings = save_settings({"auth": auth})
        HEALTH.invalidate()
        self._send_json({"settings": settings, "health": HEALTH.get(refresh=True)})

    def _terminal(self, body: Dict[str, Any]) -> None:
        """Open Terminal with an agent's official install or sign-in command. Only commands
        defined on the server run; the page just names the agent and the step."""
        agent, action = str(body.get("agent") or ""), str(body.get("action") or "")
        if agent not in AGENTS or action not in ("install", "login") or sys.platform != "darwin":
            return self._error(t("key.unknown"))
        if action == "install":
            command = install_command(agent)
        else:
            binary = HEALTH.binaries()[agent]
            command = login_command(agent, binary) if binary else None
        if not command:
            return self._error(t("terminal.none"), 409)
        folder = DATA_DIR / "terminal"
        folder.mkdir(parents=True, exist_ok=True)
        script = folder / f"pixelcrew-{agent}-{action}.command"
        title = t("terminal.title." + action, tool=TOOL[agent])
        script.write_text("\n".join([
            "#!/bin/zsh -l",
            "clear",
            f"print -r -- {_sh(title)}",
            f"print -r -- {_sh('$ ' + command)}",
            "print",
            command,
            "code=$?",
            "print",
            f"if [ $code -eq 0 ]; then print -r -- {_sh(t('terminal.done'))}; else print -r -- {_sh(t('terminal.failed_step'))}; fi",
            'rm -f "$0"',
            "",
        ]), "utf-8")
        script.chmod(0o700)
        proc = subprocess.run(["/usr/bin/open", "-a", "Terminal", str(script)], capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            return self._error(t("terminal.failed", err=proc.stderr.strip()[:200]), 500)
        self._send_json({"ok": True, "command": command})

    def _start_run(self, body: Dict[str, Any]) -> None:
        prompt = str(body.get("prompt") or "").strip()
        if not prompt:
            return self._error(t("run.empty"))
        with _start_lock:
            if HUB.active():
                return self._error(t("run.busy"), 409)
            settings = load_settings()
            try:
                is_default = Path(settings["workspace"]) == DEFAULT_WORKSPACE
                workspace = resolve_workspace(settings["workspace"], create=is_default)
            except ValueError as exc:
                return self._error(str(exc))
            health = HEALTH.get(refresh=True) if not settings.get("demo") else {}
            run = Run(HUB, prompt, str(workspace), settings)
            HUB.current = run
            orch = Orchestrator(run, settings, health, HEALTH.binaries())
            run.orchestrator = orch
            orch.start()
        self._send_json({"run": run.meta()})

    def _ask(self, body: Dict[str, Any]) -> None:
        question = str(body.get("question") or "").strip()
        if not question:
            return self._error(t("ask.empty"))
        settings = load_settings()
        to = body.get("to") or team_of(settings)
        if not isinstance(to, list):
            return self._error(t("ask.to_list"))
        active = HUB.active()
        if active:
            workspace = active.workspace  # questions during a run are about that run's folder
        else:
            try:
                workspace = str(resolve_workspace(settings["workspace"], create=Path(settings["workspace"]) == DEFAULT_WORKSPACE))
            except ValueError as exc:
                return self._error(str(exc))
        context = active.orchestrator.context_summary() if active and active.orchestrator else None
        health = {} if settings.get("demo") else HEALTH.get()
        to = [str(a) for a in to]
        note = ""
        guard = QuotaGuard(settings)
        if guard.enabled and len(to) > 1:
            guard.read()
            low = [a for a in to if guard.level(a) in ("critical", "empty")]
            if low and len(low) < len(to):
                to = [a for a in to if a not in low]
                note = t("ask.note.only" if len(to) == 1 else "ask.note", why=guard.describe(low[0]), names=names(to))
        try:
            qid = CHAT.ask(question, to, settings, health, HEALTH.binaries(), workspace, context, note)
        except ValueError as exc:
            return self._error(str(exc), 409)
        self._send_json({"qid": qid})

    def _pick_folder(self) -> None:
        """Open the native macOS folder chooser and return the chosen path."""
        if sys.platform != "darwin":
            return self._error(t("pick.macos_only"), 501)
        if not _pick_lock.acquire(blocking=False):
            return self._error(t("pick.open"), 409)
        try:
            current = load_settings()["workspace"]
            start = current if os.path.isdir(current) else os.path.expanduser("~")
            script = [
                "-e", "activate",
                "-e", "set f to choose folder with prompt " + _applescript_str(t("pick.prompt"))
                + " default location (POSIX file " + _applescript_str(start) + ")",
                "-e", "POSIX path of f",
            ]
            try:
                proc = subprocess.run(["osascript", *script], capture_output=True, text=True, timeout=600)
            except subprocess.TimeoutExpired:
                return self._send_json({"cancelled": True})
            if proc.returncode != 0:
                if proc.returncode < 0 or "-128" in proc.stderr:  # Cancel pressed, or the dialog was closed
                    return self._send_json({"cancelled": True})
                return self._error(t("pick.failed", err=proc.stderr.strip()[:200]), 500)
            return self._send_json({"path": proc.stdout.strip().rstrip("/") or "/"})
        finally:
            _pick_lock.release()

    def _chat_reset(self) -> None:
        try:
            CHAT.reset()
        except ValueError as exc:
            return self._error(str(exc), 409)
        self._send_json({"ok": True})

    def _current_orchestrator(self, body: Dict[str, Any]) -> Optional[Orchestrator]:
        run = HUB.active()
        if not run or (body.get("runId") and body["runId"] != run.id):
            return None
        return run.orchestrator

    def _approve(self, body: Dict[str, Any]) -> None:
        orch = self._current_orchestrator(body)
        if not orch:
            return self._error(t("approve.none"), 409)
        assignees = body.get("assignees") or {}
        efforts = body.get("efforts") or {}
        try:
            orch.approve({str(k): str(v) for k, v in assignees.items()} if isinstance(assignees, dict) else {},
                         {str(k): str(v) for k, v in efforts.items()} if isinstance(efforts, dict) else {})
        except ValueError as exc:
            return self._error(str(exc), 409)
        self._send_json({"ok": True})

    def _cancel(self, body: Dict[str, Any]) -> None:
        orch = self._current_orchestrator(body)
        if not orch:
            return self._error(t("cancel.none"), 409)
        orch.cancel()
        self._send_json({"ok": True})

    def _stream(self) -> None:
        q, snapshot = HUB.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            run = HUB.current
            hello = {"kind": "hello", "run": run.meta() if run else None, "events": snapshot}
            self.wfile.write(f"retry: 2000\ndata: {json.dumps(hello, ensure_ascii=False)}\n\n".encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if event is None:
                    break
                self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            HUB.unsubscribe(q)
            self.close_connection = True

    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        if WEB_DIR.resolve() not in target.parents or not target.is_file():
            return self._error(t("req.not_found"), 404)
        body = target.read_bytes()
        if rel == "index.html":  # the page reads its language from <html lang>
            body = body.replace(b'<html lang="en">', f'<html lang="{i18n.lang()}">'.encode(), 1)
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def shutdown() -> None:
    """Stop agent processes so nothing keeps editing files after the server exits."""
    run = HUB.active()
    if run and run.orchestrator:
        run.orchestrator.cancel()
    CHAT.cancel()


def serve(host: str, port: int) -> ThreadingHTTPServer:
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server

