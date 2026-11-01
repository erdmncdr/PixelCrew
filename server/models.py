"""Which model each job runs on, and what each agent is on the team for.

Strong models are slow and burn quota; the work that shapes everything else
(the plan, its review, hard tasks, the final integration check) gets them,
while routine work goes to faster, cheaper models of the same CLI:

  tier      jobs
  strong    plan, plan_review, integrate, implement/fix of a "high" task,
            review of a "high" task
  standard  implement/fix/review of a "medium" task, chat
  fast      implement/fix/review of a "low" task, every re-review after a fix

Settings > Models can set any tier of any agent to a model from that CLI's own list
(modelOverrides); the "cli" model mode leaves every job on the CLI's own default.

Roles split the team: seniors plan, take the hard and cross-cutting tasks and
check the juniors' work; workers take well-specified, mechanical tasks.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Model ids as each CLI accepts them (Claude Code takes aliases).
TIERS: Dict[str, Dict[str, str]] = {
    "claude": {"strong": "opus", "standard": "sonnet", "fast": "haiku"},
    "codex": {"strong": "gpt-6-astra", "standard": "gpt-6.1-sol", "fast": "gpt-6-luna"},
    # Fallback until Antigravity has listed its models; then the best Gemini family it offers
    # is used throughout (high thinking, low for easy work), see gemini_tiers().
    "gemini": {"strong": "gemini-3.8-flash-high", "standard": "gemini-3.8-flash-high", "fast": "gemini-3.8-flash-low"},
    # grok-4.7-build-fast is faster but costs twice as much, so Grok stays on 4.7 and the
    # lower tiers save quota through a lower reasoning effort instead.
    "grok": {"strong": "grok-4.7", "standard": "grok-4.7", "fast": "grok-4.7"},
}

TIER_NAMES = ("strong", "standard", "fast")

# Model ids (and display names) a CLI reported as available, filled by the health check.
AVAILABLE: Dict[str, List[str]] = {}
LABELS: Dict[str, Dict[str, str]] = {}

CLAUDE_MODELS = [("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku")]  # Claude Code's aliases, until listed
CLAUDE_SKIP = {"default", "best", "opusplan"}   # shortcuts, not models


def parse_claude_models(text: str) -> Tuple[List[str], str]:
    """From `claude -p /model`: the aliases the account can use, and the current default model id.
    e.g. "Current model: `Fable 5.1 (default)` ... Available: sonnet, opus, haiku, fable, best, sonnet[1m], ..."""
    m = re.search(r"Available:\s*(.+?)(?:,\s*or a full model ID|\.|$)", text, re.S)
    ids: List[str] = []
    if m:
        for part in m.group(1).split(","):
            alias = part.strip().strip("`").strip()
            if re.match(r"^[a-z]+(\[1m\])?$", alias) and alias not in CLAUDE_SKIP:
                ids.append(alias)
    default = ""
    cur = re.search(r"Current model:\s*`?([A-Za-z]+)\s+(\d+(?:\.\d+)?)", text)
    if cur:
        default = f"claude-{cur.group(1).lower()}-{cur.group(2).replace('.', '-')}"
    return ids, default


def claude_label(alias: str) -> str:
    base = alias.replace("[1m]", "")
    return base.capitalize() + (" (1M)" if alias.endswith("[1m]") else "")
CODEX_CACHE = Path.home() / ".codex" / "models_cache.json"


def set_available(agent: str, ids: List[str], labels: Optional[Dict[str, str]] = None) -> None:
    AVAILABLE[agent] = [i for i in ids if i]
    if labels:
        LABELS[agent] = dict(labels)


def codex_models() -> List[Tuple[str, str]]:
    """The models Codex lists in its picker, from the CLI's own cache, best first."""
    try:
        data = json.loads(CODEX_CACHE.read_text("utf-8"))
    except (OSError, ValueError):
        return []
    rows = [m for m in data.get("models") or [] if isinstance(m, dict) and m.get("visibility") == "list" and m.get("slug")]
    rows.sort(key=lambda m: m.get("priority", 999))
    return [(m["slug"], str(m.get("display_name") or m["slug"])) for m in rows]


GEMINI_ID = re.compile(r"^gemini-(\d+(?:\.\d+)?)-([a-z]+)-(high|medium|low)$")


def gemini_tiers(ids: List[str]) -> Optional[Dict[str, str]]:
    """The best Gemini family Antigravity offers (newest version; Pro over Flash), on its
    high-thinking variant for strong and standard work and its low one for easy work."""
    families: Dict[Tuple[float, int, str], Dict[str, str]] = {}
    for model in ids:
        m = GEMINI_ID.match(model)
        if not m:
            continue
        version, name, level = float(m.group(1)), m.group(2), m.group(3)
        rank = {"ultra": 3, "pro": 2, "flash": 1}.get(name, 0)
        families.setdefault((version, rank, name), {})[level] = model
    if not families:
        return None
    levels = families[max(families)]
    high = levels.get("high") or levels.get("medium") or levels.get("low")
    low = levels.get("low") or levels.get("medium") or high
    return {"strong": high, "standard": high, "fast": low}


def catalog() -> Dict[str, List[Dict[str, str]]]:
    """Per agent, the models its CLI offers right now, for the model picker in Settings."""
    claude = [(a, claude_label(a)) for a in AVAILABLE.get("claude", [])] or CLAUDE_MODELS
    out: Dict[str, List[Dict[str, str]]] = {"claude": [{"id": i, "label": l} for i, l in claude]}
    out["codex"] = [{"id": i, "label": l} for i, l in codex_models()]
    gemini = [m for m in AVAILABLE.get("gemini", []) if m.startswith("gemini-")]
    out["gemini"] = [{"id": m, "label": LABELS.get("gemini", {}).get(m, m)} for m in gemini]
    out["grok"] = [{"id": m, "label": m} for m in AVAILABLE.get("grok", [])]
    return out


def tiers() -> Dict[str, Dict[str, str]]:
    """The automatic model per tier: TIERS, moved onto what each CLI actually offers."""
    out = {a: dict(t) for a, t in TIERS.items()}
    if "fable" in AVAILABLE.get("claude", []):   # Fable sits above Opus when the account has it
        out["claude"]["strong"] = "fable"
    best = gemini_tiers(AVAILABLE.get("gemini", []))
    if best:
        out["gemini"] = best
    for agent, offered in (("codex", [i for i, _ in codex_models()]), ("grok", AVAILABLE.get("grok", []))):
        if offered:  # a tier model the CLI no longer lists falls back to its best one
            for level, model in out[agent].items():
                if model not in offered:
                    out[agent][level] = offered[0]
    return out


def override(agent: str, level: str, settings: Dict[str, Any]) -> str:
    """A model chosen for this agent and tier in Settings > Models, or ""."""
    chosen = ((settings.get("modelOverrides") or {}).get(agent) or {}).get(level)
    return str(chosen or "").strip()


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
    fixed = str(settings.get(f"{agent}Model") or "").strip()   # older settings: one model for every job
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
    return override(agent, level, settings) or tiers().get(agent, {}).get(level), level


def role(agent: str, settings: Dict[str, Any]) -> str:
    value = (settings.get("roles") or {}).get(agent)
    return value if value in ROLES else DEFAULT_ROLES.get(agent, "worker")
