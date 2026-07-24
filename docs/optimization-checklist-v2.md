# Cycle 1 文档差异评估清单 v2

STATUS: CONVERGED

评估日期：2026-07-22
评估轮次：第 2 轮（最多 5 轮）
评估依据：
- 期望 = `docs/technical-design.md`（PROPOSED 设计，§15 Cycle 1 范围）
- 事实 = `docs/feature-flow-v2.md`（CODE_AS_READ on 2026-07-22，从代码读出）
- 范围 = `docs/dev-cycle-1.md` §5 文件所有权 + §10 周期完成清单
- 上一轮差异 = `docs/optimization-checklist-v1.md`（P1×1 + P2×2，STATUS: NEEDS_ITERATION）
- 旁证 = `docs/test-report-v1.md`（已订正文件计数 45）、`docs/harness/rule-traceability.md`（已订正汇总算术 46 + SRC-001 单周期）

## 评估方法

本轮聚焦两件事：

1. 逐条复核上一轮 P1/P2 是否真正解决（RESOLVED / 仍存在）；
2. 对 feature-flow-v2 做 design-vs-feature-flow diff + dev-cycle-1 §10 一致性复查，确认无新偏离。

### 关键判读（避免误报，与 v1 一致）

以下已确认为 **有意推迟或设计预期**，不作为本周期 P0/P1：

- Cycle 2–5 推迟能力（持续 TOCTOU、真实 sandbox、managed/CLI adapter、外部 Codebase 实际读取、PreToolUse 写/执行阻断、`/chatbi-analyze`/`maintain-model`/`maintain-knowledge`/`evaluate`/`correction` 命令、真实 Claude SessionStart Hook E2E）--feature-flow-v2 §9 gap 1–9 已列为已知差距，属 planned Cycle N。
- `production_ready` 硬编码 `False`（`diagnostics.py:336-339`）--Cycle 1 设计预期，非 bug。
- 文件 inventory 的 45 = 40 Cycle 1 交付物 + 5 编排工具文件（`.claude/agents/*`×3、`.claude/commands/orchestrate.md`、`.claude/SKILL-PATHS.md`）；工具文件非 Cycle 1 交付物但被 `find .claude` 范围计入。feature-flow-v2(45) == test-report-v1(45) == ground-truth(45)，§10「inventory 一致」满足。
- 唯一 secret-scan 命中 `TEST_SECRET_CANARY_NOT_A_CREDENTIAL`--负测试 fixture，非泄漏。

---

## 上一轮 P1/P2 复核

### P1-1 复核：feature-flow-v1 §9 gap 10 与 §10.3 错误声称 README 缺失 + inventory 42 vs 43 不一致 -- RESOLVED

上一轮指出三个失准点：(1) §9 gap 10 称 README 缺失；(2) §10.3 harness docs 计数 3、文件总数 42；(3) §0 source-file 列表缺 README。要求修正后使 feature-flow == test-report == ground-truth。

逐点核验 feature-flow-v2：

| 失准点 | 修正前（v1） | 修正后（v2） | 核验结论 |
| --- | --- | --- | --- |
| §9 gap 10 | "README.md not present… absent from inventory" | "README.md delivered (no longer a gap). Present (211 lines, substantive content…)"（feature-flow-v2 第 658–664 行） | RESOLVED：README 存在性已如实反映 |
| §10.3 harness docs 计数 | "3 harness docs" | "5 harness docs (README, compatibility, configuration, installation, rule-traceability)"（第 712–714 行） | RESOLVED：5 文件枚举完整 |
| §10.3 文件总数 | 42 | "45 files listed… 40 deliverables + 5 workspace orchestration tooling"（第 708–718 行） | RESOLVED：45 且附明细 |
| §0 source-file 列表 | 不含 README | 含 "docs/harness/README.md (Cycle 1 harness documentation: entry point, scope, document map)"（第 45–46 行） | RESOLVED |

三源一致性核验：

| 来源 | 文件计数 | 状态 |
| --- | --- | --- |
| feature-flow-v2 §10.3 | 45（40 交付物 + 5 工具） | 一致 |
| test-report-v1 SC-008 / 命令 3 | 45（原 43 已订正，附说明） | 一致 |
| ground-truth（find 实际输出） | 45 | 一致 |

**结论：P1-1 RESOLVED。** 三源一致，事实文档不再误报 README 缺失。

### P2-1 复核：rule-traceability.md 汇总表算术不一致（合计 43≠46） -- RESOLVED

上一轮指出汇总表 5 个单元格计数偏差（PARTIAL 5→4、Cycle 2 2→1、Cycle 3 13→18、Cycle 5 8→9、合计 43≠46），逐条行正确但汇总表算术错误。

核验 rule-traceability.md 第 352–369 行订正后的汇总表：

| 状态 | 汇总表计数 | 逐条行验证 | 是否一致 |
| --- | --- | --- | --- |
| IMPLEMENTED (Cycle 1) | 10 | SCOPE-001, SCOPE-003, SEC-003, SEM-003, PORT-001, EVAL-004, FBK-003, HOOK-001, HOOK-002, HOOK-004 = 10 | ✓ |
| PARTIAL (Cycle 1) | 4 | SCOPE-002, SEC-001, SEC-002, HOOK-003 = 4 | ✓ |
| PLANNED: Cycle 2 | 1 | DOC-004 = 1 | ✓ |
| PLANNED: Cycle 3 | 18 | REQ-001..004(4) + SEM-001/002(2) + RAW-001..003(3) + SRC-001/002(2) + QLT-001(1) + REV-001..003(3) + ANS-001..003(3) = 18 | ✓ |
| PLANNED: Cycle 4 | 4 | DOC-001/002/003/005 = 4 | ✓ |
| PLANNED: Cycle 5 | 9 | EVAL-001/002/003/005(4) + ABL-001/002(2) + FBK-001/002(2) + HOOK-005(1) = 9 | ✓ |
| **合计** | **46** | 10+4+1+18+4+9 = 46 | ✓ |

逐条行总数独立核验（Grep `^### [A-Z]+-\d{3}` 共 46 条独立条目）：SCOPE×3 + SEC×3 + REQ×4 + SEM×3 + RAW×3 + SRC×2 + DOC×5 + PORT×1 + QLT×1 + REV×3 + ANS×3 + EVAL×5 + ABL×2 + FBK×3 + HOOK×5 = 46。

汇总表第 365–368 行附注说明：「continuous SCOPE-001 enforcement」不再作为独立 Cycle 2 条目（属已 IMPLEMENTED 的 SCOPE-001 的 Cycle 2 子方面），消除了 v1 中将非规则条目计入汇总的根因。

**结论：P2-1 RESOLVED。** 汇总表算术订正为 46，与逐条行一致。

### P2-2 复核：SRC-001 计划周期标注歧义（「Cycle 3/4」双周期） -- RESOLVED

上一轮指出 SRC-001 状态为 `PLANNED: Cycle 3/4`，与其余 45 条单周期标注不一致。

核验 rule-traceability.md 第 146–154 行：

```
- Status: **PLANNED: Cycle 3**. Historical-SQL-as-clue is enforced on the
  analysis path (Cycle 3); the underlying historical SQL/notebook artifacts may
  also be curated during Cycle 4 knowledge maintenance, but the rule's
  enforcement point (treating them as candidate clues, not correctness proof)
  is the analysis path.
```

SRC-001 现为单一主归属周期 `PLANNED: Cycle 3`，附注说明 Cycle 4 的关系（底层 artifacts 可能在知识维护中被 curate，但规则执行点是分析路径）。汇总表第 357 行将 SRC-001 列入 Cycle 3 的 18 条，第 362–364 行附注解释归属理由。

**结论：P2-2 RESOLVED。** SRC-001 不再双周期标注，归属单一且可追溯。

---

## 本轮 design-vs-feature-flow(v2) diff

上一轮 P1/P2 全部 RESOLVED 后，对 feature-flow-v2 做 Cycle 1 范围内的 design-vs-fact diff，确认无新偏离。

### 1. 根契约（设计 §6.1/§9.1 vs feature-flow §1）

- 期望：CLAUDE.md ~200 行，路由 6 命令，Cycle 1 仅 init 已安装；领域模型为硬前置；5 契约 artifact 规则 ID 覆盖 46 条。
- 实际：feature-flow §1 确认 `CLAUDE.md:4-7` 领域模型前置、`CLAUDE.md:17-19` 加载 CONTEXT.md + rules、`gates.py:170-233 validate_domain_contract` 强制契约形状；§10.6 确认 CLAUDE.md 112 行（预算内）；§10.5 确认 46 规则 ID EXACT MATCH。
- 偏离：无。

### 2. 配置（设计 §7 vs feature-flow §4）

- 期望：shared/local 分层、Schema 子集、跨字段校验、不可变 EffectiveConfig、无密钥/机器路径。
- 实际：feature-flow §4 确认 `config.py:385-388 load_effective_config`、schema 子集（schema:6-18 `x-implemented-keywords`）、`_freeze` 递归冻结、shared 拒绝 path_bindings/cli_adapters、local 仅允许此两键、secret/absolute-path 扫描。
- 偏离：无。

### 3. 路径（设计 §7.3 vs feature-flow §5）

- 期望：realpath 组件级包含判断、symlink 拒绝、可移植引用 `{alias, relative_path, revision, revision_kind}`、无 Git 用 content_sha256、TOCTOU 边界标注为 Cycle 2。
- 实际：feature-flow §5 确认 `paths.py:355-360`、组件比较（非字符串前缀）、`_reject_symlink` 每组件检查、`PortablePathReference` 无绝对根、`paths.py:1-5` 明确 Cycle 1 为 point-in-time、Cycle 2 持续重验。
- 偏离：无。

### 4. Gates（设计 §11 vs feature-flow §6）

- 期望：统一 pass/warn/block、GateError 含 rule_ids/evidence_refs/reason/recovery、`__post_init__` 强制净化、fail_closed 为唯一异常逃逸。
- 实际：feature-flow §6 确认 `gates.py:52-141 GateDecision`、`gates.py:62-72 __post_init__` 强制净化、`gates.py:143-150 GateError` 仅接受 block、`gates.py:153-167 fail_closed`。
- 偏离：无。

### 5. /chatbi-init 与诊断（设计 §9.1 vs feature-flow §2-3）

- 期望：版本/doctor/Schema/realpath/权限/沙箱/适配器/owner/PII/threshold 诊断；PASS 不设 production_ready=true；fail-closed 逐阶段早返回。
- 实际：feature-flow §3 确认 `diagnostics.py:384-682` 六阶段 pipeline、逐阶段早返回、`diagnostics.py:336-339` 硬编码 False、status 聚合逻辑（block→BLOCKED, warn→WARN, else→PASS）。
- 偏离：无。

### 6. SessionStart Hook（设计 §11.1 vs feature-flow §7）

- 期望：仅映射 SessionStart、薄入口调共享库、binding validator 拒绝 symlink/相对/Workspace 内 Python、事件形状校验、fail-closed exit 2。
- 实际：feature-flow §7 确认 `settings.json:3-13` 仅 SessionStart、`python_binding_launcher.py:94-112` binding validator、`session_diagnose.py:104-174 _validate_event`、`session_diagnose.py:219-229` fail-closed。
- 偏离：无。

### 7. 已知差距（设计 §15 Cycle 2-5 vs feature-flow §9）

- 期望：Cycle 2–5 能力标为 planned，不伪装已交付。
- 实际：feature-flow §9 gap 1–9 覆盖全部 Cycle 2–5 推迟项（production_ready false、TOCTOU、真实 Hook E2E、sandbox、adapter、Codebase 读取、PreToolUse、5 命令、login 未验）；gap 10 现正确标为「已交付（不再为差距）」。
- 偏离：无。

---

## dev-cycle-1 §10 完成清单复查

| # | 检查项 | v1 状态 | v2 状态 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | tickets 已批准，skills 已加载 | ✓ | ✓ | 85/85 测试全绿 |
| 2 | Task 1–5 文件存在、非空、归属清楚 | ✓ | ✓ | inventory 40 交付物验证 |
| 3 | CLAUDE.md ~200 行 | ✓ | ✓ | §10.6：112 行 |
| 4 | 领域/配置/路径失败关闭 | ✓ | ✓ | Flow A/C/D/E |
| 5 | 临时 Fixture 跑通 init，非硬编码 PASS | ✓ | ✓ | test_diagnostics.py 297-340 |
| 6 | GateError 含 rule/evidence/reason/recovery | ✓ | ✓ | Flow F |
| 7 | 共享产物无机器路径/密钥/PII | ✓ | ✓ | §10.4 扫描 |
| 8 | 无 Git 用内容散列 | ✓ | ✓ | paths.py:248-284 |
| 9 | SessionStart 离线契约全绿，真实 Hook 未演练标注 | ✓ | ✓ | compatibility.md NOT YET EXERCISED |
| 10 | unittest discover 全绿 | ✓ | ✓ | 85/85 |
| 11 | rule-traceability 逐条 | ✓ | ✓ | 46 条独立（汇总表算术已订正为 46） |
| 12 | feature-flow 从代码生成，含行引用 | △ | ✓ | v2 已订正 README 描述，行引用齐全 |
| 13 | test-agent ALL_PASSED | ✓ | ✓ | test-report-v1.md |
| 14 | plan-agent checklist 无 CRITICAL/HIGH 且 CONVERGED | - | ✓ | 本文档产出 |
| 15 | inventory/feature-flow/测试/设计 Cycle 1 范围一致 | △ | ✓ | feature-flow-v2(45) == test-report-v1(45) == ground-truth(45) |

第 12、15 项在 v1 为 △（因 P1-1），v2 均转为 ✓。全部 15 项满足。

---

## P0（必改）

无。

## P1（建议改）

无。

## P2（可选）

无。

---

## 收敛判定

- 上一轮 P1-1（feature-flow README 失准 + inventory 不一致）：**RESOLVED**--feature-flow-v2 §0/§9/§10.3 三处订正，三源一致（45/45/45）。
- 上一轮 P2-1（汇总表算术 43≠46）：**RESOLVED**--汇总表订正为 46，5 个单元格修正，与逐条行一致。
- 上一轮 P2-2（SRC-001 双周期歧义）：**RESOLVED**--改为单周期 `PLANNED: Cycle 3`，附 Cycle 4 关系说明。
- 本轮 design-vs-feature-flow(v2) diff：Cycle 1 范围内无新偏离（7 个维度逐项核验通过）。
- 本轮 §10 一致性：15/15 项全部满足（v1 的第 12、15 项已转 ✓）。
- 本轮新发现 P0/P1/P2：无。

P0=0，P1=0，P2=0。收敛达成，Cycle 1 可进入 AS_BUILT。

STATUS: CONVERGED
