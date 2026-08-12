---
name: chatbi-governance
description: Procedural runbook for triaging drift candidates, invoked by /chatbi-audit-drift. Reads drift_report.json, classifies each candidate via drift.classify_finding, routes to the target maintenance command via DRIFT_ROUTES, escalates unavailable/skipped candidates to human triage, and re-evaluates after a fix. Does not fix, approve, or publish (SEM-003/META-008). Carries reusable procedure, not easily-stale facts.
---

# chatbi-governance

Triage + routing runbook for the candidates produced by `/chatbi-audit-drift`.
The audit command detects accumulated FM-STALE drift and writes
`drift_report.json`; this runbook reads it, routes each candidate to the right
maintenance command, and re-evaluates. This runbook is a **hand-off**: it does
not itself fix, approve, publish, or expand source boundaries (SEM-003/META-008;
SCOPE-001/SEC-001 for source expansion). Every target command carries its own
STOP / human-approval gate.

## 1. Read the report

Read the most recent `drift_report.json` via
`chatbi_harness.harness_state.read_state_with_fallback(workspace_root,
session_id, "drift_report.json")` (`harness_state.py:85`). If absent, STOP and
ask the operator to run `/chatbi-audit-drift` first. The report is untrusted
on-disk state: read the three `classes` arrays
(`stale_reference` / `source_drift` / `model_doc_drift`), `head_shas`,
`status`, and `recovery_actions`.

## 2. Classify each candidate

For every candidate in every class, call
`chatbi_harness.drift.classify_finding(candidate)` -> `RouteDecision`
(deterministic, HOOK-001). The mapping (defined in `drift.DRIFT_ROUTES`):

| kind | status / subtype | route | target |
| --- | --- | --- | --- |
| stale_reference | candidate (sha stale) | C | /chatbi-maintain-knowledge |
| stale_reference | unavailable / skipped | TRIAGE | STOP human triage |
| source_drift | candidate + scope_expansion | B | /chatbi-bootstrap (human approval) |
| source_drift | candidate + shape_change | E | /chatbi-maintain-model |
| source_drift | unavailable | TRIAGE | STOP human triage |
| model_doc_drift | candidate (lint field) | C | /chatbi-maintain-knowledge |
| model_doc_drift | unavailable / skipped | TRIAGE | STOP human triage |

Present each candidate with its `evidence_ref`, `reason`, `recovery`, and the
`RouteDecision`. Distinguish observation (the report says this drifted) from
interpretation.

## 3. Route hand-off (do not auto-fix)

Hand each candidate to its target command. Do not perform the fix here:

- **B -> /chatbi-bootstrap**: a source scope expansion (new table / new column)
  requires human approval of the boundary expansion and incremental
  introspection. This runbook never expands the boundary (SCOPE-001/SEC-001).
- **C -> /chatbi-maintain-knowledge**: a stale reference (cited git_sha drifted
  from HEAD) or a model-doc lint field. Re-author / re-lint the reference and,
  when authoring from a codebase read, capture a fresh `## Citation` git_sha.
- **D -> /chatbi-correction**: a governed artifact that contradicts the source /
  model. `/chatbi-correction` produces a dual candidate with
  `owner_approved=false` (SEM-003); it does not auto-approve.
- **E -> /chatbi-maintain-model**: a source shape change (removed table/column,
  data_type change, PK change) requires a model update.

Each target command has its own STOP / human-approval gate. This runbook records
the hand-off and waits for the target command's outcome; it does not chain past
a STOP.

## 4. Human triage for unavailable / skipped

`unavailable` candidates (no trusted git executable, no live query adapter,
git_metadata error, cited alias not declared) and `skipped` candidates
(reference without a usable `## Citation`) cannot be auto-routed. STOP and
escalate them to a human for adjudication (TRIAGE). Do not guess a fix or
silently drop them (HOOK-004/FBK-003). Surface the `recovery` action for each.

## 5. Regression evaluation after a fix

After a routed fix is applied (by the target command, under its own gates), run
`/chatbi-evaluate` to regress the affected model / reference. Re-run
`/chatbi-audit-drift` to confirm the candidate cleared. FBK-003: a passing
evaluation is evidence the fix held, not a guarantee silent failure is
eliminated.

## 6. Footer

State the number of candidates per class, the routes assigned, any TRIAGE
escalations, and the report `status` (`complete` / `partial`). Reiterate the
`fbk_003_statement`: drift candidates are evidence of accumulated staleness,
not a guarantee silent failure is eliminated; they require human triage before
any fix. No secrets / absolute paths / PII in the summary (SEC-003/PORT-001).

## 7. Applicable governing rules

FM-STALE, DOC-001/002/004, SRC-002, SCOPE-001/002/003, SEC-001/003, PORT-001,
SEM-003, META-008, HOOK-001/004, FBK-003. No new rule is added; the 46-rule
count is unchanged.
## 对话触发指令（agno 运行形态）

对话触发语义 = CC 的 skill 触发：用户以自然话语提问，本 runbook 的
when-to-use 匹配后进入治理流（agent-ui 选择 chatbi-agno 开新会话，原生路由
/agents/chatbi-agno/runs，SSE 流式返回）。直接说（示例）：

> 检查一下语义层和模型注册表有没有漂移。

模型会为 actor/purpose/supported_decision 填入标准默认值；若它追问缺失信息
（时间范围/实体等，REQ-001），按提示回复即可。

🧪 模板待逐字验证
