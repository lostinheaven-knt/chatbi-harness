---
name: chatbi-init
description: Installation readiness diagnostic runbook for /chatbi-init. Runs the nine-check diagnostic on the run's workspace (shared config, domain contract, config validation, path resolution, capability probe, checks assembly) via chatbi_init_diagnostic, reports the machine-readable readiness result with production_ready semantics and recovery actions, and honestly reports the runtime capability snapshot (claude_available=False on the agno target). Carries reusable procedure, not easily-stale facts.
---

# chatbi-init

Diagnostic runbook for `/chatbi-init` (installation readiness). It produces a
machine-readable readiness report: the nine checks, a capability snapshot,
`production_ready` semantics, and concrete `recovery_actions`. It NEVER
reports a capability as available when no technical gate exists (FBK-003
honest reporting) and never exposes credentials or machine paths
(SEC-003/PORT-001).

## 1. Entry and inputs

- Run the init diagnostic with `chatbi_init_diagnostic(request)`.
- The request must carry a workspace-relative `shared_config` path (e.g.
  `.claude/chatbi-harness.json`); an `local_config` path is optional.
- A missing shared config is denied at the tool edge (fail-closed): provide
  the workspace-relative path and re-run.

## 2. What the diagnostic checks

The kernel chain (`run_init_diagnostic`) assembles the nine checks in order:

1. domain contract — the workspace carries the governed domain model;
2. shared config — readable, schema-valid effective configuration;
3. local config — confirmed local bindings only (no absolute paths);
4. config validation — effective config loads with policy-decidable values;
5. path resolution — workspace-relative references resolve inside the
   workspace;
6. capability probe — the runtime capability snapshot (see §3);
7. runtime checks — the runtime's own readiness signals;
8. evidence store — the run's `.chatbi` evidence root is writable;
9. production readiness — the aggregate `production_ready` verdict.

Each check records pass/warn/blocked. Any blocked check keeps
`production_ready=False` — the report is NOT a production-readiness claim.

## 3. Capability snapshot (honest reporting)

The runtime injects its capability snapshot; on this agno target the honest
projection is:

- `claude_available=False` — this runtime is not Claude Code;
- `doctor_status` reflects the agno runtime state (`pass` when the runtime
  is importable, `unavailable` otherwise);
- `sandbox_available` mirrors the runtime state;
- `available_adapters` reports only what the runtime actually exercises
  (fixture/example evidence is test evidence, never a silent production
  fallback — FBK-003).

Do not claim Claude-specific capabilities that this runtime does not carry.

## 4. Report the result

Report the diagnostic outcome with:

- the aggregate `status` (PASS / WARN / BLOCKED);
- `production_ready` (stays False unless every gate is clean — never a
  fabrication);
- every blocked check with its `recovery_action` (the kernel's concrete
  recovery list);
- the capability snapshot summary.

No secrets, no absolute paths, no PII in the report (SEC-003/PORT-001):
reference configs by workspace-relative path and hashes only.

## 5. After the report

A BLOCKED status ends the run: hand the blocked checks and their recovery
actions to the user and wait for instructions — do not silently proceed to
bootstrap or analysis. A PASS status is the entry gate for subsequent
workflows; the init run itself ends at the report.

## 6. Applicable governing rules

REQ-001, PORT-001, SEC-003, SEM-003, HOOK-001, HOOK-004, FBK-003,
CAP-001/002. No new rule is added.

## 对话触发指令（agno 运行形态）

本工作流在 agno runtime 下通过对话触发：agent-ui 选择 chatbi-agno 开新会话（原生路由 /agents/chatbi-agno/runs，SSE 流式返回），输入：

> 执行 chatbi-init 工作流：诊断当前数仓能力与生产就绪状态。

🧪 模板待逐字验证
