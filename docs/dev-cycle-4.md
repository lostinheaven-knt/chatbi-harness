# Development Cycle 4 原生任务计划：模型维护、知识共置与变更影响门控

STATUS: PLANNED

> 版本号修正：`docs/dev-cycles.md` 把本周期 feature-flow/收敛文档标为 `v4`，但
> Cycle 3 已全局使用 `v4`（`feature-flow-v4.md` + `optimization/test-checklist/test-report-v4.md`）。
> 版本号跨周期全局递增，故本周期使用 **`v5`**（`feature-flow-v5.md`、
> `optimization-checklist-v5.md`、`test-checklist-v5.md`、`test-report-v5.md`）。`dev-cycles.md`
> 的 `v4` 标号为陈旧标签，不据此覆盖实际产物。

## 1. 周期目标

在 Cycle 1/2/3 已 CONVERGED 的基础上，交付模型维护与知识维护闭环：`/chatbi-maintain-model`
与 `/chatbi-maintain-knowledge` 两个 Command、`chatbi-maintenance` 与 `chatbi-knowledge` 两个
Skill、领域参考模板与 fixture-domain、变更影响清单（impact manifest）、`PostToolUse` 影响记录
Hook，以及模型-metadata-semantic-reference-Skill-tests-downstream-eval 的同步门控。一个真实
合成模型变更必须能生成 impact manifest、候选代码/metadata/reference/eval 变更与测试证据；只改
模型而不同步时 Stop gate 失败，同步完成且受影响测试/评测存在时通过。

知识资料必须适合路由且包含“用于/不得用于”与必填元数据；规范指标定义、权限策略、生产发布、
破坏性迁移继续要求人类批准（SEM-003）。`PostToolUse` 只记录已发生影响，不声称撤销；安全首防
仍是 Cycle 2 PreToolUse/OS sandbox。它不是只写空模板、永远 PASS 的影响门、把 PostToolUse 当成
撤销能力、或让维护扩展绕过 Cycle 3 分析闭环。

### 1.1 成功结果

- `impact.py` 提供 `ImpactManifest`（变更类型 model/column/semantic/reference/Skill/downstream/eval、
  受影响资产图、证据充分/缺失/不确定、P0 eval 失败标志、protected action 标志）；候选变更与
  受影响资产用证据绑定，缺证据/不确定影响显式处置，不降级为空。
- `hooks/posttool_impact.py` 是 `PostToolUse` 薄入口：只记录已发生影响，不声称撤销，不修改变更；
  复用 `gates`/`evidence`；失败 exit 2 + 规则 ID + 净化证据 + 恢复；忽略未知字段（HOOK-003）。
- `commands/chatbi-maintain-model.md` + `skills/chatbi-maintenance/SKILL.md`：模型变更生成 impact
  manifest + 候选代码/metadata/reference/eval 变更；只改模型 -> Stop gate 失败；完整同步且受影响
  测试/评测存在 -> 通过；protected action（approve_metric/change_access_policy/production_publish/
  destructive_migration）未审批 -> 阻断（SEM-003）。
- `commands/chatbi-maintain-knowledge.md` + `skills/chatbi-knowledge/SKILL.md` + `references/_template.md`
  + `references/fixture-domain.md`：知识模板含业务上下文/粒度/标准过滤/维度/关键模型/范围-排除/
  连接/易错点/最佳实践/交叉引用/owner/新鲜度/用于-不得用于（DOC-002/003）。
- `test_maintenance.py` 覆盖影响矩阵分支（model/column/semantic/reference/Skill/downstream/eval；
  证据充分/缺失/不确定；P0 eval 失败；protected action 未批准）。
- `test_knowledge.py` 覆盖知识 lint/retrieval（必填元数据、用于/不得用于、机器绝对路径、重复/冲突、
  历史 SQL `candidate_only`、相邻领域交叉引用）。
- `test_e2e.py` 维护/知识切片：模型单改阻断、完整同步通过、protected action 阻断、知识模板通过
  必填字段/路径/冲突/路由 tests。
- `docs/harness/maintenance.md`、`knowledge-authoring.md`、`feature-flow-v5.md`、
  `rule-traceability/security/compatibility.md` 增量反映 Cycle 4 真实能力；PostToolUse 记录性质、
  不撤销、首防仍是 Cycle 2 均明确，不把 official-only 写成 verified。

## 2. 非目标

Cycle 4 明确不实现以下内容，但不能从最终范围删除：

- 不实现离线评测、纠正闭环、消融、真实 Claude 全事件 E2E；属于 Cycle 5。
- 不连接真实 managed/CLI 数据源、不修复 Claude 登录、不运行真实 Claude reviewer/Hook 进程；
  Cycle 4 用合成模型变更 + 合成 impact manifest 离线验证，真实 E2E 是 Cycle 5。
- 不把 `PostToolUse` 实现为撤销/回滚能力；它只记录已发生影响（安全首防仍是 Cycle 2 PreToolUse/
  sandbox）。
- 不让维护扩展绕过 Cycle 3 分析闭环（evidence/current-run/Stop gate API 不变）。
- 不批准 protected action、不生成组织 PII policy/owner/threshold、不把 Fixture 结果当生产认证。
- 不把 Cycle 2 OS sandbox BLOCKING GAP 伪造为通过；该缺口延续到 Cycle 5。

## 3. 前置证据

实现者在创建 tickets 前必须重新确认以下证据仍成立：

- Cycle 3 已 CONVERGED：`evidence.py`、`adversarial-reviewer.md`、`subagent_review_gate.py`、
  `stop_gate.py`、`commands/chatbi-analyze.md`、`skills/chatbi-runbook/SKILL.md`、5 场景 fixture
  存在且 `python3 -B -m unittest discover -s tests/harness -p 'test_*.py'` 全绿（429 tests，1 skip =
  sandbox BLOCKING GAP）。
- `EvidenceEntry`/`RunRecord`/`compute_candidate_sha`/`validate_review`/`validate_provenance`（`evidence.py`）
  与 `GateDecision`/`GateError`（`gates.py`）、`stop_gate` API 稳定，本周期复用不修改其公共契约。
- `dev-cycles.md` Cycle 4 节与本计划一致；`requirements.md`/`technical-design.md` 的维护/知识路由
  （DOC-001..005、SEM-003、SRC-001/002）为规范来源。
- `docs/chatbi-harness-domain-model.md` 可读且与本计划无冲突；冲突则停止并上报。

## 4. 实现前强制审批门

- 本计划由 main agent 直接起草（沿用 Cycle 3 直接模式；API 已稳定，可由 plan-agent 复核）。用户
  明确批准本计划后，按选定的实现模式（coder-agent dispatch 或 main-agent 直接）应用
  `/Users/admin/.agents/skills/to-tickets/SKILL.md` 起草 tracer-bullet tickets，等待用户批准后发布
  到 `.scratch/chatbi-harness-cycle-4/issues/`，再加载 `implement/SKILL.md` 开始实现。
- 本规划阶段只生成计划文件，不生成或发布 tickets，不加载上述 Skills。

## 5. 文件所有权

| Task | 拥有文件 | 依赖 |
| --- | --- | --- |
| 1 impact manifest + PostToolUse | `lib/chatbi_harness/impact.py`, `hooks/posttool_impact.py`, `schemas/impact-manifest.schema.json`, `tests/harness/test_maintenance.py` | Cycle 3 |
| 2 maintain-model + maintenance SKILL | `commands/chatbi-maintain-model.md`, `skills/chatbi-maintenance/SKILL.md` | 1 |
| 3 maintain-knowledge + knowledge SKILL + template + fixture-domain | `commands/chatbi-maintain-knowledge.md`, `skills/chatbi-knowledge/SKILL.md`, `skills/chatbi-knowledge/references/{_template,fixture-domain}.md`, `tests/harness/test_knowledge.py` | Cycle 3 |
| 4 维护/知识 E2E 切片 | `tests/harness/test_e2e.py`(append) | 1, 2, 3 |
| 5 文档 + 回归 | `docs/harness/maintenance.md`, `docs/harness/knowledge-authoring.md`, `docs/feature-flow-v5.md`, `docs/harness/{rule-traceability,security,compatibility}.md` | 1-4 |

Task 1 与 Task 3 可并行（disjoint：impact/posttool/test_maintenance vs knowledge 命令/Skill/模板/
test_knowledge，且 Task 3 只依赖 Cycle 3 不依赖 Task 1）。Task 2 依赖 Task 1（impact manifest）。
Task 4 依赖 1/2/3；Task 5 依赖 1-4。

## 6. 任务顺序

### Task 1：变更影响清单与 PostToolUse 影响记录

#### 行为

1. `impact.py`：`ImpactManifest`（不可变：change_kind ∈ model/column/semantic/reference/Skill/
   downstream/eval、affected_assets 图、evidence_state ∈ sufficient/missing/uncertain、p0_eval_failed、
   protected_action、candidate_sha 绑定）；缺证据/不确定影响显式记录，不降级为空。
2. `schemas/impact-manifest.schema.json`：覆盖 change_kind/affected_assets/evidence_state/
   p0_eval_failed/protected_action/candidate_sha 全字段。
3. `hooks/posttool_impact.py`：`PostToolUse` 薄入口，复用 `gates`/`evidence`/`impact`；**只记录已发生
   影响，不声称撤销，不修改变更**；缺影响证据/阻断性漂移未处理 -> exit 2 + 规则 ID + 净化证据 +
   恢复；忽略未知字段（HOOK-003）；确定性（HOOK-001）。
4. 复用 Cycle 2 PreToolUse/ConfigChange 字段容忍模式与 `stop_gate` 的“未关闭 finding 阻断”语义：
   模型单改（受影响资产未同步）-> Stop gate 失败。

#### 测试/验证

`test_maintenance.py`：影响矩阵分支（model/column/semantic/reference/Skill/downstream/eval）；证据
充分通过、证据缺失阻断、不确定影响显式告警、P0 eval 失败阻断、protected action 未批准阻断；
PostToolUse 只记录不撤销；无 canary 泄漏。

#### 失败处理

- 影响无法确定：显式 uncertain，不假定充分；不降级为空。
- 字段与官方 Hook 文档不一致：停止映射，记录偏差交 plan-agent。

#### 规则 / AC

- AC-05（影响门控）、AC-09（维护场景）；DOC-004、HOOK-001/003/004/005、EVAL-001..005（受影响证据
  协调）、SEM-003、PORT-001、SEC-003。

### Task 2：/chatbi-maintain-model 命令与 maintenance SKILL

#### 行为

1. `commands/chatbi-maintain-model.md`：模型变更路由 -> 生成 impact manifest + 候选代码/metadata/
   reference/eval 变更 -> 同步门控（模型-metadata-semantic-reference-Skill-tests-downstream-eval）。
2. `skills/chatbi-maintenance/SKILL.md`：维护 runbook，明确影响清单生成、同步步骤、Stop gate 复用、
   protected action 人工批准。
3. 只改模型（受影响资产未同步）-> `stop_gate` 失败（复用 Cycle 3）；完整同步且受影响测试/评测存在
   -> 通过；protected action 未审批 -> 阻断（SEM-003）。

#### 测试/验证

由 Task 4 `test_e2e.py` 维护切片覆盖。Task 2 交付命令 + runbook 文件。

#### 失败处理

- 同步无法确定是否完整：显式 uncertain，Stop gate 失败，不假定通过。
- protected action：Agent 起草不等于批准，阻断等 owner（SEM-003）。

#### 规则 / AC

- AC-02、AC-05、AC-09；DOC-001/004/005、SEM-003、SRC-001/002、PORT-001、ABL-001/002。

### Task 3：/chatbi-maintain-knowledge 命令与 knowledge SKILL + 模板

#### 行为

1. `commands/chatbi-maintain-knowledge.md`：知识维护路由 -> lint + retrieval 适合路由。
2. `skills/chatbi-knowledge/SKILL.md`：知识维护 runbook。
3. `references/_template.md`：必填字段骨架（业务上下文/粒度/标准过滤/维度/关键模型/范围-排除/连接/
   易错点/最佳实践/交叉引用/owner/新鲜度/用于-不得用于）。
4. `references/fixture-domain.md`：合成领域参考样本（无组织真实事实/密钥/路径）。
5. 历史 SQL 标 `candidate_only`；相邻领域交叉引用；机器绝对路径拒绝；重复/冲突检测（DOC-002/003）。

#### 测试/验证

`test_knowledge.py`：必填元数据、用于/不得用于、机器绝对路径、重复/冲突、历史 SQL `candidate_only`、
相邻领域交叉引用；fixture-domain 通过 lint。

#### 失败处理

- 模板缺必填字段或含绝对路径：lint 失败，不通过。
- 知识与治理事实冲突：披露冲突交领域 owner（SRC-002）。

#### 规则 / AC

- AC-02；DOC-001/002/003/005、SEM-003、SRC-001/002、PORT-001。

### Task 4：维护/知识 E2E 切片

#### 行为

1. `test_e2e.py` 追加维护/知识切片：模型单改阻断（Stop gate 失败）；完整同步通过；protected action
   阻断；知识模板通过必填字段/绝对路径/冲突/路由 tests。
2. 用合成模型变更 + 合成 impact manifest + 合成知识 fixture 离线验证；不运行真实 Claude/Hook 进程。

#### 测试/验证

`python3 -B -m unittest tests.harness.test_maintenance tests.harness.test_knowledge tests.harness.test_e2e`
+ Cycle 1/2/3 全回归。

#### 失败处理

- 闭环因真实能力缺失无法跑通：保留明确偏差 + Cycle 5 硬门，不 mock 关闭。

#### 规则 / AC

- AC-02/05/09；DOC-001..005、SEM-003、SRC-001/002、EVAL-001..005、ABL-001/002、HOOK-001/004/005。

### Task 5：文档、feature-flow-v5 与周期回归证据

#### 行为

1. `docs/harness/maintenance.md`：维护路由、impact manifest、PostToolUse 记录性质（不撤销）、同步
   门控、protected action、Cycle 5 live E2E 缺口。
2. `docs/harness/knowledge-authoring.md`：知识模板字段、lint/retrieval、用于-不得用于、易错点。
3. `docs/feature-flow-v5.md`：从代码读取，含 impact manifest/PostToolUse/maintain-model/maintain-
   knowledge/同步门控的真实行引用、分支、数据流、错误与差距（含 Cycle 5 live Hook 缺口）。版本 v5。
4. `docs/harness/{rule-traceability,security,compatibility}.md` 增量：rule-traceability 把 DOC-001..005
   从 PLANNED 升级为 IMPLEMENTED (Cycle 4) 或注明后续周期；security/compatibility 区分 PostToolUse
   离线契约实测 vs 真实 Hook NOT YET EXERCISED，sandbox BLOCKING GAP 延续。
5. 运行 Cycle 4 目标测试 + Cycle 1/2/3 全回归；记录 exact command 与结果。

#### 测试/验证

```text
python3 -B -m unittest tests.harness.test_maintenance tests.harness.test_knowledge tests.harness.test_e2e
python3 -B -m unittest discover -s tests/harness -p 'test_*.py'
find .claude/lib/chatbi_harness .claude/hooks .claude/commands .claude/skills .claude/schemas .claude/fixtures tests/harness docs/harness -type f -print
rg -n '/Users/|BEGIN .*PRIVATE KEY|api[_-]?key|token\s*[:=]' .claude docs/harness
```

#### 失败处理

- 某规则尚无 Cycle 4 实现：标 PLANNED: Cycle N，不填假文件/假测试。
- feature-flow 无法给出行引用：说明入口不存在或文档过早，不生成想象调用链。
- plan-agent 报 CRITICAL/HIGH：进入优化迭代，不进入 Cycle 5。

#### 规则 / AC

- AC-02/05/09；HOOK-002/003/005；FBK-003；DOC-001..005。

## 7. 集成数据流

```text
/chatbi-maintain-model request (model change: kind, target, change)
        │
        ▼
  policy.decide (protected action? approve_metric/change_access_policy/
                 production_publish/destructive_migration) ──block──> SEM-003 人工批准
        │pass
        ▼
  impact.py: ImpactManifest(change_kind, affected_assets[metadata/semantic/
            reference/Skill/tests/downstream/eval], evidence_state, p0_eval_failed)
        │
        ▼
  同步门控：每个受影响资产有候选变更 + 证据？──no──> stop_gate 失败（未关闭 finding）
        │yes（完整同步 + 受影响测试/评测存在）
        ▼
  PostToolUse (posttool_impact.py)：记录已发生影响（不撤销、不修改）
        │
        ▼
  Stop gate：阻断性漂移未处理？──yes──> exit 2
        │no
        ▼
  交付维护结果 + impact manifest（含 change_kind/affected/evidence_state/p0/protected）


/chatbi-maintain-knowledge request
        │
        ▼
  knowledge lint（必填元数据/用于-不得用于/绝对路径/重复-冲突/历史SQL candidate_only/交叉引用）
        │
        ▼
  retrieval 适合路由（DOC-003）
```

PostToolUse 与 Cycle 2 PreToolUse/sandbox 互补：PreToolUse/sandbox 是事前阻断首防，PostToolUse 是
事后影响记录。PostToolUse 不声称撤销；真实 Hook 进程演练是 Cycle 5。

## 8. 测试矩阵

| 层次 | 关键用例 | 通过条件 |
| --- | --- | --- |
| impact 单元 | model/column/semantic/reference/Skill/downstream/eval 分支；证据充分/缺失/不确定 | evidence_state 显式；缺证据阻断；不确定告警 |
| PostToolUse 契约 | 只记录不撤销/不修改；缺影响证据阻断；未知字段容忍 | exit 0/2 符合契约；规则 ID/证据/恢复齐全；无泄漏 |
| maintain-model | 模型单改阻断/完整同步通过/protected action 阻断 | Stop gate 复用；SEM-003 不可自批 |
| knowledge lint/retrieval | 必填/用于-不得用于/绝对路径/重复-冲突/历史SQL/交叉引用 | lint 失败即不通过；fixture-domain 通过 |
| 离线纵向 | 合成模型变更 + impact manifest + 知识 fixture | policy->impact->sync->PostToolUse->Stop 跑通 |
| 兼容性 | PostToolUse 离线契约 vs 真实 Hook | 离线实测；真实 Hook 标 NOT YET EXERCISED（Cycle 5） |

## 9. 周期失败与升级策略

- **真实 Claude/Hook live E2E 缺失**：Cycle 4 只离线验证 impact manifest/PostToolUse 契约与同步
  门控；真实 Hook 进程演练保留在 Cycle 5 退出门，不得用 mock 关闭或删除。
- **OS sandbox BLOCKING GAP**（Cycle 2 延续）：不伪造通过；延续到 Cycle 5。
- **PostToolUse 被当作撤销能力**：契约失败，明确只记录不撤销；不放宽。
- **影响无法确定**：显式 uncertain，Stop gate 失败，不假定充分。
- **protected action 自批**：阻断（SEM-003），不可由 Agent 批准。
- **维护扩展绕过分析闭环**：不允许；evidence/current-run/Stop gate API 不变。
- **领域/需求冲突**：停止实现，提交冲突规则、候选解释与影响范围。
- **非 Cycle 4 缺口**（评测、纠正、真实 E2E）：记录到 feature-flow/design gap，不提前实现。

## 10. 周期完成清单

实现回合只有全部项目都有真实证据时才能提交 plan-agent 评审：

- [ ] 用户已批准 Cycle 4 计划与 tickets，已按选定模式加载 executing-plans。
- [ ] Task 1-5 的预期文件存在、非空、归属清楚，无计划外生产文件。
- [ ] `impact.py` 原子 impact manifest、证据状态显式、缺证据不降级；PostToolUse 只记录不撤销。
- [ ] `/chatbi-maintain-model` + maintenance SKILL：模型单改阻断、完整同步通过、protected action
      阻断均有证据。
- [ ] `/chatbi-maintain-knowledge` + knowledge SKILL + 模板 + fixture-domain：必填字段/用于-不得用于/
      绝对路径/重复-冲突/历史SQL/交叉引用 lint 通过。
- [ ] `test_maintenance`/`test_knowledge`/`test_e2e`(维护切片) 全绿 + Cycle 1/2/3 全回归。
- [ ] canary secret/PII/绝对 Workspace 路径不出现在任何 impact/PostToolUse/知识输出。
- [ ] `rule-traceability.md` 对 DOC-001..005 逐条标 Cycle 4 真实证据或注明后续周期。
- [ ] `maintenance.md`/`knowledge-authoring.md`/`security.md`/`compatibility.md` 区分 PostToolUse 离线
      契约实测 vs 真实 Hook NOT YET EXERCISED；sandbox BLOCKING GAP 延续标注。
- [ ] `feature-flow-v5.md` 从代码生成，含真实行引用、分支、数据流、错误与差距。
- [ ] test-agent 的 Cycle 4 报告目标测试通过；plan-agent 优化清单无 CRITICAL/HIGH 且 `STATUS: CONVERGED`。
- [ ] 文件 inventory、feature-flow-v5、测试报告与技术设计的 Cycle 4 范围一致。
- [ ] settings.json 仍为 SessionStart-only（hook 注册 DEFERRED 到 Cycle 5，防 dev 会话自死锁）。

本清单完成只允许 Cycle 4 进入 CONVERGED，不代表 Harness v1 COMPLETE，也不允许跳过 Cycle 5。
真实 Claude/Hook live E2E 与 OS sandbox 运行证据保留为 Cycle 5 退出门硬门。
