---
name: chatbi-knowledge
description: Procedural runbook for authoring and maintaining governed knowledge references co-located with the Warehouse model. Enforces required metadata, "use for"/"do not use for" triggers, no machine absolute paths, historical-SQL-as-candidate-only, and cross-references (DOC-001/002/003/005). Carries reusable procedure, not easily-stale facts.
---

# chatbi-knowledge

Authoring + maintenance runbook for governed knowledge references. A reference
must be route-ready before it can be retrieved by `/chatbi-analyze` or updated by
`/chatbi-maintain-knowledge`.

## 1. Bind to the template

Start from `references/_template.md`. Every section is required (DOC-002/003).
Do not delete a section because you have nothing to say - state "not applicable"
with the reason.

## 2. Required fields (DOC-002)

Business context, Grain, Standard filters, Dimensions, Key models, Scope and
exclusions, Joins, Common pitfalls, Best practices, Cross-references, Owner,
Freshness, Use for, Do not use for.

## 3. "Use for" / "Do not use for" (DOC-003)

State explicit trigger conditions, not step-by-step recipes that go stale. "Use
this reference when ..."; "Do NOT use this reference when ...". Both must be
non-empty.

## 4. Paths and references (PORT-001, DOC-001)

Use logical aliases and relative references only. Never machine absolute paths
(`/Users/...`, `/home/...`). Cross-references must list at least one neighbor
reference (`.md`/`.sql`/`.json`).

## 5. Historical SQL (RAW-001/002, SRC-001/002)

Historical SQL / notebooks / dashboard queries are candidate clues only, never
canonical definitions. Any SQL block must carry the `candidate_only` marker.

## 6. Capture `## Citation` when authoring from a codebase read (OD1)

When a reference is authored or updated from a Business Codebase read (via
`select_codebase_reader` + `CodebaseReader.read`/`git_metadata`), capture a
`## Citation` section from the resulting `CodebaseEvidence.portable_reference`
(`alias` / `relative_path` / `revision` / `revision_kind`). Write `alias`,
`relative_path`, and `git_sha` (the `revision`) into the optional `## Citation`
section. Do not hand-edit the `git_sha` - it is machine evidence. Omit the
section when the reference is not codebase-derived. `/chatbi-audit-drift`
compares the cited `git_sha` against the codebase alias's current HEAD to detect
accumulated reference staleness (FM-STALE); a missing `## Citation` is skipped
(not an error) until back-filled here.

## 7. Lint before publish

Run `chatbi_harness.knowledge.lint_reference(text)`. An empty issue tuple means
route-ready. Every issue must be resolved (fail-closed); never publish a
reference with open issues. Conflicts between a reference and governed facts go
to the domain owner (SRC-002).

## 8. Maintenance and pruning (DOC-005)

When a model improves, prune obsolete scaffolding and negative-value references
rather than lengthening prompts to paper over failure. Remove a reference only
when evidence supports doing so; record the removal as an atomic change.
## 对话触发指令（agno 运行形态）

对话触发语义 = CC 的 skill 触发：用户以自然话语提问，本 runbook 的
when-to-use 匹配后进入治理流（agent-ui 选择 chatbi-agno 开新会话，原生路由
/agents/chatbi-agno/runs，SSE 流式返回）。直接说（示例）：

> 帮我登记一下收入指标的语义文档。

模型会为 actor/purpose/supported_decision 填入标准默认值；若它追问缺失信息
（时间范围/实体等，REQ-001），按提示回复即可。

🧪 模板待逐字验证
