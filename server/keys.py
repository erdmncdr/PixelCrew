"""API keys for the agents, kept in the macOS login keychain.

A key is stored as a generic password (service SERVICE, account = agent name)
through /usr/bin/security. The key goes to `security -i` on stdin, never on a
command line where other processes could see it, and it is never written to
settings.json or the logs. Each CLI gets its key as the environment variable
it reads (see ENV) and only when the agent is set to API-key mode.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
from typing import Dict, List, Optional

SERVICE = "app.pixelcrew.PixelCrew"
SECURITY = "/usr/bin/security"

# Environment variables each CLI reads its API key from.
ENV: Dict[str, List[str]] = {
    "claude": ["ANTHROPIC_API_KEY"],
    "codex": ["CODEX_API_KEY", "OPENAI_API_KEY"],  # `codex exec` reads CODEX_API_KEY
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "grok": ["XAI_API_KEY", "GROK_CODE_XAI_API_KEY"],
}
# Where to create a key.
KEY_PAGES = {
    "claude": "https://console.anthropic.com/settings/keys",
    "codex": "https://platform.openai.com/api-keys",
    "gemini": "https://aistudio.google.com/apikey",
    "grok": "https://console.x.ai",
}
KEY_FORMAT = re.compile(r"^[A-Za-z0-9_\-.:+/=]{16,400}$")

_lock = threading.Lock()
_cache: Dict[str, Optional[str]] = {}


def supported() -> bool:
    return sys.platform == "darwin"


def get(agent: str) -> Optional[str]:
    if agent not in ENV or not supported():
        return None
    with _lock:
        if agent in _cache:
            return _cache[agent]
    try:
        proc = subprocess.run([SECURITY, "find-generic-password", "-s", SERVICE, "-a", agent, "-w"],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        key = proc.stdout.strip() if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None
    with _lock:
        _cache[agent] = key or None
    return key or None


def has(agent: str) -> bool:
    return bool(get(agent))


def save(agent: str, key: str) -> None:
    """Raises ValueError with a short reason when the key can't be stored."""
    key = (key or "").strip()
    if agent not in ENV:
        raise ValueError("unknown agent")
    if not KEY_FORMAT.match(key):
        raise ValueError("format")
    if not supported():
        raise ValueError("unsupported")
    command = f"add-generic-password -U -s {SERVICE} -a {agent} -l PixelCrew-{agent} -w {key}\n"
    proc = subprocess.run([SECURITY, "-i"], input=command, capture_output=True, text=True, timeout=15)
    if proc.returncode != 0 or proc.stderr.strip():
        raise ValueError((proc.stderr or "security failed").strip()[:200].replace(key, "…"))
    with _lock:
        _cache[agent] = key


def delete(agent: str) -> None:
    if agent not in ENV or not supported():
        return
    subprocess.run([SECURITY, "delete-generic-password", "-s", SERVICE, "-a", agent],
                   stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    with _lock:
        _cache[agent] = None


def apply_env(agent: str, env: Dict[str, str], mode: str) -> None:
    """API-key mode: give the CLI the stored key (or keep one already in the environment).
    Account mode: hide every key so the CLI bills the signed-in subscription."""
    names = ENV.get(agent, [])
    if mode == "key":
        key = get(agent)
        if key:
            for name in names:
                env.pop(name, None)  # Antigravity warns when GOOGLE_API_KEY and GEMINI_API_KEY are both set
            for name in names[:1] if agent == "gemini" else names:
                env[name] = key
        return
    for name in names:
        env.pop(name, None)


def env_key(agent: str, env: Dict[str, str]) -> bool:
    return any(env.get(name) for name in ENV.get(agent, []))
