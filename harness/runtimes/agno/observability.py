"""Rate limiting, monitoring hook points and log sanitization for the Agno
target (module 6, MR-E3 — production-hardening hook points, design §6
non-functional requirements).

This module provides the ADAPTER-LEVEL hook points only (per modification
§8: "限流与监控接入点" — no full monitoring product is required):

- :class:`SlidingWindowRateLimiter` — a deterministic per-key sliding-window
  limiter (product layer, fail-closed: ``allow()`` returns False once the
  window is exhausted; the router answers 429);
- :class:`MonitoringHooks` — injectable callable set
  (``on_run_started`` / ``on_run_terminal`` / ``on_error``) the controller
  invokes at lifecycle points; a deployment wires its own sink, the default
  is a no-op :class:`NullHooks` (honest: nothing is collected unless wired);
- :func:`sanitize_log_line` — log/trace sanitization: kernel
  ``_sanitize_text`` (secret/PII patterns, SEC-003) plus an explicit
  key-pattern scrub (``sk-...``, ``Bearer ...``) so traces never leak
  credentials (design §17 row 10: "Trace 泄漏敏感字段 -> Kernel sanitizer +
  trace filter").

No business rule lives here (invariant 2); the limiter/monitoring are
infrastructure seams, not governance decisions. All defaults are fail-closed
or no-op — never a silent degradation.

Applicable rules: SEC-003, PORT-001, MR-005, HOOK-001, invariant 5,
design §17 row 10.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from chatbi_governance.gates import _sanitize_text

#: Secret-shaped token scrub for log/trace lines (SEC-003).
_SECRET_TOKEN = re.compile(r"(sk-[A-Za-z0-9_\-]{12,}|Bearer\s+[A-Za-z0-9._\-]{12,})")


def sanitize_log_line(text: str) -> str:
    """Sanitize one log/trace line: kernel sanitizer + secret-token scrub.

    Applied to every payload/log line the adapter surfaces; a canary secret
    must never survive into events, evidence, traces or SSE payloads.
    """
    if not isinstance(text, str):
        return text
    cleaned = _sanitize_text(text)
    return _SECRET_TOKEN.sub("[REDACTED]", cleaned)


@dataclass(frozen=True)
class RateLimitPolicy:
    """Rate-limit policy (from deployment config; disabled by default)."""

    enabled: bool = False
    max_requests: int = 100
    window_seconds: int = 60
    #: Key source: "actor" (run actor) or "subject" (authenticated subject).
    key_source: str = "actor"

    @classmethod
    def from_config(cls, data: Mapping[str, Any] | None) -> "RateLimitPolicy":
        if not isinstance(data, Mapping):
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            max_requests=int(data.get("max_requests", 100)),
            window_seconds=int(data.get("window_seconds", 60)),
            key_source=str(data.get("key_source", "actor")),
        )


class SlidingWindowRateLimiter:
    """Per-key sliding-window limiter (monotonic clock, no wall-clock drift).

    ``allow(key)`` returns True while the request count inside the current
    window is below ``max_requests``. Deterministic per policy; a broken
    policy (max_requests <= 0) is fail-closed (never allows).
    """

    def __init__(self, policy: RateLimitPolicy, clock: Any = None) -> None:
        self.policy = policy
        self._clock = clock or time.time
        self._hits: dict[str, list[float]] = {}
        self._lock_owner = "single-process"  # MVP single instance (裁决 10)

    def allow(self, key: str) -> bool:
        if not self.policy.enabled:
            return True
        if not isinstance(key, str) or not key:
            return False  # no key -> fail-closed
        if self.policy.max_requests <= 0 or self.policy.window_seconds <= 0:
            return False
        now = self._clock()
        window_start = now - self.policy.window_seconds
        hits = [t for t in self._hits.get(key, []) if t > window_start]
        if len(hits) >= self.policy.max_requests:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True

    def remaining(self, key: str) -> int:
        if not self.policy.enabled:
            return -1
        now = self._clock()
        window_start = now - self.policy.window_seconds
        hits = [t for t in self._hits.get(key, []) if t > window_start]
        return max(0, self.policy.max_requests - len(hits))


class MonitoringHooks:
    """Injection points for a deployment monitoring sink.

    Every hook is optional; the default NullHooks records nothing (honest
    FBK-003: no telemetry is claimed unless the deployer wires a sink).
    """

    def __init__(
        self,
        on_run_started: Callable[[str, str, str], None] | None = None,
        on_run_terminal: Callable[[str, str, str], None] | None = None,
        on_error: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self.on_run_started = on_run_started or (lambda run_id, workflow_id, actor: None)
        self.on_run_terminal = on_run_terminal or (
            lambda run_id, workflow_id, final_status: None
        )
        self.on_error = on_error or (
            lambda run_id, workflow_id, message: None
        )

    @classmethod
    def null(cls) -> "MonitoringHooks":
        return cls()

    def run_started(self, run_id: str, workflow_id: str, actor: str) -> None:
        self.on_run_started(run_id, workflow_id, sanitize_log_line(actor))

    def run_terminal(self, run_id: str, workflow_id: str, final_status: str) -> None:
        self.on_run_terminal(run_id, workflow_id, sanitize_log_line(final_status))

    def error(self, run_id: str, workflow_id: str, message: str) -> None:
        self.on_error(run_id, workflow_id, sanitize_log_line(message))


#: Sentinel used by the controller when no monitoring sink is wired.
NullHooks = MonitoringHooks.null
