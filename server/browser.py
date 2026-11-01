"""A headless Chrome for the agents, driven over the DevTools protocol with the standard library.

Each job that asks for it gets its own browser (killed when the job ends). The agents never
touch Chrome themselves: their `pixelcrew-browser` command drops a request in the job's mailbox
and the orchestrator calls this module, so the browser works from inside every CLI's sandbox
and can reach local dev servers. Screenshots land in the mailbox folder, where the agent can
read them.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Arc.app/Contents/MacOS/Arc",
    "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
]
TEXT_LIMIT = 24000
WIDTH, HEIGHT = 1280, 900


def find_chrome() -> Optional[str]:
    env = os.environ.get("PIXELCREW_CHROME")
    if env and os.path.exists(env):
        return env
    home = str(Path.home())
    for c in CANDIDATES + [home + c for c in CANDIDATES]:
        if os.path.exists(c):
            return c
    return None


def available() -> bool:
    return find_chrome() is not None


# -- a small WebSocket client (RFC 6455, client side, text frames) --------------------------------
class WebSocket:
    def __init__(self, url: str, timeout: float = 30.0) -> None:
        m = re.match(r"ws://([^/:]+):(\d+)(/.*)$", url)
        if not m:
            raise ValueError(f"bad websocket url: {url}")
        host, port, path = m.group(1), int(m.group(2)), m.group(3)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("websocket handshake refused: " + head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.lock = threading.Lock()

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(max(65536, n - len(self.buf)))
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text: str, opcode: int = 1) -> None:
        payload = text.encode() if isinstance(text, str) else text
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        with self.lock:
            self.sock.sendall(head + mask + masked)

    def recv(self) -> str:
        parts: List[bytes] = []
        while True:
            b1, b2 = self._read(2)
            fin, opcode, masked, n = b1 & 0x80, b1 & 0x0F, b2 & 0x80, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if masked else b""
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 8:
                raise ConnectionError("websocket closed by peer")
            if opcode == 9:
                self.send(data.decode(errors="replace"), opcode=10)
                continue
            if opcode == 10:
                continue
            parts.append(data)
            if fin:
                return b"".join(parts).decode("utf-8", errors="replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# -- page helpers run inside the page ---------------------------------------------------------------
JS_SNAPSHOT = r"""
(() => {
  const sel = 'h1,h2,h3,a[href],button,input,select,textarea,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[contenteditable=true],label,summary';
  const out = []; let n = 0;
  const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  for (const el of document.querySelectorAll(sel)) {
    if (!vis(el)) continue;
    n += 1; el.setAttribute('data-pc-ref', String(n));
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'button' ? 'button' : /^h\d$/.test(tag) ? 'heading' : tag);
    let name = (el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 80);
    if (tag === 'input') name = `${el.type || 'text'}${el.name ? ' name=' + el.name : ''}${name ? ' "' + name + '"' : ''}${el.value ? ' value=' + JSON.stringify(String(el.value).slice(0, 40)) : ''}${el.checked ? ' checked' : ''}`;
    const extra = tag === 'a' ? ' -> ' + (el.getAttribute('href') || '') : el.disabled ? ' (disabled)' : '';
    out.push(`[${n}] ${role}${el.id ? '#' + el.id : ''} ${name}${extra}`.trim());
    if (out.length >= 300) break;
  }
  return `${document.title} | ${location.href}\n` + (out.join('\n') || '(no interactive elements found)');
})()
"""
JS_FIND = r"""
((target) => {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  let el = null;
  if (/^\d+$/.test(target)) el = document.querySelector(`[data-pc-ref="${target}"]`);
  if (!el) { try { el = document.querySelector(target); } catch (e) { el = null; } }
  if (!el) {
    const t = target.trim().toLowerCase();
    const all = [...document.querySelectorAll('button,a,[role=button],[role=link],[role=tab],[role=menuitem],label,summary,input[type=submit],input[type=button],li,span,div')].filter(vis);
    const txt = (e) => (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().toLowerCase();
    el = all.find((e) => txt(e) === t) || all.find((e) => txt(e).includes(t) && txt(e).length < t.length + 40);
  }
  if (!el) return null;
  el.scrollIntoView({ block: 'center', inline: 'center' });
  const r = el.getBoundingClientRect();
  return { x: r.left + r.width / 2, y: r.top + r.height / 2, tag: el.tagName.toLowerCase(),
           text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 60),
           editable: el.isContentEditable || ['input', 'textarea', 'select'].includes(el.tagName.toLowerCase()) };
})
"""
KEYS = {"enter": ("Enter", "Enter", 13, "\r"), "tab": ("Tab", "Tab", 9, ""), "escape": ("Escape", "Escape", 27, ""),
        "backspace": ("Backspace", "Backspace", 8, ""), "delete": ("Delete", "Delete", 46, ""),
        "arrowdown": ("ArrowDown", "ArrowDown", 40, ""), "arrowup": ("ArrowUp", "ArrowUp", 38, ""),
        "arrowleft": ("ArrowLeft", "ArrowLeft", 37, ""), "arrowright": ("ArrowRight", "ArrowRight", 39, ""),
        "space": (" ", "Space", 32, " "), "home": ("Home", "Home", 36, ""), "end": ("End", "End", 35, "")}


class Browser:
    """One headless Chrome with one page, plus an optional static file server."""

    def __init__(self, shots_dir: Path) -> None:
        self.shots_dir = shots_dir
        self.profile = Path(tempfile.mkdtemp(prefix="pixelcrew-chrome-"))
        self.proc: Optional[subprocess.Popen] = None
        self.ws: Optional[WebSocket] = None
        self.port = 0
        self.msg_id = 0
        self.events: List[Dict[str, Any]] = []
        self.console: List[str] = []
        self.shots = 0
        self.server: Optional[ThreadingHTTPServer] = None
        self.server_url = ""
        self.lock = threading.RLock()

    # -- lifecycle ------------------------------------------------------------------
    def start(self) -> None:
        chrome = find_chrome()
        if not chrome:
            raise RuntimeError("No Chromium-based browser found (Google Chrome, Brave, Edge). Install one for UI checks.")
        cmd = [chrome, "--headless=new", "--remote-debugging-port=0", "--remote-allow-origins=*",
               f"--user-data-dir={self.profile}", "--no-first-run", "--no-default-browser-check", "--disable-gpu",
               "--hide-scrollbars", f"--window-size={WIDTH},{HEIGHT}", "--disable-extensions",
               "--disable-background-networking", "--disable-sync", "--mute-audio", "about:blank"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                     start_new_session=True)
        assert self.proc.stderr is not None
        deadline = time.time() + 20
        ws_url = ""
        while time.time() < deadline:
            line = self.proc.stderr.readline()
            if not line:
                if self.proc.poll() is not None:
                    break
                continue
            m = re.search(r"DevTools listening on (ws://[^\s]+)", line)
            if m:
                ws_url = m.group(1)
                break
        if not ws_url:
            self.close()
            raise RuntimeError("Chrome did not start (no DevTools endpoint).")
        threading.Thread(target=self._drain, daemon=True).start()
        self.port = int(re.search(r":(\d+)/", ws_url).group(1))
        page = None
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=5) as r:
                    targets = json.loads(r.read().decode())
                page = next((t for t in targets if t.get("type") == "page"), None)
                if page:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.25)
        if not page:
            self.close()
            raise RuntimeError("Chrome opened no page.")
        self.ws = WebSocket(page["webSocketDebuggerUrl"])
        for method in ("Page.enable", "Runtime.enable", "Log.enable"):
            self.send(method)
        self.send("Emulation.setDeviceMetricsOverride", width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False)

    def _drain(self) -> None:
        try:
            assert self.proc and self.proc.stderr
            for _ in self.proc.stderr:
                pass
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        with self.lock:
            if self.server:
                try:
                    self.server.shutdown()
                except Exception:
                    pass
                self.server = None
            if self.ws:
                self.ws.close()
                self.ws = None
            if self.proc and self.proc.poll() is None:
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                    self.proc.wait(timeout=5)
                except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(self.proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
            self.proc = None
            shutil.rmtree(self.profile, ignore_errors=True)

    # -- protocol -------------------------------------------------------------------
    def send(self, method: str, timeout: float = 30.0, **params: Any) -> Dict[str, Any]:
        assert self.ws is not None
        with self.lock:
            self.msg_id += 1
            mid = self.msg_id
            self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            end = time.time() + timeout
            while time.time() < end:
                self.ws.sock.settimeout(max(0.1, end - time.time()))
                try:
                    msg = json.loads(self.ws.recv())
                except socket.timeout:
                    break
                if msg.get("id") == mid:
                    if "error" in msg:
                        raise RuntimeError(msg["error"].get("message") or str(msg["error"]))
                    return msg.get("result") or {}
                self._event(msg)
            raise RuntimeError(f"{method}: no reply from the browser")

    def _event(self, msg: Dict[str, Any]) -> None:
        method = msg.get("method", "")
        p = msg.get("params") or {}
        if method == "Runtime.consoleAPICalled" and p.get("type") in ("error", "warning", "assert"):
            text = " ".join(str(a.get("value", a.get("description", ""))) for a in p.get("args") or [])
            self.console.append(f"[{p.get('type')}] {text[:500]}")
        elif method == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails") or {}
            desc = (d.get("exception") or {}).get("description") or d.get("text") or "exception"
            self.console.append(f"[exception] {str(desc)[:500]}")
        elif method == "Log.entryAdded":
            e = p.get("entry") or {}
            if e.get("level") in ("error", "warning") and e.get("source") != "deprecation" \
                    and "favicon.ico" not in str(e.get("url") or ""):
                self.console.append(f"[{e.get('source')}] {str(e.get('text'))[:300]}")
        else:
            self.events.append(msg)
        del self.console[:-60]
        del self.events[:-200]

    def pump(self, seconds: float) -> None:
        """Read events (console, load) for a while without sending anything."""
        assert self.ws is not None
        end = time.time() + seconds
        with self.lock:
            while time.time() < end:
                self.ws.sock.settimeout(max(0.05, end - time.time()))
                try:
                    self._event(json.loads(self.ws.recv()))
                except socket.timeout:
                    break

    def wait_loaded(self, timeout: float = 20.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if any(e.get("method") == "Page.loadEventFired" for e in self.events):
                break
            self.pump(0.3)
        self.events = [e for e in self.events if e.get("method") != "Page.loadEventFired"]
        for _ in range(20):  # the SPA's first render
            if self.eval_value("document.readyState") == "complete":
                break
            time.sleep(0.1)
        self.pump(0.6)

    def eval_value(self, js: str, timeout: float = 30.0) -> Any:
        res = self.send("Runtime.evaluate", timeout=timeout, expression=js, returnByValue=True, awaitPromise=True)
        if res.get("exceptionDetails"):
            d = res["exceptionDetails"]
            raise RuntimeError((d.get("exception") or {}).get("description") or d.get("text") or "JavaScript error")
        return (res.get("result") or {}).get("value")

    # -- commands ------------------------------------------------------------------------
    def open(self, target: str, cwd: str) -> str:
        url = target.strip()
        if not re.match(r"^[a-z]+://", url):
            path = Path(url if os.path.isabs(url) else os.path.join(cwd, url))
            if path.exists():
                url = path.resolve().as_uri()
            elif re.match(r"^(localhost|127\.0\.0\.1)(:\d+)?(/|$)", url):
                url = "http://" + url
            else:
                raise RuntimeError(f"Not a URL and no such file: {target}")
        self.console = []
        self.events = []
        self.send("Page.navigate", url=url)
        self.wait_loaded()
        title = self.eval_value("document.title") or ""
        here = self.eval_value("location.href") or url
        text = str(self.eval_value("document.body ? document.body.innerText : ''") or "")
        preview = " ".join(text.split())[:1200]
        errors = f"\n{len(self.console)} console error(s); run `pixelcrew-browser console`." if self.console else ""
        return f"Opened {here}\nTitle: {title}\nText: {preview or '(empty page)'}{errors}"

    def serve(self, folder: str, cwd: str, file: str = "") -> str:
        root = Path(folder if os.path.isabs(folder) else os.path.join(cwd, folder)).resolve()
        if not root.is_dir():
            raise RuntimeError(f"Not a folder: {folder}")
        with self.lock:
            if self.server:
                self.server.shutdown()
            handler = partial(QuietHandler, directory=str(root))
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            self.server.daemon_threads = True
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            self.server_url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        page = file or ("index.html" if (root / "index.html").exists() else "")
        opened = self.open(self.server_url + page, cwd)
        return f"Serving {root} at {self.server_url}\n{opened}"

    def text(self) -> str:
        text = str(self.eval_value("document.body ? document.body.innerText : ''") or "")
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text[:TEXT_LIMIT] + ("\n… (truncated)" if len(text) > TEXT_LIMIT else "") or "(no visible text)"

    def snapshot(self) -> str:
        return str(self.eval_value(JS_SNAPSHOT) or "")

    def find(self, target: str) -> Dict[str, Any]:
        hit = self.eval_value(f"({JS_FIND})({json.dumps(target)})")
        if not hit:
            raise RuntimeError(f"No element matches {target!r}. Run `pixelcrew-browser snapshot` for refs.")
        return hit

    def click(self, target: str) -> str:
        hit = self.find(target)
        self.events = []
        for kind in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", type=kind, x=hit["x"], y=hit["y"], button="left", clickCount=1)
        self.pump(0.8)
        nav = any(e.get("method") == "Page.frameNavigated" for e in self.events)
        if nav:
            self.wait_loaded(10)
        here = self.eval_value("location.href")
        return f"Clicked <{hit['tag']}> {hit['text']!r}" + (f"; now at {here}" if nav else "")

    def type(self, target: str, text: str) -> str:
        hit = self.find(target)
        if not hit.get("editable"):
            raise RuntimeError(f"<{hit['tag']}> {hit['text']!r} is not an editable field.")
        for kind in ("mousePressed", "mouseReleased"):
            self.send("Input.dispatchMouseEvent", type=kind, x=hit["x"], y=hit["y"], button="left", clickCount=1)
        self.eval_value("(() => { const a = document.activeElement; if (a && 'select' in a) a.select(); })()")
        self.send("Input.insertText", text=text)
        self.pump(0.3)
        return f"Typed {text!r} into <{hit['tag']}>"

    def press(self, key: str) -> str:
        info = KEYS.get(key.strip().lower())
        if not info:
            raise RuntimeError(f"Unknown key {key!r}; one of: {', '.join(sorted(KEYS))}")
        name, code, vk, text = info
        self.events = []
        self.send("Input.dispatchKeyEvent", type="keyDown", key=name, code=code, windowsVirtualKeyCode=vk, text=text)
        self.send("Input.dispatchKeyEvent", type="keyUp", key=name, code=code, windowsVirtualKeyCode=vk)
        self.pump(0.6)
        if any(e.get("method") == "Page.frameNavigated" for e in self.events):
            self.wait_loaded(10)
        return f"Pressed {name}"

    def eval(self, js: str) -> str:
        value = self.eval_value(js)
        out = json.dumps(value, ensure_ascii=False, indent=None) if not isinstance(value, str) else value
        return (out or "undefined")[:TEXT_LIMIT]

    def screenshot(self) -> str:
        self.shots += 1
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        path = self.shots_dir / f"shot-{self.shots}.png"
        data = self.send("Page.captureScreenshot", format="png")
        path.write_bytes(base64.b64decode(data.get("data") or ""))
        return f"Screenshot saved: {path}\nView it with your image/file reading tool (it is a PNG of the {WIDTH}x{HEIGHT} viewport)."

    def console_log(self) -> str:
        self.pump(0.2)
        return "\n".join(self.console) if self.console else "No console errors or exceptions."

    def handle(self, args: Dict[str, Any]) -> str:
        cmd = str(args.get("cmd") or "")
        cwd = str(args.get("cwd") or os.getcwd())
        if cmd == "open":
            return self.open(str(args.get("target") or ""), cwd)
        if cmd == "serve":
            return self.serve(str(args.get("target") or "."), cwd, str(args.get("file") or ""))
        if cmd == "text":
            return self.text()
        if cmd == "snapshot":
            return self.snapshot()
        if cmd == "click":
            return self.click(str(args.get("target") or ""))
        if cmd == "type":
            return self.type(str(args.get("target") or ""), str(args.get("text") or ""))
        if cmd == "press":
            return self.press(str(args.get("target") or ""))
        if cmd == "eval":
            return self.eval(str(args.get("js") or ""))
        if cmd == "screenshot":
            return self.screenshot()
        if cmd == "console":
            return self.console_log()
        raise RuntimeError(f"Unknown browser command {cmd!r}")


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        pass


class BrowserService:
    """Lazily starts one Browser for a job and routes its mailbox requests."""

    def __init__(self, shots_dir: Path) -> None:
        self.shots_dir = shots_dir
        self.browser: Optional[Browser] = None
        self.lock = threading.Lock()
        self.uses = 0

    def handle(self, args: Dict[str, Any]) -> Dict[str, Any]:
        with self.lock:
            if args.get("cmd") == "close":
                self.close()
                return {"ok": True, "text": "Browser closed."}
            if not self.browser:
                b = Browser(self.shots_dir)
                b.start()
                self.browser = b
            self.uses += 1
            try:
                return {"ok": True, "text": self.browser.handle(args)}
            except (RuntimeError, OSError, ValueError, ConnectionError) as exc:
                if isinstance(exc, ConnectionError):
                    self.close()
                return {"ok": False, "text": f"Browser error: {exc}"}

    def close(self) -> None:
        if self.browser:
            self.browser.close()
            self.browser = None
