"""Which model each job runs on, and what each agent is on the team for.

Strong models are slow and burn quota; the work that shapes everything else
(the plan, its review, hard tasks, the final integration check) gets them,
while routine work goes to faster, cheaper models of the same CLI:

  tier      jobs
  strong    plan, plan_review, integrate, implement/fix of a "high" task,
            review of a "high" task
  standard  implement/fix/review of a "medium" task, chat
  fast      implement/fix/review of a "low" task, every re-review after a fix

A model typed into the settings for an agent pins that agent to it; the
"cli" model mode leaves every job on the CLI's own default.

Roles split the team: seniors plan, take the hard and cross-cutting tasks and
check the juniors' work; workers take well-specified, mechanical tasks.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# Model ids as each CLI accepts them (Claude Code takes aliases).
TIERS: Dict[str, Dict[str, str]] = {
    "claude": {"strong": "opus", "standard": "sonnet", "fast": "haiku"},
    "codex": {"strong": "gpt-6-astra", "standard": "gpt-6.1-sol", "fast": "gpt-6-luna"},
    # Gemini stays on its best model (3.1 Pro) throughout; easy work uses its low-thinking variant.
    "gemini": {"strong": "gemini-3.1-pro-high", "standard": "gemini-3.1-pro-high", "fast": "gemini-3.1-pro-low"},
    # grok-4.7-build-fast is faster but costs twice as much, so Grok stays on 4.7 and the
    # lower tiers save quota through a lower reasoning effort instead.
    "grok": {"strong": "grok-4.7", "standard": "grok-4.7", "fast": "grok-4.7"},
}

# Model ids a CLI reported as available (filled by the health check); used to move a
# tier onto a newer model as soon as the account gets it.
AVAILABLE: Dict[str, List[str]] = {}


def set_available(agent: str, ids: List[str]) -> None:
    AVAILABLE[agent] = [i for i in ids if i]


def tiers() -> Dict[str, Dict[str, str]]:
    """TIERS, with Gemini's strong tier on Gemini 4 once Antigravity offers it."""
    out = {a: dict(t) for a, t in TIERS.items()}
    newer = [m for m in AVAILABLE.get("gemini", []) if re.match(r"gemini-4", m)]
    if newer:
        out["gemini"]["strong"] = next((m for m in newer if m.endswith("-high")), newer[0])
    return out


ROLES = ("senior", "worker")
DEFAULT_ROLES = {"claude": "senior", "codex": "senior", "gemini": "worker", "grok": "worker"}

BY_COMPLEXITY = {"high": "strong", "medium": "standard", "low": "fast"}


def tier(job: str, task: Optional[Dict[str, Any]] = None) -> str:
    task = task or {}
    complexity = task.get("complexity") if task.get("complexity") in BY_COMPLEXITY else "medium"
    if job in ("plan", "plan_review", "integrate"):
        return "strong"
    if job == "review" and task.get("fixes"):
        return "fast"  # a re-review only checks that the requested fixes landed
    if job in ("implement", "fix", "review"):
        return BY_COMPLEXITY[complexity]
    return "standard"


STEP_DOWN = {"strong": "standard", "standard": "fast", "fast": "fast"}


def pick(agent: str, job: str, settings: Dict[str, Any], task: Optional[Dict[str, Any]] = None,
         binary_kind: str = "", quota_level: str = "ok") -> Tuple[Optional[str], Optional[str]]:
    """(model id, tier) for this job; (None, None) leaves it to the CLI's default.
    An agent running low on quota drops a tier (tight) or goes straight to fast (critical)."""
    fixed = str(settings.get(f"{agent}Model") or "").strip()
    if fixed:
        return fixed, None
    if settings.get("modelMode", "auto") != "auto":
        return None, None
    if agent == "gemini" and binary_kind != "agy":
        return None, None  # the tier ids are Antigravity's; Gemini CLI keeps its default
    level = tier(job, task)
    if quota_level == "tight":
        level = STEP_DOWN[level]
    elif quota_level in ("critical", "empty"):
        level = "fast"
    return tiers().get(agent, {}).get(level), level


def role(agent: str, settings: Dict[str, Any]) -> str:
    value = (settings.get("roles") or {}).get(agent)
    return value if value in ROLES else DEFAULT_ROLES.get(agent, "worker")
