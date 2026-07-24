# Development Cycle 1 原生任务计划：骨架、配置边界与初始化诊断

STATUS: PLANNED

## 1. 周期目标

交付一个最小但真实的 Claude Code ChatBI Harness 纵向骨架：主 Agent 从根契约获得边界与
路由，`/chatbi-init` 使用共享/本地配置，经配置和路径库验证后生成结构化诊断；同一诊断逻辑
由 `SessionStart` 薄 Hook 调用；所有失败使用统一 GateError，包含规则 ID、净化证据和恢复动作。

该纵向切片必须在无 Git、无 Claude 登录、无真实数仓凭证的当前环境中用合成临时目录可重复
验证，同时明确标记哪些 Claude/Hook 能力仍等待真实 E2E。它不是空目录、空 Prompt、永远
PASS 的检查器或只写“未来将支持”的文档骨架。

### 1.1 成功结果

- 有效合成配置从 JSON 读取、分层合并、结构/跨字段校验、`realpath` 根验证，最终输出机器
  可读 diagnostic result。
- 无效领域模型、绝对共享路径、密钥值、缺字段、非法别名、根重叠、路径穿越、符号链接越界、
  cwd 不匹配或安全能力缺失时确定性失败关闭。
- `CLAUDE.md` 和三份 rules 明确 Agent 主导/人类治理、四层栈、范围/安全、完成定义和按需路由，
  不复制整份领域知识。
- `/chatbi-init` 声明输入、前置条件、可修改范围、停止条件、输出证据和规则 ID。
- `SessionStart` 离线事件 Fixture 能调用同一 library，不能形成一套不同的安全语义。
- 初始文档说明当前真实能力、配置方法、失败恢复和兼容性证据；后续周期能力标记为 planned，
  不伪装成已交付。

## 2. 非目标

Cycle 1 明确不实现以下内容，但不能从最终范围删除：

- 不实现外部 Codebase 实际读取、PreToolUse 写/执行阻断、OS sandbox 实跑或 managed/CLI
  adapter；这些属于 Cycle 2。
- 不实现分析问答、运行证据、质量检查、对抗性 reviewer、SubagentStop/Stop；属于 Cycle 3。
- 不实现模型/知识维护、PostToolUse 影响图和领域 reference；属于 Cycle 4。
- 不实现完整离线评测、纠正闭环、消融和真实 Claude 全事件 E2E；属于 Cycle 5。
- 不连接真实 Warehouse、不安装未知依赖、不修复 Claude 登录、不生成组织 PII policy、owner
  或 release threshold。
- 不创建规范指标、不批准 protected action、不声称 Fixture 结果代表生产认证。
- 不实现完整 JSON Schema 标准。若采用标准库零依赖策略，只实现本项目配置所需的明确结构与
  关键字子集，并在文档中说明边界；不能使用“schema validated”暗示完整标准兼容。

## 3. 前置证据

实现者在创建 tickets 前必须重新确认以下证据仍成立：

| 证据 | 当前权威来源 | Cycle 1 用法 |
| --- | --- | --- |
| 46 条领域规则与信任层级 | `docs/chatbi-harness-domain-model.md` 第 7–10 节 | Prompt、GateError、配置/路径检查的规则 ID 来源 |
| P0-01/02/05/08/11 与 AC | `docs/requirements.md` 第 6、9 节 | 定义本周期最小交付和不能提前声称的能力 |
| 文件结构、配置、路径不变量 | `docs/technical-design.md` 第 6–7 节 | 模块边界和测试分支 |
| Hook 映射与输入/退出约定 | `docs/technical-design.md` 第 11 节 | SessionStart offline contract；不外推真实 E2E |
| 当前本地基线 | `docs/technical-design.md` 第 3、18 节 | 2.1.216 / Python 3.14.2 / Darwin arm64 / no Git / not logged in |
| 编排批准状态 | `docs/orchestrator-state.md` | 需求和技术设计已确认 |
| Tracker 约定 | `docs/agents/issue-tracker.md` | 获批后才在 `.scratch/<feature>/issues/` 发布 tickets |

实现开始时还需用只读命令复核：

```text
claude --version
claude doctor
python3 --version
uname -s -m
git rev-parse --is-inside-work-tree
find . -maxdepth 3 -type f -print
```

`claude doctor` 因未登录或 keychain 限制返回非零时，应被记录为诊断事实；它只阻断要求真实
Claude 会话的验收，不阻断本周期离线 Fixture。任何与技术设计记录不同的 Hook/版本行为必须
先更新兼容性证据并交 plan-agent 判断，不能静默适配。

## 4. 实现前强制审批门

以下步骤属于后续实现回合，本规划回合不执行：

1. coder-agent 完整读取 `/Users/admin/.agents/skills/to-tickets/SKILL.md`。
2. 以本计划 Task 1–6 为 tracer-bullet 依据起草 tickets；ticket 不得把一组空文件当作纵向
   交付，也不得把 Cycle 2–5 范围偷放进 Cycle 1。
3. 展示 tickets 并等待用户明确批准；未经批准不得写 `.scratch/`。
4. 批准后按 `docs/agents/issue-tracker.md` 发布本地 Markdown tickets。
5. coder-agent 完整读取 `/Users/admin/.agents/skills/implement/SKILL.md`，按获批顺序实施。

如果任一 Skill 缺失，按 coder-agent 合同返回 `STATUS: BLOCKED`；不得用通用推理绕过。

## 5. 文件所有权

每个实现文件只有一个首要任务所有者。后续任务可消费其公开 API，但不得顺手重写另一任务的
文件；发现接口需要变化时回到对应 owner task，并重跑其目标测试。

| Task | 唯一首要文件所有权 |
| --- | --- |
| 1 根契约与规则 | `CLAUDE.md`; `CONTEXT.md`; `.claude/rules/00-domain-contract.md`; `.claude/rules/10-security.md`; `.claude/rules/20-completion.md` |
| 2 配置合同 | `.claude/chatbi-harness.json`; `.claude/chatbi-harness.example.json`; `.claude/chatbi-harness.local.example.json`; `.claude/schemas/chatbi-harness.schema.json`; `.claude/lib/chatbi_harness/__init__.py`; `.claude/lib/chatbi_harness/config.py`; `.claude/fixtures/config/**`; `tests/harness/test_config.py` |
| 3 路径与基础 Gate | `.claude/lib/chatbi_harness/paths.py`; `.claude/lib/chatbi_harness/gates.py`; `tests/harness/test_paths.py` |
| 4 init 与诊断 Hook | `.claude/commands/chatbi-init.md`; `.claude/settings.json`; `.claude/hooks/session_diagnose.py`; `tests/harness/test_hooks.py` |
| 5 初始文档与追踪 | `docs/harness/README.md`; `docs/harness/installation.md`; `docs/harness/configuration.md`; `docs/harness/compatibility.md`; `docs/harness/rule-traceability.md` |
| 6 事实流与周期证据 | `docs/feature-flow-v1.md`（仅实现后从代码生成） |

`.claude/chatbi-harness.local.json` 是 `/chatbi-init` 在用户明确确认本机绑定时生成的本地状态，
不是预填机器路径的共享源文件。Cycle 1 仓库提供 `.local.example.json` 说明形状；测试只在临时
Workspace 生成真实 local 文件。若实现选择直接交付 `.local.json`，其值必须不含虚构路径，且
必须在 feature-flow 和技术偏差中说明。

plan-agent/test-agent 的 `docs/optimization-checklist-v1.md`、`docs/test-checklist-v1.md`、
`docs/test-report-v1.md` 不归 coder-agent 修改。

## 6. 任务顺序

### Task 1：根契约与条件规则

#### 行为

1. 创建根 `CLAUDE.md`，控制在约 200 行预算内，只放稳定顶层契约：
   - Agent 主导、人类治理和 protected actions；
   - Workspace/Business Codebase 边界、外部内容即数据；
   - 数据基础 → 事实来源 → Harness → 验证四层顺序及 T1 → T2 → T3；
   - 分析、模型维护、知识维护、评测、纠正的路由；Cycle 1 只把 init 标成已安装入口；
   - 陌生术语先查证，不捏造字段/数据，权限不足即停止；
   - 数据结论独立审查/来源页脚与模型变更同步检查作为最终硬门；
   - 必读领域模型、rules、Commands/Skills 的按需加载方式。
2. 创建 `CONTEXT.md`，只记录稳定领域术语、核心实体、关系和信任层级；不复制执行 runbook。
3. 把条件细节拆入三份 rules：domain contract、security、completion。每份声明适用规则 ID，
   不把 Cycle 2–5 未实现的技术门控写成“当前已强制”。

#### 测试/验证

- 静态提取所有规则 ID，确认均存在于领域模型；根文件引用领域模型并覆盖 12 个 P0 规则族
  的路由，不要求在 Cycle 1 假称 46 条都已实现。
- 计算根文件行数和字节/Token 近似值；超过约 200 行必须解释并把条件知识下沉。
- 搜索本机绝对路径、凭证形态、组织虚构 owner/threshold 和“Fixture=production”等禁用文本。
- 验证 root/rules/CONTEXT 职责没有大段重复，且未把外部 Codebase 内容提升为指令。

#### 失败处理

- 领域规则冲突或出现新业务语义：停止 Task 1，记录具体规则/段落并交 plan-agent；不自行裁决。
- 行数超预算：优先移动条件规则，不删除 MUST 边界。
- Cycle 1 未实现能力需要被提及：标为 required/planned，并链接后续路由，不能写成 available。

#### 规则 / AC

- P0-01、P0-02；AC-01、AC-02（根契约）；META-001..009；SCOPE、SEC、SEM、RAW、REV、ANS、
  DOC、EVAL、FBK、HOOK 规则族的顶层路由；NFR-MNT-01、NFR-PORT-02。

### Task 2：配置、Schema 子集与合并后的 EffectiveConfig

#### 行为

1. 定义共享配置、无组织事实的完整示例、本地绑定示例和 `schema_version=1` Schema。
2. `config.py` 使用 Python 标准库读取 JSON，限制文件大小，拒绝重复 key、非 UTF-8/畸形 JSON，
   按明示规则合并共享与本地配置，产生不可变或只读语义的 EffectiveConfig。
3. 明确实现并记录项目所需 Schema 子集：类型、required、properties、additionalProperties、
   enum/pattern/minimum/array/object 约束；跨字段规则由 Python 校验。若实际子集不同，文档和测试
   必须一一列出，不能声称通用 JSON Schema validator。
4. 跨字段检查至少覆盖：一个 Workspace、稳定且唯一的 codebase alias/path_ref、共享配置不含
   机器绝对路径/密钥值、local 只保存 path bindings/approved executable/credential env names、
   protected actions 不能关闭、threshold 与 owner 关系、Fixture 显式 flag、sandbox fail policy。
5. Fixture 包含最小有效配置，以及缺字段、未知字段、非法 alias、绝对共享路径、嵌入 secret、
   owner/threshold 冲突、生产 Fixture 回退等无效配置。

#### 测试/验证

- `python3 -m unittest tests.harness.test_config`
- 同一输入产生稳定 EffectiveConfig；合并不能让 local 覆盖 managed/protected deny 语义。
- 错误为结构化 GateError（由 Task 3 API 提供）；若 Task 3 尚未落地，先以明确 typed exception
  接口测试，集成后改为唯一 GateError，不保留双重错误协议。
- 共享配置和输出中 secret canary/真实绝对根为零；credential 只保存 env var name。

#### 失败处理

- 标准库不足以准确实现已声明 Schema 子集：缩小“声明的子集”只能在不削弱 P0 校验时进行；
  否则提出锁定且审核的 validator 依赖供 plan-agent/用户裁决，禁止自写不完整 validator 却默默
  接受未知结构。
- 配置选择要求组织事实：保持字段未配置并诊断 BLOCKED/NOT_PRODUCTION_READY，不填假值。
- 配置跨字段与技术设计冲突：停止并记录偏差，不通过宽松解析隐藏冲突。

#### 规则 / AC

- P0-01、P0-05、P0-08、P0-11；AC-01、AC-03（配置边界）、AC-08；SCOPE-001..003、
  SEC-001..003、SEM-003、PORT-001、HOOK-002..004；NFR-COR-02、NFR-SEC-03/04。

### Task 3：路径身份与统一 GateError

#### 行为

1. `paths.py` 只接受 EffectiveConfig 和显式 alias/target，使用 `Path.resolve`/`realpath` 后的路径
   组件关系判断 Workspace/外部根/目标；拒绝字符串前缀包含。
2. 校验根存在且为目录；根之间不得相同、祖先/后代或经 symlink 重合；目标必须留在对应根；
   外部根不得在 Workspace 内，Workspace 也不得在外部根内。
3. 生成可移植引用 `{alias, relative_path, revision, revision_kind}`；当前无 Git 时使用
   `content_sha256`，不得在共享产物/面向用户错误暴露绝对根。
4. `gates.py` 定义唯一的 pass/warn/block 模型和 GateError：`rule_ids`、`evidence_refs`、
   `reason`、`recovery`；输出净化、稳定序列化、失败关闭。
5. 明确 TOCTOU 边界：每次工具调用前必须重验属于 Cycle 2；Cycle 1 只提供可重复调用 API，
   不声称 init-time check 永久保证路径安全。

#### 测试/验证

- `python3 -m unittest tests.harness.test_paths`
- 使用临时目录覆盖：合法 Workspace/外部根；未配置路径；`../`；绝对目标；`foo`/`foobar`
  前缀；相同/嵌套/重叠根；入口 symlink；内部 symlink 越界；broken symlink；不存在根；
  无 Git content hash；相对引用 round-trip。
- 每个负例断言规则 ID、净化 evidence、具体 recovery；错误/日志不出现临时绝对根或 canary。

#### 失败处理

- 当前平台无法可靠创建 symlink：记录平台限制并保留测试 skip 为 HIGH 偏差，Cycle 1 不可将
  AC-03 路径门标为完成；不要用字符串模拟替代 OS 行为。
- Git 不可用/目录不是 worktree：按设计使用 content hash，而不是跳过 revision evidence。
- 净化会破坏诊断可操作性：保留 alias + relative path + error category，不回退泄露绝对路径。

#### 规则 / AC

- P0-05、P0-08；AC-03 路径部分、AC-08 恶意输入部分；SCOPE-001..003、SEC-003、PORT-001、
  HOOK-004；NFR-COR-01/02、NFR-SEC-02/04、NFR-UX-01。

### Task 4：`/chatbi-init` 与 `SessionStart` 诊断纵向切片

#### 行为

1. `.claude/commands/chatbi-init.md` 明确输入、前置、可修改范围、步骤、停止条件、输出证据、
   规则 ID；配置本地路径前要求用户明确确认。
2. `session_diagnose.py` 是薄入口：stdin 限大小后 `json.load`，校验已确认的 SessionStart
   字段形状，只调用 config/paths/gates API；不 `eval`、不开 shell、不执行外部 codebase。
3. 成功输出单个机器可读 JSON；失败按当前 Hook 契约使用 exit 2 + 简短 stderr，所有异常都
   转成 fail-closed GateError。普通非 ChatBI 对话不被无关的生产配置缺口永久阻塞；诊断将
   ChatBI commands 标记为不可运行并给恢复动作。
4. `.claude/settings.json` 只映射已由当前官方文档和本地设计确认的 SessionStart command Hook，
   使用相对 Workspace 的安全 argv/command；不写机器路径、密钥或尚未实现的 Hook 命令。
5. diagnostic 至少记录 Claude version/doctor 摘要、platform、Python、Git SHA-or-content-hash、
   config/schema/path/sandbox/owner/PII/threshold/adapter readiness；区分 PASS/WARN/BLOCKED 与
   “真实 Hook 未演练”。不得把命令 stdout 原样注入错误或 Prompt。

#### 测试/验证

- `python3 -m unittest tests.harness.test_hooks`
- 直接向 Hook stdin 注入最小有效 SessionStart Fixture、缺字段、未知危险形状、畸形 JSON、
  超大输入、cwd mismatch、绝对/穿越 target、secret canary 和 library exception。
- 断言 exit 0/2、stdout 只有一个 JSON、stderr 具体且净化、重复/未知危险字段策略明确；
  `claude doctor` timeout/非零不会被误报为成功。
- 在临时 Workspace 执行一次 end-to-end offline diagnostic：配置 → 路径 → capability → JSON。
  这只是 Hook contract test，compatibility 文档必须写“尚未由真实 SessionStart 触发”。

#### 失败处理

- 官方字段/退出语义与技术设计不一致：停止 Hook 映射，将 CLI `/chatbi-init` 保留为明确后备，
  记录偏差给 plan-agent；安全缺口不能用 exit 0 告警掩盖。
- `claude doctor` 挂起/输出包含本机信息：使用超时、摘要白名单和净化；不持久化完整 stdout。
- settings 无法通过 `claude doctor`：该文件不得交付为有效配置；修复 Schema 或记录阻断偏差。

#### 规则 / AC

- P0-01、P0-03 初始化/诊断、P0-05、P0-08、P0-11；AC-02 init、AC-03 路径基础、AC-08
  探测/Hook 恶意输入；SCOPE-001..003、SEC-001..003、PORT-001、HOOK-001..004；
  NFR-PORT-03、NFR-UX-01/03。

### Task 5：实质初始文档与规则追踪

#### 行为

1. `docs/harness/README.md` 给出 v1 目标、当前 Cycle 1 可用入口、后续能力状态、硬边界和文档地图。
2. installation 记录安装前置、文件放置、`/chatbi-init`、managed settings 建议、本地路径确认、
   无 Git hash 后备和卸载/恢复；不指导自动执行外部脚本。
3. configuration 逐字段解释共享/local 分层、Workspace、codebase alias、adapters、PII ref、owners、
   threshold、sandbox policy 和 protected actions；示例不含组织事实。
4. compatibility 记录 exact probe command、执行日期、2.1.216/Python 3.14.2/Darwin arm64、登录/Git
   状态，并分列 verified offline、official-only、not-yet-exercised、blocked-for-real-E2E。
5. rule traceability 初始矩阵逐条列出 46 条规则，Cycle 1 已实现的给文件/测试证据，其余明确
   标注 planned Cycle 2–5；禁止用“规则族覆盖”伪装逐条落地。

#### 测试/验证

- 链接/文件路径存在性；46 个 exact rule ID 集合与领域模型相等，无遗漏/重复/虚构。
- 文档里的命令能够在当前环境安全执行，输出声称与实际 test/probe 一致。
- 搜索绝对本机路径、secret/PII canary、虚构 owner/threshold、Anthropic 准确率承诺和“全部
  支持”等过度声称。
- 非空/非占位检查：每份文档至少包含实际 Cycle 1 行为、限制和恢复路径。

#### 失败处理

- 某规则尚无实现：标 `PLANNED: Cycle N`，不能填假文件/假测试；若它本应是 Cycle 1 规则则
  本周期退出失败。
- capability 仅来自官方文档：标 official-only；只有真实命令/Fixture 结果才标 verified。
- 文档与代码不同：以代码/命令为事实修正文档和 feature-flow，不反向修改事实来迎合设计。

#### 规则 / AC

- P0-01、P0-11；AC-01、AC-02 文档部分、AC-08、AC-09 文件清单一致；全部 46 条追踪，重点
  PORT-001、HOOK-002..004、FBK-003（不得声称绝对正确）。

### Task 6：周期集成验证与真实 feature-flow

#### 行为

1. 从根契约 → `/chatbi-init` → config → paths → gates → SessionStart hook 逐文件读取实际调用链，
   写 `docs/feature-flow-v1.md`；必须含入口、带行号调用链、分支、数据流、错误处理和设计差距。
2. 运行所有 Cycle 1 目标测试、静态 inventory/规则 ID/禁用内容检查，保存 exact command 和结果。
3. 将未登录、真实 Hook 尚未触发、真实 sandbox/adapter 未实现列为后续门或已知差距；不得把
   预期行为复制进 feature-flow。
4. 交给 test-agent 生成 checklist/report，再由 plan-agent 对照技术设计生成 optimization checklist。

#### 测试/验证

```text
python3 -m unittest tests.harness.test_config tests.harness.test_paths tests.harness.test_hooks
python3 -m unittest discover -s tests/harness -p 'test_*.py'
find CLAUDE.md CONTEXT.md .claude docs/harness tests/harness -type f -print
rg -n '/Users/|BEGIN .*PRIVATE KEY|api[_-]?key|token\s*[:=]' CLAUDE.md CONTEXT.md .claude docs/harness
rg -o '[A-Z]{2,5}-[0-9]{3}' CLAUDE.md CONTEXT.md .claude docs/harness | sort -u
```

最后两个 `rg` 的判读必须考虑文档中合法的规则 ID 和“credential env name”说明；任何真实
secret/机器路径命中为失败，不能机械把所有单词 `token` 当泄漏。

#### 失败处理

- 任一目标测试失败：停在 Cycle 1，按实际失败修复并重跑目标与全周期回归。
- feature-flow 无法给出行引用：说明实现/入口不存在或文档过早；不得生成想象调用链。
- plan-agent 报 CRITICAL/HIGH：由 orchestrator 进入优化迭代，不进入 Cycle 2。

#### 规则 / AC

- AC-01、AC-02 Cycle 1 部分、AC-03 路径部分、AC-08 探测部分、AC-09 自动检查与 inventory；
  NFR-COR-01..03、NFR-MNT-02、NFR-PORT-03。

## 7. 集成数据流

Cycle 1 预期实现的数据流必须可由代码与测试证明：

```text
Claude SessionStart JSON / 用户执行 /chatbi-init
                │
                ▼
        限长 + JSON 形状检查
                │
                ▼
  shared config + optional local bindings
                │
                ▼
  structure/subset schema + cross-field checks
                │
                ▼
 Workspace/codebase roots realpath validation
                │
                ▼
 capability probes + production readiness status
                │
                ▼
 PASS/WARN/BLOCKED diagnostic JSON
       或 GateError(exit 2, rules/evidence/recovery)
```

诊断只证明安装配置在当前时点的状态。它不授权读取/写入，不替代每次工具调用前重验，不证明
语义层答案正确，也不证明真实 Claude Hook/Agent 已完成 E2E。

## 8. 测试矩阵

| 层次 | 关键用例 | 通过条件 |
| --- | --- | --- |
| 静态合同 | 根/rules 分层、规则 ID、共享配置、文档链接 | 无虚构规则/绝对机器路径/密钥；Cycle 1 状态准确 |
| 配置单元 | parse/merge/subset schema/cross-field | 有效稳定；未知/危险结构拒绝；错误可恢复 |
| 路径单元 | realpath、重叠、穿越、symlink、reference | 无逃逸；alias/relative/revision 可移植 |
| Gate 单元 | pass/warn/block、序列化、净化 | 相同输入稳定；异常 fail-closed；无敏感泄漏 |
| Hook contract | SessionStart good/bad/malicious JSON | exit/stdout/stderr 符合设计；调用共享库 |
| 离线纵向 | 临时 Workspace 的 init diagnostic | 从配置到结果真实跑通，不依赖 Git/登录/凭证 |
| 兼容性 | version/doctor/platform/git probes | 事实记录，不把未登录或 official-only 写成 verified E2E |

## 9. 周期失败与升级策略

- **领域/需求冲突**：停止实现，向 plan-agent 和用户提交冲突规则、候选解释和影响范围。
- **安全检查无法确定**：失败关闭；不得从 BLOCKED 降级 WARN 来让测试变绿。
- **当前环境缺能力**：能用可信 Fixture 验证的继续；能力本身需要真实 Claude/sandbox 时保留
  明确偏差和后续硬门。Cycle 5 前不能用 mock 关闭真实 E2E 要求。
- **无 Git**：使用内容散列并标 `revision_kind=content_sha256`；不缩减 provenance。
- **无真实组织配置**：诊断 `NOT_PRODUCTION_READY/BLOCKED`，示例保持无组织事实；不填假 owner、
  PII policy、连接或 threshold。
- **非 Cycle 1 缺口**：记录到 feature-flow/design gap，不提前实现造成依赖倒置。

## 10. 周期完成清单

实现回合只有全部项目都有真实证据时才能提交 plan-agent 评审：

- [ ] 用户已批准 Cycle 1 tickets，coder-agent 已按顺序加载 `to-tickets` 与 `implement`。
- [ ] Task 1–5 的预期文件存在、非空、归属清楚，无计划外生产文件。
- [ ] 根 `CLAUDE.md` 只承担顶层契约/路由，约 200 行预算经过检查。
- [ ] 领域模型缺失/冲突、配置危险形状和路径越界均失败关闭。
- [ ] 有效临时 Fixture 完整跑通 init diagnostic，且不是硬编码 PASS。
- [ ] GateError 始终含规则 ID、净化 evidence、reason、recovery。
- [ ] 共享产物没有本机绝对路径、密钥值或未经授权 PII。
- [ ] 无 Git 使用内容散列；无凭证/owner/PII/threshold 明确阻止生产认证。
- [ ] SessionStart 离线 Hook contract 全绿，真实 Hook 尚未演练被明确标注。
- [ ] `python3 -m unittest discover -s tests/harness -p 'test_*.py'` 全绿。
- [ ] `docs/harness/rule-traceability.md` 对 46 条规则逐条标真实证据或计划周期。
- [ ] `docs/feature-flow-v1.md` 从代码生成，含真实行引用、分支、数据流、错误和差距。
- [ ] test-agent 的 Cycle 1 报告为 ALL_PASSED。
- [ ] plan-agent 的 Cycle 1 optimization checklist 无 CRITICAL/HIGH 且 `STATUS: CONVERGED`。
- [ ] 文件 inventory、feature-flow、测试报告和技术设计的 Cycle 1 范围一致。

本清单完成只允许 Cycle 1 进入 CONVERGED，不代表 Harness v1 COMPLETE，也不允许跳过 Cycle 2–5。
