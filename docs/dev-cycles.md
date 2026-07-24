# Claude Code ChatBI Harness v1 开发周期总计划

STATUS: PLANNED

## 1. 计划依据与使用方式

本计划把已由用户确认的 `docs/requirements.md` 与
`docs/technical-design.md` 拆成五个按依赖顺序推进、可独立验证的开发周期。领域术语、
事实层级、执行方边界和 46 条可执行规则继续以
`docs/chatbi-harness-domain-model.md` 为规范来源；本计划不修改需求或技术设计。

每个周期进入实现前都必须经过同一审批链：

1. coder-agent 为当前周期生成 `docs/dev-cycle-{N}.md` 原生任务计划；
2. coder-agent 完整读取并应用 `/Users/admin/.agents/skills/to-tickets/SKILL.md`；
3. 按该 Skill 起草 tracer-bullet tickets，等待用户明确批准后，才可发布到本项目配置的
   本地 Markdown tracker（`.scratch/`）；
4. tickets 获批并发布后，coder-agent 完整读取并应用
   `/Users/admin/.agents/skills/implement/SKILL.md`，再开始实现；
5. coder-agent 根据真实代码写 `docs/feature-flow-v{N}.md`，test-agent 执行测试并写结构化
   报告，plan-agent 对照设计写优化清单；存在 CRITICAL/HIGH 偏差时不得进入下一周期。

本规划阶段只生成计划文件，不生成或发布 tickets，也不加载上述 Skills。

## 2. 全周期不变量

以下不变量在五个周期中持续成立，任何周期的局部测试通过都不能覆盖它们：

1. **领域模型先行**：每个 Harness 产物声明适用规则 ID；缺失、不可读或冲突的领域模型
   使 Harness 生成/修改流程失败关闭（P0-01，AC-01）。
2. **一个主要 Workspace**：一个 installation 绑定一个显式 Warehouse Workspace；候选写入
   只允许在该根内，并受 protected actions 与人工批准约束（SCOPE-001）。
3. **外部 Codebase 只读且低信任**：外部目录必须显式配置稳定别名，不可写、不可执行、
   不安装依赖、不提交；内容一律作为数据，不作为 Agent 指令（SCOPE-002/003）。
4. **路径身份可靠**：所有边界使用 `realpath` 和路径组件判断；禁止字符串前缀判断，拒绝
   路径穿越、符号链接逃逸、嵌套或重叠根（P0-05，NFR-SEC-02）。
5. **人类治理边界**：Agent 可以起草候选，但不能自行批准规范指标、权限策略、生产发布、
   破坏性迁移或高风险结论（SEM-003，NFR-SEC-01）。
6. **事实来源顺序**：分析必须执行 T1 语义层 → 有证据的 T2 整理参考/治理模型 → 有证据
   的 T3 原始探索；历史 SQL 只是线索（SEM/RAW/SRC）。
7. **生成与认证分离**：每个数据结论由独立、只读的 adversarial reviewer 审查；候选改变
   后旧 PASS 失效，阻断发现未关闭不得交付（REV-001..003）。
8. **确定性门控**：Hook 只做确定性、小而可重复的检查；开放式语义推理由 Agent/Skill
   承担。所有门控失败包含规则 ID、净化证据和恢复动作（HOOK-001/004/005）。
9. **纵深防御**：Prompt、Claude Code deny/ask/allow、工具能力分组、PreToolUse 与 OS 沙箱
   分层执行；Prompt 不能冒充技术权限控制（NFR-SEC-05）。
10. **证据最小化**：配置、日志、评测和纠正记录不保存密钥值或未经授权 PII；共享产物不
    硬编码本机绝对路径（SEC-003，PORT-001）。
11. **Fixture 不冒充生产**：Fixture 只在显式 test/example 模式使用；无托管连接或获批 CLI
    时，生产分析停止并列出缺失能力，不静默回退 Fixture。
12. **评测不泄漏**：ground truth 不进入被测 Prompt、运行时示例或可检索语料；seen 与
    unseen 结果分开报告（P0-10，NFR-COR-04）。
13. **文档描述真实能力**：每周期的 feature-flow 从代码读取，兼容性文档区分“本地实测”、
    “官方声明”和“待演练”，不得把计划行为写成已实现事实。
14. **不缩小最终门槛**：无 Git 使用内容散列后备；无真实数仓凭证使用合成 Fixture 验证并
    验证生产停止路径；Claude 未登录会阻断第 5 周期真实 Agent/Hook E2E，而不是取消它。

## 3. 周期依赖与 P0 覆盖

```text
Cycle 1 骨架/诊断
   │
   ▼
Cycle 2 安全/适配器
   │
   ▼
Cycle 3 分析/独立审查
   │
   ▼
Cycle 4 模型/知识维护
   │
   ▼
Cycle 5 评测/纠正/兼容终验
```

| P0 能力 | 主交付周期 | 最终复核 |
| --- | --- | --- |
| P0-01 领域模型前置 | 1 | 5 |
| P0-02 根 `CLAUDE.md` | 1 | 5 |
| P0-03 六个 Commands | 1：init；3：analyze；4：model/knowledge；5：evaluate/correction | 5 |
| P0-04 独立审查 Agent | 3 | 5 |
| P0-05 Workspace/多 Codebase | 1：配置/路径；2：实际只读执行边界 | 5 |
| P0-06 事实来源路由 | 2：适配协议/选择；3：完整分析闭环 | 5 |
| P0-07 参考资料共置维护 | 4 | 5 |
| P0-08 确定性 Hooks | 1：诊断；2：安全；3：审查/完成；4：影响；5：终验 | 5 |
| P0-09 分析答案契约 | 3 | 5 |
| P0-10 评测与纠正 | 5 | 5 |
| P0-11 文档交付 | 1 起持续增量 | 5 |

## Cycle 1: 骨架、配置边界与初始化诊断

- Scope: 建立最小但真实可运行的纵向骨架：根契约与条件规则、共享/本地配置及明确的
  Schema 子集验证策略、`realpath` 路径边界、统一 GateError、`/chatbi-init`、
  `SessionStart` 诊断 Hook、基础文档和 unit/Hook contract tests。有效 Fixture 必须能从
  配置加载走到结构化诊断；无效领域模型、重叠根、穿越路径或安全能力缺失必须产生带
  规则 ID 与恢复建议的失败，而不是空占位符。
- Dependencies: none；但需求与技术设计必须已确认，且实现前必须完成本计划第 1 节的
  `to-tickets` 用户审批门和 `implement` Skill 加载。
- Deliverable: 在无 Git、无 Claude 登录、无真实数仓凭证的当前环境中，标准库测试可以用
  合成目录执行有效/无效初始化诊断；共享配置无机器绝对路径或密钥；根 Prompt/规则能够
  路由到 init 且不把后续未实现能力宣称为可用；兼容性文档记录真实探测结果与待演练项。
- Expected files: `CLAUDE.md`, `CONTEXT.md`, `.claude/rules/00-domain-contract.md`,
  `.claude/rules/10-security.md`, `.claude/rules/20-completion.md`,
  `.claude/commands/chatbi-init.md`, `.claude/settings.json`,
  `.claude/chatbi-harness.json`, `.claude/chatbi-harness.example.json`,
  `.claude/chatbi-harness.local.example.json`, `.claude/schemas/chatbi-harness.schema.json`,
  `.claude/lib/chatbi_harness/__init__.py`, `.claude/lib/chatbi_harness/config.py`,
  `.claude/lib/chatbi_harness/paths.py`, `.claude/lib/chatbi_harness/gates.py`,
  `.claude/hooks/session_diagnose.py`, `.claude/fixtures/config/**`,
  `tests/harness/test_config.py`, `tests/harness/test_paths.py`,
  `tests/harness/test_hooks.py`, `docs/harness/README.md`,
  `docs/harness/installation.md`, `docs/harness/configuration.md`,
  `docs/harness/compatibility.md`, `docs/harness/rule-traceability.md`,
  `docs/feature-flow-v1.md`；编排复核另产出 `docs/optimization-checklist-v1.md`、
  `docs/test-checklist-v1.md` 与 `docs/test-report-v1.md`。

### Cycle 1 进入门

- `docs/requirements.md` 为 `STATUS: REQUIREMENTS_FINALIZED`，技术设计为已由用户确认的
  `STATUS: PROPOSED`，当前 orchestrator phase 指向 Cycle planning/implementation。
- 用户已批准本周期 tickets；tickets 明确覆盖真实纵向诊断而非只有文件占位。
- 当前环境探测结果被当作输入：Claude Code 2.1.216、Python 3.14.2、Darwin arm64、无 Git、
  Claude 未登录；未知能力不得伪造为通过。
- JSON Schema 策略在 ticket 中明确：v1 使用项目所需的、明示范围的结构/跨字段校验；若
  采用零依赖实现，不得宣称完整实现 JSON Schema 标准。

### Cycle 1 退出门

- 配置、路径、GateError 和 Hook contract 目标测试全绿，且至少一条有效 init 纵向 Fixture
  与领域模型缺失、重叠根、符号链接/穿越、安全能力缺失等负例可重复。
- `SessionStart` 映射通过离线事件 Fixture；兼容性文档明确它尚未等同真实 Claude Hook E2E。
- 根契约保持路由职责，约 200 行预算不被领域知识堆积突破；所有新增 Harness 文档包含实质
  契约、当前能力或恢复说明，不允许空 `TODO`/“待实现”页冒充交付。
- AC-01 已有可执行初始追踪；AC-02 的骨架部分、AC-03 的路径部分、AC-08 的探测部分有命令
  和测试证据。Cycle 1 对应规则无 CRITICAL/HIGH 设计偏差。
- `docs/feature-flow-v1.md` 与真实文件/调用链一致；test-agent 报告目标测试通过；plan-agent
  优化清单为 `STATUS: CONVERGED` 后才能进入 Cycle 2。

### Cycle 1 对应 AC / 规则族

- AC: AC-01；AC-02（根契约、init、配置/检查器、基础文档）；AC-03（路径规范化/重叠/符号
  链接基础）；AC-08（能力探测与 SessionStart 契约）；AC-09（本周期测试与清单一致）。
- 规则族: SCOPE-001/003，SEC-001/003，PORT-001，HOOK-001..004；根契约同时声明全部规则族
  的路由与后续完成门，但不冒充后续执行已实现。

### Cycle 1 目标测试

- `python3 -m unittest tests.harness.test_config tests.harness.test_paths tests.harness.test_hooks`
- 配置正/反例：共享+本地合并、未知/缺失字段、凭证值/绝对共享路径、owner/threshold 关系。
- 路径正/反例：未配置路径、`..`、相似字符串前缀、重叠根、外部位于 Workspace 内、
  Workspace 位于外部内、符号链接越界、无 Git 内容散列引用。
- Hook contract：有效/恶意/超大/畸形 JSON、cwd 不匹配、异常 fail-closed、错误中
  `rule_ids/evidence_refs/reason/recovery` 齐全且无绝对路径/secret canary。
- 静态检查：预期文件存在且非空；Harness 产物引用领域模型；共享产物无本机绝对路径、密钥
  值；计划外实现文件有明确设计依据。

## Cycle 2: 安全纵深、只读 Codebase 与适配器选择

- Scope: 在 Cycle 1 边界库上实现 policy、平台中立 adapter protocol、显式 Fixture、
  managed → approved CLI → STOP 选择链、只读 `codebase_reader`、`PreToolUse` 与
  `ConfigChange` 门控，并把 Claude permissions 与 OS sandbox 默认值映射到 settings。通过
  独立测试分别证明权限层和沙箱层能阻断外部写/执行；生产路径绝不静默采用 Fixture。
- Dependencies: Cycle 1 已 CONVERGED；实现前完成本周期 `to-tickets` 用户审批门并加载
  `implement`。
- Deliverable: 外部 Codebase 仅能按显式别名执行 read/search/stat/git-metadata，并返回
  alias + relative path + revision；Prompt 注入和执行/安装/提交企图被拒绝。适配器在 managed
  不可用时只尝试获批 CLI，两者均不可用时结构化停止；Fixture 只在 test/example flag 下运行。
- Expected files: `.claude/lib/chatbi_harness/policy.py`,
  `.claude/lib/chatbi_harness/adapters/__init__.py`,
  `.claude/lib/chatbi_harness/adapters/base.py`,
  `.claude/lib/chatbi_harness/adapters/fixture.py`,
  `.claude/lib/chatbi_harness/adapters/codebase_reader.py`,
  `.claude/hooks/pretool_guard.py`, `.claude/hooks/config_change_gate.py`,
  `.claude/fixtures/semantic-catalog.json`, `.claude/fixtures/warehouse.json`,
  `.claude/fixtures/codebases/**`, `tests/harness/test_adapters.py`,
  `tests/harness/test_security.py`, Cycle 1 配置/settings/Hook tests 的增量更新，
  `docs/harness/security.md`, `docs/harness/compatibility.md`,
  `docs/harness/rule-traceability.md`, `docs/feature-flow-v2.md`；编排复核产物为
  `docs/optimization-checklist-v2.md`、`docs/test-checklist-v2.md`、`docs/test-report-v2.md`。

### Cycle 2 进入门

- Cycle 1 退出门全部满足，且路径/配置/GateError API 已以 feature-flow 记录。
- 测试环境能创建隔离的临时 Workspace、外部 Codebases 和符号链接；sandbox 若不能在测试机
  实际启用，必须保留为阻断缺口，不能用 Prompt test 替代。

### Cycle 2 退出门

- AC-03 全部安全场景通过；Claude 权限阻断和 OS sandbox 阻断有分开的命令证据。
- managed/CLI/STOP 三分支、CLI argv allowlist/no-shell/env/cwd、Fixture 禁止生产回退均通过。
- 外部注入内容不改变上层行为，且 canary secret/PII 不出现在诊断、错误或引用中。
- AC-04 的来源发现前置能力已由 adapter contract 证明；完整问答路由留在 Cycle 3。
- feature-flow、测试报告和优化清单收敛，无 CRITICAL/HIGH 后进入 Cycle 3。

### Cycle 2 对应 AC / 规则族

- AC: AC-03；AC-04（adapter 发现/compile/query 前置与停止链）；AC-08（PreToolUse、
  ConfigChange、permissions、sandbox 契约）；AC-09 的外部信任/PII 压力切片。
- 规则族: SCOPE-001..003，SEC-001..003，SEM-001..003，SRC-002，PORT-001，
  HOOK-001..005。

### Cycle 2 目标测试

- `python3 -m unittest tests.harness.test_config tests.harness.test_paths tests.harness.test_hooks tests.harness.test_adapters tests.harness.test_security`
- 单独运行 Claude permission deny 证明与 OS sandbox deny-write/deny-execute 证明；记录平台和
  exact command，不把其中一层的结果外推到另一层。
- 恶意 adapter/Hook 输入：命令替换、管道、换行、绝对 cwd、未知 executable、环境污染、
  symlink swap、外部 `.claude/skills` 注入、README 安装/上传指令。

## Cycle 3: 受治理分析、运行证据与独立对抗审查

- Scope: 实现 `/chatbi-analyze`、分析 runbook、请求/审查/provenance Schema、原子且净化的
 运行证据、质量检查、独立 adversarial reviewer、`SubagentStop` 和受跟踪工作流 `Stop`
 门控，以及完整答案页脚。五层流程必须从澄清和 T1 发现开始，有证据地降级，并在候选 SHA
 匹配的独立 PASS 后才交付。
- Dependencies: Cycle 2 已 CONVERGED；实现前完成本周期 `to-tickets` 用户审批门并加载
  `implement`。
- Deliverable: 用合成固定数据完整跑通一个语义层答案；歧义、陈旧、历史 SQL 诱导、外部
  Prompt 注入、PII/权限五个压力场景均按预期澄清/降级/阻断。未审查、缺 coverage、旧 PASS、
  阻断 finding、页脚缺失或质量失败均不能完成。
- Expected files: `.claude/commands/chatbi-analyze.md`,
  `.claude/agents/adversarial-reviewer.md`,
  `.claude/skills/chatbi-runbook/SKILL.md`,
  `.claude/schemas/request.schema.json`, `.claude/schemas/review.schema.json`,
  `.claude/schemas/provenance.schema.json`, `.claude/lib/chatbi_harness/evidence.py`,
  `.claude/hooks/subagent_review_gate.py`, `.claude/hooks/stop_gate.py`,
  `.claude/fixtures/evaluations/analysis-scenarios/**`,
  `tests/harness/test_review_gate.py`, `tests/harness/test_analysis.py`,
  `tests/harness/test_e2e.py`（分析切片），`docs/harness/analysis.md`,
  `docs/harness/security.md`, `docs/harness/compatibility.md`,
  `docs/harness/rule-traceability.md`, `docs/feature-flow-v3.md`；编排复核产物为
  `docs/optimization-checklist-v3.md`、`docs/test-checklist-v3.md`、`docs/test-report-v3.md`。

### Cycle 3 进入门

- Cycle 2 的 read-only adapter、source capability、policy 和 PreToolUse 已有独立安全证据。
- 审查者最小工具和权限可按当前 Claude 版本的已确认格式配置。本周期验证 Prompt、Schema、
  gate 和合成分析闭环；真实 Claude reviewer/Hook 进程演练是 Cycle 5 明示的兼容终验目标。

### Cycle 3 退出门

- AC-04、AC-06 完整通过；P0-09 每个字段均由 Schema/Stop gate/测试覆盖。
- reviewer Prompt 在隔离上下文自包含 SCOPE/SEC/REV/ANS、事实层级和停止条件，且无 Bash、
  Write、Edit、Agent 或 mutating MCP。
- 五个压力场景的自动 Fixture 全绿；候选变更使旧 review SHA 失效并强制新轮次。
- 文档不得把离线 reviewer contract 测试写成真实 Claude reviewer 已运行；该 live E2E 要求必须
  保留在 Cycle 5 退出门，不能因当前未登录而删除或替换成 mock。
- 本周期 CRITICAL/HIGH 已关闭，feature-flow/测试报告/优化清单一致。

### Cycle 3 对应 AC / 规则族

- AC: AC-04，AC-06，AC-08（隔离 reviewer/Hook 契约），AC-09 五压力场景分析切片。
- 规则族: REQ-001..004，SEM-001..003，RAW-001..003，SRC-001..002，QLT-001，
  REV-001..003，ANS-001..003，SCOPE/SEC，HOOK-001..005。

### Cycle 3 目标测试

- Cycle 2 全回归，加
  `python3 -m unittest tests.harness.test_review_gate tests.harness.test_analysis tests.harness.test_e2e`。
- reviewer contract：11 项 coverage、finding 结构、PASS/BLOCKED/ERROR、run/round/SHA 绑定、
  输出净化、缺失证据阻断、递归停止保护。
- E2E 分析：T1 命中；T1 未覆盖有证据到 T2；T2 不足有证据到 T3；无证据绕过失败；
  质量、观察/解释、局限、owner、新鲜度、置信度、复核提示完整。

## Cycle 4: 模型维护、知识共置与变更影响门控

- Scope: 实现模型维护和知识维护 Commands、knowledge/maintenance Skills、领域参考模板与
  fixture-domain、影响清单、`PostToolUse` 影响记录、模型—metadata—semantic—reference—
  Skill—tests—downstream—eval 同步门控。知识资料必须适合路由且包含“用于/不得用于”；
  规范指标/权限/发布/破坏性迁移继续要求人批。
- Dependencies: Cycle 3 已 CONVERGED；实现前完成本周期 `to-tickets` 用户审批门并加载
  `implement`。真实 Claude E2E 按既定计划在 Cycle 5 执行，不属于 Cycle 3 的未关闭偏差。
- Deliverable: 一个真实合成模型变更能生成 impact manifest、候选代码/metadata/reference/
  eval 变更和测试证据；只改模型时 Stop gate 失败，同步完成且受影响测试/评测存在时通过。
  知识模板通过必填字段、绝对路径、冲突/过期与路由 tests。
- Expected files: `.claude/commands/chatbi-maintain-model.md`,
  `.claude/commands/chatbi-maintain-knowledge.md`,
  `.claude/skills/chatbi-knowledge/SKILL.md`,
  `.claude/skills/chatbi-knowledge/references/_template.md`,
  `.claude/skills/chatbi-knowledge/references/fixture-domain.md`,
  `.claude/skills/chatbi-maintenance/SKILL.md`, `.claude/hooks/posttool_impact.py`,
  `tests/harness/test_maintenance.py`, `tests/harness/test_knowledge.py`,
  `tests/harness/test_e2e.py`（维护/知识切片），`docs/harness/maintenance.md`,
  `docs/harness/knowledge-authoring.md`, `docs/harness/rule-traceability.md`,
  `docs/feature-flow-v4.md`；编排复核产物为 `docs/optimization-checklist-v4.md`、
  `docs/test-checklist-v4.md`、`docs/test-report-v4.md`。

### Cycle 4 进入门

- Cycle 3 的 evidence/current-run/Stop gate API 稳定，且分析闭环不会因维护扩展而被绕过。
- ticket 明确一个纵向模型变更 Fixture 及其预期同步集；不允许只实现通用空模板。

### Cycle 4 退出门

- AC-05 全部通过；模型单改阻断、完整同步通过、protected action 未审批阻断均有证据。
- reference template 包含业务上下文、粒度、标准过滤、维度、关键模型、范围/排除、连接、
  易错点、最佳实践、交叉引用、owner、新鲜度和用于/不得用于。
- `PostToolUse` 只记录已发生影响，不声称撤销；安全首防仍是 Cycle 2 PreToolUse/sandbox。
- 分析全回归通过，feature-flow/测试报告/优化清单一致，无 CRITICAL/HIGH 后进入 Cycle 5。

### Cycle 4 对应 AC / 规则族

- AC: AC-02（model/knowledge Commands 与知识模板），AC-05，AC-09 维护场景。
- 规则族: SEM-003，DOC-001..005，PORT-001，SRC-001..002，EVAL-001..005（受影响证据
  协调），ABL-001..002，HOOK-001/004/005。

### Cycle 4 目标测试

- 前三周期全回归，加
  `python3 -m unittest tests.harness.test_maintenance tests.harness.test_knowledge tests.harness.test_e2e`。
- 影响矩阵分支：model/column/semantic/reference/Skill/downstream/eval；证据充分、证据缺失、
  不确定影响告警显式处置、P0 eval 失败、protected action 未批准。
- 知识 lint/retrieval：必填元数据、用于/不得用于、机器绝对路径、重复/冲突、历史 SQL
  `candidate_only`、相邻领域交叉引用。

## Cycle 5: 评测、纠正闭环、全量兼容演练与交付收敛

- Scope: 实现 evaluate/correction Commands、evaluation Skill、evaluation/correction Schema、
  evaluator、ground-truth 隔离 runner、固定 suite、双候选纠正、基线/消融/负面实验记录，补齐
  故障排查和全规则追踪；运行全测试、五压力场景、六 Commands 与真实 Claude Code 2.1.216
  Agent/Hook E2E。所有事实文件、feature-flow、报告和技术设计 AS_BUILT 必须最终一致。
- Dependencies: Cycle 4 已 CONVERGED；实现前完成本周期 `to-tickets` 用户审批门并加载
  `implement`。真实 Claude E2E 前需修复登录/keychain 运行前置；不需要用真实组织数据替代
  固定合成 Fixture，但必须验证无真实连接时生产路径明确 STOP。
- Deliverable: 高频/长尾和五压力场景的固定评测可重复，ground truth 对被测会话不可读，
  run 记录版本/hash/model/assertion/token/time；一条纠正同时生成修复与评测候选且不自动批准
  指标。全量测试绿色，2.1.216 上真实 reviewer 与 Hook 事件 E2E 通过，兼容/限制文档完整，
  46/46 规则具有真实文件/测试证据。
- Expected files: `.claude/commands/chatbi-evaluate.md`,
  `.claude/commands/chatbi-correction.md`, `.claude/skills/chatbi-evaluation/SKILL.md`,
  `.claude/schemas/evaluation.schema.json`, `.claude/schemas/correction.schema.json`,
  `.claude/lib/chatbi_harness/evaluator.py`, `.claude/fixtures/evaluations/**`,
  `tests/harness/test_evaluation.py`, `tests/harness/test_correction.py`,
  `tests/harness/test_e2e.py`（五工作流/五压力场景/兼容切片），
  `docs/harness/evaluation.md`, `docs/harness/troubleshooting.md`,
  `docs/harness/negative-experiments.md`, `docs/harness/compatibility.md`,
  `docs/harness/rule-traceability.md`, `docs/feature-flow-v5.md`；编排复核产物为
  `docs/optimization-checklist-v5.md`、`docs/test-checklist-v5.md`、
  `docs/test-report-v5.md`，最终 plan-agent 更新 `docs/technical-design.md` 为 AS_BUILT。

### Cycle 5 进入门

- Cycle 4 无 CRITICAL/HIGH，全部本地 Fixture 回归可运行；真实 Claude E2E 作为本周期既定
  目标仍在 compatibility/验收矩阵中明确追踪，未被静默删除。
- 用户批准本周期 tickets；评测 ticket 明确 ground truth 物理/逻辑隔离、seen/unseen、稳定
  快照和“不等于消除静默失败”的断言。

### Cycle 5 退出门

- AC-01..AC-09 全部有强证据；六 Commands、独立 reviewer、全部确定性 Hooks、文档和配置
  文件存在且按真实行为验证。
- 完整 `python3 -m unittest discover -s tests -p 'test_*.py'` 绿色；五压力场景、维护单改、
  review SHA 失效、泄漏 canary、生产无连接 STOP 均在全套中。
- 实际登录的 Claude Code 2.1.216 在 Darwin arm64 上触发真实 SessionStart、PreToolUse、
  PostToolUse、SubagentStop、Stop、ConfigChange 契约和隔离 reviewer；记录 exact commands、
  exit/输出和模型信息。未登录或无法触发任一 P0 事件时，本周期不能退出。
- 组织 PII policy、真实 owner、真实连接或发布门槛仍未提供时，文档/诊断明确标记
  “无法生产认证”；Fixture 结果不冒充组织验收。此状态不影响 Harness 的合成正确性验收，
  但禁止生产使用声称。
- plan-agent 完成最终设计—实现比较且无 CRITICAL/HIGH，test-agent 报告 ALL_PASSED；随后
  plan-agent 按真实代码把 `docs/technical-design.md` 更新为唯一 `STATUS: AS_BUILT`。
- 主编排器执行文件 inventory、规则 46/46、报告/feature-flow/AS_BUILT 一致性和原生命令
  证据审计，所有 mandatory completion checklist 项打勾后才可宣称 COMPLETE。

### Cycle 5 对应 AC / 规则族

- AC: AC-07；AC-08 全量真实演练；AC-09；AC-01..06 最终回归与清单一致性。
- 规则族: EVAL-001..005，ABL-001..002，FBK-001..003，SEC-003，SEM-003，
  HOOK-001..005，并最终复核全部 46 条规则。

### Cycle 5 目标测试

- `python3 -m unittest discover -s tests -p 'test_*.py'`
- 固定 suite：高频、长尾、五压力场景、维护同步、审查复用、PII/secret canary；动态行为只
  评分 query/entity/contract，固定数字锚定合成快照。
- 泄漏验证：ground truth 目录 deny-read；问题可见但答案/SQL 不在 Prompt、Skill、reference
  或 runtime verified examples；seen/unseen 分开。
- 真实 Claude 命令：实现阶段依据 installation/compatibility 文档记录准确命令，不在计划中
  伪造未执行结果；覆盖 doctor、agent tool list、六 Commands、全部 Hook 事件和 sandbox。

## 4. 跨周期验收与变更控制

- 若实现发现需求/领域语义缺失，只允许记录偏差并交回 plan-agent/用户裁决；coder-agent 不在
  代码中创造新的指标或权限语义。
- 新增文件必须归属某一周期 Deliverable、规则或测试；无法归属的复杂能力不进入 P0。
- 后续周期可增量修改早期文件，但必须在该周期 feature-flow 与 Expected files 中记录，且
  全量回归证明未破坏早期退出门。
- 每周期使用本地 Markdown tracker 只是实施审批与追踪机制；`.scratch/` tickets 不是 Harness
  运行时产物，不代替 `docs/` handoff contract。
- 本计划的 `STATUS: PLANNED` 仅表示周期规划就绪，不表示任何 Harness 实现或测试已完成。
