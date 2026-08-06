"""Conformance equivalence judgment: Agno target vs the Golden Contract
(module 5, MR-D4).

Implements the impl-doc §9.4 comparison keys — the SAME semantics the
Claude-target golden chain pins:

- ``final_status`` — equality (completed | blocked | stopped | failed | paused);
- ``gate_conclusion`` — pass/block per target, with ``block_rule_ids``
  (the rule_ids of any blocking decision) compared as a set. PASS gates
  carry no rule_ids in the golden (the hook exits 0 without a decision), so
  rule_ids are only compared when the gate blocked;
- ``candidate_sha`` — exact equality (REV-001 binding);
- ``evidence_chain`` — the (source_tier, content_sha256) sequence, exact;
- ``review.status`` + ``review.candidate_sha`` — equality;
- ``approval.resolution`` — equality (absent on both sides = OK).

NOT compared: tokens, prompt text, native event names, timestamps, footer
prose, adapter evidence shapes (design §14: "比较标准事件、Evidence 和最终
治理结论").

Any P0 scenario difference makes the caller (``test-conformance --target
agno``) exit non-zero and writes ``conformance-report.json``.

Applicable rules: HOOK-001, ADR-002, MR-010, FBK-003.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

SCHEMA_VERSION = "chatbi.conformance-compare/v1"


def _gate_conclusion(decisions: Any) -> tuple[str, set[str]]:
    """Normalize a target's gate_decisions into (conclusion, block_rule_ids).

    Expected (golden) entries look like
    ``{"gate": "subagent_review_gate", "exit": 0|2, "decision": {...}|None}``
    — exit != 0 or a decision with status block => blocked. Agno entries look
    like ``{"gate": "delivery_gate", "decision": {status, rule_ids, ...}}``.
    """
    if not isinstance(decisions, list):
        return "pass", set()
    blocked = False
    block_rule_ids: set[str] = set()
    for item in decisions:
        if not isinstance(item, Mapping):
            continue
        decision = item.get("decision")
        if isinstance(decision, Mapping) and decision.get("status") == "block":
            blocked = True
            rule_ids = decision.get("rule_ids") or ()
            block_rule_ids.update(str(r) for r in rule_ids)
        elif isinstance(decision, Mapping) and decision.get("status") == "pass":
            continue
        elif item.get("exit") not in (None, 0):
            # Hook-style exit code: non-zero = blocked.
            blocked = True
            decision = item.get("decision")
            if isinstance(decision, Mapping):
                block_rule_ids.update(str(r) for r in (decision.get("rule_ids") or ()))
    return ("block" if blocked else "pass"), block_rule_ids


def _evidence_chain(evidence: Any) -> list[tuple[str, str]]:
    """(source_tier, content_sha256) sequence from either target's chain."""
    if not isinstance(evidence, list):
        return []
    pairs = []
    for entry in evidence:
        if not isinstance(entry, Mapping):
            continue
        source_tier = entry.get("source_tier")
        content_sha256 = entry.get("content_sha256")
        if isinstance(source_tier, str) and isinstance(content_sha256, str):
            pairs.append((source_tier, content_sha256))
    return pairs


def _normalize(result: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the comparison keys from a scenario result dict."""
    gate_conclusion, block_rule_ids = _gate_conclusion(result.get("gate_decisions"))
    return {
        "final_status": result.get("final_status"),
        "gate_conclusion": gate_conclusion,
        "block_rule_ids": sorted(block_rule_ids),
        "candidate_sha": result.get("candidate_sha"),
        "evidence_chain": _evidence_chain(result.get("evidence_chain")),
        "review_status": (result.get("review") or {}).get("status")
        if isinstance(result.get("review"), Mapping) else None,
        "review_candidate_sha": (result.get("review") or {}).get("candidate_sha")
        if isinstance(result.get("review"), Mapping) else None,
        "approval_resolution": (
            (result.get("approval") or {}).get("resolution")
            if isinstance(result.get("approval"), Mapping)
            else (
                (result.get("final_approval_state") or {}).get("resolution")
                or (result.get("final_approval_state") or {}).get("status")
            )
            if isinstance(result.get("final_approval_state"), Mapping)
            else None
        ),
    }


def compare_scenario(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> list[str]:
    """Return difference strings between one expected and one actual result
    ([] = equivalent). A key absent on BOTH sides is equivalent; absent on
    one side only is a difference (fail-closed)."""
    diffs: list[str] = []
    norm_expected = _normalize(expected)
    norm_actual = _normalize(actual)
    for key in sorted(norm_expected.keys()):
        exp_value = norm_expected[key]
        act_value = norm_actual[key]
        exp_present = exp_value not in (None, "", [], {})
        act_present = act_value not in (None, "", [], {})
        if not exp_present and not act_present:
            continue
        if exp_value != act_value:
            diffs.append(
                f"{key}: expected {exp_value!r}, agno produced {act_value!r}"
            )
    return diffs


def compare_all(
    expected: Mapping[str, Any],
    actual: Mapping[str, Any],
    scenario_ids: list[str],
) -> dict[str, Any]:
    """Compare every requested scenario; build the conformance report."""
    results: dict[str, Any] = {}
    diffs: list[str] = []
    for scenario_id in scenario_ids:
        exp = expected.get(scenario_id)
        act = actual.get(scenario_id)
        if exp is None:
            diffs.append(f"{scenario_id}: missing expected golden output")
            results[scenario_id] = "fail"
            continue
        if act is None:
            diffs.append(f"{scenario_id}: agno runner produced no output")
            results[scenario_id] = "fail"
            continue
        scenario_diffs = compare_scenario(exp, act)
        if scenario_diffs:
            results[scenario_id] = "fail"
            diffs.append(f"{scenario_id}: NOT EQUIVALENT ({len(scenario_diffs)} diff(s))")
            diffs.extend(f"  {scenario_id}: {d}" for d in scenario_diffs)
        else:
            results[scenario_id] = "pass"
    return {
        "schema_version": SCHEMA_VERSION,
        "target": "agno",
        "status": "pass" if not diffs else "fail",
        "scenarios": results,
        "diffs": diffs,
    }


def write_report(report: Mapping[str, Any], path: Any) -> None:
    from pathlib import Path

    Path(path).write_text(
        json.dumps(dict(report), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n",
        encoding="utf-8",
    )
