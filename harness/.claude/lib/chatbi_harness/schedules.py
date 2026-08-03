"""Portable crontab template portability guard for governance scheduling.

Ships a DETERMINISTIC guard (validate_crontab_portability) that enforces the
shipped crontab template contains no machine paths and uses operator-set env vars
(CHATBI_WORKSPACE / CHATBI_INVOKE) rather than hardcoded paths/invocations. It is
a config-layer guard (build + unit tests); it does NOT schedule or execute
anything (FR-2 non-goal: v1 does not assume a resident Agent; harness ships no
scheduler).

Applicable rules (existing, no new rule): PORT-001, HOOK-004.
"""
from __future__ import annotations

import re

from .gates import GateDecision, GateError

# Narrow machine-path prefixes - STRICTER than build-product.sh canary (which only
# catches /Users/[a-z]|/home/[a-z]). Slash-command names like /chatbi-audit-drift
# legitimately start with '/' and MUST NOT be flagged, so we do NOT reuse
# config._ABSOLUTE_PATH (which false-positives on /chatbi-*). /bin/sh is a universal
# path and is intentionally not in the list. [PORT-001]
_MACHINE_PATH = re.compile(r"/(?:Users|home|opt|var|etc|root|private)/[A-Za-z0-9_]")

# A crontab env assignment line, e.g. "SHELL=/bin/sh". Not a command line.
_ENV_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\s*=")

# 5-field cron expression; each field is digits, *, /, comma, hyphen (e.g. */5, 1,2, 1-5).
# Semantic range validation (minute 0-59, etc.) is deferred to the operator's infra.
_CRON_FIELD = r"[\d*/,-]+"
_FIVE_FIELD = re.compile(
    rf"^\s*({_CRON_FIELD})\s+({_CRON_FIELD})\s+({_CRON_FIELD})\s+"
    rf"({_CRON_FIELD})\s+({_CRON_FIELD})\s+(.+)$"
)
_AT_FORM = re.compile(r"^\s*@(?:daily|weekly|hourly|monthly|yearly)\s+(.+)$")

_EVIDENCE_REF = "schedules:chatbi-governance.crontab"


def _gate(rule_ids: tuple[str, ...], reason: str, recovery: str) -> GateError:
    return GateError(
        GateDecision.block(
            rule_ids=rule_ids,
            evidence_refs=(_EVIDENCE_REF,),
            reason=reason,
            recovery=recovery,
        )
    )


def validate_crontab_portability(text: str) -> None:
    """Validate a crontab template is PORT-001-portable and structurally sane.

    Enforces (existing rules PORT-001, HOOK-004; no new rule is added):
      1. No machine-path prefixes anywhere (/Users/, /home/, /opt/, /var/, /etc/,
         /root/, /private/ followed by an identifier char).
      2. Every cron command line references both CHATBI_WORKSPACE and CHATBI_INVOKE
         (operator-set env vars), never a hardcoded path or invocation.
      3. Every command line's cadence is a 5-field cron expression or an
         @daily/@weekly/@hourly/@monthly/@yearly token (structural; semantic range
         validation is the operator infra's job)

    Raises GateError on the first violation. Guards the SHIPPED portable template
    only; an operator's resolved copy (which legitimately contains a real workspace
    path) is out of scope and never reaches this validator.
    """
    if not isinstance(text, str) or not text:
        raise _gate(
            ("PORT-001", "HOOK-004"),
            "crontab template is empty or not a string",
            "Provide a non-empty crontab template string",
        )

    hit = _MACHINE_PATH.search(text)
    if hit is not None:
        raise _gate(
            ("PORT-001", "HOOK-004"),
            f"crontab template contains a machine path: {hit.group(0)!r}",
            "Remove the absolute path; reference $CHATBI_WORKSPACE instead "
            "(operator sets it in their scheduling infrastructure)",
        )

    saw_command_line = False
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _ENV_LINE.match(stripped):
            continue
        m5 = _FIVE_FIELD.match(raw)
        mat = _AT_FORM.match(raw)
        if m5 is None and mat is None:
            continue  # non-cron prose line; machine-path scan above already covered it
        saw_command_line = True
        command = m5.group(6) if m5 else mat.group(1)
        if "CHATBI_WORKSPACE" not in command:
            raise _gate(
                ("PORT-001", "HOOK-004"),
                f"line {lineno}: command does not reference $CHATBI_WORKSPACE "
                f"(hardcoded workspace path violates PORT-001)",
                'Use cd "${CHATBI_WORKSPACE:?}" && ... ; operator sets CHATBI_WORKSPACE',
            )
        if "CHATBI_INVOKE" not in command:
            raise _gate(
                ("PORT-001", "HOOK-004"),
                f"line {lineno}: command does not reference $CHATBI_INVOKE "
                f"(hardcoded invocation assumes a specific infra/Agent)",
                "Use ${CHATBI_INVOKE:?} /chatbi-<cmd> ; operator sets CHATBI_INVOKE "
                "to their infra's slash-command trigger",
            )

    if not saw_command_line:
        raise _gate(
            ("HOOK-004",),
            "crontab template has no active cron command lines",
            "Add at least one 5-field or @-form command line",
        )


__all__ = ["validate_crontab_portability"]
