# Development Cycle 5 原生任务计划：评测、纠正闭环、全量兼容演练与交付收敛（最终周期）

STATUS: PLANNED

> 版本号修正：`docs/dev-cycles.md` 把本周期 feature-flow/收敛文档标为 `v5`，但
> Cycle 4 已全局使用 `v5`。版本号跨周期全局递增，故本周期使用 **`v6`**
>（`feature-flow-v6.md`、`optimization-checklist-v6.md`、`test-checklist-v6.md`、
> `test-report-v6.md`）。最终 plan-agent 把 `docs/technical-design.md` 更新为唯一
> `STATUS: AS_BUILT`。`dev-cycles.md` 的 `v5` 标号为陈旧标签。

## 1. 周期目标

在 Cycle 1–4 已 CONVERGED 的基础上，交付评测与纠正闭环、全量兼容演练与交付收敛：`/chatbi-evaluate`
与 `/chatbi-correction` 两个 Command、`chatbi-evaluation` Skill、evaluation/correction Schema、
`evaluator.py`（evaluator + ground-truth 隔离 runner）、固定评测 suite、双候选纠正、基线/消融/负面实验
记录、故障排查与全规则追踪；运行全测试、五压力场景、六 Commands 与**真实 Claude Code 2.1.216
Agent/Hook E2E**。所有事实文件、feature-flow、报告和技术设计 AS_BUILT 必须最终一致。

高频/长尾 + 五压力场景的固定评测可重复；ground truth 对被测会话不可读；run 记录 Skill 版本/内容
hash（无 Git）/model ID/逐断言/token/耗时；一条纠正同时生成修复候选与评测候选且不自动批准指标
（FBK-002）；评测通过不等于消除静默失败（FBK-003）。全量测试绿色，2.1.216 上真实 reviewer 与 Hook
事件 E2E 通过，兼容/限制文档完整，46/46 规则有真实文件/测试证据。

### 1.1 成功结果

- `evaluator.py` 提供 evaluator + ground-truth 隔离 runner：ground truth 物理隔离（被测会话不可读），
  逐断言评分，run 记录 Skill 版本/内容 hash/model ID/assertion/token/latency；seen/unseen 分开；
  评测锚定快照/稳定事实或评分查询/实体选择（EVAL-001/002/003）。
- `schemas/{evaluation,correction}.schema.json` 定义评测 run 与纠正记录结构（含 fix 候选 + eval case
  候选，不自动批准）。
- `commands/chatbi-evaluate.md` + `skills/chatbi-evaluation/SKILL.md`：固定 suite（高频/长尾 + 五压力），
  可配置 owner 确认门槛（EVAL-004，不硬编码 90%）；语义层覆盖时断言命中语义层（EVAL-005）。
- `commands/chatbi-correction.md`：每条有效 correction 同时生成参考资料/Skill/模型修复候选 + Evaluation
  Case 候选，经 owner 批准后合并（FBK-002）；不自动批准规范指标（SEM-003）。
- `test_evaluation.py` / `test_correction.py`：ground-truth 隔离、逐断言、run 记录字段、双候选纠正、
  不自动批准、FBK-003 断言。
- `test_e2e.py` 五工作流/五压力/兼容切片：六 Commands 路由、review SHA 失效、canary 泄漏、生产无连接
  STOP 均在全套。
- `fixtures/evaluations/**`：高频/长尾 + 五压力固定评测 suite（合成，无组织真实事实/密钥/路径）。
- `docs/harness/{evaluation,troubleshooting,negative-experiments}.md` + `feature-flow-v6.md` +
  `rule-traceability/security/compatibility.md` 最终增量；46/46 规则真实证据。
- **真实 Claude Code 2.1.216 E2E**（Darwin arm64）：触发真实 SessionStart/PreToolUse/PostToolUse/
  SubagentStop/Stop/ConfigChange 契约与隔离 reviewer；记录 exact commands/exit/输出/model。
- **AS_BUILT**：plan-agent 按真实代码把 `docs/technical-design.md` 更新为唯一 `STATUS: AS_BUILT`。

## 2. 非目标

Cycle 5 不实现以下内容，但不能从最终范围删除：

- 不用真实组织数据替代固定合成 Fixture；但必须验证无真实连接时生产路径明确 STOP。
- 不自动批准规范指标/权限/发布/破坏性迁移（SEM-003，始终人批）。
- 不把评测通过描述为绝对正确性保证（FBK-003）。
- 不在 dev 会话注册阻断 Hook（沿用死锁教训）；live Hook 注册只在真实 E2E 环境进行。
- 不伪造真实 Claude/Hook/sandbox 运行证据；无法触发任一 P0 事件时本周期不退出（退出门硬门）。
- 组织 PII policy/真实 owner/真实连接/发布门槛未提供时，文档/诊断明确标“无法生产认证”，Fixture 结果
  不冒充组织验收。

## 3. 前置证据

- Cycle 4 已 CONVERGED：`impact.py`/`knowledge.py`/`posttool_impact.py`/maintain-model/knowledge 命令与
  Skill 存在，`python3 -B -m unittest discover -s tests/harness -p 'test_*.py'` 全绿（481 tests，1 skip）。
- Cycle 1–4 全部 Hook 脚本（session_diagnose/pretool_guard/config_change_gate/subagent_review_gate/
  stop_gate/posttool_impact）存在且离线契约测试通过；本周期复用不修改其公共契约。
- 真实 Claude E2E 前需用户修复登录/keychain 运行前置（agent 无法自行登录）。无 Git 工作区用内容 hash
  替代 Git SHA（EVAL-003）。
- `dev-cycles.md` Cycle 5 节与本计划一致；`requirements.md`/`technical-design.md` 的评测/纠正路由
  （EVAL/ABL/FBK）为规范来源。
- `docs/chatbi-harness-domain-model.md` 可读且与本计划无冲突；冲突则停止并上报。

## 4. 实现前强制审批门

- 本计划由 main agent 直接起草。用户明确批准后，按选定模式（coder-agent dispatch 或 main-agent 直接）
  应用 `to-tickets/SKILL.md` 起草 tracer-bullet tickets，等待用户批准后发布到
  `.scratch/chatbi-harness-cycle-5/issues/`，再加载 `implement/SKILL.md` 开始实现。
- **真实 E2E（Task 06）是人工环境门**：agent 准备 E2E 流程 + 证据录制脚本；用户在已登录的 Claude Code
  2.1.216（Darwin arm64）运行并记录 exact commands/exit/输出/model。未登录或无法触发任一 P0 事件时，
  本周期不退出。
- 本规划阶段只生成计划文件，不生成或发布 tickets，不加载上述 Skills。

## 5. 文件所有权

| Task | 拥有文件 | 依赖 |
| --- | --- | --- |
| 1 evaluator + schemas | `lib/chatbi_harness/evaluator.py`, `schemas/{evaluation,correction}.schema.json`, `tests/harness/test_evaluation.py` | Cycle 1-4 |
| 2 evaluate command + SKILL | `commands/chatbi-evaluate.md`, `skills/chatbi-evaluation/SKILL.md` | 1 |
| 3 correction command + test | `commands/chatbi-correction.md`, `tests/harness/test_correction.py` | 1 |
| 4 eval suite fixtures + E2E slice | `fixtures/evaluations/**`(eval suite), `tests/harness/test_e2e.py`(append) | 1, 2, 3 |
| 5 文档 + 全规则追踪 | `docs/harness/{evaluation,troubleshooting,negative-experiments}.md`, `docs/feature-flow-v6.md`, `docs/harness/{rule-traceability,security,compatibility}.md` | 1-4 |
| 6 真实 Claude E2E（人工环境门） | E2E 流程脚本 + 证据记录（`docs/harness/compatibility.md` E2E 证据段） | 1-5 + 用户登录环境 |
| 7 AS_BUILT + 最终审计 | `docs/technical-design.md`(STATUS: AS_BUILT), 最终 46/46 审计 + completion checklist | 1-6 |

Task 2 与 Task 3 可并行（disjoint：evaluate 命令/SKILL vs correction 命令/test_correction；都依赖
Task 1）。Task 4 依赖 1/2/3；Task 5 依赖 1-4；Task 6（人工 E2E）依赖 1-5 + 用户环境；Task 7（AS_BUILT）
依赖 1-6。

## 6. 任务顺序

### Task 1：evaluator、ground-truth 隔离 runner 与 Schema

#### 行为

1. `evaluator.py`：`EvaluationRun`（run_id/skill_version/content_hash/model_id/assertions[]/tokens/
   latency/seen_unseen）+ `GroundTruthVault`（物理隔离：ground truth 存于被测会话不可读路径，runner 只
   暴露断言结果不暴露答案）；逐断言评分；锚定快照/稳定事实或评分查询/实体选择（EVAL-001/002）。
2. `schemas/evaluation.schema.json`：run_id/skill_version/content_hash/model_id/assertions(seen/unseen)/
   tokens/latency/threshold_owner_confirmed。
3. `schemas/correction.schema.json`：correction_id/fix_candidate(reference/Skill/model)/eval_case_candidate/
   owner_approved（默认 false）/rule_ids。
4. 复用 `evidence`/`gates` 净化与 SHA；无 Git 用内容 hash 替代 Git SHA（EVAL-003）。

#### 测试/验证

`test_evaluation.py`：ground-truth 隔离（被测会话不可读答案）；逐断言；run 记录字段齐全；seen/unseen
分开；FBK-003 断言（评测通过 != 绝对正确）；无 canary 泄漏。

#### 失败处理

- ground truth 隔离无法保证：失败关闭，不暴露答案。
- 门槛未 owner 确认：标未配置，不硬编码。

#### 规则 / AC

- AC-07、AC-09；EVAL-001/002/003/004/005、FBK-003、HOOK-001/004、SEC-003、PORT-001。

### Task 2：/chatbi-evaluate 命令与 evaluation SKILL

#### 行为

1. `commands/chatbi-evaluate.md`：路由固定 suite（高频/长尾 + 五压力）评测；可配置 owner 确认门槛
   （EVAL-004，不硬编码 90%）；语义层覆盖时断言命中语义层（EVAL-005）；记录 run（EVAL-003）。
2. `skills/chatbi-evaluation/SKILL.md`：评测 runbook，明确 ground-truth 隔离、seen/unseen、快照锚定、
   逐断言、run 记录、门槛确认、“不等于消除静默失败”。

#### 测试/验证

由 Task 4 `test_e2e.py` 评测切片覆盖。Task 2 交付命令 + runbook。

#### 失败处理

- 门槛与 owner 确认缺失：标未配置，不假定通过。
- 评测通过：明确不宣称绝对正确（FBK-003）。

#### 规则 / AC

- AC-07；EVAL-001..005、FBK-003、SEM-003、PORT-001。

### Task 3：/chatbi-correction 命令与双候选纠正

#### 行为

1. `commands/chatbi-correction.md`：每条有效 correction 同时生成 fix 候选（reference/Skill/model）+
   eval case 候选，经 owner 批准后合并（FBK-002）；不自动批准规范指标（SEM-003）；结构化收集进入
   周期审查，跟踪语义层解决比例与纠正性语言比例（FBK-001）。
2. `test_correction.py`：双候选生成、owner_approved 默认 false、不自动批准指标、结构化字段、无泄漏。

#### 测试/验证

`test_correction.py` + Task 4 E2E 纠正切片。

#### 失败处理

- correction 无法确定修复候选：显式记录，不自动批准。
- 规范指标修改：阻断等 owner（SEM-003）。

#### 规则 / AC

- AC-09；FBK-001/002/003、SEM-003、ABL-001/002、SEC-003、PORT-001。

### Task 4：评测 suite fixtures 与五工作流/五压力/兼容 E2E 切片

#### 行为

1. `fixtures/evaluations/**`：高频/长尾 + 五压力固定评测 suite（合成，无组织真实事实/密钥/路径）；
   ground truth 与被测输入分离。
2. `test_e2e.py` 追加：六 Commands 路由（init/analyze/maintain-model/maintain-knowledge/evaluate/
   correction）；五压力场景；review SHA 失效；canary 泄漏；生产无连接 STOP；消融单组件变更（ABL-001）。

#### 测试/验证

`python3 -B -m unittest tests.harness.test_evaluation tests.harness.test_correction tests.harness.test_e2e`
+ Cycle 1-4 全回归。

#### 失败处理

- 闭环因真实能力缺失无法跑通：保留偏差 + Task 6 真实 E2E 硬门，不 mock 关闭。

#### 规则 / AC

- AC-01..09；EVAL/ABL/FBK 全族、HOOK-001..005。

### Task 5：文档、feature-flow-v6 与全规则追踪

#### 行为

1. `docs/harness/evaluation.md`：评测路由、ground-truth 隔离、seen/unseen、run 记录、门槛、FBK-003。
2. `docs/harness/troubleshooting.md`：故障排查（登录/keychain、Hook 不触发、sandbox 不可用、生产无连接
   STOP、reviewer 隔离）。
3. `docs/harness/negative-experiments.md`：精简负面实验清单（ABL-002）：检索扩张无效、文档膨胀无效、
   廉价 reviewer 替换无效等已记录的负面结果。
4. `docs/feature-flow-v6.md`：从代码读取，含 evaluator/correction/evaluate/correction 命令/六 Commands
   路由/真实 E2E 缺口与证据，真实行引用。版本 v6。
5. `docs/harness/{rule-traceability,security,compatibility}.md` 最终增量：EVAL/ABL/FBK 升级 IMPLEMENTED
  (Cycle 5)；HOOK-003/005 从 PARTIAL 升级（真实 E2E 后）；46/46 真实证据；sandbox/生产认证缺口最终标注。

#### 测试/验证

```text
python3 -B -m unittest discover -s tests/harness -p 'test_*.py'
find .claude tests/harness docs/harness -type f -print
rg -n '/Users/|BEGIN .*PRIVATE KEY|api[_-]?key|token\s*[:=]' .claude docs/harness
```

#### 失败处理

- 某规则尚无 Cycle 5 实现：标 PLANNED/缺证据，不填假文件。
- feature-flow 无法给出行引用：说明入口不存在，不生成想象调用链。

#### 规则 / AC

- AC-01..09；HOOK-002/003/005；FBK-003；DOC-001..005。

### Task 6：真实 Claude Code 2.1.216 E2E（人工环境门）

#### 行为

1. agent 准备 E2E 流程脚本 + 证据录制模板（不自行登录）。
2. 用户在已登录的 Claude Code 2.1.216（Darwin arm64）注册 live Hook（**仅在 E2E 环境，非 dev 会话**
   - 沿用死锁教训）并触发真实 SessionStart、PreToolUse、PostToolUse、SubagentStop、Stop、ConfigChange
   契约与隔离 adversarial-reviewer。
3. 记录 exact commands、exit code、输出、model 信息到 `docs/harness/compatibility.md` E2E 证据段。
4. 验证生产无连接时路径明确 STOP（无真实 managed/CLI 连接时 fail-closed）。

#### 测试/验证

人工运行 + 证据记录。未登录或无法触发任一 P0 事件 -> 本周期不退出（退出门硬门）。

#### 失败处理

- 登录/keychain 未修复：阻塞 Task 6，明确记录，不伪造 E2E。
- 某 Hook 事件在 2.1.216 字段不一致：记录偏差，不掩盖。

#### 规则 / AC

- AC-08 全量真实演练；HOOK-001..005 真实证据；SEC-003、SEM-003、REV-001..003。

### Task 7：AS_BUILT 与最终审计

#### 行为

1. plan-agent 按真实代码把 `docs/technical-design.md` 更新为唯一 `STATUS: AS_BUILT`（设计-实现最终一致）。
2. 主编排器执行：文件 inventory、规则 46/46 真实证据审计、报告/feature-flow-v6/AS_BUILT 一致性、
   原生命令证据审计。
3. mandatory completion checklist 全部打勾后才可宣称 COMPLETE。

#### 测试/验证

全量 `python3 -B -m unittest discover` 绿色；46/46 规则证据；AS_BUILT 一致性。

#### 失败处理

- 任一 mandatory 项未满足：不宣称 COMPLETE，记录缺口。
- AS_BUILT 与代码不一致：修正 AS_BUILT 或代码，不伪造一致。

#### 规则 / AC

- AC-01..09 全部；46/46 规则最终复核；FBK-003。

## 7. 集成数据流

```text
/chatbi-evaluate (fixed suite: high-freq/long-tail + 5 stress)
        │
        ▼
  GroundTruthVault (物理隔离，被测会话不可读) + evaluator 逐断言评分
        │
        ▼
  EvaluationRun(skill_version/content_hash/model_id/assertions[seen/unseen]/tokens/latency)
        │
        ▼
  owner-confirmed threshold (EVAL-004, 不硬编码) ──未确认──> 标未配置，不假定通过
        │
        ▼
  记录 run + FBK-003 断言（通过 != 绝对正确）


/chatbi-correction (有效纠正记录)
        │
        ▼
  同时生成 fix 候选 (reference/Skill/model) + eval case 候选 (FBK-002)
        │
        ▼
  owner_approved? ──no──> 不合并，不自动批准规范指标 (SEM-003)
        │yes
        ▼
  合并 fix + eval case；结构化进入周期审查 (FBK-001)


真实 E2E (Task 6, 人工环境门):
  已登录 Claude Code 2.1.216 (Darwin arm64) + live Hook 注册 (E2E 环境, 非 dev)
    -> 真实 SessionStart/PreToolUse/PostToolUse/SubagentStop/Stop/ConfigChange + 隔离 reviewer
    -> 记录 exact commands/exit/输出/model
    -> 生产无连接 STOP 验证
```

PostToolUse/sandbox 仍是首防；评测/纠正不绕过 Cycle 1-4 门控。真实 E2E 是 Cycle 5 退出门硬门。

## 8. 测试矩阵

| 层次 | 关键用例 | 通过条件 |
| --- | --- | --- |
| evaluator 单元 | ground-truth 隔离/逐断言/run 字段/seen-unseen | 答案不可读；字段齐全；FBK-003 断言 |
| correction | 双候选/owner_approved 默认 false/不自动批准指标 | fix+eval case 生成；SEM-003 阻断 |
| evaluate E2E | 六 Commands 路由/五压力/review SHA 失效/canary/生产无连接 STOP | 全套绿；STOP fail-closed |
| 消融 | 单组件变更/前后差异/成本延迟（ABL-001） | 一次一组件；记录差异 |
| 真实 E2E（人工） | 2.1.216 六 Hook 事件 + 隔离 reviewer | exact commands/exit/输出/model 记录；未触发不退出 |
| 兼容性 | 46/46 规则证据/AS_BUILT 一致性 | 逐条真实证据；AS_BUILT 与代码一致 |

## 9. 周期失败与升级策略

- **真实 Claude/Hook E2E 无法触发**（最高优先级风险）：Task 6 阻塞，明确记录登录/keychain 前置未满足；
  不伪造 E2E；未触发任一 P0 事件时本周期不退出。agent 准备流程 + 证据模板，用户负责运行。
- **OS sandbox BLOCKING GAP**（Cycle 2 延续）：真实 E2E 中如能启用 sandbox 则补运行证据；否则延续标注
  PRODUCTION BLOCKER，不伪造。
- **登录/keychain 未修复**：阻塞 Task 6，不跳过。
- **门槛硬编码**：违反 EVAL-004；必须 owner 确认，不硬编码 90%。
- **评测通过宣称绝对正确**：违反 FBK-003；必须断言“不等于消除静默失败”。
- **correction 自动批准指标**：违反 SEM-003/FBK-002；阻断。
- **dev 会话注册阻断 Hook**：违反死锁教训；live 注册只在 E2E 环境。
- **领域/需求冲突**：停止，提交冲突规则、候选解释与影响范围。

## 10. 周期完成清单

实现回合只有全部项目都有真实证据时才能提交 plan-agent 评审并宣称 COMPLETE：

- [ ] 用户已批准 Cycle 5 计划与 tickets，已按选定模式加载 executing-plans。
- [ ] Task 1-7 的预期文件存在、非空、归属清楚，无计划外生产文件。
- [ ] `evaluator.py` ground-truth 隔离、逐断言、run 记录字段齐全；无 Git 用内容 hash（EVAL-001/002/003）。
- [ ] `/chatbi-evaluate` + evaluation SKILL：固定 suite、owner 确认门槛（不硬编码）、语义层命中断言（EVAL-004/005）。
- [ ] `/chatbi-correction`：双候选（fix+eval case）、owner_approved 默认 false、不自动批准指标（FBK-002，SEM-003）。
- [ ] `test_evaluation`/`test_correction`/`test_e2e`(五工作流/五压力/兼容) 全绿 + Cycle 1-4 全回归。
- [ ] FBK-003 断言（评测通过 != 绝对正确）在评测套件中。
- [ ] 消融：单组件变更 + 前后差异/成本/延迟记录（ABL-001）；负面实验清单（ABL-002）。
- [ ] canary secret/PII/绝对路径不出现在任何评测/纠正/E2E 输出。
- [ ] `rule-traceability.md` 46/46 规则逐条真实证据；HOOK-003/005 真实 E2E 后从 PARTIAL 升级。
- [ ] `evaluation.md`/`troubleshooting.md`/`negative-experiments.md`/`security.md`/`compatibility.md` 完整；
      sandbox/生产认证缺口最终标注；不把 official-only 写成 verified。
- [ ] `feature-flow-v6.md` 从代码生成，含真实行引用、分支、数据流、错误与差距（含真实 E2E 证据）。
- [ ] **真实 Claude Code 2.1.216 E2E**（人工环境门）：六 Hook 事件 + 隔离 reviewer 触发并记录 exact
      commands/exit/输出/model；未触发任一 P0 事件则不退出。
- [ ] 生产无连接 STOP 验证（无真实 managed/CLI 连接时 fail-closed）。
- [ ] plan-agent 把 `docs/technical-design.md` 更新为唯一 `STATUS: AS_BUILT`，设计-实现最终一致。
- [ ] 主编排器：文件 inventory、46/46 规则审计、报告/feature-flow-v6/AS_BUILT 一致性、原生命令证据审计
      全部打勾。
- [ ] settings.json：dev 仍 SessionStart-only；live Hook 注册只在 E2E 环境（死锁教训）。

本清单全部满足才允许宣称 Harness v1 COMPLETE。真实 Claude/Hook/sandbox E2E 与生产认证是硬门，
不得用 mock/合成替代或删除。组织 PII policy/真实 owner/真实连接未提供时标“无法生产认证”，不影响
合成正确性验收但禁止生产使用声称。
