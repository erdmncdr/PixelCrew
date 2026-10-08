"""Model and subscription-quota status for both agents.

Codex writes `rate_limits` (plan, used percent per window, reset time) into
its own session logs under ~/.codex/sessions, so its quota can be read at
any time. Only that account-level line is read; session contents are not.

Claude reports quota only inside a live stream (`rate_limit_event`), so the
last value seen during a PixelCrew job is kept in data/status.json and shown
with its age."""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import DATA_DIR, HOME

STATUS_FILE = DATA_DIR / "status.json"
CODEX_SESSIONS = HOME / ".codex" / "sessions"
CODEX_CONFIG = HOME / ".codex" / "config.toml"
CLAUDE_SETTINGS = HOME / ".claude" / "settings.json"

_lock = threading.Lock()


# ------------------------------------------------------------------ store
def _load() -> Dict[str, Any]:
    try:
        return json.loads(STATUS_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, STATUS_FILE)


def record_model(agent: str, model: Optional[str], effort: Optional[str] = None) -> None:
    if not model:
        return
    with _lock:
        data = _load()
        entry = data.setdefault(agent, {})
        entry["model"], entry["model_at"] = model, time.time()
        if effort:
            entry["effort"] = effort
        _save(data)


def record_spend(agent: str, cost_usd: float) -> None:
    """API-priced spend of a finished job, for CLIs that do not report their remaining quota."""
    if not cost_usd:
        return
    with _lock:
        data = _load()
        entry = data.setdefault(agent, {})
        week_ago = time.time() - 7 * 86400
        spend = [s for s in entry.get("spend") or [] if s[0] > week_ago]
        spend.append([time.time(), round(float(cost_usd), 5)])
        entry["spend"] = spend
        _save(data)


def spend_summary(agent: str, stored: Dict[str, Any]) -> Dict[str, float]:
    now = time.time()
    spend = (stored.get(agent) or {}).get("spend") or []
    return {"5h": round(sum(c for t, c in spend if t > now - 5 * 3600), 2),
            "week": round(sum(c for t, c in spend if t > now - 7 * 86400), 2)}


def record_claude_limit(window: Optional[str], utilization: Any, resets_at: Any, status: Optional[str]) -> None:
    if window is None or not isinstance(utilization, (int, float)):
        return
    with _lock:
        data = _load()
        limits = data.setdefault("claude", {}).setdefault("limits", {})
        limits[window] = {"used": float(utilization), "resets_at": resets_at, "status": status, "at": time.time()}
        _save(data)


# ------------------------------------------------------------------ claude /usage
# `claude -p "/usage"` is answered locally by the CLI (no model call, no cost)
# and is the only source of exact Claude percentages: the stream's
# rate_limit_event carries a number only once usage nears a warning threshold.
USAGE_LINE = re.compile(r"^Current (session|week(?: \(([^)]*)\))?):\s*(\d+(?:\.\d+)?)% used(?:\s*\S\s*resets (.+))?$", re.M)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _parse_reset(text: Optional[str]) -> Optional[float]:
    """'Oct 6 at 6:19am (Europe/Istanbul)' -> epoch seconds."""
    if not text:
        return None
    m = re.match(r"([A-Za-z]{3})[a-z]*\s+(\d{1,2})(?:,?\s+(\d{4}))?\s+at\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\s*(?:\(([^)]+)\))?", text.strip(), re.I)
    if not m:
        return None
    month = MONTHS.get(m.group(1).lower()[:3])
    if not month:
        return None
    hour = int(m.group(4)) % 12 if m.group(6) else int(m.group(4))
    if (m.group(6) or "").lower() == "pm":
        hour += 12
    minute = int(m.group(5) or 0)
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(m.group(7)) if m.group(7) else None
        now = datetime.now(tz)
        year = int(m.group(3)) if m.group(3) else now.year
        when = datetime(year, month, int(m.group(2)), hour, minute, tzinfo=tz)
        if not m.group(3) and (now - when).days > 180:  # "Jan 2" seen in late December
            when = when.replace(year=year + 1)
        return when.timestamp()
    except Exception:
        return None


def parse_claude_usage(text: str) -> List[Dict[str, Any]]:
    windows = []
    for m in USAGE_LINE.finditer(text or ""):
        kind, scope, pct, reset = m.group(1), m.group(2), float(m.group(3)), m.group(4)
        if kind == "session":
            key, label = "five_hour", "5 saat"
        elif not scope or scope.lower().startswith("all"):
            key, label = "seven_day", "hafta"
        else:
            key, label = "seven_day_" + re.sub(r"\W+", "_", scope.lower()).strip("_"), f"hafta ({scope})"
        windows.append({"key": key, "label": label, "used": pct, "resets_at": _parse_reset(reset)})
    return windows


class ClaudeUsagePoller:
    """Refreshes Claude's /usage in the background; never blocks a request."""

    MIN_GAP = 45      # seconds between probes
    MAX_AGE = 300     # refresh when older than this
    agent = "claude"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._last_try = 0.0

    def request(self, force: bool = False) -> None:
        with self._lock:
            if self._running or time.time() - self._last_try < (self.MIN_GAP if force else self.MAX_AGE):
                return
            self._running, self._last_try = True, time.time()
        threading.Thread(target=self._probe, daemon=True).start()

    def refresh_if_older(self, max_age: float) -> None:
        """Synchronous refresh for decisions that need a current number (run start)."""
        usage = (_load().get(self.agent) or {}).get("usage") or {}
        if time.time() - float(usage.get("at") or 0) < max_age:
            return
        with self._lock:
            if self._running:
                return
            self._running, self._last_try = True, time.time()
        self._probe()

    def _probe(self) -> None:
        try:
            from .config import HEALTH, child_env, load_settings
            binary = HEALTH.binaries().get("claude")
            if not binary:
                return
            proc = subprocess.run(
                [binary, "-p", "--output-format", "json", "--no-session-persistence"],
                input="/usage", capture_output=True, text=True, timeout=45, cwd=str(DATA_DIR),
                env=child_env("claude", load_settings()),
            )
            result = json.loads(proc.stdout or "{}").get("result") or ""
            windows = parse_claude_usage(result)
            if windows:
                with _lock:
                    data = _load()
                    data.setdefault("claude", {})["usage"] = {"windows": windows, "at": time.time()}
                    _save(data)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        finally:
            with self._lock:
                self._running = False


CLAUDE_USAGE = ClaudeUsagePoller()


def parse_agy_usage(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """`agy -p /usage --output-format json` -> windows of the Gemini model group.
    Each bucket reports remaining_fraction and an ISO reset_time."""
    groups = (((payload.get("command") or {}).get("data") or {}).get("groups")) or []
    windows: List[Dict[str, Any]] = []
    for group in groups:
        if not str(group.get("name") or "").lower().startswith("gemini"):
            continue  # the other group covers Claude/GPT models served through Antigravity
        for bucket in group.get("buckets") or []:
            label = {"5h": "5 saat", "weekly": "hafta"}.get(bucket.get("window"))
            fraction = bucket.get("remaining_fraction")
            if not label or not isinstance(fraction, (int, float)):
                continue
            resets = None
            try:
                from datetime import datetime
                resets = datetime.fromisoformat(str(bucket.get("reset_time")).replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
            windows.append({"label": label, "used": round((1 - float(fraction)) * 100), "resets_at": resets})
    windows.sort(key=lambda w: 0 if w["label"] == "5 saat" else 1)
    return windows


class AgyUsagePoller(ClaudeUsagePoller):
    """Gemini's limits through Antigravity's /usage, which is answered locally (no model call)."""
    agent = "gemini"

    def _probe(self) -> None:
        try:
            from .config import HEALTH, child_env, load_settings
            binary = HEALTH.binaries().get("gemini")
            if not binary or Path(binary).name != "agy":
                return
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                [binary, "-p", "/usage", "--output-format", "json"], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=45, cwd=str(DATA_DIR), env=child_env("gemini", load_settings()),
            )
            windows = parse_agy_usage(json.loads(proc.stdout or "{}"))
            if windows:
                with _lock:
                    data = _load()
                    data.setdefault("gemini", {})["usage"] = {"windows": windows, "at": time.time()}
                    _save(data)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        finally:
            with self._lock:
                self._running = False


AGY_USAGE = AgyUsagePoller()


GROK_PERIODS = {"USAGE_PERIOD_TYPE_WEEKLY": "hafta", "USAGE_PERIOD_TYPE_MONTHLY": "ay", "USAGE_PERIOD_TYPE_DAILY": "gün"}


def _iso(value: Any) -> Optional[float]:
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_grok_billing(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Grok's `_x.ai/billing` answer -> one window. creditUsagePercent is a protobuf field,
    so it is left out entirely while the usage is still 0."""
    cfg = result.get("config") or {}
    if not cfg:
        return []
    period = cfg.get("currentPeriod") or {}
    pct = cfg.get("creditUsagePercent", 0)
    if isinstance(pct, dict):
        pct = pct.get("val", 0)
    try:
        pct = float(pct or 0)
    except (TypeError, ValueError):
        pct = 0.0
    return [{"label": GROK_PERIODS.get(str(period.get("type")), "hafta"), "used": round(pct),
             "resets_at": _iso(period.get("end") or cfg.get("billingPeriodEnd")),
             "plan": result.get("subscription_tier")}]


class GrokUsagePoller(ClaudeUsagePoller):
    """Grok's allowance as its own /usage screen gets it: the `_x.ai/billing` extension method
    of `grok agent stdio`, authenticated with the CLI's cached login. No session, no model call."""
    agent = "grok"

    def _probe(self) -> None:
        proc = None
        try:
            import select
            from .config import HEALTH, child_env, load_settings
            binary = HEALTH.binaries().get("grok")
            if not binary:
                return
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            proc = subprocess.Popen([binary, "agent", "--no-leader", "stdio"], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
                                    cwd=str(DATA_DIR), env=child_env("grok", load_settings()))
            deadline = time.time() + 40

            def call(rid: int, method: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                assert proc and proc.stdin and proc.stdout
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
                proc.stdin.flush()
                while time.time() < deadline:
                    ready, _, _ = select.select([proc.stdout], [], [], 0.5)
                    if not ready:
                        continue
                    line = proc.stdout.readline()
                    if not line:
                        return None
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("id") == rid:
                        return msg
                return None

            if not call(1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}}):
                return
            auth = call(2, "authenticate", {"methodId": "cached_token"})
            if not auth or auth.get("error"):
                return
            answer = call(3, "_x.ai/billing", {})
            windows = parse_grok_billing((answer or {}).get("result") or {})
            if windows:
                with _lock:
                    data = _load()
                    data.setdefault("grok", {})["usage"] = {"windows": windows, "at": time.time()}
                    _save(data)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        finally:
            if proc and proc.poll() is None:
                proc.kill()
            with self._lock:
                self._running = False


GROK_USAGE = GrokUsagePoller()


# ------------------------------------------------------------------ codex
def _recent_rollouts(limit: int = 12) -> List[Path]:
    files: List[Path] = []
    if not CODEX_SESSIONS.is_dir():
        return files
    for year in sorted((p for p in CODEX_SESSIONS.iterdir() if p.is_dir()), reverse=True):
        for month in sorted((p for p in year.iterdir() if p.is_dir()), reverse=True):
            for day in sorted((p for p in month.iterdir() if p.is_dir()), reverse=True):
                files.extend(day.glob("rollout-*.jsonl"))
                if len(files) >= limit:
                    break
            if len(files) >= limit:
                break
        if len(files) >= limit:
            break
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def _tail_lines(path: Path, max_bytes: int = 400_000) -> List[str]:
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - max_bytes))
        chunk = fh.read().decode("utf-8", errors="replace")
    lines = chunk.splitlines()
    return lines[1:] if size > max_bytes else lines


def _find_key(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key(v, key)
            if found is not None:
                return found
    return None


def codex_rate_limits() -> Optional[Dict[str, Any]]:
    """Newest rate_limits block across recent Codex sessions."""
    for path in _recent_rollouts():
        try:
            lines = _tail_lines(path)
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                rl = _find_key(json.loads(line), "rate_limits")
            except ValueError:
                continue
            if isinstance(rl, dict) and (rl.get("primary") or rl.get("secondary")):
                return {"limits": rl, "at": path.stat().st_mtime}
    return None


def codex_thread_info(thread_id: str) -> Dict[str, Any]:
    """Model/effort actually used by one of our Codex threads, plus its latest limits."""
    out: Dict[str, Any] = {}
    if not thread_id or not re.fullmatch(r"[0-9a-fA-F-]{8,64}", thread_id):
        return out
    matches = sorted(CODEX_SESSIONS.glob(f"*/*/*/rollout-*-{thread_id}.jsonl"), key=lambda p: p.stat().st_mtime)
    if not matches:
        return out
    for line in _tail_lines(matches[-1]):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        payload = d.get("payload") or {}
        if d.get("type") == "turn_context":
            out["model"] = payload.get("model") or out.get("model")
            out["effort"] = payload.get("effort") or out.get("effort")
        rl = _find_key(d, "rate_limits") if '"rate_limits"' in line else None
        if isinstance(rl, dict):
            out["limits"] = rl
    return out


def _codex_config_default() -> Dict[str, Optional[str]]:
    model = effort = None
    try:
        for line in CODEX_CONFIG.read_text("utf-8").splitlines():
            m = re.match(r'^\s*(model|model_reasoning_effort)\s*=\s*"([^"]+)"', line)
            if m and m.group(1) == "model":
                model = m.group(2)
            elif m:
                effort = m.group(2)
    except OSError:
        pass
    return {"model": model, "effort": effort}


def _window_label_minutes(minutes: Any) -> str:
    try:
        m = int(minutes)
    except (TypeError, ValueError):
        return "limit"
    if m == 300:
        return "5 saat"
    if m == 10080:
        return "hafta"
    if m % 1440 == 0:
        return f"{m // 1440} gün"
    if m % 60 == 0:
        return f"{m // 60} saat"
    return f"{m} dk"


CLAUDE_WINDOWS = {"five_hour": "5 saat", "seven_day": "hafta", "seven_day_opus": "hafta (Opus)", "seven_day_sonnet": "hafta (Sonnet)"}


def _backfill_claude() -> None:
    """Seed Claude's last-known model/limits from PixelCrew's own event logs."""
    logs = sorted(list((DATA_DIR / "runs").glob("*/events.jsonl")) + list((DATA_DIR / "chats").glob("*/events.jsonl")),
                  key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    model = None
    limits: Dict[str, Any] = {}
    for path in logs:
        try:
            lines = path.read_text("utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if '"claude"' not in line or ('"agent.limit"' not in line and '"agent.session"' not in line):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("agent") != "claude":
                continue
            if e.get("kind") == "agent.session" and e.get("model") and not model:
                model = e["model"]
            if e.get("kind") == "agent.limit" and e.get("window") and isinstance(e.get("utilization"), (int, float)):
                prev = limits.get(e["window"])
                if not prev or e.get("ts", 0) > prev["at"]:
                    limits[e["window"]] = {"used": float(e["utilization"]), "resets_at": e.get("resets_at"),
                                           "status": e.get("status"), "at": e.get("ts", 0)}
        if model and limits:
            break
    if not model and not limits:
        return
    with _lock:
        data = _load()
        entry = data.setdefault("claude", {})
        if model and not entry.get("model"):
            entry["model"], entry["model_at"] = model, time.time()
        if limits and not entry.get("limits"):
            entry["limits"] = limits
        _save(data)


def _backfill_spend(agent: str) -> None:
    """Seed an agent's spend for the last week from PixelCrew's own event logs (once)."""
    week_ago = time.time() - 7 * 86400
    spend: List[List[float]] = []
    for path in list((DATA_DIR / "runs").glob("*/events.jsonl")) + list((DATA_DIR / "chats").glob("*/events.jsonl")):
        try:
            if path.stat().st_mtime < week_ago:
                continue
            lines = path.read_text("utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if '"agent.usage"' not in line or f'"{agent}"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("agent") == agent and e.get("cost_usd") and e.get("ts", 0) > week_ago:
                spend.append([float(e["ts"]), round(float(e["cost_usd"]), 5)])
    with _lock:
        data = _load()
        entry = data.setdefault(agent, {})
        if not entry.get("spend_backfilled"):
            entry["spend"] = sorted(spend + list(entry.get("spend") or []))
            entry["spend_backfilled"] = True
            _save(data)


# ------------------------------------------------------------------ public
def snapshot(settings: Dict[str, Any]) -> Dict[str, Any]:
    stored = _load()
    if not stored.get("claude"):
        _backfill_claude()
        stored = _load()

    # Claude
    c = stored.get("claude", {})
    claude_model = settings.get("claudeModel") or c.get("model")
    if not claude_model:
        try:
            claude_model = json.loads(CLAUDE_SETTINGS.read_text("utf-8")).get("model")
        except (OSError, ValueError):
            claude_model = None
    claude_windows = []
    usage = c.get("usage") or {}
    CLAUDE_USAGE.request()  # refreshes in the background when older than MAX_AGE
    for w in usage.get("windows") or []:
        resets = w.get("resets_at")
        rolled = isinstance(resets, (int, float)) and resets < time.time()
        claude_windows.append({
            "label": w.get("label"), "used": 0 if rolled else round(float(w.get("used") or 0)), "resets_at": resets,
            "status": None, "seen_at": usage.get("at"), "rolled": rolled,
        })
    # Fallback for when /usage has never succeeded: the last numeric stream event.
    for key, v in ({} if claude_windows else (c.get("limits") or {})).items():
        resets = v.get("resets_at")
        rolled = isinstance(resets, (int, float)) and resets < time.time()
        used = 0.0 if rolled else float(v.get("used") or 0)  # a new window has started since we last saw it
        claude_windows.append({
            "label": CLAUDE_WINDOWS.get(key, key), "used": round(used * 100), "resets_at": resets,
            "status": None if rolled else v.get("status"), "seen_at": v.get("at"), "rolled": rolled,
        })
    claude = {"model": claude_model, "model_source": "settings" if settings.get("claudeModel") else ("seen" if c.get("model") else None),
              "windows": claude_windows}

    # Codex
    x = stored.get("codex", {})
    default = _codex_config_default()
    codex_model = settings.get("codexModel") or default["model"] or x.get("model")
    codex = {"model": codex_model, "effort": default["effort"] or x.get("effort"), "windows": [], "plan": None}
    rl = codex_rate_limits()
    if rl:
        limits = rl["limits"]
        codex["plan"] = limits.get("plan_type")
        for slot in ("primary", "secondary"):
            w = limits.get(slot)
            if not isinstance(w, dict):
                continue
            resets = w.get("resets_at")
            rolled = isinstance(resets, (int, float)) and resets < time.time()
            used = 0.0 if rolled else float(w.get("used_percent") or 0)
            codex["windows"].append({
                "label": _window_label_minutes(w.get("window_minutes")), "used": round(used),
                "resets_at": resets, "seen_at": rl["at"], "rolled": rolled,
                "status": "rejected" if limits.get("rate_limit_reached_type") else None,
            })
    # Gemini (Antigravity /usage) and Grok (no headless quota; model only)
    gemini = {"model": settings.get("geminiModel") or (stored.get("gemini") or {}).get("model"), "windows": []}
    gusage = (stored.get("gemini") or {}).get("usage") or {}
    AGY_USAGE.request()
    for w in gusage.get("windows") or []:
        resets = w.get("resets_at")
        rolled = isinstance(resets, (int, float)) and resets < time.time()
        gemini["windows"].append({"label": w.get("label"), "used": 0 if rolled else w.get("used"), "resets_at": resets,
                                  "status": None, "seen_at": gusage.get("at"), "rolled": rolled})
    if not (stored.get("grok") or {}).get("spend_backfilled"):
        _backfill_spend("grok")
        stored = _load()
    grok = {"model": settings.get("grokModel") or (stored.get("grok") or {}).get("model") or _grok_default_model(),
            "windows": [], "spend": spend_summary("grok", stored), "plan": None}
    xusage = (stored.get("grok") or {}).get("usage") or {}
    GROK_USAGE.request()
    for w in xusage.get("windows") or []:
        resets = w.get("resets_at")
        rolled = isinstance(resets, (int, float)) and resets < time.time()
        grok["plan"] = w.get("plan")
        grok["windows"].append({"label": w.get("label"), "used": 0 if rolled else w.get("used"), "resets_at": resets,
                                "status": None, "seen_at": xusage.get("at"), "rolled": rolled})
    return {"claude": claude, "codex": codex, "gemini": gemini, "grok": grok, "now": time.time()}


def _grok_default_model() -> Optional[str]:
    try:
        data = json.loads((HOME / ".grok" / "models_cache.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    models = data.get("models")
    return next(iter(models), None) if isinstance(models, dict) else None  # the CLI lists its default first
