"""llm_router.py — Configuration, prompt construction, and decision validation
for the Wee "router" runtime (issue #506).

Design note: this module is intentionally free of any agent_manager /
dispatch imports. The actual call to the router "brain" (an LLM invoked via
a full runtime dispatch — e.g. `wee` + a local Ollama model, or `copilot` +
haiku) is made by `SessionManager.run_router()` in agent_manager.py, which
injects an `invoke_brain` callable into `LLMRouter.route()`. Keeping the
invocation out of this module makes prompt-building and decision validation
trivially unit-testable without spinning up any runtime.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

CONFIG_DIR = Path(os.path.dirname(os.path.abspath(__file__))) / "config"
ROUTER_CONFIG_PATH = CONFIG_DIR / "router_config.json"

REQUIRED_TEMPLATE_PLACEHOLDERS = ("{allowlist_table}", "{user_message}")

DEFAULT_ROUTER_CONFIG = {
    "enabled": False,
    "brain": {"runtime": "wee", "model": "ollama/qwen3:8b"},
    "timeout_seconds": 30,
    "prompt_template": (
        "You are a model router for an AI assistant platform. Given the "
        "candidate runtime/model pairs below and the user's message, pick "
        "the single best pair for this request.\n\n"
        "Candidates:\n{allowlist_table}\n\n"
        "{stickiness_hint}\n"
        "User message:\n{user_message}\n\n"
        "Respond with ONLY a JSON object, no other text:\n"
        '{{"runtime": "...", "model": "...", "reason": "..."}}'
    ),
    "allowlist": [],
    "fallback": {"runtime": "copilot", "model": "auto"},
    "stickiness": {"enabled": True, "prefer_same_runtime": True, "window_seconds": 900},
    "cooldown_seconds": 300,
}

# Infra-failure detection, mirroring agent_manager.py's background-task
# fallback patterns (issue #219/#243) so a runtime that is rate-limited,
# unauthorized, unreachable, or timing out gets a router-side cooldown using
# the same signal already trusted for background-task fallback. Kept as its
# own copy here — not imported — because the original lives as a local
# closure inside agent_manager's background-task endpoint, not a module-level
# helper.
_INFRA_FAILURE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\b429\b",
        r"\brate[\s\-]?limit(?:ed|ing)?\b",
        r"\bquota[\s\-]exceeded\b",
        r"\b401\b",
        r"\bunauthorized\b",
        r"\bmissing[\s\-]authentication\b",
        r"\bapi[\s_\-]?key[\s_\-]?(?:invalid|expired|missing)\b",
        r"\b503\b",
        r"\bservice[\s\-]unavailable\b",
        r"\b502\b",
        r"\bbad[\s\-]gateway\b",
        r"\bconnection[\s\-]refused\b",
        r"\btimed?\s*out\b",
        r"\betimedout\b",
        r"\boverloaded\b",
    ]
]
_INFRA_EXCLUSION_RE = re.compile(
    r"^(?:assert(?:ion)?error|typeerror|valueerror|keyerror"
    r"|attributeerror|nameerror|runtimeerror)\s*:",
    re.IGNORECASE,
)


def is_infra_failure_text(error_text: Optional[str]) -> bool:
    """True if error_text looks like an infra failure (rate limit, auth, 5xx, timeout)."""
    if not error_text:
        return False
    if _INFRA_EXCLUSION_RE.match(error_text.strip()):
        return False
    return any(pat.search(error_text) for pat in _INFRA_FAILURE_PATTERNS)


@dataclass
class RouteDecision:
    runtime: str
    model: str
    reason: str = ""
    latency_ms: int = 0
    source: str = "router"  # "router" | "single" | "fallback"


class RouterConfigError(ValueError):
    """Raised by RouterConfig.save() when the config fails validation."""


class RouterConfig:
    """Loads/saves/validates config/router_config.json, mtime-cached like
    the model-manifest loader so edits apply live without a restart."""

    def __init__(self, path: Path = ROUTER_CONFIG_PATH):
        self._path = Path(path)
        self._cache: Optional[dict] = None
        self._cache_mtime: Optional[float] = None

    def load(self) -> dict:
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            return json.loads(json.dumps(DEFAULT_ROUTER_CONFIG))
        if self._cache is not None and self._cache_mtime == mtime:
            return self._cache
        try:
            with open(self._path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return json.loads(json.dumps(DEFAULT_ROUTER_CONFIG))
        merged = {**DEFAULT_ROUTER_CONFIG, **data}
        self._cache = merged
        self._cache_mtime = mtime
        return merged

    def save(self, config: dict) -> None:
        errors = self.validate(config)
        if errors:
            raise RouterConfigError("; ".join(errors))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            try:
                self._path.replace(self._path.with_suffix(".json.bak"))
            except OSError:
                pass
        with open(self._path, "w") as f:
            json.dump(config, f, indent=2)
        self._cache = None
        self._cache_mtime = None

    @staticmethod
    def validate(config: dict) -> list:
        errors = []
        brain = config.get("brain") or {}
        if not brain.get("runtime"):
            errors.append("brain.runtime is required")
        elif brain.get("runtime") == "router":
            errors.append("brain.runtime cannot be 'router' (no recursive routing)")
        if not brain.get("model"):
            errors.append("brain.model is required")

        allowlist = config.get("allowlist") or []
        if not allowlist:
            errors.append("allowlist must contain at least one runtime/model pair")
        for i, entry in enumerate(allowlist):
            if not entry.get("runtime") or not entry.get("model"):
                errors.append(f"allowlist[{i}] must have both runtime and model")
            elif entry.get("runtime") == "router":
                errors.append(f"allowlist[{i}] cannot target 'router' itself")

        fallback = config.get("fallback") or {}
        if not fallback.get("runtime") or not fallback.get("model"):
            errors.append("fallback.runtime and fallback.model are required")
        elif fallback.get("runtime") == "router":
            errors.append("fallback.runtime cannot be 'router'")

        template = config.get("prompt_template") or ""
        for placeholder in REQUIRED_TEMPLATE_PLACEHOLDERS:
            if placeholder not in template:
                errors.append(f"prompt_template missing required placeholder {placeholder}")

        timeout = config.get("timeout_seconds")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            errors.append("timeout_seconds must be a positive number")

        cooldown = config.get("cooldown_seconds")
        if not isinstance(cooldown, (int, float)) or cooldown < 0:
            errors.append("cooldown_seconds must be a non-negative number")

        return errors


class RuntimeCooldownTracker:
    """Tracks runtimes temporarily excluded from routing after an infra failure."""

    def __init__(self):
        self._cooldowns = {}  # runtime -> {"until": ts, "reason": str}

    def mark_failure(self, runtime: str, reason: str, cooldown_seconds: float) -> None:
        self._cooldowns[runtime] = {"until": time.time() + cooldown_seconds, "reason": reason}

    def is_cooling(self, runtime: str) -> bool:
        entry = self._cooldowns.get(runtime)
        if not entry:
            return False
        if time.time() >= entry["until"]:
            del self._cooldowns[runtime]
            return False
        return True

    def status(self) -> dict:
        now = time.time()
        return {
            rt: {"reason": e["reason"], "seconds_remaining": max(0, round(e["until"] - now))}
            for rt, e in self._cooldowns.items()
            if e["until"] > now
        }


def build_allowlist_table(allowlist: list) -> str:
    lines = []
    for entry in allowlist:
        hint = entry.get("hint", "")
        line = f"- runtime={entry['runtime']} model={entry['model']}"
        if hint:
            line += f" — {hint}"
        lines.append(line)
    return "\n".join(lines) if lines else "(none available)"


def build_stickiness_hint(last_routed: Optional[dict], stickiness_cfg: dict) -> str:
    if not stickiness_cfg.get("enabled") or not last_routed:
        return ""
    age = time.time() - last_routed.get("ts", 0)
    window = stickiness_cfg.get("window_seconds", 900)
    if age > window:
        return ""
    pair = f"runtime={last_routed.get('runtime')} model={last_routed.get('model')}"
    if stickiness_cfg.get("prefer_same_runtime", True):
        return (
            f"The previous message in this conversation was routed to {pair}. "
            "Prefer staying on the same runtime/model unless the new request "
            "clearly needs a different one — this reuses cached context and "
            "is cheaper and faster."
        )
    return f"The previous message in this conversation was routed to {pair}."


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_decision_json(raw: Optional[str]) -> Optional[dict]:
    """Tolerantly extract a JSON object from an LLM's free-form reply."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class LLMRouter:
    """Stateless decision engine: builds the router prompt, validates the
    brain's reply against the eligible allowlist, and falls back safely on
    any failure. `route()` never raises — every failure path returns a
    RouteDecision with source="fallback" (empty runtime/model only if even
    the fallback pair is unavailable, in which case the caller must supply
    an agent-primary last resort)."""

    def __init__(self, config: RouterConfig, cooldowns: RuntimeCooldownTracker):
        self.config = config
        self.cooldowns = cooldowns

    def eligible_pairs(self, allowlist: list, runtime_available: Callable[[str], bool]) -> list:
        return [
            entry
            for entry in allowlist
            if entry.get("runtime") != "router"
            and not self.cooldowns.is_cooling(entry["runtime"])
            and runtime_available(entry["runtime"])
        ]

    def route(
        self,
        *,
        prompt: str,
        last_routed: Optional[dict],
        runtime_available: Callable[[str], bool],
        invoke_brain: Callable[[str, str, str, float], Optional[str]],
        resolve_model: Callable[[str, str], Optional[str]],
    ) -> RouteDecision:
        cfg = self.config.load()
        allowlist = cfg.get("allowlist") or []
        fallback = cfg.get("fallback") or {}
        eligible = self.eligible_pairs(allowlist, runtime_available)

        if not eligible:
            return self._fallback_decision(fallback, runtime_available, "no eligible runtimes")

        if len(eligible) == 1:
            only = eligible[0]
            return RouteDecision(
                runtime=only["runtime"], model=only["model"],
                reason="only eligible pair", source="single",
            )

        stickiness_hint = build_stickiness_hint(last_routed, cfg.get("stickiness", {}))
        allowlist_table = build_allowlist_table(eligible)
        truncated_prompt = prompt[:1500]
        try:
            full_prompt = cfg["prompt_template"].format(
                allowlist_table=allowlist_table,
                stickiness_hint=stickiness_hint,
                user_message=truncated_prompt,
            )
        except (KeyError, IndexError):
            return self._fallback_decision(fallback, runtime_available, "invalid prompt_template")

        start = time.time()
        try:
            raw = invoke_brain(
                cfg["brain"]["runtime"], cfg["brain"]["model"], full_prompt,
                cfg.get("timeout_seconds", 30),
            )
        except Exception as exc:  # the brain call must never break the request
            return self._fallback_decision(
                fallback, runtime_available, f"brain error: {type(exc).__name__}",
            )
        latency_ms = int((time.time() - start) * 1000)

        data = parse_decision_json(raw)
        if not data:
            return self._fallback_decision(
                fallback, runtime_available, "unparseable brain reply", latency_ms,
            )

        chosen_runtime = str(data.get("runtime", "")).strip()
        chosen_model_raw = str(data.get("model", "")).strip()
        reason = str(data.get("reason", ""))[:300]

        eligible_by_runtime = {e["runtime"]: e for e in eligible}
        if chosen_runtime not in eligible_by_runtime:
            return self._fallback_decision(
                fallback, runtime_available,
                f"runtime '{chosen_runtime}' not eligible", latency_ms,
            )

        allowed_entry = eligible_by_runtime[chosen_runtime]
        resolved_model = resolve_model(chosen_model_raw, chosen_runtime) or chosen_model_raw
        if resolved_model != allowed_entry["model"] and chosen_model_raw != allowed_entry["model"]:
            # Runtime is eligible but the brain named a model outside that
            # pair's allowlisted model — trust the allowlist, not free text.
            return RouteDecision(
                runtime=chosen_runtime, model=allowed_entry["model"],
                reason=reason or "model normalized to allowlisted pair",
                latency_ms=latency_ms, source="router",
            )

        return RouteDecision(
            runtime=chosen_runtime, model=allowed_entry["model"],
            reason=reason, latency_ms=latency_ms, source="router",
        )

    def _fallback_decision(
        self, fallback: dict, runtime_available: Callable[[str], bool],
        reason: str, latency_ms: int = 0,
    ) -> RouteDecision:
        rt = fallback.get("runtime")
        model = fallback.get("model")
        if rt and model and not self.cooldowns.is_cooling(rt) and runtime_available(rt):
            return RouteDecision(runtime=rt, model=model, reason=reason, latency_ms=latency_ms, source="fallback")
        return RouteDecision(
            runtime="", model="", reason=f"{reason}; fallback also unavailable",
            latency_ms=latency_ms, source="fallback",
        )
