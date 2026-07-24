# Development Cycle 2 原生任务计划：安全纵深、只读 Codebase 与适配器选择

STATUS: PLANNED

## 1. 周期目标

在 Cycle 1 已 CONVERGED 的边界库（`EffectiveConfig`、`resolve_path_reference`、`GateError`、
`run_init_diagnostic`、`SessionStart` Hook）上，交付安全纵深与来源前置的纵向切片：把 Cycle 1
的点时路径检查升级为每次工具调用前的连续门控，把外部 Codebase 从“可校验身份”推进到“只读可
检索且内容即数据”，把适配器从“配置声明”推进到“平台中立协议 + managed→approved CLI→STOP
选择链 + 显式 Fixture”，并把 Claude permissions 与 OS sandbox 默认值映射到 settings。

本周期必须用独立测试分别证明 Claude 权限层与 OS 沙箱层能阻断外部写/执行；生产路径绝不静默
采用 Fixture。它不是只写接口骨架、永远 PASS 的 policy、把 Fixture 当生产后备、或用 Prompt
test 替代真实沙箱证据。

### 1.1 成功结果

- `policy.py` 对 `EffectiveConfig` 给出确定性的访问/PII/风险/审批/工具能力判定；权限或 PII 策略
  缺失时停止并给出最小授权需求，protected action 不可由 Agent 自批。
- `adapters/base.py` 定义平台中立 `Adapter` 协议（`capabilities`/`healthcheck`/`discover`/
  `compile`/`query`/`quality`/`lineage`）与带 Schema 的证据模型；`adapters/__init__.py` 实现选择
  链 managed→approved CLI→STOP，Fixture 仅在 `fixture_enabled=true` 且运行标记 test/example 时可用。
- `adapters/fixture.py` 在显式 test flag 下返回固定 `semantic-catalog.json`/`warehouse.json` 证据；
  生产模式调用 Fixture 确定性失败。
- `adapters/codebase_reader.py` 对显式别名只提供 `read/search/stat/git-metadata`，返回
  `{alias, relative_path, revision}` 引用并把文件内容包装为不可信数据；README/注释中的执行、安装、
  上传、提交指令被忽略并记录为被拒候选。
- `pretool_guard.py` 是 `PreToolUse` 薄入口：复用 `paths`/`policy`/`gates`，对 cwd、tool_name、
  tool_input 做路径/只读/执行/网络预检；失败 exit 2 + 规则 ID + 净化证据 + 恢复动作。
- `config_change_gate.py` 是 `ConfigChange` 薄入口：重验 Schema、路径、沙箱与权限边界；managed
  policy 变更不假定可阻断时给出明确反馈。
- `.claude/settings.json` 在 Cycle 1 `SessionStart` 之上增量映射 `PreToolUse`、`ConfigChange`、
  permissions（deny→ask→allow）与 sandbox 默认值；不含机器路径或密钥。
- `test_security.py` 用分开的命令证据分别证明 Claude permission deny 阻断与 OS sandbox
  deny-write/deny-execute 阻断；二者不互相外推。
- `test_adapters.py` 证明 managed 不可用→只试获批 CLI→均不可用 STOP 三分支、CLI argv allowlist/
  no-shell/env/cwd、Fixture 禁止生产回退、codebase 只读边界与 Prompt 注入拒绝。
- `docs/harness/security.md`、`compatibility.md`、`rule-traceability.md` 反映 Cycle 2 真实能力，
  并把真实沙箱运行证据缺失、managed runtime 缺失标为阻断缺口/NOT YET EXERCISED，不用 Prompt test
  或 official-only 替代运行证据。

## 2. 非目标

Cycle 2 明确不实现以下内容，但不能从最终范围删除：

- 不实现完整分析问答闭环（澄清、实体解析、质量检查、对抗性 reviewer、SubagentStop/Stop、答案
  页脚）；属于 Cycle 3。本周期只交付 adapter discover/compile/query 的**前置协议与停止链**。
- 不实现模型/知识维护、PostToolUse 影响图、DOC-004 同步门控、领域 reference 模板；属于 Cycle 4。
  DOC-004（修改模型时检查 Skill/reference/评测/下游同步）是 Cycle 4 规则，本周期只铺设 Hook 基础
  设施（薄入口 + GateError 复用 + settings 映射模式），不实现影响检查本身。
- 不实现离线评测、纠正闭环、消融、真实 Claude 全事件 E2E；属于 Cycle 5。
- 不连接真实 managed/CLI 数据源、不修复 Claude 登录、不生成组织 PII policy/owner/threshold、不
  批准 protected action、不把 Fixture 结果当生产认证。
- 不启用 `read_mode=direct_add_dir`；该模式若后续启用必须作为单独安全偏差批准与测试。
- 不把真实 OS sandbox 运行证据伪造为通过。沙箱若在测试机无法实际启用，保留为阻断缺口。

## 3. 前置证据

实现者在创建 tickets 前必须重新确认以下证据仍成立：

| 证据 | 当前权威来源 | Cycle 2 用法 |
| --- | --- | --- |
| Cycle 1 退出门已 CONVERGED | `docs/feature-flow-v2.md`、`docs/optimization-checklist-v2.md` | 路径/配置/GateError/SessionStart API 稳定可扩展 |
| 46 条领域规则与信任层级 | `docs/chatbi-harness-domain-model.md` 第 7–10 节 | policy/adapter/PreToolUse/ConfigChange 的规则 ID 来源 |
| Cycle 2 范围与 AC | `docs/dev-cycles.md` 157–209；`docs/requirements.md` AC-03/04/08/09 | 定义本周期交付与不能提前声称的能力 |
| 适配器协议/选择链/工具组 | `docs/technical-design.md` 第 8、11、13 节 | adapter base、选择链、PreToolUse/ConfigChange 映射 |
| Hook 事件映射与退出语义 | `docs/technical-design.md` 第 11 节；`docs/harness/compatibility.md` | PreToolUse/ConfigChange 契约；不外推真实 E2E |
| Cycle 1 已知差距 | `docs/feature-flow-v2.md` §9 第 2/4/5/6/7 项 | TOCTOU、sandbox、adapter、Codebase 读取、PreToolUse |
| 当前本地基线 | `docs/technical-design.md` 第 3 节 | 2.1.216 / Python 3.14.2 / Darwin arm64 / no Git / not logged in |
| 编排批准状态 | `docs/orchestrator-state.md` | cycle_2_planning；用户审批门在前 |

**范围澄清（DOC-004）**：`docs/dev-cycles.md` Cycle 2 规则族为 SCOPE-001..003、SEC-001..003、
SEM-001..003、SRC-002、PORT-001、HOOK-001..005，**不含 DOC-004**。DOC-004 属 Cycle 4。编排指令
提及“DOC-004”指 Cycle 2 Hook 基础设施是 Cycle 4 PostToolUse 的前置，本周期不实现 DOC-004 本身。

**版本号澄清（feature-flow）**：`docs/dev-cycles.md` Cycle 2 expected files 列 `docs/feature-flow-v2.md`，
但 v2 已被 Cycle 1 修正版占用。按 `CLAUDE.md` “版本号每次迭代 +1、不覆盖旧版本”，Cycle 2 feature-flow
为 `docs/feature-flow-v3.md`。

实现开始时还需用只读命令复核：

```text
claude --version
claude doctor
python3 --version
uname -s -m
python3 -B -m unittest discover -s tests/harness -p 'test_*.py'
find .claude/lib/chatbi_harness .claude/hooks .claude/fixtures tests/harness -type f -print
```

`claude doctor` 未登录/keychain 限制返回非零时记录为诊断事实；它只阻断要求真实 Claude 会话的验收
（含真实 sandbox 运行证据），不阻断本周期离线 Fixture 与权限层证据。沙箱运行证据缺失必须保留为
阻断缺口，不得用 Prompt test 替代（见 §9）。

## 4. 实现前强制审批门

以下步骤属于后续实现回合，本规划回合不执行：

1. 用户审批本计划与 `.scratch/chatbi-harness-cycle-2/issues/*.md` tickets；未经批准不得写实现代码。
2. coder-agent 完整读取并应用 `~/.codex/superpowers/skills/executing-plans/SKILL.md`（新功能开发延续）。
3. 按获批 ticket 依赖链实施：`01` -> `02` -> `{03, 04}`；`01` -> `05` -> `06`；`{03, 04, 06}` -> `07`。
   05 只依赖 01（PreToolUse 复用 Cycle 1 `paths` + Ticket 01 `policy`，不消费 codebase_reader，可与
   02/03/04 并行）；07 依赖全部叶子 ticket 以覆盖 feature-flow 与全回归。每个 ticket 完成跑其目标测试。
4. coder-agent 从真实代码写 `docs/feature-flow-v3.md`，test-agent 执行测试写结构化报告，plan-agent
   对照设计写优化清单；存在 CRITICAL/HIGH 偏差时不得进入 Cycle 3。

## 5. 文件所有权

每个实现文件只有一个首要任务所有者。后续任务可消费其公开 API 并向共享测试文件**追加**用例，但
不得重写另一任务已交付的文件或测试用例。

| Task | 唯一首要文件所有权 |
| --- | --- |
| 1 policy 原语 | `.claude/lib/chatbi_harness/policy.py`；`tests/harness/test_security.py`（创建文件，policy 单元用例段） |
| 2 适配器协议与选择链 | `.claude/lib/chatbi_harness/adapters/__init__.py`；`.claude/lib/chatbi_harness/adapters/base.py`；`tests/harness/test_adapters.py`（创建文件，协议+选择链用例段） |
| 3 显式 Fixture 适配器与数据 | `.claude/lib/chatbi_harness/adapters/fixture.py`；`.claude/fixtures/semantic-catalog.json`；`.claude/fixtures/warehouse.json`；`tests/harness/test_adapters.py`（追加 fixture 用例段） |
| 4 只读 codebase_reader 与合成外部树 | `.claude/lib/chatbi_harness/adapters/codebase_reader.py`；`.claude/fixtures/codebases/**`；`tests/harness/test_adapters.py`（追加 codebase 用例段） |
| 5 PreToolUse 门控与 settings 权限/沙箱默认 | `.claude/hooks/pretool_guard.py`；`.claude/settings.json`（PreToolUse + permissions + sandbox 段，在 Cycle 1 SessionStart 之上增量）；`tests/harness/test_security.py`（追加 PreToolUse + 权限层/沙箱层分开证明用例段） |
| 6 ConfigChange 门控 | `.claude/hooks/config_change_gate.py`；`.claude/settings.json`（追加 ConfigChange 段）；`tests/harness/test_security.py`（追加 ConfigChange 用例段） |
| 7 文档、追踪与周期证据 | `docs/harness/security.md`；`docs/harness/compatibility.md`（增量更新）；`docs/harness/rule-traceability.md`（增量更新）；`docs/feature-flow-v3.md`（仅实现后从代码生成） |

`.claude/settings.json` 跨 Task 5/6 共同编辑：Task 5 拥有 `SessionStart`（Cycle 1 既有）+ `PreToolUse`
+ `permissions` + `sandbox` 段；Task 6 追加 `ConfigChange` 段。两任务不得修改对方已交付段。

plan-agent/test-agent 的 `docs/optimization-checklist-v3.md`、`docs/test-checklist-v3.md`、
`docs/test-report-v3.md` 不归 coder-agent 修改。

## 6. 任务顺序

### Task 1：policy 原语（访问/PII/风险/审批/工具能力）

#### 行为

1. `policy.py` 只接受 `EffectiveConfig` 与显式请求上下文（请求类型、目标实体、用户/角色、决策用途），
   返回不可变 `PolicyDecision`（pass/warn/block + rule_ids + evidence_refs + reason + recovery），复用
   `gates.py` 的 `GateDecision` 与净化，不发明第二套错误协议。
2. 实现访问预检：权限不足时停止并给最小授权需求（SEC-001）；PII 策略缺失或受限披露策略要求 sql_only
   时不返回结果/样本，只给可运行 SQL 指引（SEC-002）；protected action（approve_metric 等）只能由
   人类 owner 批准，Agent 起草不等于批准（SEM-003）。
3. 实现工具能力组判定（对应 technical-design §8.3）：`discover_read`/`query_read`/`codebase_read`/
   `workspace_candidate_write`/`mutate_warehouse`/`network` 的允许/拒绝；`mutate_warehouse` 默认禁用，
   `network` 默认拒绝并按适配器声明域放行，deny 优先。
4. 实现风险分类（high_risk_classes：executive/regulated_or_pii/core_finance/raw_exploration/
   freshness_unknown）：命中高风险类时返回需人类签字/复核的判定，不自动提权。
5. 不做开放式业务术语解释（HOOK-001）；术语解析留给 Cycle 3 Agent/Skill。policy 只做确定性字段比对。

#### 测试/验证

- `python3 -B -m unittest tests.harness.test_security.py`（policy 用例段）
- 权限不足→block + 最小授权；PII 缺失→block；sql_only→不返回样本只给 SQL 指引；protected action
  Agent 起草→block 等待 owner；mutate_warehouse→block；network 默认 deny；高风险类→warn 需签字。
- 同一输入产生稳定 `PolicyDecision`；错误经 `GateDecision` 净化，无密钥/PII/绝对路径泄漏（canary 回归）。
- policy 不读外部 Codebase、不开 shell、不执行子进程。

#### 失败处理

- 组织 PII/owner/threshold 缺失：保持字段未配置并返回 block/warn，不填假值。
- policy 字段与 technical-design 工具组冲突：停止并记录偏差交 plan-agent，不通过宽松解析隐藏。
- 标准库不足以表达已声明判定：缩小声明子集只能在 不削弱 SEC/SEM MUST 时进行；否则记录偏差。

#### 规则 / AC

- SEC-001、SEC-002、SEC-003、SEM-003；AC-03（权限/PII 停止且不给出受限结果）、AC-04（Agent 不得
  自动批准新规范指标）；HOOK-001（确定性 only）；NFR-SEC-03/04。

### Task 2：平台中立适配器协议与选择链

#### 行为

1. `adapters/base.py` 定义平台中立 `Adapter` 协议：`capabilities() -> {discover,query,quality,lineage,
   mutate?}`、`healthcheck(context) -> HealthResult`、`discover/compile/query/quality/lineage`，所有结果
   为带 Schema 的 JSON 证据（adapter ID、时间、来源、状态、错误类别、内容散列）。不得把工具 stdout
   直接拼入 Shell 或系统提示（technical-design §8.1）。
2. `adapters/__init__.py` 实现选择链 dispatcher（technical-design §8.2）：managed 可用且获授权→使用；
   否则 approved CLI 可用、argv 合法且获授权→使用；否则 STOP 列出缺失能力与最小授权。Fixture 仅在
   `fixture_enabled=true` 且运行标记 test/example 时可用，绝不静默作为生产后备。
3. CLI 必须以 argv 数组直接启动，禁止 Shell 字符串/管道/重定向/命令替换；可执行文件解析到 allowlist
   绝对路径，cwd 固定为 Workspace，环境变量按白名单构造（无 `--token`/`--api-key` 等敏感 flag，复用
   Cycle 1 `config.py` 的 `_contains_secret_argv` 语义）。
4. managed 分支无真实 managed runtime：标 official-only / NOT YET EXERCISED，不伪造可用；CLI 分支可用
   fake approved CLI 验证；STOP 分支可验证；Fixture 分支只在 test flag 验证。
5. 不含机器绝对路径（PORT-001）：adapter ID 形如 `managed:semantic`/`cli:query`/`fixture:semantic`，
   与 Cycle 1 `_ADAPTER_ID` 一致。

#### 测试/验证

- `python3 -B -m unittest tests.harness.test_adapters.py`（协议+选择链用例段）
- managed 不可用→只试 CLI→均不可用 STOP 三分支；STOP 输出缺失能力与最小授权；Fixture 在非 test
  模式被拒绝；CLI argv 含 shell 元字符/换行/命令替换/`--token` 被拒绝；可执行不在 allowlist 被拒绝；
  cwd/env 被强制。
- 证据 JSON 含 adapter ID/时间/来源/状态/内容散列；无 stdout 直接拼入提示；无机器绝对路径泄漏。

#### 失败处理

- managed runtime 不可用：标 official-only，不伪造；选择链继续试 CLI。
- CLI 可执行无法解析到 allowlist：STOP，不回退 shell。
- 证据 Schema 与 technical-design §8.1 冲突：停止并记录偏差。

#### 规则 / AC

- SEM-001（discover/compile 前置协议）、SEM-002（catalog 证据含 metrics/dimensions/segments 的契约
  定义）、PORT-001（选择链、无机器路径）、AC-04（adapter 发现/compile/query 前置与停止链）；
  HOOK-001（dispatcher 确定性）；NFR-SEC-02/04。

### Task 3：显式 Fixture 适配器与数据

#### 行为

1. `adapters/fixture.py` 实现 `Adapter` 协议，只在 `fixture_enabled=true` 且运行标记 test/example 时
  可用；生产模式（无 test flag）调用 Fixture 确定性 block，规则 ID PORT-001，恢复动作明示“显式启用
  test 模式或配置真实 adapter”。
2. `discover` 返回 `.claude/fixtures/semantic-catalog.json` 证据，必含 metrics/dimensions/segments
  （SEM-002）；`query` 返回 `.claude/fixtures/warehouse.json` 固定合成快照；`quality`/`lineage` 返回
  固定证据。所有证据标记 `evidence_source=fixture`，不冒充 local_probe。
3. Fixture 数据无组织事实、无真实密钥/PII、无机器绝对路径；数字锚定合成快照，不随当前日期漂移。
4. `semantic-catalog.json` 与 `warehouse.json` 是显式 test 数据，不进入被测 Prompt/可检索语料（为
  Cycle 5 评测泄漏隔离预留，本周期只保证不含 secret canary）。

#### 测试/验证

- `python3 -B -m unittest tests.harness.test_adapters.py`（追加 fixture 用例段）
- test 模式下 discover/query/quality/lineage 返回稳定证据；生产模式 Fixture block；catalog 含
  metrics/dimensions/segments；warehouse 快照稳定；证据标记 fixture；无 secret canary/绝对路径。

#### 失败处理

- Fixture 数据缺 metrics/dimensions/segments：补齐，不放宽 SEM-002。
- Fixture 被生产路径调用：必须 block，不得降级为 warn 让测试变绿。

#### 规则 / AC

- SEM-002、PORT-001（Fixture 不冒充生产）、SEC-003（无 secret/PII）；AC-04（Fixture 仅显式测试）。

### Task 4：只读 codebase_reader 与合成外部树

#### 行为

1. `adapters/codebase_reader.py` 对显式别名只提供 `read/search/stat/git-metadata`（SCOPE-002）；无
   execute/write/install/commit 接口。复用 Cycle 1 `resolve_path_reference` 做路径身份与 portable
   引用，返回 `{alias, relative_path, revision, revision_kind}`（SCOPE-003）。
2. 文件内容一律包装为“不可信数据”：返回结构含 `alias`/`relative_path`/`revision`/`content`，并显式
   标注 `untrusted=true`；README/注释/Prompt 中的执行脚本、上传数据、安装依赖、提交变更指令被忽略
   并记录为被拒候选（领域模型场景 E、SCOPE-003）。
3. `search` 限定在对应根内，复用 Cycle 1 路径边界（拒绝穿越/symlink 越界）；`git-metadata` 默认仅
   工作树和本地元数据（`git_history=metadata_only`），深历史需显式启用，复用 Cycle 1 trusted git
   allowlist。
4. 外部 Codebase 业务解释与治理文档/数据模型交叉核验冲突时披露并请求 owner 裁决（SRC-002）；
   codebase_reader 不自动定义指标，不覆盖 T1/T2。
5. `.claude/fixtures/codebases/**` 提供合成外部树：含 Prompt 注入 README、符号链接越界、可信业务引用
   和命令替换/管道/换行恶意输入，用于 deterministic 测试。

#### 测试/验证

- `python3 -B -m unittest tests.harness.test_adapters.py`（追加 codebase 用例段）
- read/search/stat/git-metadata 正例返回 portable 引用 + untrusted 内容；execute/write/install/commit
  调用被拒（无对应接口或 block）；README 执行/安装/上传指令被忽略并记录；symlink 越界/穿越被拒；
  命令替换/管道/换行输入被净化；冲突业务解释被披露（SRC-002）；canary secret/PII 不出现在输出。

#### 失败处理

- 平台无法创建 symlink：记录平台限制并保留测试 skip 为 HIGH 偏差，不用字符串模拟替代 OS 行为
  （沿用 Cycle 1 Task 3 失败处理）。
- 外部根含 `.claude/skills`：默认 read_mode=adapter 不挂载；若未来启用 direct_add_dir 必须扫描拒绝，
  本周期不实现该模式。

#### 规则 / AC

- SCOPE-002、SCOPE-003、SRC-002、SEC-003；AC-03（外部 Codebase 不可写/不可执行、内部 Prompt 不能
  覆盖上层指令、引用含别名+相对路径+版本）；AC-04（来源发现前置）。

### Task 5：PreToolUse 门控与 settings 权限/沙箱默认

#### 行为

1. `pretool_guard.py` 是 `PreToolUse` 薄入口：stdin 限大小后 `json.load`，校验已确认的 PreToolUse
   字段形状（`cwd`/`tool_name`/`tool_input`/`tool_use_id`），只调用 `paths`/`policy`/`gates` API；不
   `eval`、不开 shell、不执行外部 codebase。实际 cwd 必须与 EffectiveConfig Workspace realpath 匹配。
2. 每次工具调用前重验路径身份（把 Cycle 1 点时检查升级为连续 TOCTOU 防护，feature-flow-v2 §9 gap 2）；
   外部根永远 deny write/execute；Workspace 内候选写受 protected actions 与影响检查约束（影响检查
   本身属 Cycle 4，本周期只做路径/只读/执行/网络预检）。
3. exit 0=通过；exit 2=阻断（按官方 PreToolUse 阻断语义）+ 规则 ID + 净化 evidence + recovery；
   其他异常转 exit 2 fail-closed（HOOK-004）。每个错误含 `rule_ids`/`evidence_refs`/`reason`/`recovery`。
4. `.claude/settings.json` 在 Cycle 1 `SessionStart` 之上增量：映射 `PreToolUse`（matcher 覆盖相关
   工具）、`permissions`（deny→ask→allow，外部根 deny-write/execute，凭证目录 deny-read）、`sandbox`
   默认值（enabled、fail-if-unavailable、禁止 unsandboxed commands、Workspace 有限写、外部 roots
   deny-write、网络默认拒绝按适配器域放行）。不含机器路径/密钥。
5. **分开证明两层**：`test_security.py` 用独立命令证明 (a) Claude permission deny 阻断越界写/执行
   （离线确定性：settings deny 规则 + PreToolUse gate 逻辑）；(b) OS sandbox deny-write/deny-execute
   阻断。二者记录平台与 exact command，不互相外推（AC-03）。

#### 测试/验证

- `python3 -B -m unittest tests.harness.test_security.py`（PreToolUse + 权限层/沙箱层分开证明用例段）
- PreToolUse 注入最小有效 Fixture、缺字段、恶意 JSON、超大输入、cwd mismatch、绝对/穿越 target、
  外部写/执行企图、secret canary、library exception；断言 exit 0/2、stderr 净化、规则 ID 齐全。
- 权限层 deny 证明：settings deny 规则 + PreToolUse 逻辑对外部 Edit/Write/Bash 阻断，exact command 记录。
- 沙箱层 deny 证明：尝试真实 OS sandbox deny-write/deny-execute；**若测试机无法实际启用 sandbox，
  该证明标为 BLOCKING GAP（known-gap），测试 skip 并记录 HIGH 偏差，不得用 Prompt test 伪造通过**。
- canary secret/PII/绝对 Workspace 路径不出现在 stdout/stderr。

#### 失败处理

- **沙箱无法在测试机启用**：保留为阻断缺口（dev-cycles.md Cycle 2 进入门）；settings 仍映射 sandbox
  默认值（配置层交付），但运行证据缺失必须写入 compatibility.md PRODUCTION BLOCKER 与 feature-flow
  §9。不得降级为 warn 或用 Prompt test 替代。
- 官方 PreToolUse 字段/退出语义与 technical-design 不一致：停止 Hook 映射，记录偏差交 plan-agent；
  安全缺口不能用 exit 0 掩盖。
- settings 无法通过 `claude doctor`：该文件不得交付为有效配置；修复或记录阻断偏差。

#### 规则 / AC

- SCOPE-001（连续 per-operation 执行）、SCOPE-002（tool-layer execute/write block）、SEC-001（per-action
  访问）、HOOK-001（确定性 only）、HOOK-003（只使用已验证的 PreToolUse 事件名/字段/退出语义，不假设）、
  HOOK-004（rule/evidence/recovery）、HOOK-005（Hook 只做确定性预检，不跑高成本评测）；AC-03（路径不
  越界、外部不可写/执行、权限层与沙箱层分开证明）；AC-08（PreToolUse 契约、permissions/sandbox 映射）。

### Task 6：ConfigChange 门控

#### 行为

1. `config_change_gate.py` 是 `ConfigChange` 薄入口：stdin 限大小后 `json.load`，校验已确认的
   ConfigChange 字段形状（`source`、可选 `file path`），复用 `config.load_effective_config`/
   `paths`/`policy`/`gates` 重验 Schema、路径、沙箱与权限边界。
2. 对可阻断来源（项目 settings/config）拒绝无效变更（exit 2 + 规则 ID + 证据 + 恢复）；managed policy
   变更不假定可阻断，给出明确反馈而非静默放行（technical-design §11.1）。
3. 配置变化使既有 `EffectiveConfig` 失效（technical-design §7.3 第 10 条）；ConfigChange 触发重诊断，
  不复用旧 config 缓存。
4. `.claude/settings.json` 追加 `ConfigChange` 段（Task 6 拥有此段，不改 Task 5 已交付段）。
5. exit 0=通过（重验通过）；exit 2=阻断 + 规则 ID + 净化证据 + 恢复；异常转 exit 2 fail-closed。

#### 测试/验证

- `python3 -B -m unittest tests.harness.test_security.py`（ConfigChange 用例段）
- 有效 ConfigChange 重验通过；移除 deny/禁用沙箱/路径穿越/密钥注入/字段降级被阻断；managed policy
  变更给出明确反馈；canary/绝对路径不泄漏；exit 0/2 符合契约。

#### 失败处理

- managed policy 来源不可阻断：给出明确反馈，不假装阻断成功；不静默放行。
- ConfigChange 字段与官方文档不一致：停止映射，记录偏差交 plan-agent。

#### 规则 / AC

- HOOK-001、HOOK-003（只使用已验证的 ConfigChange 事件名/字段，不假设）、HOOK-004、HOOK-005（Hook
  只判定重验与证据，不跑高成本评测）、SEC-001（重验访问边界）、SCOPE-001（重验路径）；AC-08
  （ConfigChange 契约）。

### Task 7：文档、追踪与周期证据

#### 行为

1. `docs/harness/security.md` 给出威胁模型（对应 technical-design §13：外部 Prompt 注入、路径穿越/
   symlink、外部写入/执行、Shell 注入、凭证/PII 泄露、配置降级、供应链/自动安装）、权限层与沙箱层
   分层、审计与事件响应；明确记录权限层离线证据与沙箱层阻断缺口。
2. `docs/harness/compatibility.md` 增量：增加 PreToolUse/ConfigChange 契约的离线证据、permissions/
   sandbox 默认值映射、managed=NOT YET EXERCISED、sandbox 运行证据=PRODUCTION BLOCKER；不把 official-only
   写成 verified。
3. `docs/harness/rule-traceability.md` 增量：把 SCOPE-001..003、SEC-001..003、SEM-001..003、SRC-002、
   PORT-001、HOOK-001..005 的 Cycle 2 证据从 PLANNED/PARTIAL 升级为 IMPLEMENTED (Cycle 2) 或保留
   PARTIAL 并注明后续周期；不用规则族概括冒充逐条落地。
4. `docs/feature-flow-v3.md` 从真实代码读取：PreToolUse、ConfigChange、policy、adapter 选择链、
   codebase_reader 的入口、带行号调用链、分支、数据流、错误处理与已知差距（含 sandbox/managed 阻断
   缺口）。
5. 运行 Cycle 2 目标测试 + Cycle 1 全回归；记录 exact command 与结果。

#### 测试/验证

```text
python3 -B -m unittest tests.harness.test_config tests.harness.test_paths tests.harness.test_hooks tests.harness.test_adapters tests.harness.test_security
python3 -B -m unittest discover -s tests/harness -p 'test_*.py'
find .claude/lib/chatbi_harness .claude/hooks .claude/fixtures tests/harness docs/harness -type f -print
rg -n '/Users/|BEGIN .*PRIVATE KEY|api[_-]?key|token\s*[:=]' .claude docs/harness
rg -o '[A-Z]{2,5}-[0-9]{3}' .claude docs/harness | sort -u
```

最后两个 `rg` 判读须区分合法规则 ID 与“credential env name”说明；真实 secret/机器路径命中为失败。

#### 失败处理

- 某规则尚无 Cycle 2 实现：标 PLANNED: Cycle N，不填假文件/假测试。
- sandbox 运行证据缺失：compatibility/security 明确标 PRODUCTION BLOCKER，不伪造 verified。
- feature-flow 无法给出行引用：说明入口不存在或文档过早，不生成想象调用链。
- plan-agent 报 CRITICAL/HIGH：进入优化迭代，不进入 Cycle 3。

#### 规则 / AC

- AC-03（安全文档与分层证明）、AC-08（兼容性证据区分 verified/official-only/not-yet-exercised/
  blocked）、AC-09（文件清单与追踪一致）；HOOK-002/003（事件映射只使用已验证事件名/字段 + 版本探测
  证据）、HOOK-005（Hook 不跑高成本评测的合规记录）；FBK-003（不得声称绝对正确）。

## 7. 集成数据流

Cycle 2 预期实现的数据流必须可由代码与测试证明：

```text
Claude PreToolUse JSON (cwd/tool_name/tool_input/tool_use_id)
        │
        ▼
  限长 + JSON 形状检查 + cwd 匹配 Workspace realpath
        │
        ▼
  policy.access_check(config, request context) ──block──> exit 2 (SEC-001/002, SEM-003)
        │pass
        ▼
  paths.resolve_path_reference(config, alias, target) ──block──> exit 2 (SCOPE-001/002/003)
        │pass (连续 TOCTOU 重验)
        ▼
  tool capability group check (write/execute/network deny for external)
        │
        ▼
  exit 0 (允许) 或 exit 2 (阻断 + rule_ids/evidence/recovery)


Adapter selection (discover/compile/query)
        │
        ▼
  managed available + authorized? ──yes──> use (official-only / NOT YET EXERCISED if no runtime)
        │no
        ▼
  approved CLI available + argv legal + authorized? ──yes──> use (argv array, no shell, allowlist, cwd=Workspace)
        │no
        ▼
  fixture_enabled + test/example flag? ──yes──> Fixture (explicit test only)
        │no
        ▼
  STOP: list missing capabilities + minimal authorization


Codebase read (read/search/stat/git-metadata)
        │
        ▼
  resolve_path_reference (Cycle 1 boundary) + untrusted content wrap
        │
        ▼
  return {alias, relative_path, revision, content(untrusted=true)}
  + rejected instruction candidates logged (SCOPE-003)


ConfigChange
        │
        ▼
  reload EffectiveConfig + re-validate schema/path/sandbox/permissions
        │
        ▼
  exit 0 (重验通过) 或 exit 2 (阻断 / managed policy 明确反馈)
```

PreToolUse 与 OS sandbox 是互补防线（technical-design §13）：PreToolUse 是确定性 Claude 层，sandbox
是 OS 层。二者必须分开证明，不互相外推。sandbox 运行证据缺失时为阻断缺口，不替代。

## 8. 测试矩阵

| 层次 | 关键用例 | 通过条件 |
| --- | --- | --- |
| policy 单元 | 访问/PII/审批/工具组/风险判定 | 权限不足/PII 缺失/protected action 阻断；高风险需签字；无 canary 泄漏 |
| 适配器协议 | 选择链三分支、CLI argv、证据 Schema | managed→CLI→STOP；Fixture 非生产；无 shell/机器路径；证据含散列 |
| Fixture | test/生产模式、catalog/warehouse | test 模式稳定证据；生产模式 block；catalog 含 metrics/dimensions/segments |
| codebase_reader | read/search/stat/git-metadata、注入、symlink | 只读无 execute/write；指令忽略并记录；引用 portable；冲突披露 |
| PreToolUse 契约 | 有效/恶意/超大/畸形 JSON、cwd mismatch | exit 0/2 符合契约；规则 ID/证据/恢复齐全；无泄漏 |
| 权限层 deny 证明 | settings deny + PreToolUse 外部写/执行 | 离线确定性阻断；exact command 记录 |
| 沙箱层 deny 证明 | OS sandbox deny-write/deny-execute | 真实阻断；**无法启用则 BLOCKING GAP + HIGH 偏差，不伪造** |
| ConfigChange 契约 | 有效/降级/密钥/managed policy | 重验通过/阻断；managed policy 明确反馈；无泄漏 |
| 离线纵向 | 临时 Workspace + 合成 codebase + Fixture | 从 policy→adapter→codebase→PreToolUse 真实跑通 |
| 兼容性 | version/doctor/platform probes | 事实记录；sandbox/managed 标 NOT YET EXERCISED/PRODUCTION BLOCKER |

## 9. 周期失败与升级策略

- **沙箱无法在测试机启用**（最高优先级风险）：settings 仍交付 sandbox 默认值配置层；运行证据缺失
  保留为阻断缺口，写入 compatibility.md PRODUCTION BLOCKER 与 feature-flow §9。**不得用 Prompt test
  替代、不得降级为 warn、不得删除该退出门要求**。该缺口延续到 Cycle 5 真实 E2E，由人决策是否放行
  Cycle 2 部分退出。
- **managed runtime 缺失**：managed 分支标 official-only / NOT YET EXERCISED；选择链继续试 CLI；不
  伪造 managed 可用。CLI 分支用 fake approved CLI 验证；STOP 分支可验证。
- **领域/需求冲突**：停止实现，向 plan-agent/用户提交冲突规则、候选解释与影响范围。
- **安全检查无法确定**：失败关闭；不得从 BLOCKED 降级 WARN 让测试变绿。
- **当前环境缺能力**：能用可信 Fixture 验证的继续；能力本身需真实 Claude/sandbox 时保留明确偏差与
  后续硬门。Cycle 5 前不能用 mock 关闭真实 E2E 要求。
- **PreToolUse/ConfigChange 字段与官方文档不一致**：停止 Hook 映射，记录偏差交 plan-agent；安全缺口
  不能用 exit 0 掩盖。
- **非 Cycle 2 缺口**（如 DOC-004、分析闭环、评测）：记录到 feature-flow/design gap，不提前实现造成
  依赖倒置。

## 10. 周期完成清单

实现回合只有全部项目都有真实证据时才能提交 plan-agent 评审：

- [ ] 用户已批准 Cycle 2 tickets，coder-agent 已按依赖链加载 executing-plans。
- [ ] Task 1–6 的预期文件存在、非空、归属清楚，无计划外生产文件。
- [ ] `policy.py` 对权限/PII/审批/工具组/风险给出确定性判定，protected action 不可自批。
- [ ] adapter 选择链 managed→CLI→STOP 三分支、CLI argv allowlist/no-shell/env/cwd、Fixture 禁止生产
      回退均有测试证据。
- [ ] codebase_reader 只读（无 execute/write/install/commit），外部内容包装为不可信数据，Prompt 注入
      指令被忽略并记录，引用含 alias+relative_path+revision。
- [ ] PreToolUse 把 Cycle 1 点时检查升级为连续 TOCTOU 门控；外部写/执行被阻断。
- [ ] ConfigChange 重验 Schema/路径/沙箱/权限；managed policy 不假定可阻断。
- [ ] `.claude/settings.json` 映射 PreToolUse/ConfigChange/permissions/sandbox 默认值，无机器路径/密钥。
- [ ] **Claude permission deny 阻断与 OS sandbox deny 阻断有分开的命令证据**；沙箱无法启用时为明确
      BLOCKING GAP + HIGH 偏差，未用 Prompt test 替代。
- [ ] managed runtime 缺失标 official-only / NOT YET EXERCISED；不伪造可用。
- [ ] canary secret/PII/绝对 Workspace 路径不出现在任何 Hook stdout/stderr/证据。
- [ ] `python3 -B -m unittest discover -s tests/harness -p 'test_*.py'` 全绿（Cycle 1+2 回归）。
- [ ] `docs/harness/rule-traceability.md` 对 SCOPE-001..003、SEC-001..003、SEM-001..003、SRC-002、
      PORT-001、HOOK-001..005 逐条标 Cycle 2 真实证据或注明后续周期（HOOK-002/003 事件映射证据、
      HOOK-005 不跑评测的合规记录）。
- [ ] `docs/harness/security.md`、`compatibility.md` 区分 verified/official-only/not-yet-exercised/
      blocked，sandbox/managed 阻断缺口明确。
- [ ] `docs/feature-flow-v3.md` 从代码生成，含真实行引用、分支、数据流、错误与差距（含 sandbox/managed）。
- [ ] test-agent 的 Cycle 2 报告目标测试通过；plan-agent 优化清单无 CRITICAL/HIGH 且 `STATUS: CONVERGED`。
- [ ] 文件 inventory、feature-flow、测试报告与技术设计的 Cycle 2 范围一致。

本清单完成只允许 Cycle 2 进入 CONVERGED，不代表 Harness v1 COMPLETE，也不允许跳过 Cycle 3–5。沙箱
运行证据缺失为已知阻断缺口，由人决策是否允许带缺口进入 Cycle 3 或等待真实沙箱环境。
