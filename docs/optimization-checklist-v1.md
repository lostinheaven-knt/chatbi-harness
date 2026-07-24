# Cycle 1 文档差异评估清单 v1

STATUS: NEEDS_ITERATION

评估日期：2026-07-22
评估轮次：第 1 轮（最多 5 轮）
评估依据：
- 期望 = `docs/technical-design.md`（PROPOSED 设计）
- 事实 = `docs/feature-flow-v1.md`（CODE_AS_READ，从代码读出）
- 范围 = `docs/dev-cycle-1.md` §5 文件所有权 + §10 周期完成清单
- 旁证 = `docs/test-report-v1.md`、`docs/harness/*.md`、实际文件 inventory（Glob 验证）

## 评估方法

逐项 diff 以下维度，仅关注 **Cycle 1 范围内**的偏离：

1. 实现是否偏离 Cycle 1 设计预期（非 Cycle 2–5 推迟项）；
2. dev-cycle-1 §5 预期文件是否齐全且非占位；
3. feature-flow 是否真实反映代码（行号引用可溯、无虚构调用链）；
4. 有无过度声称 / 虚构规则 / 机器路径 / 密钥泄漏；
5. 规则追踪是否逐条（非规则族概括）；
6. dev-cycle-1 §10 完成清单是否满足。

### 关键判读（避免误报）

以下已确认为 **有意推迟或设计预期**，不作为本周期 P0/P1：

- Cycle 2–5 能力（持续 TOCTOU、真实 sandbox、managed/CLI adapter、外部 Codebase 实际读取、PreToolUse 写/执行阻断、`/chatbi-analyze`/`maintain-model`/`maintain-knowledge`/`evaluate`/`correction` 命令、真实 Claude SessionStart Hook E2E）——feature-flow §9 已列为已知差距，属 planned Cycle N。
- `production_ready` 硬编码 `False`（`diagnostics.py:336-339`）——Cycle 1 设计预期，非 bug。
- 唯一 secret-scan 命中 `TEST_SECRET_CANARY_NOT_A_CREDENTIAL`——负测试 fixture，非泄漏。
- `policy.py`/`evidence.py`/`evaluator.py`/`adapters/*` 未实现——属 Cycle 2–5，dev-cycle-1 §5 未分配给 Cycle 1。

---

## P0（必改）

无。

Cycle 1 功能验收全部满足：85/85 测试全绿（test-report-v1.md）；根契约 46 条规则逐条覆盖（test_contract.py:41-89）；配置/路径/Gate/Hook 离线契约均 fail-closed 且净化；`production_ready` 正确硬编码 False；无密钥/机器路径泄漏；规则追踪逐条（rule-traceability.md 9.1–9.7 共 46 条独立条目）。dev-cycle-1 §5 预期的 Task 1–5 文件全部存在且非占位。

---

## P1（建议改）

### P1-1：feature-flow-v1.md §9 gap 10 与 §10.3 错误声称 `docs/harness/README.md` 缺失，与实际文件 inventory 不符

- **期望**：feature-flow 作为「事实文档」准确反映 Cycle 1 实际文件清单，即 `docs/harness/README.md` 存在（dev-cycle-1 §5 Task 5 预期文件之一，且已交付）。
- **实际**：
  - feature-flow-v1.md §9 gap 10（第 640–644 行）写：「`docs/harness/README.md` not present… absent from the Cycle 1 inventory」。
  - feature-flow-v1.md §10.3（第 688–694 行）写：「3 harness docs… Note: `docs/harness/README.md` is absent (gap 10 above)」，文件计数 42。
  - 但 Glob 验证 + 实际读取确认 `docs/harness/README.md` **存在**：212 行，内容实质（v1 目标、Cycle 1 入口、后续周期状态、硬边界、文档地图），头部标注 `STATUS: CODE_AS_READ on 2026-07-22`，与 feature-flow 同日。
  - test-report-v1.md SC-008（第 29 行）文件清单计数为 **43**，已反映 README 存在；feature-flow 仍为 42。两份文档对同一 inventory 的计数不一致。
- **依据**：
  - dev-cycle-1.md §10 完成清单：「文件 inventory、feature-flow、测试报告和技术设计的 Cycle 1 范围一致」——当前不满足（feature-flow 42 / test-report 43 / 实际含 README）。
  - dev-cycle-1.md §5 Task 5 文件所有权：`docs/harness/README.md` 列为预期文件——已交付，feature-flow 却标为缺失。
  - 任务硬约束「feature-flow 是否真实反映代码」——此处事实文档与代码/inventory 不符。
- **偏离方向**：有利方向（实际交付多于文档声称），非过度声称，无功能/安全影响。
- **建议**：更新 `docs/feature-flow-v1.md`：(1) 移除 §9 gap 10 或改为「已交付」；(2) 修正 §10.3 harness docs 计数 3→5、文件总数与 test-report 对齐；(3) 确认 §0 source-file 列表包含 README。此为文档订正，不涉及代码改动，可在本轮迭代中由 coder-agent 快速修正后重新评估。

---

## P2（可选）

### P2-1：rule-traceability.md 汇总表算术不一致（合计 43≠46，逐条行正确）

- **期望**：汇总表（rule-traceability.md 第 348–356 行）各状态计数与逐条行一致，合计 46。
- **实际**：逐条行（9.1–9.7）覆盖全部 46 条且逐条标注证据/状态（满足「规则追踪逐条」要求）；但汇总表计数偏差：
  - PARTIAL 标 5，逐条实为 4（SCOPE-002、SEC-001、SEC-002、HOOK-003）；
  - PLANNED Cycle 2 标 2，逐条实为 1（DOC-004；「continuous SCOPE-001 enforcement」是已计入 IMPLEMENTED 的 SCOPE-001 的子方面，非独立规则）；
  - PLANNED Cycle 3 标 13，逐条实为 17（REQ-001..004、SEM-001/002、RAW-001..003、SRC-002、QLT-001、REV-001..003、ANS-001..003）；
  - PLANNED Cycle 5 标 8，逐条实为 9（EVAL-001/002/003/005、ABL-001/002、FBK-001/002、HOOK-005）；
  - 合计 10+5+2+13+5+8=43≠46。
- **依据**：rule-traceability.md 第 357–359 行自注「The per-rule rows above are authoritative; this table is a summary aid」——汇总表为辅助，逐条行为权威。逐条行无遗漏，故不构成 P1；但汇总表计数错误影响可读性和对账。
- **建议**：修正汇总表各格计数使合计=46；或移除「continuous SCOPE-001 enforcement」这类非规则条目，仅统计 46 条规则 ID。

### P2-2：SRC-001 计划周期标注歧义（「Cycle 3/4」双周期）

- **期望**：每条规则归属单一计划周期，便于追踪和后续周期验收。
- **实际**：rule-traceability.md 第 149 行 SRC-001 状态为 `PLANNED: Cycle 3/4`，其余 45 条均为单一周期。CLAUDE.md:59 将 `/chatbi-maintain-knowledge` 路由到 Cycle 4，但 SRC-001（历史 SQL/notebook 作为候选线索）在分析（Cycle 3）和知识维护（Cycle 4）中均可能涉及。
- **依据**：technical-design.md §16 规则追踪矩阵 SRC-001 行未指定周期；dev-cycle-1.md §2 将分析归 Cycle 3、维护归 Cycle 4，SRC-001 跨两者。
- **建议**：根据实现计划确定 SRC-001 主归属周期（建议 Cycle 3，因 historical-SQL-as-clue 主要在分析路径触发），另一周期作为备注。

---

## 逐项核对记录

### 1. dev-cycle-1 §5 预期文件 vs 实际 inventory

| Task | 预期文件 | 实际 | 结论 |
| --- | --- | --- | --- |
| 1 | CLAUDE.md; CONTEXT.md; 3×rules | 全部存在 | ✓ |
| 2 | chatbi-harness.json/example/local.example; schema; __init__.py; config.py; fixtures/config/**; test_config.py | 全部存在（9 fixtures） | ✓ |
| 3 | paths.py; gates.py; test_paths.py | 全部存在 | ✓ |
| 4 | chatbi-init.md; settings.json; session_diagnose.py; test_hooks.py | 全部存在（另含 session_diagnose shell + python_binding_launcher.py + test_diagnostics.py + test_gates.py + test_contract.py，均为 Task 4 纵向切片合理组成） | ✓ |
| 5 | README.md; installation.md; configuration.md; compatibility.md; rule-traceability.md | 全部存在且非占位（README 212 行、rule-traceability 360 行） | ✓（feature-flow 误报 README 缺失，见 P1-1） |
| 6 | feature-flow-v1.md | 存在，含行号调用链 | ✓（README 描述需订正，见 P1-1） |

无 Cycle 1 预期文件缺失或占位。

### 2. 过度声称 / 虚构规则 / 机器路径 / 密钥扫描

- **过度声称**：未发现。README、compatibility.md 均明确区分 verified-offline / not-yet-exercised / production-blocker；`production_ready` 硬编码 False；Cycle 2–5 能力标 planned。
- **虚构规则**：未发现。feature-flow §10.5 确认 46 unique rule IDs 与领域模型 EXACT MATCH，无 SCOPE-999 类虚构。
- **机器路径**：feature-flow §10.4 扫描 `CLAUDE.md CONTEXT.md .claude docs/harness`，唯一命中为 canary fixture。technical-design.md §3.1 含 `/Users/admin/.local/bin/claude`（探测记录），但该文件是设计文档非共享产物，且本轮不可改动，留待 AS_BUILT 阶段处理。
- **密钥**：唯一命中 `TEST_SECRET_CANARY_NOT_A_CREDENTIAL`，负测试 fixture，非泄漏。

### 3. 规则追踪逐条

rule-traceability.md §9.1–9.7 逐条列出 46 条规则，每条含 Evidence（file:line 或 test）+ Tests + Status。无规则族概括替代逐条。**满足**。汇总表计数错误见 P2-1。

### 4. feature-flow 行号引用可溯性

feature-flow 各 Flow 均以 `file:line` 标注调用链（如 `gates.py:170-233`、`diagnostics.py:384-682`、`config.py:385-388`、`paths.py:355-360`），分支条件、数据流、错误处理、TOCTOU 边界均有行引用。§9 已知差距从代码注释和 `False` 返回读出，非复述设计文档。唯一失准点为 §9 gap 10 / §10.3 README 描述（P1-1）。

### 5. dev-cycle-1 §10 完成清单核对

| # | 检查项 | 状态 | 说明 |
| --- | --- | --- | --- |
| 1 | tickets 已批准，skills 已加载 | ✓ | 85/85 测试全绿佐证 |
| 2 | Task 1–5 文件存在、非空、归属清楚 | ✓ | inventory 验证通过 |
| 3 | CLAUDE.md ~200 行 | ✓ | feature-flow §10.6：112 行 |
| 4 | 领域/配置/路径失败关闭 | ✓ | feature-flow Flow A/C/D/E |
| 5 | 临时 Fixture 跑通 init，非硬编码 PASS | ✓ | test_diagnostics.py 297-340 |
| 6 | GateError 含 rule/evidence/reason/recovery | ✓ | feature-flow Flow F |
| 7 | 共享产物无机器路径/密钥/PII | ✓ | §10.4 扫描 |
| 8 | 无 Git 用内容散列 | ✓ | paths.py:248-284 |
| 9 | SessionStart 离线契约全绿，真实 Hook 未演练标注 | ✓ | compatibility.md NOT YET EXERCISED |
| 10 | unittest discover 全绿 | ✓ | 85/85 |
| 11 | rule-traceability 逐条 | ✓ | 46 条独立（汇总表计数 P2-1） |
| 12 | feature-flow 从代码生成，含行引用 | △ | 行引用齐全，但 README 描述失准（P1-1） |
| 13 | test-agent ALL_PASSED | ✓ | test-report-v1.md |
| 14 | plan-agent checklist 无 CRITICAL/HIGH 且 CONVERGED | — | 本文档产出中 |
| 15 | inventory/feature-flow/测试/设计 Cycle 1 范围一致 | △ | feature-flow 42 vs test-report 43 vs 实际含 README（P1-1） |

---

## 收敛判定

- P0：0 条
- P1：1 条（feature-flow README 描述失准，文档订正类，不涉及代码）
- P2：2 条（汇总表算术、SRC-001 周期歧义）

存在 P1 项，本轮不收敛。P1-1 为事实文档与实际 inventory 不符，修正后即可收敛。建议 orchestrator dispatch coder-agent 订正 `docs/feature-flow-v1.md` §9 gap 10 + §10.3 + §0 文件列表，使事实文档与 test-report（43 文件）和实际 inventory 一致，随后重新评估。

STATUS: NEEDS_ITERATION
