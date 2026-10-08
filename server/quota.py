"""Quota-aware balancing between the agents of the team.

Claude's 5-hour window drains much faster than Codex's weekly one, so the
team should notice and shift work before an agent runs dry, instead of
failing halfway through a run. Each agent gets a level from the tightest of
its live windows:

  ok        plenty left: normal behaviour
  tight     below `critical + 20`%: no heavy tasks, one effort step down
  critical  below `critical`%: hand its tasks, reviews and the lead role over
  empty     no quota left (or a job just failed on the limit): leave it out

A window that resets within 20 minutes counts one level lighter, since it is
about to refill. Unknown quota counts as ok.

Two environment variables exist only for trying this without spending quota:
PIXELCREW_FAKE_QUOTA="claude=12,codex=80,gemini=50" pins remaining percentages, and
PIXELCREW_DEMO_QUOTA_FAIL=claude makes that demo agent's first task fail on its limit.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Optional

from . import status
from .config import AGENTS
from .i18n import t

NAME = {a: info["name"] for a, info in AGENTS.items()}
OF = {a: info["of"] for a, info in AGENTS.items()}  # genitive, for "X'in 5 saatlik limitinde"
TO = {a: info["to"] for a, info in AGENTS.items()}  # dative, for "X'e geçti"
ORDER = {"ok": 0, "unknown": 0, "tight": 1, "critical": 2, "empty": 3}
SOON = 20 * 60

QUOTA_ERROR = re.compile(
    r"usage limit|limit reached|rate.?limit|quota|too many requests|\b429\b|limitine ulaş|out of credits"
    r"|resource.?exhausted|insufficient credits|spending limit", re.I)


def is_quota_error(text: str) -> bool:
    return bool(QUOTA_ERROR.search(text or ""))


def _fake() -> Dict[str, int]:
    out: Dict[str, int] = {}
    for part in (os.environ.get("PIXELCREW_FAKE_QUOTA") or "").split(","):
        name, _, value = part.partition("=")
        if name.strip() in NAME and value.strip().isdigit():
            out[name.strip()] = int(value)
    return out


def clock(ts: Optional[float]) -> str:
    if not ts:
        return ""
    when = datetime.fromtimestamp(ts)
    if when.date() == datetime.now().date():
        return when.strftime("%H:%M")
    return when.strftime("%d.%m %H:%M")


class QuotaGuard:
    def __init__(self, settings: Dict[str, Any]) -> None:
        self.settings = settings
        self.enabled = bool(settings.get("quotaBalance", True))
        self.critical = int(settings.get("quotaCritical", 15))
        self.tight = self.critical + 20
        self.exhausted: Dict[str, float] = {}  # agent -> time until which it counts as empty
        self.fake = _fake()
        self.last: Dict[str, Dict[str, Any]] = {}

    def refresh(self) -> None:
        """Re-read Claude's and Gemini's /usage now (local calls, no model request) when stale."""
        if self.settings.get("demo"):
            return
        if "claude" not in self.fake:
            status.CLAUDE_USAGE.refresh_if_older(90)
        if "gemini" in (self.settings.get("team") or []) and "gemini" not in self.fake:
            status.AGY_USAGE.refresh_if_older(90)
        if "grok" in (self.settings.get("team") or []) and "grok" not in self.fake:
            status.GROK_USAGE.refresh_if_older(90)

    def read(self) -> Dict[str, Dict[str, Any]]:
        snap = status.snapshot(self.settings) if not self.settings.get("demo") else {}
        now = time.time()
        out: Dict[str, Dict[str, Any]] = {}
        for agent in NAME:
            info: Dict[str, Any] = {"left": None, "window": None, "resets_at": None, "level": "unknown", "soon": False}
            windows = [w for w in (snap.get(agent) or {}).get("windows") or [] if not w.get("rolled")]
            if windows:
                worst = max(windows, key=lambda w: float(w.get("used") or 0))
                info.update(left=max(0, round(100 - float(worst.get("used") or 0))), window=worst.get("label"),
                            resets_at=worst.get("resets_at"))
                if worst.get("status") == "rejected":
                    info["left"] = 0
            if agent in self.fake:
                info.update(left=self.fake[agent], window={"claude": "5 saat", "codex": "hafta"}.get(agent, "gün"),
                            resets_at=None)
            if self.exhausted.get(agent, 0) > now:
                info.update(left=0, resets_at=self.exhausted[agent] if self.exhausted[agent] - now < 6 * 3600 else info["resets_at"])
            info["level"] = self._level(info, now)
            out[agent] = info
        self.last = out
        return out

    def _level(self, info: Dict[str, Any], now: float) -> str:
        left = info["left"]
        if left is None:
            return "unknown"
        if left <= 2:
            return "empty"
        level = "critical" if left < self.critical else "tight" if left < self.tight else "ok"
        resets = info.get("resets_at")
        if level != "ok" and isinstance(resets, (int, float)) and 0 < resets - now < SOON:
            info["soon"] = True
            level = "tight" if level == "critical" else "ok"
        return level

    def mark_exhausted(self, agent: str) -> None:
        """A job just failed on the limit: treat the agent as empty until its window resets."""
        info = self.last.get(agent) or {}
        resets = info.get("resets_at")
        self.exhausted[agent] = resets if isinstance(resets, (int, float)) and resets > time.time() else time.time() + 30 * 60

    def level(self, agent: str) -> str:
        return (self.last.get(agent) or {}).get("level", "unknown")

    def worse(self, a: str, b: str) -> bool:
        """True when agent a is in a clearly worse quota position than agent b."""
        return ORDER[self.level(a)] > ORDER[self.level(b)]

    def describe(self, agent: str) -> str:
        info = self.last.get(agent) or {}
        if info.get("left") is None:
            return t("quota.unknown", name=NAME[agent], of=OF[agent])
        key = "window." + (info.get("window") or "")
        window = t(key) if key != t(key) else ""
        text = t("quota.left", name=NAME[agent], of=OF[agent], window=window, left=info["left"])
        if info.get("resets_at"):
            text += t("quota.resets", when=clock(info["resets_at"]))
        return text

    def prompt_note(self, available: list) -> str:
        """Plain facts for the planner, in English like the other prompts."""
        lines = []
        for agent in available:
            info = self.last.get(agent) or {}
            if info.get("left") is None:
                continue
            advice = {
                "ok": "plenty left",
                "tight": "running low: give it only light tasks, the other agents should take the heavy ones",
                "critical": "almost exhausted: do not assign it tasks unless there is no other agent",
                "empty": "exhausted: do not assign it any task",
            }.get(info["level"], "")
            window = {"5 saat": "5-hour", "hafta": "weekly", "gün": "daily", "ay": "monthly"}.get(info.get("window") or "", "")
            lines.append(f'- "{agent}": {info["left"]}% of its {window} usage quota left ({advice})')
        return "\n".join(lines)
