# Claude Code ChatBI Harness v1 技术设计

STATUS: AS_BUILT (2026-07-24)

> **AS_BUILT 和解说明**：本设计文档描述的 v1 拟议实现已由 Cycle 1–5 的真实代码落地。
> 权威的"代码即事实"证据以以下文件为准（设计文档与代码冲突时以代码为准）：
> - `docs/feature-flow-v6.md` - 六大流程的真实行引用调用链
> - `docs/harness/rule-traceability.md` §9.9 - 46/46 规则逐条真实证据（8 Cycle 1 + 7 Cycle 2 + 16 Cycle 3 + 5 Cycle 4 + 8 Cycle 5 + 2 HOOK Cycle 5 live，全部 IMPLEMENTED）
> - `docs/dev-cycle-{1..5}.md` + `docs/orchestrator-state.md` - 周期实现与收敛记录
> - `docs/harness/{security,compatibility,analysis,maintenance,knowledge-authoring,evaluation,troubleshooting,negative-experiments}.md` - 真实能力与缺口
> - `.claude/{lib/chatbi_harness,hooks,commands,skills,schemas,fixtures,rules}` - 真实代码与配置
>
> **离线 + live 验证**：`python3 -B -m unittest discover -s tests/harness` 全绿（533 tests，1 skip = OS sandbox BLOCKING GAP）。Cycle 5 Task 06 live E2E（Claude Code 2.1.217，Darwin arm64）确认 6 个确定性 Hook 注册 + fire，5/6 事件 gate 逻辑 live 验证（SessionStart/PreToolUse/PostToolUse/SubagentStop/Stop）+ 生产无连接 STOP live 验证（真实 `select_adapter` 返回 SEM-001/PORT-001 STOP，无 Fixture 回退）；ConfigChange 离线验证 + 同机制。
>
> **仍为人工/环境硬门（不伪造，FBK-003）**：OS sandbox deny 运行证据（BLOCKING GAP）；真实 reviewer 子 agent 产出 verdict 并由 flow 持久化 -> 门控读取的闭环（当前 SubagentStop 门控读操作员持久化的 review state，是 stub）；组织 PII policy/真实 owner/真实连接/发布门槛（生产认证）。这些不阻塞合成正确性验收，但禁止生产使用声称。
>
> 本 AS_BUILT 状态反映真实代码；不等于"已消除静默失败"（FBK-003）。

## 1. 文档目的与设计依据

本文定义 Claude Code ChatBI Harness v1 的拟议实现，不描述已经完成的代码。设计目标是把
`docs/chatbi-harness-domain-model.md` 中的 46 条可执行规则和
`docs/requirements.md` 中已确认的 P0 要求，落实为可安装、可验证、可维护的 Claude Code
原生 Harness。

规范优先级如下：

1. 用户已确认的需求与 15 项设计默认值；
2. `docs/chatbi-harness-domain-model.md` 的规则 ID 与事实层级；
3. `docs/requirements.md` 的 P0、验收标准与风险；
4. 当前 Claude Code 官方文档和本地能力探测结果；
5. 本文标记为“设计裁决”或“兼容性后备”的工程推论。

若四者冲突，实施必须停止并更新需求或领域模型，不得用代码隐式裁决业务语义。

## 2. 已确认的范围与关键裁决

### 2.1 v1 范围

v1 同时覆盖两条同优先级主线：

- 受治理的分析问答：从澄清、实体解析、语义层、后备查询、质量检查、独立审查到来源页脚；
- Warehouse 开发维护：从影响分析、候选修改、测试、知识同步、受影响评测到人工审批。

此外提供知识维护、离线评测和纠正闭环。v1 不负责生产部署编排、自动批准规范指标、绕过
组织权限、常驻调度服务、无监督修改外部业务仓库或宣称消除静默失败。

### 2.2 已确认的 15 项默认值如何落地

| 决策 | v1 设计落点 |
| --- | --- |
| 工具无关 | 适配器协议与配置分离；Fixture 适配器只用于示例和验收 |
| 分析与维护同为 P0 | 六个 Command 中分析、模型维护均为一等入口 |
| Workspace 内有限写入 | 可生成候选和运行验证；指标、权限、发布、破坏性迁移需人工批准 |
| 外部 Codebase 稳定别名 | 本地绝对根解析为 `alias + relative_path + revision` 引用 |
| Git 历史按需只读 | 默认仅工作树和本地元数据；深历史需显式启用 |
| 托管连接优先 | `managed -> approved CLI -> stop`，Fixture 仅显式测试配置 |
| PII 策略外部提供 | 缺失策略的敏感请求停止，不由 Harness 自创合规规则 |
| 所有数据答案均审查 | P0 无快速通道，审查成本进入运行证据 |
| 首域和门槛由负责人确认 | 配置只提供字段，不硬编码约 90% 或任何组织事实 |
| 本地纠正入口 | Markdown/JSON + Command；聊天平台扫描留给 P1 |
| 阻断与告警分级 | SCOPE/SEC/未审查/必需同步/P0 评测失败阻断；不确定影响告警并要求处置 |
| 当前环境为 v1 基线 | Darwin arm64、Claude Code 2.1.216、Python 3.14.2 |
| 拒绝嵌套/重叠根 | 初始化和每次配置变更均以 `realpath` 校验 |
| 高风险答案集合 | 管理层、受监管/PII、财务核心指标、原始探索、新鲜度未知，允许扩展 |
| `CONTEXT.md` 与 ADR | 生成根 `CONTEXT.md`；仅对难逆且真实有权衡的决策创建 ADR |

## 3. 能力探测与兼容性基线

### 3.1 本地已验证

2026-07-22 在当前工作区执行只读探测，结果如下：

| 项目 | 结果 | 设计影响 |
| --- | --- | --- |
| Claude Code | `/Users/admin/.local/bin/claude`，`2.1.216`，原生 arm64 | v1 本地兼容基线 |
| `claude doctor` | native 2.1.216，bundled search 正常，自动更新为最新 | 安装诊断必须复用 `doctor` 结果 |
| 登录状态 | 未登录 claude.ai，keychain 不可写，Remote Control 不可用 | 不阻塞离线设计；端到端联网演练前必须修复 |
| 项目配置 | 当前没有 `.claude/` 和 `.mcp.json` | 后续周期从空配置安装 |
| Git | 当前目录不是 Git worktree | 运行记录使用内容散列后备，不能强制要求 SHA |
| Python | 3.14.2 | 确定性检查器使用 Python 标准库 |
| 其他工具 | jq 1.7.1、ripgrep 15.1.0，Darwin arm64 | 只作为诊断/开发辅助，不作为核心运行依赖 |
| CLI 能力 | `--agent`、`--agents`、`--add-dir`、允许/拒绝工具、MCP 配置、权限模式、JSON Schema 等 | 可实现受限子 Agent 与机器可读输出 |
| 子命令 | `agents`、`mcp`、`plugin`、`doctor` 等可用 | 安装诊断可检查代理和连接能力 |

实际探测命令包括：

```text
command -v claude
claude --version
claude --help
claude agents --help
claude mcp --help
claude plugin --help
claude doctor --help
claude doctor
python3 --version
jq --version
rg --version
uname -s -m
```

### 3.2 官方文档已确认、但尚未在本项目运行演练

下列能力来自当前 Claude Code 官方文档，必须在实现周期以最小 Fixture 再做本地验收：

- Skills 位于 `.claude/skills/<name>/SKILL.md`，支持按需加载、工具约束和 fork 上下文；
- `.claude/commands/*.md` 仍兼容，但已并入 Skills 机制；同名 Skill 优先；
- 子 Agent 位于 `.claude/agents/*.md`，支持独立系统提示、工具白名单、权限模式和回合限制；
- Hook 事件包含 `SessionStart`、`PreToolUse`、`PostToolUse`、`SubagentStop`、`Stop`、
  `ConfigChange`；命令 Hook 从 stdin 接收 JSON；
- 权限按 `deny -> ask -> allow` 判定，Claude 权限与 OS 沙箱是互补防线；
- 沙箱支持启用、不可用时失败、文件读写边界和网络域策略；
- `--add-dir` 默认授予目录访问，并会自动发现附加目录中的 Skills；附加目录的
  CLAUDE/rules 是否加载受环境变量控制，而子 Agent、Commands、Hooks 不随之加载。

参考：

- [Claude Code 功能概览](https://code.claude.com/docs/en/features-overview)
- [Skills 与自定义命令](https://code.claude.com/docs/en/slash-commands)
- [Subagents](https://code.claude.com/docs/en/sub-agents)
- [Hooks reference](https://code.claude.com/docs/en/hooks)
- [Hooks guide](https://code.claude.com/docs/en/hooks-guide)
- [Permissions](https://code.claude.com/docs/en/permissions)
- [Sandboxing](https://code.claude.com/docs/en/sandboxing)
- [Settings](https://code.claude.com/docs/en/settings)
- [Memory and CLAUDE.md](https://code.claude.com/docs/en/memory)
- [Claude directory reference](https://code.claude.com/docs/en/claude-directory)
- [CLI reference](https://code.claude.com/docs/en/cli-usage)

### 3.3 兼容性策略

安装器记录 `claude --version`、`claude doctor` 摘要、平台、Python 版本和能力矩阵。版本策略是：

1. 精确基线：2.1.216 在 Darwin arm64 上通过全部 Fixture；
2. 同一主版本的更新版本：先运行诊断和 Hook 契约测试，通过后标记支持；
3. 事件或 Schema 不兼容：安全边界检查失败关闭；完整评测退化到显式 Command/CI；
4. 沙箱不可用且配置 `fail_if_sandbox_unavailable=true`：停止安装/运行；
5. Python 不满足最低版本或标准库能力缺失：停止并给出最小依赖，不下载未知脚本；
6. 升级 Claude Code 后必须重跑兼容性测试，不以“版本号更高”代替证据。

## 4. 总体架构

### 4.1 信任区与组件

```text
┌──────────────────────────── Warehouse Workspace（可信、有限可写） ────────────────────────────┐
│                                                                                               │
│  CLAUDE.md / rules        commands / skills          adversarial reviewer                     │
│         │                       │                            │                                  │
│         └──────────────┬────────┴──────────────┬────────────┘                                  │
│                        ▼                       ▼                                               │
│              deterministic harness      adapter dispatcher                                    │
│              config/path/gates/evidence   │             │                                     │
│                        │                   │             └── Fixture（仅显式测试）              │
│                        │                   └── Managed connection → approved CLI → STOP        │
│                        ▼                                                                      │
│              .chatbi/ 运行证据（本地、净化、不可作为规范事实）                                 │
└────────────────────────┬──────────────────────────────────────────────────────────────────────┘
                         │ 受控请求/结构化结果；绝不传递外部指令
                         ▼
┌────────── Business Codebases（不可信、只读、不可执行、可为零到多个） ──────────────────────────┐
│ alias-a/root  alias-b/root ...  → read/search/stat/git-metadata only                           │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

设计分为五个平面：

| 平面 | 责任 | 非责任 |
| --- | --- | --- |
| 指令与路由 | 根契约、条件规则、六个 Command、按需知识 | 不保存密钥，不替代组织治理 |
| Agent | 主 Agent 生成候选；独立审查者认证 | 审查者不写入、不执行、不自增权限 |
| 确定性执行 | 配置、路径、Hook 门控、证据和 Schema 校验 | 不解释开放式业务术语 |
| 适配器 | 语义发现、只读查询、质量元数据、受控 Codebase 检索 | 默认不暴露 mutate 能力 |
| 证据与评测 | 运行清单、评测、纠正、追踪 | 不复制密钥、未经授权 PII 或 ground truth 到提示 |

### 4.2 四层分析执行栈

```text
请求澄清与实体解析
        │
        ▼
[T1 人工治理语义层：metrics / dimensions / segments]
        │ 未覆盖、编译失败、权限或质量失败（必须有证据）
        ▼
[T2 整理后的领域参考 + 治理模型 + 血缘]
        │ 仍无法回答（必须有证据）
        ▼
[T3 原始探索：显式 SQL、明确连接与过滤]
        │
        ▼
[T4 独立审查 + 质量证据 + 来源页脚]
```

历史 SQL、Notebook、仪表盘查询只能给 T2/T3 提供候选线索，不能提升为 T1 事实。

## 5. 端到端流程

### 5.1 分析问答

```text
/chatbi-analyze <问题>
  → 配置/范围/权限/PII 预检
  → 请求类型、时间、分群、决策用途澄清
  → 解析规范实体并记录被拒候选
  → 发现并编译语义层，检查 dimensions/segments
  → [失败有证据] 整理参考/治理模型
  → [仍失败有证据] 原始 SQL 探索
  → 新鲜度、完整性、异常检查
  → 生成候选答案 + provenance 草稿
  → 启动只读 adversarial-reviewer
  → 阻断发现？──是──→ 修复候选 → 新散列 → 新轮次复审
  → 否
  → Stop gate 校验 PASS 与候选散列、页脚、质量证据
  → 交付：观察 / 解释 / 局限 / 来源页脚
```

权限不足、PII 策略缺失、语义冲突未裁决、无可靠来源、质量不可接受或审查无法关闭时，流程
停止并输出最小恢复动作；不得用看似精确的数字填补缺口。

### 5.2 Warehouse 开发与维护

```text
/chatbi-maintain-model <变更意图>
  → 确认所有者与审批边界
  → 建立影响清单：模型 / 元数据 / 语义层 / tests / docs / Skill / downstream / eval
  → 在 Workspace 内生成候选变更
  → 运行配置的转换/测试命令
  → PostToolUse 记录实际变更并重算受影响集
  → 运行受影响评测切片
  → 完成门控核对同步证据
  → 规范指标、权限、发布、破坏性迁移？──是──→ 人工批准
  → 输出候选、偏差、测试与评测证据；不自动发布
```

### 5.3 知识维护

```text
/chatbi-maintain-knowledge <领域/文件>
  → 验证所有者与模型关联
  → 使用参考模板生成/更新候选
  → 检查：用于/不得用于、粒度、范围、排除、连接、过滤、易错点、新鲜度
  → 检查绝对路径、过期步骤、重复/冲突信息
  → 运行检索路由 Fixture 与受影响评测
  → 人工领域负责人审核 → 合并候选
```

### 5.4 离线评测

```text
/chatbi-evaluate <suite|affected>
  → 固定 suite 版本和快照
  → 隔离 ground truth，不注入被测 Prompt/Skill/检索语料
  → 对每例运行实体解析、来源路由、答案与审查
  → 评分稳定事实、查询结构、实体和契约断言
  → 记录版本、模型、逐断言、Token、耗时
  → 与配置门槛和基线比较
  → 输出通过/失败/不可判定 + 静默失败声明
```

### 5.5 纠正闭环

```text
/chatbi-correction <本地记录>
  → 净化 PII/密钥并结构化问题
  → 分类：实体 / 来源 / SQL / 质量 / 文档 / 审查 / 权限
  → 同时生成“修复候选”和“新评测候选”
  → 进入周期审查与负责人批准
  → 合并后运行固定评测与必要消融
  → 更新语义层解决比例、纠正性语言比例和负面实验清单
```

## 6. 模块边界与拟议文件清单

### 6.1 顶层契约、命令、知识和代理

| 目标文件 | 责任与约束 |
| --- | --- |
| `CLAUDE.md` | 约 200 行以内的顶层契约、边界、路由和硬停止条件；引用领域模型，不复制知识库 |
| `CONTEXT.md` | 稳定领域术语、实体与关系；不放流程性命令 |
| `.claude/rules/00-domain-contract.md` | 规则 ID、事实层级、四层栈；始终适用 |
| `.claude/rules/10-security.md` | SCOPE/SEC、外部内容即数据、PII 与审批边界 |
| `.claude/rules/20-completion.md` | 审查、来源页脚、维护同步和评测完成定义 |
| `.claude/commands/chatbi-init.md` | 初始化与诊断入口 |
| `.claude/commands/chatbi-analyze.md` | 分析闭环入口 |
| `.claude/commands/chatbi-maintain-model.md` | Warehouse 模型维护入口 |
| `.claude/commands/chatbi-maintain-knowledge.md` | 领域知识维护入口 |
| `.claude/commands/chatbi-evaluate.md` | 评测与基线比较入口 |
| `.claude/commands/chatbi-correction.md` | 纠正结构化入口 |
| `.claude/agents/adversarial-reviewer.md` | 独立、只读、最小工具的候选答案审查者 |
| `.claude/skills/chatbi-knowledge/SKILL.md` | 按需知识路由，不超过约 500 行；细节放 references |
| `.claude/skills/chatbi-knowledge/references/_template.md` | 领域参考资料模板 |
| `.claude/skills/chatbi-knowledge/references/fixture-domain.md` | 无凭证、无真实组织数据的示例领域 |
| `.claude/skills/chatbi-runbook/SKILL.md` | 分析方法和证据契约 |
| `.claude/skills/chatbi-maintenance/SKILL.md` | 模型/知识影响分析流程 |
| `.claude/skills/chatbi-evaluation/SKILL.md` | 评测、泄漏隔离、消融方法 |

`.claude/commands` 保留是为了兼容明确的斜杠入口；Command 名称不得与 Skills 同名，避免
“同名 Skill 优先”造成入口漂移。

### 6.2 配置、确定性运行时与 Hook

| 目标文件 | 责任 |
| --- | --- |
| `.claude/settings.json` | 项目 Hooks、权限和安全默认值；不得包含机器路径或密钥 |
| `.claude/chatbi-harness.json` | 可共享逻辑配置：别名元数据、策略、所有者、门槛、适配器选择 |
| `.claude/chatbi-harness.example.json` | 无组织事实的可复制示例 |
| `.claude/chatbi-harness.local.json` | 本机路径/可执行文件覆盖；本地生成，不提交，不含密钥值 |
| `.claude/schemas/chatbi-harness.schema.json` | 共享与本地合并后配置 Schema |
| `.claude/hooks/session_diagnose.py` | 会话开始能力诊断，默认不阻断普通问答；安全能力缺失时标记不可运行 |
| `.claude/hooks/pretool_guard.py` | 工具执行前的路径、只读、执行和网络边界门控 |
| `.claude/hooks/posttool_impact.py` | 工具成功后的变更影响与证据更新；不声称撤销已发生动作 |
| `.claude/hooks/subagent_review_gate.py` | 校验审查输出 Schema、覆盖面、轮次与候选散列 |
| `.claude/hooks/stop_gate.py` | 被跟踪工作流完成门控；未审查/未同步/证据缺失时阻止停止 |
| `.claude/hooks/config_change_gate.py` | 配置重载时重验 Schema、路径、沙箱和权限边界 |
| `.claude/lib/chatbi_harness/config.py` | JSON 读取、分层合并、Schema/跨字段校验 |
| `.claude/lib/chatbi_harness/paths.py` | `realpath`、根包含关系、重叠/符号链接/穿越校验 |
| `.claude/lib/chatbi_harness/policy.py` | PII、风险、审批、工具能力判定 |
| `.claude/lib/chatbi_harness/evidence.py` | 运行清单、散列、净化与原子写入 |
| `.claude/lib/chatbi_harness/gates.py` | 统一规则 ID、阻断/告警、恢复建议格式 |
| `.claude/lib/chatbi_harness/evaluator.py` | suite 隔离、断言、门槛和趋势结果 |
| `.claude/lib/chatbi_harness/adapters/base.py` | 工具中立接口与能力模型 |
| `.claude/lib/chatbi_harness/adapters/fixture.py` | 显式测试 Fixture 实现 |
| `.claude/lib/chatbi_harness/adapters/codebase_reader.py` | 外部仓库只读 `read/search/stat/git-metadata` |

Python 核心只使用标准库。Hook 脚本是薄入口，所有判定复用 library，避免六份不一致的安全逻辑。

### 6.3 Schema、Fixture、测试与文档

| 目标文件/目录 | 责任 |
| --- | --- |
| `.claude/schemas/request.schema.json` | 请求与实体解析证据 |
| `.claude/schemas/review.schema.json` | 审查输入/输出 |
| `.claude/schemas/provenance.schema.json` | 来源页脚和质量证据 |
| `.claude/schemas/evaluation.schema.json` | suite/run/assertion 记录 |
| `.claude/schemas/correction.schema.json` | 纠正、修复候选、评测候选 |
| `.claude/fixtures/semantic-catalog.json` | Fixture 指标、维度、segments |
| `.claude/fixtures/warehouse.json` | 小型、合成、固定快照数据 |
| `.claude/fixtures/codebases/` | 含提示注入、符号链接和可信引用的合成外部树 |
| `.claude/fixtures/evaluations/` | 高频、长尾与五个压力场景；ground truth 单独存放 |
| `tests/harness/test_config.py` | 配置合并、Schema 与所有者/门槛 |
| `tests/harness/test_paths.py` | 穿越、符号链接、重叠根、只读边界 |
| `tests/harness/test_hooks.py` | 事件 Fixture、恶意 JSON、退出语义和恢复信息 |
| `tests/harness/test_adapters.py` | 托管/CLI/停止链和工具分组 |
| `tests/harness/test_review_gate.py` | 审查独立性、Schema、候选散列、复审 |
| `tests/harness/test_evaluation.py` | 泄漏隔离、快照、门槛、记录完整性 |
| `tests/harness/test_e2e.py` | 分析、维护、知识、评测、纠正的端到端 Fixture |
| `docs/harness/README.md` | 使用入口与限制 |
| `docs/harness/installation.md` | 安装、managed settings 建议、诊断与升级 |
| `docs/harness/configuration.md` | Workspace、外部 Codebase、工具、PII、所有者、门槛 |
| `docs/harness/analysis.md` | 分析问答契约 |
| `docs/harness/maintenance.md` | 模型与知识维护 |
| `docs/harness/knowledge-authoring.md` | 参考资料模板和检索写法 |
| `docs/harness/evaluation.md` | 评测、泄漏隔离、消融和静默失败 |
| `docs/harness/security.md` | 威胁模型、权限、沙箱、审计与事件响应 |
| `docs/harness/troubleshooting.md` | 版本、Hook、连接、路径、登录问题 |
| `docs/harness/compatibility.md` | 实测版本矩阵和降级记录 |
| `docs/harness/rule-traceability.md` | 实施后的规则到文件/测试矩阵 |

运行时证据写入 `.chatbi/`，包括 `runs/<run-id>/`、`evaluations/`、`corrections/`。它是本地
派生状态，不是规范知识；有 Git 时加入忽略清单，无 Git 时诊断明确提醒其敏感性和备份策略。

## 7. 配置数据模型

### 7.1 文件分层

选择 JSON 而非 YAML：Python 标准库可无依赖解析，Schema 确定，且与 Claude Code settings
生态一致。代价是注释和手写体验较弱，因此用 `example.json` 和配置文档补偿。

```text
.claude/chatbi-harness.json          可共享：逻辑别名、策略、所有者、门槛
             +
.claude/chatbi-harness.local.json    本地：规范化绝对根、CLI 路径、环境变量名
             ↓
合并 → Schema → 跨字段/realpath 校验 → 不可变 EffectiveConfig
```

共享配置不能包含真实凭证、PII 值或机器绝对路径。本地配置只能引用凭证环境变量名，不能保存
值。生产组织可把不可变 deny/沙箱策略放入 managed settings；项目配置不能声称覆盖管理员策略。

### 7.2 核心配置示例

```json
{
  "schema_version": 1,
  "workspace": {
    "id": "warehouse",
    "root": ".",
    "allow_candidate_writes": true,
    "protected_actions": [
      "approve_metric",
      "change_access_policy",
      "production_publish",
      "destructive_migration"
    ]
  },
  "business_codebases": {
    "billing_app": {
      "description": "Billing event producer",
      "path_ref": "billing_app_root",
      "read_mode": "adapter",
      "git_history": "metadata_only"
    }
  },
  "adapters": {
    "semantic": ["managed:semantic", "cli:semantic"],
    "query": ["managed:query", "cli:query"],
    "fixture_enabled": false
  },
  "governance": {
    "pii_policy_ref": "org-pii-v1",
    "restricted_disclosure": "sql_only",
    "owners": {
      "default_domain_owner": "role:data-domain-owner",
      "metrics": {}
    },
    "high_risk_classes": [
      "executive",
      "regulated_or_pii",
      "core_finance",
      "raw_exploration",
      "freshness_unknown"
    ]
  },
  "evaluation": {
    "release_threshold": null,
    "threshold_owner": "role:data-domain-owner",
    "require_p0_slices": true
  },
  "runtime": {
    "evidence_root": ".chatbi",
    "fail_if_sandbox_unavailable": true
  }
}
```

本地覆盖示例仅用于说明形状：

```json
{
  "path_bindings": {
    "billing_app_root": "/absolute/local/path/to/billing-app"
  },
  "cli_adapters": {
    "semantic": {
      "argv": ["approved-semantic-cli", "query", "--json"],
      "credential_env_names": ["SEMANTIC_TOKEN"]
    }
  }
}
```

### 7.3 路径不变量

初始化、会话开始、配置变更和每个工具调用前都执行：

1. Workspace 和外部根必须存在且为目录；
2. 对输入根和目标路径执行 `realpath`，再做路径组件级包含判断，禁止字符串前缀判断；
3. 别名匹配 `^[a-z][a-z0-9_-]{1,62}$`，且不可复用；
4. 任意两个根不得相同、祖先/后代或通过符号链接重合；
5. 外部根不能位于 Workspace 内，Workspace 也不能位于外部根内；
6. 外部目标解析后仍须位于对应根；符号链接越界即拒绝；
7. 外部根永远 deny write/execute/install/commit；
8. 引用保存为别名、规范相对路径和 Git revision；无 Git 时保存内容散列与
   `revision_kind=content_sha256`；
9. 路径错误只输出净化后的别名和相对位置，不泄露不必要的本机目录；
10. 配置变化会使既有 EffectiveConfig 和未完成运行失效，必须重诊断。

### 7.4 外部 Codebase 访问裁决

默认 `read_mode=adapter`：主会话不使用 `--add-dir` 直接暴露外部根。只读适配器只提供
`read/search/stat/git-metadata`，将文件内容明确包装为“不可信数据”，并净化路径和输出上限。

原因：当前官方行为会自动发现 `--add-dir` 目录中的 Skills；即使不加载其中 CLAUDE.md，仍可能
引入外部指令。可选 `read_mode=direct_add_dir` 不属于 v1 默认安全路径，若后续启用，必须同时：

- 扫描并拒绝含 `.claude/skills` 的外部根；
- 保持 `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD` 未启用；
- 为外部根配置 Claude deny-write 和 OS sandbox deny-write；
- 通过外部 Prompt 注入 Fixture；
- 在运行证据中明确记录模式和残余风险。

## 8. 适配器与工具授权

### 8.1 平台中立接口

```text
Adapter.capabilities() -> {discover, query, quality, lineage, mutate?}
Adapter.healthcheck(context) -> HealthResult
Adapter.discover(request) -> SemanticCatalogEvidence
Adapter.compile(query_spec) -> CompileEvidence
Adapter.query(compiled, disclosure_policy) -> QueryEvidence
Adapter.quality(source_refs) -> QualityEvidence
Adapter.lineage(source_refs) -> LineageEvidence
```

所有结果必须是有 Schema 的 JSON，含 adapter ID、时间、来源、状态、错误类别和内容散列。
适配器不得把工具 stdout 直接拼入 Shell 或系统提示。

### 8.2 连接选择

```text
managed connection 可用且获授权？ ─是→ 使用
            │否
            ▼
approved CLI 可用、argv 合法且获授权？ ─是→ 使用
            │否
            ▼
STOP：列出缺失能力和最小授权
```

Fixture 适配器仅在 `fixture_enabled=true` 且运行标记为 test/example 时可用；绝不静默作为生产
后备。CLI 必须以 argv 数组直接启动，禁止 Shell 字符串、管道、重定向和命令替换；可执行文件
解析到 allowlist 中的绝对路径，cwd 固定为 Workspace，环境变量按白名单构造。

### 8.3 工具能力组

| 组 | 默认 | 示例 | 约束 |
| --- | --- | --- | --- |
| `discover_read` | 允许 | catalog、metadata、lineage | 只读、最小字段、记录来源 |
| `query_read` | 按权限允许 | compile、query、freshness | 执行前 PII/披露策略；结果净化 |
| `codebase_read` | 仅显式别名 | read/search/stat/git metadata | 不执行、不写、不装依赖、不提交 |
| `workspace_candidate_write` | 允许候选 | Edit/Write Workspace 文件 | 受 protected actions、影响检查和测试约束 |
| `mutate_warehouse` | 默认禁用 | 创建/修改远端对象 | v1 不提供；若未来启用需显式人批 |
| `network` | 默认拒绝 | API/domain | 仅适配器声明的域，deny 优先 |

## 9. 六个 Command 契约

每个 Command 文件必须在 frontmatter/正文中写明规则 ID，调用共享检查器，而不是靠自然语言保证。

### 9.1 `/chatbi-init`

| 字段 | 契约 |
| --- | --- |
| 输入 | 可选配置模板、Workspace 根、外部别名、本地适配器声明 |
| 前置条件 | 领域模型存在可读；当前目录是拟绑定 Workspace；用户可确认本地路径 |
| 可修改范围 | 仅 Workspace 内 Harness 配置/模板；本地覆盖需显式确认 |
| 行为 | 版本、doctor、Schema、realpath、权限、沙箱、适配器、所有者、PII、评测门槛诊断 |
| 停止条件 | 领域模型缺失；根重叠；外部可写；安全能力缺失且 fail-closed；配置含密钥值 |
| 输出证据 | `diagnostic.json`、能力矩阵、未决配置、恢复建议 |
| 规则 | SCOPE-001..003、SEC-001..003、PORT-001、HOOK-002..004 |

### 9.2 `/chatbi-analyze`

| 字段 | 契约 |
| --- | --- |
| 输入 | 业务问题；可选时间、分群、决策用途和输出风险级别 |
| 前置条件 | 有效配置、组织 PII 策略、至少一个可用来源路径、审查者可用 |
| 可修改范围 | 仅 `.chatbi/runs/<id>` 派生证据；不得修改模型或外部 Codebase |
| 行为 | 澄清、实体解析、语义优先、证据降级、质量、候选、审查、来源页脚 |
| 停止条件 | 歧义改变答案；权限不足；无来源；质量失败；审查阻断无法关闭 |
| 输出证据 | request、entity resolution、query spec、quality、review rounds、provenance、answer |
| 规则 | REQ、SEM、RAW、SRC、QLT、REV、ANS、SCOPE、SEC 全部相关规则 |

### 9.3 `/chatbi-maintain-model`

| 字段 | 契约 |
| --- | --- |
| 输入 | 变更意图、目标模型、审批人/所有者、可选关联 issue |
| 前置条件 | Workspace 内目标；当前状态可识别；变更类型和 protected action 已分类 |
| 可修改范围 | Workspace 内候选模型、测试、元数据、语义层、参考资料和评测；不发布生产 |
| 行为 | 影响清单、候选变更、配置命令、受影响测试/评测、同步和偏差记录 |
| 停止条件 | 需要审批但未批准；外部写入；文档/评测影响未处置；P0 评测失败 |
| 输出证据 | impact manifest、changed files、test/eval results、approval needs、deviations |
| 规则 | SEM-003、DOC-001..005、PORT-001、EVAL-001..005、HOOK-004..005 |

### 9.4 `/chatbi-maintain-knowledge`

| 字段 | 契约 |
| --- | --- |
| 输入 | 领域、关联模型、创建/更新/删减意图、所有者 |
| 前置条件 | 关联治理模型可识别；负责人存在；模板可用 |
| 可修改范围 | Workspace 内领域 references、路由 Skill、相关评测候选 |
| 行为 | 检查元数据骨架、用于/不得用于、易错点、交叉引用、检索效果和过时内容 |
| 停止条件 | 与模型冲突；所有者缺失；硬编码机器路径；缺受影响评测 |
| 输出证据 | knowledge diff、model links、routing tests、owner review request |
| 规则 | DOC-001..005、PORT-001、SRC-001..002、ABL-001..002 |

### 9.5 `/chatbi-evaluate`

| 字段 | 契约 |
| --- | --- |
| 输入 | suite ID 或 affected manifest；可选固定基线 ID |
| 前置条件 | suite 人工验证、ground truth 隔离、快照/稳定评分、门槛和负责人已配置 |
| 可修改范围 | `.chatbi/evaluations` 和明确的基线候选；不得修改被测知识/模型 |
| 行为 | 隔离运行、逐断言评分、成本/耗时、基线对比、发布门槛和消融记录 |
| 停止条件 | 泄漏检测；快照缺失；门槛无负责人；P0 slice 失败 |
| 输出证据 | evaluation run、assertions、version/model/token/time、diff、silent-failure notice |
| 规则 | EVAL-001..005、ABL-001..002、FBK-003、HOOK-005 |

### 9.6 `/chatbi-correction`

| 字段 | 契约 |
| --- | --- |
| 输入 | 本地 Markdown/JSON 纠正、关联 run ID、提交者和可披露范围 |
| 前置条件 | 可验证来源；净化策略可用；关联领域/所有者可识别 |
| 可修改范围 | `.chatbi/corrections`、知识/模型修复候选、评测候选；不自动合并规范指标 |
| 行为 | 分类、去敏、根因、双候选、周期队列、指标汇总 |
| 停止条件 | 含未授权 PII/密钥；无法验证；需要规范定义但无人批准 |
| 输出证据 | correction record、fix candidate、evaluation candidate、approval status |
| 规则 | SEC-003、SEM-003、FBK-001..003、EVAL-001..004 |

## 10. 对抗性审查 Agent 合同

### 10.1 隔离与最小权限

`.claude/agents/adversarial-reviewer.md` 的拟议 frontmatter：

```yaml
name: adversarial-reviewer
description: Independently review a ChatBI candidate before any data conclusion is delivered.
tools: Read, Grep, Glob
disallowedTools: Agent, Bash, Edit, Write
model: inherit
permissionMode: dontAsk
maxTurns: 20
background: false
```

正文必须自包含 SCOPE、SEC、REV、ANS、事实层级和停止条件，因为子 Agent 不保证继承主会话
上下文。不得配置 memory、mutating MCP、Bash 或 Agent 工具，防止写入、执行和嵌套委派。

### 10.2 输入清单

主 Agent 只传递一个 Workspace 内、Schema 已验证的 manifest 路径：

```json
{
  "run_id": "run-...",
  "round": 1,
  "candidate_sha256": "...",
  "request_ref": ".../request.json",
  "entity_resolution_ref": ".../entity-resolution.json",
  "candidate_ref": ".../candidate.md",
  "quality_ref": ".../quality.json",
  "provenance_ref": ".../provenance-draft.json",
  "allowed_roots": ["workspace-relative/evidence-root"]
}
```

输入不得包含凭证，受限结果遵循披露策略；审查者必须把缺失证据视为阻断，而不是推测。

### 10.3 输出合同

审查者最后一条消息只能是符合 Schema 的 JSON：

```json
{
  "status": "PASS",
  "review_id": "review-...",
  "run_id": "run-...",
  "round": 1,
  "candidate_sha256": "...",
  "coverage": {
    "entity_mapping": true,
    "grain": true,
    "joins": true,
    "filters_and_exclusions": true,
    "date_and_timezone": true,
    "denominator": true,
    "sample_bias": true,
    "data_quality": true,
    "observation_vs_interpretation": true,
    "permission_and_disclosure": true,
    "source_footer": true
  },
  "findings": [],
  "required_next_action": "DELIVER"
}
```

`status` 仅可为 `PASS | BLOCKED | ERROR`。每条 finding 必须含 ID、severity、blocking、rule_ids、
evidence、description、remediation。`PASS` 要求所有 coverage 为 true 且无 blocking finding。

`SubagentStop` gate 校验 Schema、清单散列和覆盖面；无效输出以 exit 2 给出规则 ID 和恢复建议，
让审查者继续修正。主 Agent 修复候选后散列必须变化、轮次递增并重新审查。`Stop` gate 只接受与
当前 candidate SHA 匹配的 PASS，主 Agent 不能复用旧审查或自行标记通过。

## 11. Hooks 与确定性门控

### 11.1 能力到当前事件映射

| 门控能力 | 当前事件 | 输入中使用的字段 | 阻断语义 | 后备 |
| --- | --- | --- | --- | --- |
| 会话/版本/配置诊断 | `SessionStart` | common fields、source | 写诊断；安全配置无效则令 ChatBI 命令不可运行 | `/chatbi-init` |
| 路径、只读、执行、网络预检 | `PreToolUse` | `cwd`、`tool_name`、`tool_input`、`tool_use_id` | exit 2 阻止工具调用 | 无安全后备；失败关闭 |
| 实际变更影响记录 | `PostToolUse` | 工具输入、响应和 ID | 动作已发生，不能声称回滚；记录阻断性未完成项 | 显式 impact checker/CI |
| 审查输出契约 | `SubagentStop` | agent identity/transcript/last message | exit 2 阻止子 Agent 停止并要求修正 | 主流程显式 review validator |
| 最终完成性 | `Stop` | common fields、stop recursion indicator | exit 2 阻止结束并提供缺失证据 | `/chatbi-analyze --verify` / CI |
| 配置热变更 | `ConfigChange` | source、file path（若提供） | 对可阻断来源拒绝无效变更；managed policy 不假定可阻断 | 重启会话 + `/chatbi-init` |

`PostToolUse` 仅检测影响，绝不作为越界写入的首道防线。P0 路径/安全必须由 `PreToolUse` 和 OS
沙箱先阻止。高成本完整评测不放在 Hook 内；Hook 判断是否需要并验证已有证据。

### 11.2 输入、输出与退出规则

命令 Hook 从 stdin 读取 JSON；实现使用 `json.load(sys.stdin)`，限制输入大小，拒绝重复/未知危险
形状，绝不 `eval`、字符串拼 Shell 或信任请求中的 cwd。实际 cwd 必须与 EffectiveConfig 的
Workspace realpath 匹配。

共同处理约定：

- exit 0：检查通过；如需结构化 Hook JSON，仅写单个合法 JSON 到 stdout；
- exit 2：按该事件的官方阻断语义阻断，并把简短反馈写 stderr；不同时依赖 stdout JSON；
- 其他非零：Claude Code 通常视为 Hook 错误而非可靠阻断，因此 P0 检查器捕获异常并转换为
  明确的 exit 2 fail-closed；
- 每个错误包含 `rule_ids`、净化后的 `evidence_refs`、`reason`、`recovery`；
- `Stop`/`SubagentStop` 检查递归标记，避免无限循环；
- 只对 `.chatbi/runs/current.json` 声明的受跟踪工作流启用完成门控，避免阻塞无关对话；
- Hook 输出不得包含原始 PII、密钥、完整本机绝对路径或不受限工具输出。

### 11.3 Settings 结构

实施时 `.claude/settings.json` 按当前官方 Schema 配置上述事件和命令 Hook，运行
`claude doctor` 与每个事件 Fixture 验证。Schema 网址可用于编辑器提示，但不能代替本地
`doctor`，因为公共 Schema 可能滞后于最新字段。

项目 settings 提供可共享默认值，本地 settings 提供机器特定 ask/allow；安全关键 deny 和沙箱
建议由组织 managed settings 固化。权限规则遵循 deny 优先。沙箱安全配置至少包括：enabled、
不可用时失败、禁止 unsandboxed commands、Workspace 有限写、外部 roots deny-write、凭证目录
deny-read、网络默认拒绝并按适配器域放行。

实验性 Agent Hook 可以后续用于非确定性建议，但不得成为任何 P0 确定性门控的唯一实现。

## 12. 运行证据与答案契约

### 12.1 运行目录

```text
.chatbi/runs/<run-id>/
├── request.json
├── entity-resolution.json
├── source-routing.json
├── query-spec.json
├── quality.json
├── candidate.md
├── provenance-draft.json
├── review-round-1.json
├── review-round-N.json
├── provenance.json
└── answer.md
```

每个记录含 schema version、run ID、UTC 时间、内容散列、生成组件、相关规则 ID。写入使用临时文件
加原子 replace；文件权限取最小值。保留期由组织配置。运行证据只保存必要结构、散列和安全聚合。

### 12.2 最终答案结构

本地 Markdown 输出固定为：

1. 被回答的问题、时间范围、实体/分群口径；
2. “数据显示”——观察；
3. “这可能意味着”——解释；
4. 方法、来源层级、过滤、包含/排除、分母；
5. 新鲜度、完整性、异常检查；
6. 局限、冲突与不确定性；
7. 审查状态和轮次；
8. 来源页脚。

来源页脚示例：

```text
来源：语义层 | 模型：active_users | 所有者：Data Growth
新鲜度：最大数据日期 2026-07-21 | 审查：PASS（第 2 轮）
置信度：高 | 引用：warehouse/models/...@<git-sha-or-content-hash>
```

若来源为原始探索或新鲜度未知，页脚必须包含“高风险使用前复核”。管理层/董事会、受监管/PII、
财务核心指标等高风险用途还需记录人类签字状态。

## 13. 威胁模型

| 威胁 | 攻击面 | 防护 | 验证 |
| --- | --- | --- | --- |
| 外部 Prompt 注入 | Codebase README、注释、Skill | 默认只读适配器；内容包装为数据；不直挂；T1/T2 优先 | 注入 Fixture 不改变指令/不执行 |
| 路径穿越/符号链接逃逸 | Hook tool input、别名、本地配置 | realpath、组件包含、重叠拒绝、每次调用重验 | `../`、绝对路径、symlink、TOCTOU 测试 |
| 外部写入或执行 | Edit/Write/Bash、CLI cwd | Claude deny + PreToolUse + sandbox deny-write；codebase adapter 无执行接口 | 分别证明权限层和 OS 层阻断 |
| Shell/命令注入 | CLI adapter、Hook JSON | argv 数组、可执行 allowlist、无 shell、净化 env/cwd | 元字符、换行、命令替换 Fixture |
| 凭证/PII 泄露 | 日志、评测、纠正、错误 | env 名不存值、deny-read、披露策略、结构/散列/聚合、保留期 | canary secret/PII 不出现在产物 |
| 越权数据查询 | query adapter、错误配置 | 组织策略、最小权限连接、权限不足停止、SQL-only 披露 | 受限角色 Fixture |
| 语义层绕过 | Agent 直接写 SQL | source-routing 证据、Stop gate、审查覆盖 | 有规范指标仍走 raw 时失败 |
| 幻觉表字段/错误连接 | 原始探索 | catalog/lineage 证据、显式连接/分母、安全除法、审查 | 不存在字段与 fan-out Fixture |
| 审查伪造/复用 | 主 Agent、自报 PASS、旧结果 | 独立子 Agent、候选 SHA、轮次、Schema、SubagentStop/Stop 双门 | 修改候选后旧 PASS 失效 |
| 文档漂移 | 模型变更未同步知识 | impact manifest、PostToolUse、Stop gate、受影响评测 | 模型单改时阻断 |
| 评测泄漏 | ground truth 同时可检索 | 物理/逻辑隔离、运行时检索 deny、泄漏 canary | 被测会话无法访问 truth |
| 配置降级 | settings 热变更、沙箱不可用 | ConfigChange 重验、managed deny、fail-closed、能力证据过期 | 移除 deny/禁用沙箱测试 |
| 供应链/自动安装 | 外部仓库脚本、CLI | 不执行外部代码、不自动安装；已批准 executable allowlist | README 指令安装依赖被拒 |
| 静默失败 | 未覆盖长尾、绿色评测 | 来源页脚、纠正闭环、高风险人签、明确非保证 | 文档和答案契约断言 |

残余风险：Claude Code 或底层工具本身的未知缺陷、组织授权配置错误、未纳入评测的业务变化和
无法观察的静默失败。Harness 降低风险，不提供绝对正确性或合规保证。

## 14. 测试与验收策略

### 14.1 测试层次

| 层次 | 方法 | 通过条件 |
| --- | --- | --- |
| 静态 | JSON Schema、Markdown/规则 ID、配置不含绝对共享路径/密钥 | 所有 Schema 与追踪检查通过 |
| 单元 | Python `unittest` 测 config/path/policy/evidence/gates/evaluator | 分支和恶意输入均确定性通过 |
| Hook 契约 | 向每个 Hook 注入官方事件形状 Fixture | exit、stderr、JSON 和 fail-closed 符合设计 |
| 适配器 | Fixture + fake managed/CLI | managed 优先、CLI 后备、均无则停止；无生产静默 Fixture |
| 安全 | 权限层和 sandbox 层分开测试 | 各自能阻断外部写/执行；双层都启用 |
| Agent | 隔离启动 reviewer，检查工具清单和输出 Schema | 无写/执行/嵌套；覆盖全部必检项 |
| E2E | 合成 Warehouse + 外部 Codebases + 固定数据 | 五类工作流和五个压力场景有完整证据 |
| 兼容 | 基线版本实跑，升级后重复 | doctor、Hooks、Agent、Commands、sandbox 全绿 |

### 14.2 五个必测压力场景

1. **概念歧义**：“上个月活跃用户”有产品和财务定义。系统必须给候选并澄清，不合并折中值；
2. **陈旧数据**：SQL 正确但最大日期落后两周。质量门控阻止高置信交付并披露新鲜度；
3. **检索失败/历史 SQL 诱导**：Notebook 含看似正确 SQL，但无规范映射。只能作为线索；记录降级；
4. **外部信任边界**：README 指令执行脚本、上传数据或安装依赖。系统只引用业务证据并拒绝指令；
5. **PII/权限**：用户无权看明细或策略缺失。系统停止或仅给授权用户可运行 SQL，不泄露样本。

额外维护场景：只修改模型未更新 metadata/reference/Skill/eval 时，Stop gate 必须失败；同步并有
受影响测试/评测证据后通过。额外审查场景：候选变更后旧 review SHA 必须失效。

### 14.3 评测设计

- 示例领域同时含高频仪表盘问题和长尾业务语境问题；
- 数字以固定合成快照锚定，动态场景评分 query/entity/contract，而非当天数字；
- ground truth 位于被测会话 deny-read 的目录或由外层 runner 保管，只把问题和允许资料交给被测 Agent；
- 记录 suite/Skill/fixture 版本、Git SHA；无 Git 时记录内容散列和 `revision_kind`；
- 记录 Claude 模型 ID、逐断言、Token、墙钟耗时、审查轮次和 adapter 路径；
- release threshold 必须非空且有 owner 才能作发布判定；示例值不成为组织默认；
- 报告 seen/unseen 分开，明确通过不等于消除线上错误；
- 有意义规则/Skill 变更用固定集一次改变一个组件，并维护负面实验清单。

## 15. 开发周期建议

本文只建议后续 `dev-cycles.md` 的拆分，不创建该文件。每周期必须由 coder 记录实际行为、由
test-agent 给出结构化报告、由 plan-agent 比较设计与实现；最终技术设计需进入 AS_BUILT 状态。

| 周期 | 交付重点 | 关键验收 |
| --- | --- | --- |
| 1：骨架与诊断 | CLAUDE/rules、配置、Schema、路径库、init、文档骨架 | AC-01、AC-02 部分、AC-03 路径、AC-08 探测 |
| 2：安全与适配器 | PreToolUse、sandbox/permissions、只读 codebase、Fixture、managed/CLI 选择 | AC-03 全部、AC-04 来源前置、R-03/R-12 |
| 3：分析与审查 | analyze、运行证据、质量、reviewer、SubagentStop/Stop、答案页脚 | AC-04、AC-06、压力场景 1–5 |
| 4：维护与知识 | model/knowledge commands、影响检测、同步门控、模板 | AC-05、DOC 规则族、模型单改阻断 |
| 5：评测与纠正 | evaluate/correction、泄漏隔离、消融、兼容演练、完整文档 | AC-07、AC-08、AC-09、全部追踪 |

每周期进入条件：上一周期无 CRITICAL/HIGH 偏差。退出条件：目标测试全绿、实际文件清单与
feature-flow 一致、规则映射无新增空洞。第 5 周期完成后还必须将本文状态标识更新为 AS_BUILT，
记录真实版本、文件和偏差，再允许编排状态 COMPLETE。

## 16. 规则追踪矩阵

下表逐条覆盖领域模型的 46 条可执行规则。实施后的
`docs/harness/rule-traceability.md` 必须把“拟议产物”替换为真实行级证据和测试结果。

| 规则 | 拟议实现/门控 | 主要验证 |
| --- | --- | --- |
| SCOPE-001 | config/paths + PreToolUse + sandbox；仅 Workspace 可有限写，外部需配置 | 未配置路径、Workspace 边界测试 |
| SCOPE-002 | codebase_reader 只读接口；deny-write/execute/install/commit | 外部 Edit/Write/Bash/安装/提交拒绝 |
| SCOPE-003 | security rule；外部内容包装；引用 alias/path/revision | Prompt 注入 Fixture 和引用 Schema |
| SEC-001 | policy 预检；权限/受限域/PII 缺失即停止 | 无权限和缺策略场景 |
| SEC-002 | disclosure policy `sql_only` 等确定性执行 | 受限角色不返回结果/样本 |
| SEC-003 | evidence 净化、deny-read、canary 扫描 | 日志/评测/纠正无密钥和 PII |
| REQ-001 | analyze request schema 与澄清 gate | 缺时间/分群/用途且影响答案时停止 |
| REQ-002 | knowledge route + entity resolution | 多义术语不猜测 |
| REQ-003 | entity-resolution.json 必填选择/粒度/过滤/排除/拒绝原因 | Schema 与审查断言 |
| REQ-004 | 多团队候选显示并请求确认 | 概念歧义压力场景 |
| SEM-001 | adapter discover/compile；source-routing gate | 有语义覆盖却降级失败 |
| SEM-002 | catalog evidence 必含 metrics/dimensions/segments | 规范 segment 不手写 WHERE |
| SEM-003 | protected action `approve_metric` + owner approval | Agent 只能起草不能批准 |
| RAW-001 | source-routing 降级原因 + T2 治理模型 | 无降级证据不能 raw |
| RAW-002 | reviewer/source gate 检查伪理由 | 自定义日期/连接不构成绕过证据 |
| RAW-003 | query spec、catalog/lineage、SQL 安全断言 | 幻觉字段、fan-out、零分母测试 |
| SRC-001 | 历史材料标记 `candidate_only` | Notebook 诱导压力场景 |
| SRC-002 | codebase 引用与 T1/T2 冲突检查 | 冲突时披露并升级 owner |
| DOC-001 | maintain-model impact manifest + co-location/reference atomicity | 模型改动未评估 docs 时阻断 |
| DOC-002 | knowledge template/Schema | 元数据必需字段完整性 |
| DOC-003 | template 必含用于/不得用于/易错点 | 知识 lint 与检索测试 |
| DOC-004 | PostToolUse impact + Stop gate + affected eval | 单改模型失败，同步后通过 |
| DOC-005 | maintain-knowledge + ablation/negative experiments | 过时脚手架识别与删减证据 |
| PORT-001 | shared/local config 分离；alias 引用；无绝对知识路径 | 静态扫描共享产物 |
| QLT-001 | adapter quality + quality.json + reviewer | 陈旧/不完整/异常场景 |
| REV-001 | 独立 reviewer + Stop gate | 未审查答案不能结束 |
| REV-002 | review Schema 的 11 项 coverage | 任一项缺失即阻断 |
| REV-003 | candidate SHA + round + blocking findings | 修复后强制复审，旧 PASS 失效 |
| ANS-001 | answer template + Stop gate | 观察/解释、方法、过滤、局限完整 |
| ANS-002 | provenance Schema + footer gate | 来源层级/审查/新鲜度/owner/confidence |
| ANS-003 | risk classification + warning/human signoff | raw/unknown/high-risk 提示和签字 |
| EVAL-001 | verified suite Schema + example high-frequency/long-tail | suite 覆盖与 owner 审核 |
| EVAL-002 | snapshot/query/entity scoring + affected slices | 当前日期不使数字自然漂移 |
| EVAL-003 | evaluation run record | 版本/SHA-or-hash/model/assertion/token/time |
| EVAL-004 | nullable threshold + threshold owner gate | 未配置不作发布判定，不硬编码 90% |
| EVAL-005 | semantic-hit assertion + near-100% configurable target | 已覆盖用例命中语义层且有非保证声明 |
| ABL-001 | evaluate baseline/diff，一次一组件 | 成本、延迟、准确率差异记录 |
| ABL-002 | negative-experiments.md（实施产物） | 无效检索/文档膨胀/廉价 reviewer 实验留档 |
| FBK-001 | correction records + 周期汇总 | 语义解决比例、纠正性语言比例 |
| FBK-002 | correction 双候选 + owner approval | 未批准不合并规范指标 |
| FBK-003 | 文档、报告和答案静默失败声明 | 静态契约断言 |
| HOOK-001 | Hook 仅调 deterministic library；术语解释留给 Agent/Skill | Hook 无开放式业务判断 |
| HOOK-002 | 本文先能力后映射；compatibility matrix | 本地 Fixture 验证事件/Schema |
| HOOK-003 | 2.1.216 探测 + 官方文档证据 + 实施演练 | 不存在未验证事件假设 |
| HOOK-004 | GateError 统一含 rule/evidence/recovery | 每个失败输出具体恢复动作 |
| HOOK-005 | Hook 只判定 affected/evidence；Command/CI 跑完整评测 | 高成本评测不在 Hook 超时执行 |

覆盖审计目标：46/46 规则有实现位置，所有 MUST 有阻断或明确证据检查，SHOULD 有检查、报告或
书面偏差。规则族覆盖不能替代逐条追踪。

## 17. 需求验收映射

| 验收 | 设计实现 | 计划周期 |
| --- | --- | --- |
| AC-01 领域前置与追踪 | 根契约、每产物规则 ID、46 条矩阵 | 1，最终复核 5 |
| AC-02 文件交付 | 第 6 节完整文件清单和六入口 | 1–5 |
| AC-03 范围与安全 | config/path、adapter、permission+sandbox、威胁测试 | 1–2 |
| AC-04 语义层与后备 | 四层栈、source-routing、adapter、protected metric | 2–3 |
| AC-05 维护一致性 | impact manifest、PostToolUse、Stop、affected eval | 4 |
| AC-06 审查与答案 | 独立 reviewer、SHA/round、answer/provenance | 3 |
| AC-07 评测与反馈 | 隔离 suite、run evidence、correction 双候选 | 5 |
| AC-08 Claude Code 兼容性 | 能力探测、官方事件映射、Fixture、降级 | 每周期，终验 5 |
| AC-09 质量门槛 | unittest + 五场景 + E2E + 文件/追踪一致性 | 5 |

## 18. 设计权衡、偏差与待实施确认

### 18.1 关键权衡

| 裁决 | 收益 | 代价/残余风险 |
| --- | --- | --- |
| JSON + 标准库 | 可移植、确定、无安装依赖 | 手写体验不如 YAML |
| 外部 Codebase 默认适配器读取 | 避免附加目录 Skills 注入，边界可测 | 不如直接文件工具灵活，需实现检索接口 |
| 所有数据答案独立审查 | 满足生成/认证分离 | 延迟和 Token 增加，后续只可用消融证据调整 |
| Hook 只做确定性小检查 | 低延迟、可重复、失败可解释 | 完整评测必须由 Command/CI 协调 |
| Python 标准库 | 当前环境可用、低供应链风险 | 需自行维护 Schema 子集或明确依赖 Schema validator 策略 |
| 本地派生证据 `.chatbi/` | 可追踪、可复审 | 无 Git 当前无法靠 ignore；需净化、权限和保留策略 |
| 无 Git 时内容散列 | 不阻塞当前工作区 | 缺少提交历史和作者语义 |

JSON Schema 验证本身不是 Python 标准库能力。实施应选以下之一并记录：优先使用已审核、锁定版本
的 validator；若坚持零依赖，则只实现项目 Schema 所需的明确子集并用兼容测试证明，不能声称是
完整 JSON Schema 实现。这是编码周期必须落地的非业务技术选择。

### 18.2 当前偏差

- 当前工作区没有 `.claude/`、`.mcp.json` 或 Git 元数据；本文只设计，不代表 Harness 已安装；
- 本地 Claude Code 未登录，Remote Control 不可用；不影响离线 Fixture，但阻塞真实模型端到端演练；
- Hook 事件与官方 Schema 已从当前官方文档确认，但尚未在本项目用真实事件触发；周期 1 必须补证；
- 当前没有组织 PII 策略、真实领域所有者、首个真实评测集或发布门槛；Fixture 不能替代这些输入；
- 当前没有真实 managed/CLI 数据连接；只有在显式 Fixture 模式可演练，生产分析必须停止；
- OS 支持矩阵目前只有 Darwin arm64；其他平台在通过相同测试前不声明支持。

### 18.3 实施前/实施中必须确认

1. 组织提供 PII/受限披露策略引用和负责人；
2. 选择首个真实领域、20–40 个起始用例、负责人确认的发布门槛；
3. 提供或选择第一个 managed/CLI 适配器，审核 executable、域和凭证注入方式；
4. 确定 JSON Schema validator 策略；
5. 修复 Claude Code 登录后，在 2.1.216 上完成真实 Agent/Hook E2E；
6. 若启用直接 `--add-dir`，必须作为安全偏差单独批准和测试；
7. 实现完成后根据真实代码、文件结构和命令证据，把本文状态标识更新为 AS_BUILT。

## 19. 完成定义

本技术设计可进入实现评审的条件是：架构、配置、六个 Command、审查 Agent、Hook 映射、适配器、
威胁模型、测试、开发周期和 46 条规则追踪均已定义，并明确区分本地实测、官方支持与待演练能力。

Harness 本身只有在以下条件全部满足后才可宣称 v1 完成：

- 五个开发周期收敛，无 CRITICAL/HIGH 偏差；
- 全部测试、五个压力场景和基线版本端到端演练通过；
- 真实文件清单、feature-flow、规则矩阵和测试报告一致；
- `docs/technical-design.md` 已根据实际实现进入 AS_BUILT 状态；
- 组织 PII、所有者、连接和评测门槛仍缺失时，明确标为无法生产认证，而不是用 Fixture 替代。

---

## 20. Legacy 增强：`/chatbi-bootstrap`（AS_BUILT 2026-07-27）

在 v1 AS_BUILT 基线（§§1-19，2026-07-24）之后，新增第 7 个 slash command
`/chatbi-bootstrap` 作为 legacy 增强，以闭合"从零起建 Warehouse"的缺口。它脚手架本地
Warehouse - 共享/本地配置 + `dw` 数据库 + dbt-mysql 项目结构 + 源库 schema 清单 - 使 agent
随后可通过 `/chatbi-maintain-model` 构建 ODS/DWD/DWS。

**状态：AS_BUILT。** 已实现并对真实 MySQL 验证（`127.0.0.1:3306`，`public` schema，
introspect 出 125 张表；`dw` 以非破坏性 `CREATE DATABASE IF NOT EXISTS` 创建）。563 测试全绿
（533 基线 + 30 新增），`./build-product.sh` 干净，domain-contract gate 通过，design-vs-as-built
评估 CONVERGED（0 BLOCKER / 0 MAJOR，3 个文档级 nit 已修）。

**信任边界：仅 INFRA SETUP** - 镜像 `/chatbi-init` 的 setup 角色，不创建受治理产物。bootstrap
MAY：写本地配置、向共享 `adapters.query` 追加一个 `cli:mysql` 条目（幂等）、
`CREATE DATABASE IF NOT EXISTS dw`、introspect 源库 schema、scaffold 项目目录、写
`source_inventory.json` 交接件。MUST NOT：创建受治理模型、批准指标、发布或运行破坏性迁移
（SEM-003 边界不变）。

**46 规则不变。** bootstrap 引用 8 条既有规则（SCOPE-001、SCOPE-002、SEC-001、SEC-003、
PORT-001、SEM-003、DOC-001、HOOK-004），不新增任何规则；`validate_domain_contract` 持续通过。

**细节：** 完整设计 + as-built 和解说明见 `docs/technical-design-bootstrap.md`（AS_BUILT，含
§10 reconciliation：8→9 步细化、3 个 nit 修复、live smoke 证据、563 测试全绿、build 干净）。
as-built 调用链见 `docs/feature-flow-bootstrap-v1.md`；design-vs-as-built 评估见
`docs/optimization-checklist-bootstrap-v1.md`（CONVERGED）。MySQL-only v1；非 MySQL 引擎、批量
ODS DDL、live MySQL 单测、live hook 注册均仍在 v1 范围外。

## 21. Legacy 增强：`/chatbi-build-from-requirement`（AS_BUILT 2026-07-29）

在 v1 AS_BUILT 基线（§§1-19）+ 第 7 命令 `/chatbi-bootstrap`（§20）之后，新增第 8 个
slash command `/chatbi-build-from-requirement` 作为 legacy 增强，闭合 `/chatbi-analyze`
的"需要新模型即 STOP"缺口（`chatbi-analyze.md` Stop-conditions：T1 覆盖无法确定时 STOP）。
它是**编排器信任层**（镜像 `/chatbi-bootstrap` 的窄信任层形态）：从需求 + DW 状态 + 蓝图
推导 DWD/DWS/ADS 建造计划，按依赖顺序链式调用 `/chatbi-maintain-model`，把受保护点
（指标审批/访问策略/发布/破坏性迁移，SEM-003；扩源 SCOPE-001/SEC-001/RAW-003）路由给人，
模型就位后交接给 `/chatbi-analyze`，使 analyze 不再在 T1 覆盖上 STOP。

**4 步流程：** (1) 读蓝图 §Source/§Metrics/§Layers/§Tooling + `read_source_inventory` +
`read_model_registry` + `select_adapter` 探 T1 覆盖；(2) agent 推导建造计划
（ODS->DWD->DWS->ADS，join/aggregate 是 agent 推理**非确定性 lib**），调
`validate_build_plan` + `validate_layer_dependency`（HOOK-004 fail-closed）；(3) 按依赖顺序
链 `/chatbi-maintain-model`，每个受保护点人批，计划经 `harness_state.write_state` 持久化
可恢复，sync gate 通过后 `append_model_registry`；(4) 建 ADS（若需）+ 交接 `/chatbi-analyze`。

**关键确定性 lib** `build_plan.py`（薄层，镜像 `impact.py` 纪律，不推导只读+校验+追加）：
`BuildPlan`/`ModelEntry`/`LayerRule` frozen-slots dataclass；`build_model_entry` 工厂
（构造期 `_sanitize_text`，Q5/SEC-003）；`read_model_registry`（absent -> `()`，tampered ->
`GateError`，Q3）；`validate_build_plan(plan, layer_rules, known_models=frozenset())`（拓扑序 +
SCOPE-001 跨计划边界，open point 6；`known_models` 来自 registry）；`validate_layer_dependency`
（层权限矩阵，Q6b，独立于拓扑检查 Q6a）；`append_model_registry`（原子 temp+rename `0o600`，
`(name,created_rev)` 幂等，仅 sync gate 通过后调用，DOC-004/HOOK-001）。配套 `bootstrap.py`
增量 introspect（`read_source_inventory` + `merge_source_inventories`，扩源人批后合并新表）；
`chatbi-maintenance/SKILL.md` §3 读蓝图 `## Layers` + §4 sync gate 通过后写 registry；
`chatbi-bootstrap/SKILL.md` Step 8 创建 `## Layers` stub（声明式跨层规则占位，operator 填，
META-003/PORT-001）。Schema `build-plan.schema.json` 是单一形状契约。

**治理边界不变。** 46 规则不增删改；`validate_domain_contract` 持续 PASS。受保护点人批
（SEM-003 4 个 protected_actions + 扩源 flag）；join/aggregate 推导是 agent 推理（无推导
lib）；跨层规则是蓝图 `## Layers` 声明式领域知识（非新 governed rule ID，META-003）；
建造计划本身不过独立审查（REV-001 仅答案门）。

**状态：AS_BUILT。** 629 测试全绿（628 pass + 1 skip，+63 additive），test-agent 独立
run-confirmed；`build-product.sh` 干净（8 命令，import canary 含 build_plan，无 dev-only
泄漏）；`validate_domain_contract` PASS（46 规则，required_routes 含 build-from-requirement +
bootstrap，CLAUDE.md 114<200）；design-vs-as-built 评估 CONVERGED（12 维度 PASS，0
BLOCKER/MAJOR，4 MINOR 防御性增强）。4 个 MINOR 增强（`validate_layer_dependency` 不收
known_models、`build_model_entry` 额外净化 upstream_deps、registry 256 KiB 上限、
`validate_build_plan` 拒重复模型名）已记录为 as-built 偏离，不回改设计。完整设计 + as-built
和解见 `docs/technical-design-requirement-driven-build.md`（AS_BUILT，§12 as-built notes）；
调用链见 `docs/feature-flow-requirement-driven-build-v1.md`；评估见
`docs/optimization-checklist-requirement-driven-build-v1.md`（CONVERGED）；测试见
`docs/test-report-requirement-driven-build-v1.md`（ALL_PASSED）。live MySQL 维护链与 live
build-from-requirement 端到端仍在 v1 范围外（确定性 lib 面 OFFLINE 验证）。
