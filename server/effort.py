"""Automatic reasoning-effort selection.

High effort is slow and burns quota; low effort misses things. The level is
chosen per job from what the job is and how hard the planner judged the task:

  plan          high    the plan shapes everything after it
  plan_review   medium  a critique of a short document
  implement     the task's complexity (low / medium / high)
  fix           the task's complexity
  review        the task's complexity, capped at medium
  re-review     low     only checks that requested fixes landed
  integrate     medium  runs the tests and repairs seams
  chat          low     quick answers; follow-ups can go deeper

A user override on a task (set during plan approval) wins for that task's
implement / fix jobs. The global effortMode setting can pin every job to one
level, or hand control back to each CLI's own default ("cli").
"""
from __future__ import annotations

from typing import Any, Dict, Optional

LEVELS = ("low", "medium", "high")
CAP = {"low": "low", "medium": "medium", "high": "medium"}


def pick(job: str, settings: Dict[str, Any], task: Optional[Dict[str, Any]] = None) -> Optional[str]:
    mode = settings.get("effortMode", "auto")
    if mode == "cli":
        return None
    if mode in LEVELS:
        return mode
    task = task or {}
    complexity = task.get("complexity") if task.get("complexity") in LEVELS else "medium"
    chosen = task.get("effort") if task.get("effort") in LEVELS else None
    if job == "plan":
        return "high"
    if job == "plan_review":
        return "medium"
    if job in ("implement", "fix"):
        return chosen or complexity
    if job == "review":
        return "low" if task.get("fixes") else CAP[chosen or complexity]
    if job == "integrate":
        return "medium"
    if job == "chat":
        return "low"
    return "medium"
