# Development Cycle 3 原生任务计划：受治理分析、运行证据与独立对抗审查

STATUS: PLANNED

> 版本号修正：`docs/dev-cycles.md` 把本周期 feature-flow/收敛文档标为 `v3`，但
> Cycle 2 已全局使用 `v3`（`feature-flow-v3.md` + `optimization/test-checklist/test-report-v3.md`）。
> 版本号跨周期全局递增，故本周期使用 **`v4`**（`feature-flow-v4.md`、
> `optimization-checklist-v4.md`、`test-checklist-v4.md`、`test-report-v4.md`）。`dev-cycles.md`
> 的 `v3` 标号为陈旧标签，不据此覆盖实际产物。

## 1. 周期目标

在 Cycle 1（骨架/配置/路径/诊断）与 Cycle 2（安全纵深/只读 Codebase/适配器选择链）已 CONVERGED
的基础上，交付受治理的完整分析问答闭环：原子且净化的运行证据、请求/审查/provenance Schema、
独立对抗 reviewer、`SubagentStop` 与受跟踪工作流 `Stop` 门控、`/chatbi-analyze` 五层流程与完整
答案页脚。分析必须从澄清与 T1 语义层发现开始，有证据地降级到 T2/T3，并在候选 SHA 匹配的独立
PASS 之后才交付。

本周期用合成固定数据离线跑通一个语义层答案；五个压力场景（歧义、陈旧、历史 SQL 诱导、外部
Prompt 注入、PII/权限）按预期澄清/降级/阻断。它不是只写 Schema、永远 PASS 的 reviewer、把离线
reviewer 契约测试冒充真实 Claude reviewer 已运行、或在候选变更后沿用旧 PASS。

### 1.1 成功结果

- `evidence.py` 提供原子、净化的运行证据（`RunRecord`/`EvidenceEntry`），内容用 SHA-256 绑定，
  净化去除密钥/未授权 PII/本机绝对路径，失败不可降级为空占位。
- `schemas/{request,review,provenance}.schema.json` 定义请求、审查结论、provenance 页脚的结构；
  缺字段、旧 SHA、缺 coverage、阻断 finding 均由 Schema/门控/测试覆盖。
- `agents/adversarial-reviewer.md` 是自包含、最小权限的 reviewer：隔离上下文中含 SCOPE/SEC/REV/
  ANS、事实层级与停止条件；无 Bash/Write/Edit/Agent 或 mutating MCP。
- `hooks/subagent_review_gate.py` 是 `SubagentStop` 薄入口：reviewer 未 PASS 或候选 SHA 不匹配则
  exit 2 + 规则 ID + 净化证据 + 恢复；候选变更使旧 review SHA 失效并强制新轮次；递归停止保护。
- `hooks/stop_gate.py` 是受跟踪工作流 `Stop` 薄入口：未交付前停止必须携带未关闭 finding 与恢复动作。
- `commands/chatbi-analyze.md` + `skills/chatbi-runbook/SKILL.md` 实现五层流程（澄清 -> T1 发现 ->
  有证据 T2 -> 有证据 T3 -> 独立 PASS 后交付）与完整答案页脚。
- `fixtures/evaluations/analysis-scenarios/**` 提供五个压力场景的合成固定数据。
- `test_review_gate.py` 证明 reviewer 契约 11 项 coverage、finding 结构、PASS/BLOCKED/ERROR、
  run/round/SHA 绑定、输出净化、缺证据阻断、递归停止。
- `test_analysis.py` 证明证据原语净化与 SHA 绑定、Schema 校验、降级证据链。
- `test_e2e.py`（分析切片）离线跑通 T1 命中、T1 未覆盖有证据到 T2、T2 不足有证据到 T3、无证据
  绕过失败，且页脚字段完整。
- `docs/harness/analysis.md`、`feature-flow-v4.md`、`security/compatibility/rule-traceability.md`
  反映 Cycle 3 真实能力；离线 reviewer 契约测试不得写成真实 Claude reviewer 已运行，该 live E2E
  保留在 Cycle 5 退出门。

## 2. 非目标

Cycle 3 明确不实现以下内容，但不能从最终范围删除：

- 不实现模型/知识维护、PostToolUse 影响图、DOC-004 同步门控、领域 reference 模板；属于 Cycle 4。
- 不实现离线评测、纠正闭环、消融、真实 Claude 全事件 E2E；属于 Cycle 5。
- 不连接真实 managed/CLI 数据源、不修复 Claude 登录、不运行真实 Claude reviewer 进程；Cycle 3 用
  合成 Fixture adapter + 合成 reviewer 契约离线验证流程与门控，真实 reviewer/Hook live E2E 是
  Cycle 5 明示目标，不得用 mock 关闭或删除。
- 不批准 protected action、不生成组织 PII policy/owner/threshold、不把 Fixture 结果当生产认证。
- 不把 Cycle 2 的 OS sandbox BLOCKING GAP 伪造为通过；该缺口延续到 Cycle 5。
- 不接入 Cycle 2 未 wire 的 `FixtureAdapter`/`CodebaseReader` 选择链（如需，作为独立偏差批准）；
  Cycle 3 的分析闭环使用 Fixture adapter 的直接构造证据（test/example 模式）。

## 3. 前置证据

实现者在创建 tickets 前必须重新确认以下证据仍成立：

- Cycle 2 已 CONVERGED：`policy.py`、`adapters/{__init__,base,fixture,codebase_reader}.py`、
  `pretool_guard.py`、`config_change_gate.py` 存在且 `python3 -B -m unittest discover -s
  tests/harness -p 'test_*.py'` 全绿（323 tests，1 skip = sandbox BLOCKING GAP）。
- `GateError`/`GateDecision`（`gates.py`）与 `EffectiveConfig`（`config.py`）、
  `resolve_path_reference`（`paths.py`）稳定，本周期复用不修改其公共契约。
- `AdapterEvidence`（`adapters/base.py`）的 `untrusted`/`rule_ids`/`evidence_source` 字段是本周期
  `evidence.py` 的输入形状。
- `dev-cycles.md` Cycle 3 节与本计划一致；`requirements.md`/`technical-design.md` 的分析路由
  （REQ/SEM/RAW/SRC/QLT/REV/ANS）为规范来源。
- `docs/chatbi-harness-domain-model.md` 可读且与本计划无冲突；冲突则停止并上报。

## 4. 实现前强制审批门

- 本计划由 main agent 直接起草（coder-agent/plan-agent/test-agent dispatch 在 Cycle 2 收敛期因
  API 429/500 反复失败；本计划可在 API 稳定后由 plan-agent 复核）。用户明确批准本计划后，coder-agent
  完整读取并应用 `/Users/admin/.agents/skills/to-tickets/SKILL.md`，起草 tracer-bullet tickets，
  等待用户批准后发布到 `.scratch/chatbi-harness-cycle-3/issues/`，再加载
  `/Users/admin/.agents/skills/implement/SKILL.md` 开始实现。
- 本规划阶段只生成计划文件，不生成或发布 tickets，不加载上述 Skills。

## 5. 文件所有权

| Task | 拥有文件 | 依赖 |
| --- | --- | --- |
| 1 evidence + schemas | `lib/chatbi_harness/evidence.py`, `schemas/{request,review,provenance}.schema.json`, `tests/harness/test_analysis.py` | Cycle 2 |
| 2 reviewer agent | `agents/adversarial-reviewer.md` | 1 |
| 3 review/stop gates | `hooks/subagent_review_gate.py`, `hooks/stop_gate.py`, `tests/harness/test_review_gate.py` | 1, 2 |
| 4 analyze command + runbook | `commands/chatbi-analyze.md`, `skills/chatbi-runbook/SKILL.md` | 1, 2, 3 |
| 5 analysis scenarios | `fixtures/evaluations/analysis-scenarios/**` | 1, 4 |
| 6 E2E + 集成测试 | `tests/harness/test_e2e.py`, `tests/harness/test_analysis.py`(append) | 1-5 |
| 7 文档 + 回归 | `docs/harness/analysis.md`, `docs/feature-flow-v4.md`, `docs/harness/{security,compatibility,rule-traceability}.md` | 1-6 |

Task 6 与 Task 2 都写 `test_analysis.py`/共享测试关注；Task 6 在 Task 1 之后顺序追加（避免
append 竞争）。Task 1 可与 Task 5 并行（disjoint：evidence/schemas vs scenarios，scenario 格式
由本计划 §6 Task 5 固定）。Task 2/3/4 在 Task 1 完成后按依赖顺序；Task 2 与 Task 4 disjoint
可并行（agent/runbook vs command/skill 文件不重叠）。Task 3 依赖 Task 2 的 reviewer 契约。

## 6. 任务顺序

### Task 1：运行证据原语与 Schema

#### 行为

1. `evidence.py`：`RunRecord`（run_id/round/candidate_sha/created_rev/actor/purpose）、
   `EvidenceEntry`（source_tier T1/T2/T3/evidence_source/rule_ids/payload/sanitized）；payload 用
   SHA-256 绑定候选；净化复用 `gates` sanitization，去除密钥值/未授权 PII/本机绝对路径；
   缺证据/净化失败产生 `GateError`，不降级为空。
2. `schemas/request.schema.json`：分析请求（question/time_range/entity/segment/actor/purpose/
   supported_decision）。
3. `schemas/review.schema.json`：审查结论（run_id/round/candidate_sha/status PASS|BLOCKED|ERROR/
   coverage[11]/findings[]/reviewer_context_hash/sanitized_output）。
4. `schemas/provenance.schema.json`：答案页脚（question/time_range/entity/segment/method/source_tier/
   filters/inclusions/exclusions/denominator/quality/limitations/review_round/freshness/owner/
   confidence/provenance_refs）。

#### 测试/验证

`test_analysis.py`：证据 SHA 绑定、净化去除 canary 密钥/路径/PII、Schema 校验通过/缺字段失败、
降级证据链（T1->T2->T3）记录 source_tier 与 rule_ids。

#### 失败处理

- 净化无法确定：失败关闭，不输出含密钥的证据。
- Schema 字段与需求不一致：停止，记录偏差交 plan-agent，不填假字段。

#### 规则 / AC

- AC-04（来源前置）、AC-06（独立审查）、AC-09（答案页脚）；EVID/QLT-001/SEC-003/PORT-001。

### Task 2：独立对抗审查 Agent 与契约

#### 行为

1. `agents/adversarial-reviewer.md`：自包含 prompt，隔离上下文含 SCOPE/SEC/REV/ANS 规则、T1/T2/T3
   事实层级、停止条件（PASS 仅当 11 项 coverage 全过且无阻断 finding）；明确无 Bash/Write/Edit/
   Agent/mutating MCP，只读最小工具。
2. reviewer 输出符合 `review.schema.json`：coverage 11 项（entity/grain/joins/filters-exclusions/
   date-timezone/denominator/sample-bias/quality/observation-vs-interpretation/disclosure/
   provenance）、findings（severity/rule_ids/evidence_refs/reason/recovery）、status、
   run/round/candidate_sha 绑定。

#### 测试/验证

由 Task 3 `test_review_gate.py` 覆盖 reviewer 契约（11 coverage、finding 结构、PASS/BLOCKED/ERROR、
SHA 绑定、净化、缺证据阻断、递归停止）。Task 2 本身交付 prompt 文件 + 契约说明。

#### 失败处理

- reviewer prompt 携带 mutating 工具：契约失败，移除并重测。
- coverage 项与 REV 规则不一致：停止，记录偏差。

#### 规则 / AC

- AC-06、AC-08（隔离 reviewer 契约）；REV-001/002/003、SCOPE/SEC、ANS-001/002/003。

### Task 3：SubagentStop 与 Stop 门控

#### 行为

1. `subagent_review_gate.py`：`SubagentStop` 薄入口，复用 `evidence`/`gates`；校验 review 结论
   status=PASS 且 candidate_sha 匹配当前候选；旧 SHA -> exit 2 强制新轮次；缺 coverage/阻断
   finding -> exit 2 + 规则 ID + 净化证据 + 恢复；递归停止保护（round 上限）。
2. `stop_gate.py`：受跟踪工作流 `Stop` 薄入口；未交付前停止必须携带未关闭 finding 与恢复动作，
   否则 exit 2。
3. 忽略未知事件字段，只校验已确认字段（复用 Cycle 2 PreToolUse/ConfigChange 的字段容忍模式）。

#### 测试/验证

`test_review_gate.py`：11 coverage 全过 PASS；缺一项 BLOCKED；阻断 finding BLOCKED；候选变更
使旧 SHA 失效；缺证据阻断；递归 round 上限停止；输出无 canary 泄漏；未知字段容忍。

#### 失败处理

- 门控无法确定 review 状态：失败关闭 exit 2，不假定 PASS。
- 字段与官方 Hook 文档不一致：停止映射，记录偏差交 plan-agent。

#### 规则 / AC

- AC-06、AC-08；REV-001/002/003、HOOK-001/003/004/005、SEC-003。

### Task 4：/chatbi-analyze 命令与 runbook

#### 行为

1. `commands/chatbi-analyze.md`：路由到五层流程——澄清（实体/时间/分段/决策）-> T1 语义层发现
   -> 有证据 T1 gap 到 T2 整理参考 -> 有证据 T2 不足到 T3 原始探索 -> 独立 PASS 后交付；历史 SQL
   仅作线索。
2. `skills/chatbi-runbook/SKILL.md`：分析 runbook，明确澄清问题、降级证据要求、质量检查、
   reviewer 调用与页脚组装步骤。
3. 答案页脚按 `provenance.schema.json` 全字段；raw 探索或未知新鲜度带高风险复核警告。

#### 测试/验证

由 Task 6 `test_e2e.py` 覆盖端到端切片。Task 4 交付 command + runbook 文件。

#### 失败处理

- 流程无法确定 T1 是否覆盖：停止并要求人澄清，不直接降级。
- 页脚缺字段：未交付，记录缺失。

#### 规则 / AC

- AC-04/06/09；REQ-001/002/003/004、SEM-001/002/003、RAW-001/002/003、SRC-001/002、QLT-001、
  ANS-001/002/003。

### Task 5：分析压力场景 Fixture

#### 行为

`fixtures/evaluations/analysis-scenarios/**` 五个合成固定数据场景：
1. `ambiguity`：实体/分段定义歧义，预期澄清不臆造。
2. `stale`：未知/陈旧新鲜度，预期高风险复核警告。
3. `historical-sql`：历史 SQL 诱导绕过 T1，预期仅作线索、有证据降级。
4. `prompt-injection`：外部 Codebase 内容含注入指令，预期忽略并记录（复用 Cycle 2 codebase_reader）。
5. `pii-permission`：PII/权限不足，预期阻断并给最小授权。
每个场景含 request、预期 source_tier 路径、预期 review 结论与页脚断言。无组织真实事实/密钥/路径。

#### 测试/验证

由 Task 6 `test_e2e.py` 消费。Task 5 交付 fixture 文件。

#### 失败处理

- 场景预期与规则冲突：停止，记录冲突交领域 owner。

#### 规则 / AC

- AC-09（五压力场景分析切片）；SEM/RAW/SRC/SEC/REV/ANS。

### Task 6：E2E 分析切片与集成测试

#### 行为

1. `test_e2e.py`：离线纵向——T1 命中；T1 未覆盖有证据到 T2；T2 不足有证据到 T3；无证据绕过失败；
   质量/观察-解释/局限/owner/新鲜度/置信度/复核提示完整；五压力场景预期行为全绿。
2. `test_analysis.py` 追加集成：证据链 -> reviewer 契约 -> 门控 -> 页脚组装的离线闭环。
3. 使用合成 Fixture adapter（test/example 模式直接构造）+ 合成 reviewer 契约；不运行真实 Claude
   reviewer 进程（Cycle 5）。

#### 测试/验证

`python3 -B -m unittest tests.harness.test_review_gate tests.harness.test_analysis tests.harness.test_e2e`
+ Cycle 1/2 全回归。

#### 失败处理

- 闭环因真实能力缺失无法跑通：保留为明确偏差 + 后续硬门（Cycle 5），不 mock 关闭。

#### 规则 / AC

- AC-04/06/09；REQ/SEM/RAW/SRC/QLT/REV/ANS 全族。

### Task 7：文档、追踪与周期证据

#### 行为

1. `docs/harness/analysis.md`：分析路由说明、五层流程、reviewer 契约、门控、页脚、离线证据与
   Cycle 5 live E2E 缺口。
2. `docs/feature-flow-v4.md`：从代码读取，含真实行引用、分支、数据流、错误与差距（含
   reviewer/review-gate/stop-gate/evidence/analyze 五层/页脚；含 Cycle 5 live reviewer 缺口）。
3. `docs/harness/{security,compatibility,rule-traceability}.md` 增量：rule-traceability 把
   REQ/SEM/RAW/SRC/QLT/REV/ANS 从 PLANNED/PARTIAL 升级为 IMPLEMENTED (Cycle 3) 或注明后续周期；
   security/compatibility 区分离线 reviewer 契约实测 vs 真实 Claude reviewer NOT YET EXERCISED。
4. 运行 Cycle 3 目标测试 + Cycle 1/2 全回归；记录 exact command 与结果。

#### 测试/验证

```text
python3 -B -m unittest tests.harness.test_review_gate tests.harness.test_analysis tests.harness.test_e2e
python3 -B -m unittest discover -s tests/harness -p 'test_*.py'
find .claude/lib/chatbi_harness .claude/hooks .claude/agents .claude/skills .claude/schemas .claude/fixtures/evaluations tests/harness docs/harness -type f -print
rg -n '/Users/|BEGIN .*PRIVATE KEY|api[_-]?key|token\s*[:=]' .claude docs/harness
```

#### 失败处理

- 某规则尚无 Cycle 3 实现：标 PLANNED: Cycle N，不填假文件/假测试。
- feature-flow 无法给出行引用：说明入口不存在或文档过早，不生成想象调用链。
- plan-agent 报 CRITICAL/HIGH：进入优化迭代，不进入 Cycle 4。

#### 规则 / AC

- AC-04/06/08/09；HOOK-002/003/005；FBK-003；DOC-001/004。

## 7. 集成数据流

```text
/chatbi-analyze request (question/time/entity/segment/actor/purpose/decision)
        │
        ▼
  澄清：实体/时间/分段/决策未定 -> 停止问最小澄清（REQ-001/002）
        │
        ▼
  T1 语义层 discover（Fixture/managed/CLI adapter，Cycle 2 选择链）
        │ T1 覆盖
        ├──yes──> compile/query -> 证据（source_tier=T1）
        │no（记录 gap）
        ▼
  T2 整理参考/治理模型（有证据 gap）-> 证据（source_tier=T2）
        │ T2 不足（记录 gap）
        ▼
  T3 原始探索（有证据 gap，高风险复核警告）-> 证据（source_tier=T3）
        │
        ▼
  质量检查 + 观察/解释分离 + 页脚组装（provenance.schema）
        │
        ▼
  候选 SHA 绑定 -> adversarial-reviewer（隔离，11 coverage，无 mutating 工具）
        │
        ▼
  subagent_review_gate：PASS 且 SHA 匹配？──no──> exit 2（旧 SHA/缺 coverage/阻断 finding/递归停止）
        │yes
        ▼
  stop_gate：未关闭 finding？──yes──> exit 2
        │no
        ▼
  交付答案 + provenance 页脚（含 source_tier/filters/denominator/quality/limitations/
       review_round/freshness/owner/confidence/provenance_refs）
```

reviewer 与 PreToolUse/OS sandbox 互补：reviewer 是语义独立审查层，PreToolUse/sandbox 是确定性/
OS 层。真实 Claude reviewer 进程演练是 Cycle 5，Cycle 3 只离线验证契约与门控。

## 8. 测试矩阵

| 层次 | 关键用例 | 通过条件 |
| --- | --- | --- |
| evidence 单元 | SHA 绑定/净化/Schema 校验/降级链 | 密钥/路径/PII 净化；缺字段失败；source_tier 记录 |
| reviewer 契约 | 11 coverage/finding/PASS-BLOCKED-ERROR/SHA/净化/递归 | coverage 缺一 BLOCKED；阻断 finding BLOCKED；旧 SHA 失效；无泄漏 |
| review/stop 门控 | SubagentStop/Stop 字段容忍/未知字段 | exit 0/2 符合契约；规则 ID/证据/恢复齐全；递归停止 |
| analyze 五层 | T1 命中/T1->T2/T2->T3/无证据失败 | 有证据降级；无证据绕过失败；页脚全字段 |
| 五压力场景 | ambiguity/stale/historical-sql/injection/pii | 澄清/警告/线索/忽略记录/阻断 符合预期 |
| 离线纵向 | 合成 Fixture + 合成 reviewer 闭环 | policy->adapter->evidence->reviewer->gate->footer 跑通 |
| 兼容性 | reviewer 离线契约 vs 真实 Claude reviewer | 离线实测；真实 reviewer 标 NOT YET EXERCISED（Cycle 5） |

## 9. 周期失败与升级策略

- **真实 Claude reviewer/Hook live E2E 缺失**：Cycle 3 只离线验证 reviewer 契约与门控；真实
  reviewer 进程演练保留在 Cycle 5 退出门，不得用 mock 关闭或删除。compatibility/security 标
  NOT YET EXERCISED。
- **OS sandbox BLOCKING GAP**（Cycle 2 延续）：不伪造通过；延续到 Cycle 5。
- **reviewer prompt 携带 mutating 工具或依赖外部上下文**：契约失败，移除并重测；不放宽。
- **门控无法确定 review 状态**：失败关闭 exit 2，不假定 PASS。
- **Schema 字段与需求/规则不一致**：停止，记录偏差交 plan-agent/用户。
- **领域/需求冲突**：停止实现，提交冲突规则、候选解释与影响范围。
- **非 Cycle 3 缺口**（评测、纠正、模型维护）：记录到 feature-flow/design gap，不提前实现。

## 10. 周期完成清单

实现回合只有全部项目都有真实证据时才能提交 plan-agent 评审：

- [ ] 用户已批准 Cycle 3 计划与 tickets，coder-agent 已按依赖链加载 executing-plans。
- [ ] Task 1-7 的预期文件存在、非空、归属清楚，无计划外生产文件。
- [ ] `evidence.py` 原子净化、SHA 绑定、缺证据不降级；三 Schema 覆盖请求/审查/provenance 全字段。
- [ ] `adversarial-reviewer.md` 自包含、无 mutating 工具、含 SCOPE/SEC/REV/ANS + 事实层级 + 停止条件。
- [ ] `subagent_review_gate.py`/`stop_gate.py` PASS+SHA 匹配放行、旧 SHA/缺 coverage/阻断 finding/递归
      停止均 exit 2，未知字段容忍。
- [ ] `/chatbi-analyze` + runbook 五层流程有证据降级、无证据绕过失败、页脚全字段。
- [ ] 五压力场景 Fixture 全绿；无组织真实事实/密钥/路径。
- [ ] `test_review_gate`/`test_analysis`/`test_e2e` 全绿 + Cycle 1/2 全回归。
- [ ] canary secret/PII/绝对 Workspace 路径不出现在任何 reviewer 输出/证据/门控 stdout。
- [ ] `rule-traceability.md` 对 REQ/SEM/RAW/SRC/QLT/REV/ANS 逐条标 Cycle 3 真实证据或注明后续周期。
- [ ] `analysis.md`/`security.md`/`compatibility.md` 区分离线 reviewer 契约实测 vs 真实 Claude reviewer
      NOT YET EXERCISED；sandbox BLOCKING GAP 延续标注。
- [ ] `feature-flow-v4.md` 从代码生成，含真实行引用、分支、数据流、错误与差距。
- [ ] test-agent 的 Cycle 3 报告目标测试通过；plan-agent 优化清单无 CRITICAL/HIGH 且 `STATUS: CONVERGED`。
- [ ] 文件 inventory、feature-flow-v4、测试报告与技术设计的 Cycle 3 范围一致。

本清单完成只允许 Cycle 3 进入 CONVERGED，不代表 Harness v1 COMPLETE，也不允许跳过 Cycle 4-5。
真实 Claude reviewer/Hook live E2E 与 OS sandbox 运行证据保留为 Cycle 5 退出门硬门。
