"""Paths, persisted settings and CLI discovery/health checks."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = APP_DIR / "web"
# The macOS app ships the code inside its bundle, so it points data and the
# default workspace somewhere writable; run from a checkout, both stay local.
DATA_DIR = Path(os.environ.get("PIXELCREW_DATA_DIR") or APP_DIR / "data").expanduser()
RUNS_DIR = DATA_DIR / "runs"
SETTINGS_FILE = DATA_DIR / "settings.json"
DEFAULT_WORKSPACE = Path(os.environ.get("PIXELCREW_DEFAULT_WORKSPACE") or APP_DIR / "playground").expanduser()

HOME = Path.home()

# Every agent PixelCrew can drive. Order is the default preference (lead, tie-breaks).
# "of" / "to" are the Turkish genitive and dative forms used in decision messages.
AGENTS: Dict[str, Dict[str, str]] = {
    "claude": {"name": "Claude", "vendor": "Anthropic", "color": "#FFA552", "of": "Claude'un", "to": "Claude'a"},
    "codex": {"name": "Codex", "vendor": "OpenAI", "color": "#4FE0B6", "of": "Codex'in", "to": "Codex'e"},
    "gemini": {"name": "Gemini", "vendor": "Google", "color": "#5B8CFF", "of": "Gemini'nin", "to": "Gemini'ye"},
    "grok": {"name": "Grok", "vendor": "xAI", "color": "#E6E6E6", "of": "Grok'un", "to": "Grok'a"},
}

CLAUDE_CANDIDATES = [
    HOME / ".local/bin/claude",
    HOME / ".claude/local/claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
]
CODEX_CANDIDATES = [
    Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex"),
    Path("/Applications/Codex.app/Contents/Resources/codex"),
    Path("/opt/homebrew/bin/codex"),
    Path("/usr/local/bin/codex"),
    HOME / ".npm-global/bin/codex",
    HOME / ".local/bin/codex",
]
GEMINI_CANDIDATES = [
    HOME / ".local/bin/gemini",
    HOME / ".local/node/bin/gemini",
    Path("/opt/homebrew/bin/gemini"),
    Path("/usr/local/bin/gemini"),
    HOME / ".npm-global/bin/gemini",
]
GROK_CANDIDATES = [
    HOME / ".local/bin/grok",
    HOME / ".grok/bin/grok",
    Path("/opt/homebrew/bin/grok"),
    Path("/usr/local/bin/grok"),
]
# Antigravity CLI: Google's successor to Gemini CLI and, since mid 2026, the only way
# to use Gemini with a personal Google account. Preferred over `gemini` when present.
AGY_CANDIDATES = [
    HOME / ".local/bin/agy",
    Path("/opt/homebrew/bin/agy"),
    Path("/usr/local/bin/agy"),
]
AGY_GUIDE = "https://antigravity.google/docs/cli/install"
CANDIDATES = {"claude": CLAUDE_CANDIDATES, "codex": CODEX_CANDIDATES, "gemini": GEMINI_CANDIDATES, "grok": GROK_CANDIDATES}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "workspace": str(DEFAULT_WORKSPACE),
    "approvePlan": True,       # pause after planning until the user approves
    "planReview": True,        # the second agent critiques the plan
    "crossReview": True,       # every task is reviewed by the other agent
    "fixRounds": 1,            # how many review -> fix loops per task
    "permissions": "safe",     # safe | full
    "team": ["claude", "codex"],  # which agents take part
    "claudeModel": "",         # empty = CLI default
    "codexModel": "",
    "geminiModel": "",
    "grokModel": "",
    "jobTimeoutMin": 30,
    "demo": False,             # simulate agents instead of launching CLIs
    "recentWorkspaces": [],    # most recent first, shown in the folder picker
    "effortMode": "auto",      # auto | low | medium | high | cli (each CLI's own default)
    "finalCheck": True,        # after multi-task runs, verify the pieces work together
    "quotaBalance": True,      # shift work away from an agent that is running out of quota
    "quotaCritical": 15,       # % left below which an agent hands its work over
    "modelMode": "auto",       # auto: strong / standard / fast model per job (see models.py) | cli
    "roles": {"claude": "senior", "codex": "senior", "gemini": "worker", "grok": "worker"},
    "everyAgentTask": True,    # the plan gives every available agent at least one task
    # account: the CLI's own sign-in (subscription) | key: an API key from the keychain
    "auth": {"claude": "account", "codex": "account", "gemini": "account", "grok": "account"},
    "language": "auto",        # auto (system language) | en | tr
    "onboarded": False,        # the setup screen was seen once
}

# Variables injected by a surrounding Claude Code session. A nested `claude`
# inheriting them would try to attach to the parent session instead of
# starting its own.
PARENT_SESSION_VARS = [
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_HOST_SESSION_ID",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_SDK_HAS_HOST_AUTH_REFRESH",
    "CLAUDE_AGENT_SDK_VERSION",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "AI_AGENT",
]

_settings_lock = threading.Lock()


def load_settings() -> Dict[str, Any]:
    with _settings_lock:
        data: Dict[str, Any] = {}
        if SETTINGS_FILE.exists():
            try:
                data = json.loads(SETTINGS_FILE.read_text("utf-8"))
            except (OSError, ValueError):
                data = {}
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        return merged


def save_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    current = load_settings()
    for key, value in patch.items():
        if key not in DEFAULT_SETTINGS:
            continue
        default = DEFAULT_SETTINGS[key]
        if key in ("roles", "auth"):
            if not isinstance(value, dict):
                continue
            allowed = ("senior", "worker") if key == "roles" else ("account", "key")
            value = {a: (value.get(a) if value.get(a) in allowed else current[key].get(a, default[a])) for a in default}
        elif isinstance(default, list):
            if not isinstance(value, list):
                continue
            value = [str(v) for v in value if isinstance(v, str) and v][:8]
            if key == "team":
                value = [a for a in AGENTS if a in value] or ["claude", "codex"]
        elif isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        elif isinstance(default, str):
            value = str(value).strip()
        current[key] = value
    current["fixRounds"] = max(0, min(3, current["fixRounds"]))
    current["jobTimeoutMin"] = max(2, min(240, current["jobTimeoutMin"]))
    if current["permissions"] not in ("safe", "full"):
        current["permissions"] = "safe"
    current["quotaCritical"] = max(5, min(40, current["quotaCritical"]))
    if current["effortMode"] not in ("auto", "low", "medium", "high", "cli"):
        current["effortMode"] = "auto"
    if current["modelMode"] not in ("auto", "cli"):
        current["modelMode"] = "auto"
    if current["language"] not in ("auto", "en", "tr"):
        current["language"] = "auto"
    with _settings_lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(current, indent=2, ensure_ascii=False), "utf-8")
    return current


def resolve_workspace(path: str, create: bool = False) -> Path:
    """Expand and validate a workspace path. Raises ValueError with a user-facing message."""
    from .i18n import t
    if not path:
        raise ValueError(t("ws.empty"))
    p = Path(os.path.expanduser(path)).resolve()
    if p == Path("/") or p == HOME:
        raise ValueError(t("ws.home"))
    if not p.exists():
        if not create:
            raise ValueError(t("ws.not_found", path=p))
        if not p.parent.exists():
            raise ValueError(t("ws.no_parent", path=p.parent))
        p.mkdir()
    if not p.is_dir():
        raise ValueError(t("ws.not_dir", path=p))
    return p


def _find_binary(name: str, env_var: str, candidates: List[Path]) -> Optional[str]:
    override = os.environ.get(env_var)
    if override and Path(override).exists():
        return override
    for cand in candidates:
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    found = shutil.which(name)
    return found


def child_env(agent: str, settings: Dict[str, Any]) -> Dict[str, str]:
    env = dict(os.environ)
    for var in PARENT_SESSION_VARS:
        env.pop(var, None)
    # When launched from inside the Claude desktop app, the base URL points at
    # the app's own proxy, which a standalone CLI cannot use.
    if os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("claude-desktop"):
        env.pop("ANTHROPIC_BASE_URL", None)
    from . import keys
    keys.apply_env(agent, env, (settings.get("auth") or {}).get(agent, "account"))
    # Gemini CLI is a Node program installed under ~/.local; make sure node resolves.
    extra = [str(HOME / ".local/bin"), str(HOME / ".local/node/bin"), str(HOME / ".grok/bin")]
    env["PATH"] = ":".join(extra + [p for p in env.get("PATH", "").split(":") if p and p not in extra])
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")
    return env


def _run(cmd: List[str], env: Dict[str, str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout
    )


# Official install commands, run in Terminal only when the user presses Install.
INSTALL = {
    "claude": "curl -fsSL https://claude.ai/install.sh | bash",
    "grok": "curl -fsSL https://x.ai/cli/install.sh | bash",
}
DOCS = {
    "claude": "https://docs.claude.com/en/docs/claude-code/setup",
    "codex": "https://developers.openai.com/codex/cli",
    "gemini": AGY_GUIDE,
    "grok": "https://x.ai/cli",
}
TOOL = {"claude": "Claude Code CLI", "codex": "Codex CLI", "gemini": "Antigravity CLI (agy)", "grok": "Grok Build CLI"}


def install_command(agent: str) -> Optional[str]:
    if agent == "codex":
        if shutil.which("brew"):
            return "brew install codex"
        if _find_binary("npm", "PIXELCREW_NPM_BIN", [HOME / ".local/bin/npm", Path("/opt/homebrew/bin/npm"),
                                                     Path("/usr/local/bin/npm")]):
            return "npm install -g @openai/codex"
        return None
    return INSTALL.get(agent)


def login_command(agent: str, binary: str) -> str:
    quoted = "'" + binary.replace("'", "'\\''") + "'"
    return {"claude": f"{quoted} auth login", "codex": f"{quoted} login", "gemini": quoted,
            "grok": f"{quoted} login"}[agent]


class Health:
    """Cached availability/sign-in status of the agent CLIs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: Optional[Dict[str, Any]] = None
        self._at = 0.0

    def get(self, refresh: bool = False) -> Dict[str, Any]:
        with self._lock:
            if not refresh and self._cache and time.time() - self._at < 30:
                return self._cache
        settings = load_settings()
        checks = {"claude": self._check_claude, "codex": self._check_codex,
                  "gemini": self._check_gemini, "grok": self._check_grok}
        result = {a: self._finish(a, settings, checks[a]) for a in AGENTS}
        with self._lock:
            self._cache, self._at = result, time.time()
        return result

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    def peek(self) -> Optional[Dict[str, Any]]:
        """Cached result if fresh, without blocking on the CLIs."""
        with self._lock:
            if self._cache and time.time() - self._at < 30:
                return self._cache
        return None

    def binaries(self) -> Dict[str, Optional[str]]:
        found = {a: _find_binary(a, f"PIXELCREW_{a.upper()}_BIN", CANDIDATES[a]) for a in AGENTS}
        agy = _find_binary("agy", "PIXELCREW_AGY_BIN", AGY_CANDIDATES)
        if agy:
            found["gemini"] = agy
        return found

    def _finish(self, agent: str, settings: Dict[str, Any], check: Any) -> Dict[str, Any]:
        """Run one CLI check and add what the setup screen needs: whether the CLI is
        installed, which way it signs in, and whether a key is stored."""
        from . import keys
        from .i18n import t
        mode = (settings.get("auth") or {}).get(agent, "account")
        info: Dict[str, Any] = {"bin": None, "version": None, "ready": False, "problem": None, "fix": None,
                                "mode": mode, "installed": False, "loggedIn": False,
                                "hasKey": keys.has(agent), "keyPage": keys.KEY_PAGES[agent], "docs": DOCS[agent],
                                "canInstall": bool(install_command(agent))}
        binary = self.binaries()[agent]
        if not binary:
            info["problem"] = t("health.missing", tool=TOOL[agent])
            return info
        info["bin"], info["installed"] = binary, True
        env = child_env(agent, settings)
        try:
            check(binary, env, info)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            info["problem"] = t("health.check_failed", tool=TOOL[agent], err=exc)
            return info
        if mode == "key":
            info["ready"] = info["hasKey"] or keys.env_key(agent, env)
            info["auth"] = t("auth.api_key")
            info["problem"] = None if info["ready"] else t("health.no_key", name=AGENTS[agent]["name"])
            return info
        if info["loggedIn"]:
            info["ready"] = True
            info["problem"] = None
        elif not info["problem"]:
            info["problem"] = t("health.logged_out", tool=TOOL[agent])
            info["fix"] = login_command(agent, binary)
        return info

    @staticmethod
    def _check_claude(binary: str, env: Dict[str, str], info: Dict[str, Any]) -> None:
        info["version"] = _run([binary, "--version"], env).stdout.strip().split("\n")[0]
        data = json.loads(_run([binary, "auth", "status"], env).stdout or "{}")
        if data.get("loggedIn"):
            info["loggedIn"] = True
            info["auth"] = data.get("authMethod") or ""

    @staticmethod
    def _check_codex(binary: str, env: Dict[str, str], info: Dict[str, Any]) -> None:
        info["version"] = _run([binary, "--version"], env).stdout.strip().split("\n")[0]
        status = _run([binary, "login", "status"], env)
        text = (status.stdout + status.stderr).strip()
        if status.returncode == 0 and "logged in" in text.lower():
            info["loggedIn"] = True
            info["auth"] = text.split("\n")[0]

    def _check_gemini(self, binary: str, env: Dict[str, str], info: Dict[str, Any]) -> None:
        if Path(binary).name == "agy":
            return self._check_agy(binary, env, info)
        info["version"] = "gemini " + _run([binary, "--version"], env).stdout.strip().split("\n")[-1]
        # Gemini CLI still works with an API key; personal Google sign-in was retired in 2026.
        if info["mode"] != "key":
            from .i18n import t
            info["problem"] = t("health.gemini_retired")
            info["fix"] = None
        return None

    _agy_auth: Dict[str, Any] = {"ok": False, "at": 0.0}

    def _check_agy(self, binary: str, env: Dict[str, str], info: Dict[str, Any]) -> None:
        info["version"] = "agy " + _run([binary, "--version"], env).stdout.strip().split("\n")[-1]
        if info["mode"] == "key":
            return
        # Credentials live in the keychain, so ask the CLI itself: listing models needs a
        # signed-in account but makes no model call. A success is trusted for ten minutes.
        cached = self._agy_auth
        if not (cached["ok"] and time.time() - cached["at"] < 600):
            try:
                proc = _run([binary, "models"], env, timeout=40)
                out = (proc.stdout + proc.stderr).lower()
                cached["ok"] = proc.returncode == 0 and bool(proc.stdout.strip()) and "authentication" not in out
                if cached["ok"]:
                    from . import models
                    models.set_available("gemini", [line.split("\t")[0].strip() for line in proc.stdout.splitlines()
                                                    if "\t" in line])
            except (OSError, subprocess.SubprocessError):
                cached["ok"] = False
            cached["at"] = time.time()
        if cached["ok"]:
            info["loggedIn"] = True
            info["auth"] = "Google (Antigravity)"

    @staticmethod
    def _check_grok(binary: str, env: Dict[str, str], info: Dict[str, Any]) -> None:
        info["version"] = _run([binary, "--version"], env).stdout.strip().split("\n")[0]
        auth = HOME / ".grok" / "auth.json"
        if auth.exists() and auth.stat().st_size > 20:
            info["loggedIn"] = True
            info["auth"] = "xAI"


HEALTH = Health()
