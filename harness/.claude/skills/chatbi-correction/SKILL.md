---
name: chatbi-correction
description: Structured correction runbook for /chatbi-correction. Builds the dual candidate (fix candidate + eval regression case, FBK-002) via chatbi_correction, keeps owner_approved=False until the human owner approves (SEM-003), validates the record against correction.schema.json, and records the correction evidence. Carries reusable procedure, not easily-stale facts.
---

# chatbi-correction

Runbook for `/chatbi-correction` (structured correction record). Every
accepted correction proposes BOTH a fix candidate (model / knowledge / Skill
repair) AND a regression evaluation case (FBK-002). The record stays
`owner_approved=False` until a human owner approves — the agent drafts, never
approves (SEM-003, META-008).

## 1. Entry and inputs

- Build the record with `chatbi_correction(correction)`.
- The correction payload declares: `correction_id`, `fix_kind` (one of the
  governed fix kinds), `fix_target` (workspace-relative), `fix_change_summary`,
  `eval_case_assertion_id`, `eval_case_expected_hash` (the pinned regression
  expectation), and an optional `description`.
- A missing `correction_id` or an unknown `fix_kind` is denied at the tool
  edge (fail-closed, FBK-002/HOOK-004): correct the payload and re-run.

## 2. Dual candidate (FBK-002)

The kernel `build_correction_record` produces the dual candidate:

- `fix_candidate` — the repair: kind, target, change summary;
- `eval_case_candidate` — the regression case: assertion id + expected hash,
  so the fix is provable against an isolated expectation (FBK-003: a passing
  regression is evidence, not a guarantee silent failure is eliminated);
- `owner_approved` — stays `False` (no auto-merge; SEM-003);
- `rule_ids` — the feedback loop the correction closes (FBK-001/002/003,
  ABL-001).

## 3. Owner approval boundary (SEM-003)

The workflow's `owner_approval` step requires a human owner approval for a
protected action (`owner.pending(approve_metric)`). The agent:

- drafts the record and requests the approval;
- never resolves its own approval (requester == resolver is rejected
  fail-closed);
- waits for the human-owner resolution before the record is considered
  mergeable.

A protected-action correction reaching the delivery gate without a resolved
approval is blocked (the delivery gate is the only terminal authority).

## 4. Validate and record

The kernel `validate_correction` enforces `correction.schema.json` (the
delivery-gate rule set FBK-001/002/003 + ABL-001); the validated record is
recorded as correction evidence. Report the `correction_id`, the dual
candidate summary, `owner_approved=False`, and the closed feedback rules.

## 5. Applicable governing rules

FBK-001, FBK-002, FBK-003, ABL-001, SEM-003, META-008, HOOK-001, HOOK-004,
SEC-003, PORT-001. No new rule is added.
## 对话触发指令（agno 运行形态）

对话触发语义 = CC 的 skill 触发：用户以自然话语提问，本 runbook 的
when-to-use 匹配后进入治理流（agent-ui 选择 chatbi-agno 开新会话，原生路由
/agents/chatbi-agno/runs，SSE 流式返回）。直接说（示例）：

> 登记一个修正：<描述>。

模型会为 actor/purpose/supported_decision 填入标准默认值；若它追问缺失信息
（时间范围/实体等，REQ-001），按提示回复即可。

🧪 模板待逐字验证
