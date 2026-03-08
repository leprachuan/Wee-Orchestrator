"""
Auto-Runtime Configuration and Fallback Logic

Provides intelligent runtime fallback when a runtime hits its limits.
Loads priority list from .env RUNTIME_PRIORITY_LIST variable.
"""

import os
import time
import threading
import sys
from typing import Optional, List, Dict, Tuple


# Default runtime priority order
DEFAULT_PRIORITY_LIST = ["claude", "gemini", "codex", "copilot"]

# Default models per runtime (used when auto-selecting model on fallback)
DEFAULT_MODELS = {
    "claude": "sonnet",
    "gemini": "gemini-2.5-flash",
    "codex": "gpt-5.1-codex-max",
    "copilot": "gpt-5-mini",
    "opencode": "llama-3.3-70b-versatile",
}

# Error patterns that indicate runtime limit/unavailability
LIMIT_ERROR_PATTERNS = {
    "claude": [
        "rate limit",
        "rate_limit",
        "429",
        "quota",
        "too many requests",
        "overloaded",
        "capacity",
        "billing",
        "credit",
        "exceeded",
        "throttl",
        "resource_exhausted",
        # Claude.ai plan/usage limit messages (e.g. daily cap, pro plan limit)
        "usage limit",
        "limit reached",
        "limit has been",
        "reached your",
        "plan limit",
        "maximum requests",
        "out of requests",
        "no more requests",
        "limit for this",
        "limit for the",
        # Claude CLI rate-limit event messages
        "hit your limit",
        "hit the limit",
        "hit a limit",
        "you've hit",
        "you have hit",
        "rate_limit_event",
    ],
    "gemini": [
        "rate limit",
        "rate_limit",
        "429",
        "quota",
        "resource exhausted",
        "too many requests",
        "RESOURCE_EXHAUSTED",
        "capacity",
        "exceeded",
        "throttl",
        "hit your limit",
        "hit the limit",
        "you've hit",
    ],
    "codex": [
        "rate limit",
        "rate_limit",
        "429",
        "quota",
        "model not available",
        "too many requests",
        "insufficient_quota",
        "exceeded",
        "throttl",
        "capacity",
        "hit your limit",
        "hit the limit",
        "you've hit",
    ],
    "copilot": [
        "rate limit",
        "rate_limit",
        "429",
        "session error",
        "too many requests",
        "quota",
        "exceeded",
        "throttl",
        "hit your limit",
        "hit the limit",
        "you've hit",
    ],
    "opencode": [
        "rate limit",
        "rate_limit",
        "429",
        "quota",
        "too many requests",
        "exceeded",
        "throttl",
        "hit your limit",
        "hit the limit",
        "you've hit",
    ],
}

# Patterns that indicate the runtime binary/CLI is missing or broken
RUNTIME_UNAVAILABLE_PATTERNS = [
    "executable not found",
    "command not found",
    "not installed",
    "No such file or directory",
    "Permission denied",
]


class AutoRuntimeConfig:
    """Manages auto-runtime selection with priority-based fallback."""

    # Cooldown period (seconds) before retrying a runtime that hit its limit
    COOLDOWN_SECONDS = 300  # 5 minutes

    def __init__(self):
        self._lock = threading.Lock()
        # Track when each runtime last hit a limit: {runtime: timestamp}
        self._limit_timestamps: Dict[str, float] = {}
        # Track fallback events for logging
        self._fallback_log: List[Dict] = []
        # Load configuration
        self.priority_list = self._load_priority_list()
        self.show_fallback = self._load_show_fallback()
        self.auto_select_model = self._load_auto_select_model()

    def _load_priority_list(self) -> List[str]:
        """Load runtime priority list from environment."""
        env_val = os.environ.get("RUNTIME_PRIORITY_LIST", "").strip()
        if env_val:
            runtimes = [r.strip().lower() for r in env_val.split(",") if r.strip()]
            valid = {"claude", "gemini", "codex", "copilot", "opencode"}
            filtered = [r for r in runtimes if r in valid]
            if filtered:
                return filtered
        return list(DEFAULT_PRIORITY_LIST)

    def _load_show_fallback(self) -> bool:
        """Load whether to show fallback messages to user."""
        return os.environ.get("AUTO_RUNTIME_SHOW_FALLBACK", "true").lower() in ("true", "1", "yes")

    def _load_auto_select_model(self) -> bool:
        """Load whether to auto-select model on fallback."""
        return os.environ.get("AUTO_RUNTIME_AUTO_SELECT_MODEL", "false").lower() in ("true", "1", "yes")

    def reload_config(self):
        """Reload configuration from environment (call after .env changes)."""
        self.priority_list = self._load_priority_list()
        self.show_fallback = self._load_show_fallback()
        self.auto_select_model = self._load_auto_select_model()

    def is_limit_error(self, output: str, runtime: str, exit_code: int = 0) -> bool:
        """Check if the output indicates the runtime hit a limit.
        
        Args:
            output: The runtime output/stderr to check
            runtime: The runtime name (claude, gemini, codex, copilot, opencode)
            exit_code: The process exit code (0 = success, non-zero = error)
        
        Returns:
            True if output indicates a rate limit or resource exhaustion error.
            
        NOTE: Claude and other runtimes can hit rate limits while returning exit code 0.
        We check the output content. To reduce false positives from AI responses
        legitimately discussing rate limiting:
        - Non-zero exit codes: treat any limit pattern as a real error
        - Exit code 0: require error/failure context or very strong patterns
        """
        if not output:
            return False
        output_lower = output.lower()

        # Check runtime-unavailable patterns first (always indicate problems)
        for pattern in RUNTIME_UNAVAILABLE_PATTERNS:
            if pattern.lower() in output_lower:
                return True

        # Check runtime-specific limit patterns
        patterns = LIMIT_ERROR_PATTERNS.get(runtime, [])
        for pattern in patterns:
            pattern_lower = pattern.lower()
            if pattern_lower in output_lower:
                # If exit code is non-zero (real error), this is definitely a limit error
                if exit_code != 0:
                    return True
                
                # If exit code is 0 (success), apply stronger signal detection
                # to avoid false positives from AI responses discussing limits.
                
                # Patterns that are ALWAYS strong indicators (even at exit 0)
                # These appear at the start of error messages, not in discussion
                very_strong_patterns = [
                    "resource_exhausted",
                    "resource exhausted",
                    "out of requests",
                    "no more requests",
                    "usage limit",
                    "limit reached",
                    "limit has been",
                    "reached your",
                    "plan limit",
                    "maximum requests",
                    "rate_limit_error",  # JSON error type
                    "rate_limit_event",  # Claude CLI rate limit event
                    "hit your limit",
                    "hit the limit",
                    "hit a limit",
                    "you've hit",
                    "you have hit",
                ]
                if any(p.lower() in pattern_lower for p in very_strong_patterns):
                    return True
                
                # For other patterns, check for error/failure context
                error_indicators = [
                    "error:",
                    "error -",
                    "failed:",
                    "cannot ",
                    "unable to ",
                    "exceeds",
                    "exceeded",
                ]
                has_error_context = any(indicator in output_lower for indicator in error_indicators)
                
                if has_error_context:
                    return True

        return False

    def mark_runtime_limited(self, runtime: str):
        """Record that a runtime hit its limit."""
        with self._lock:
            self._limit_timestamps[runtime] = time.time()
            print(
                f"[AutoRuntime] Marked '{runtime}' as limited (cooldown {self.COOLDOWN_SECONDS}s)",
                file=sys.stderr,
            )

    def is_runtime_available(self, runtime: str) -> bool:
        """Check if a runtime is available (not in cooldown)."""
        with self._lock:
            ts = self._limit_timestamps.get(runtime)
            if ts is None:
                return True
            elapsed = time.time() - ts
            if elapsed >= self.COOLDOWN_SECONDS:
                # Cooldown expired, remove the limit marker
                del self._limit_timestamps[runtime]
                print(
                    f"[AutoRuntime] Cooldown expired for '{runtime}', now available",
                    file=sys.stderr,
                )
                return True
            return False

    def get_next_runtime(self, failed_runtime: str) -> Optional[str]:
        """Get the next available runtime after the failed one."""
        try:
            idx = self.priority_list.index(failed_runtime)
        except ValueError:
            idx = -1

        # Try runtimes after the failed one in priority order
        for i in range(idx + 1, len(self.priority_list)):
            candidate = self.priority_list[i]
            if self.is_runtime_available(candidate):
                return candidate

        # Also try runtimes before it that might be available (wrap-around)
        for i in range(0, idx):
            candidate = self.priority_list[i]
            if candidate != failed_runtime and self.is_runtime_available(candidate):
                return candidate

        return None

    def get_first_available_runtime(self) -> str:
        """Get the first available runtime from the priority list."""
        for runtime in self.priority_list:
            if self.is_runtime_available(runtime):
                return runtime
        # All in cooldown — return the first one anyway (best effort)
        return self.priority_list[0]

    def get_default_model(self, runtime: str) -> str:
        """Get the default model for a runtime."""
        return DEFAULT_MODELS.get(runtime, "gpt-5-mini")

    def log_fallback(self, from_runtime: str, to_runtime: str, reason: str):
        """Log a fallback event."""
        event = {
            "timestamp": time.time(),
            "from_runtime": from_runtime,
            "to_runtime": to_runtime,
            "reason": reason,
        }
        with self._lock:
            self._fallback_log.append(event)
            # Keep last 100 events
            if len(self._fallback_log) > 100:
                self._fallback_log = self._fallback_log[-100:]
        print(
            f"[AutoRuntime] Fallback: {from_runtime} → {to_runtime} (reason: {reason})",
            file=sys.stderr,
        )

    def get_fallback_log(self, limit: int = 20) -> List[Dict]:
        """Return recent fallback events."""
        with self._lock:
            return list(self._fallback_log[-limit:])

    def build_fallback_message(self, from_runtime: str, to_runtime: str) -> str:
        """Build user-facing fallback notification message."""
        if not self.show_fallback:
            return ""
        return (
            f"⚠️ **{from_runtime}** hit its limit. "
            f"Falling back to **{to_runtime}**."
        )

    def get_status(self) -> Dict:
        """Return current auto-runtime status for diagnostics."""
        with self._lock:
            limited = {}
            now = time.time()
            for rt, ts in self._limit_timestamps.items():
                remaining = max(0, self.COOLDOWN_SECONDS - (now - ts))
                limited[rt] = {
                    "limited_at": ts,
                    "cooldown_remaining_seconds": int(remaining),
                }
            return {
                "priority_list": self.priority_list,
                "show_fallback": self.show_fallback,
                "auto_select_model": self.auto_select_model,
                "limited_runtimes": limited,
                "recent_fallbacks": len(self._fallback_log),
            }
