# Claude Code ChatBI Harness v1 需求文档

## 1. 背景

Anthropic 的自助式数据分析实践表明，分析准确率的主要瓶颈不是 SQL 生成，而是：
业务概念能否映射到唯一、最新的受治理实体；Agent 能否稳定检索和正确使用该实体；
系统能否通过验证和反馈及时发现漂移。

本项目拟构建第一版 Claude Code Harness，使 Agent 能在指定数据仓库 Workspace 内
开发、维护和分析一个 **Agent 主导的数据仓库**，并可从用户配置的多个外部业务
Codebase 中只读检索业务语境和实现证据。

领域语言、事实层级、执行方边界和规则以
`docs/chatbi-harness-domain-model.md` 为硬前置。任何 `CLAUDE.md`、Command、Agent
Prompt、Hook、配置或文档在设计和实现前都必须读取并遵守该文件；发生冲突时必须
先修订并重新确认领域模型，不能在实现中静默偏离。

## 2. 目标用户

### 2.1 主要用户

- **数据工程师 / 分析工程师**：让 Agent 创建和维护模型、测试、元数据、语义层资料与 Skills，同时保留可审查证据。
- **数据科学家 / 业务分析师**：通过受治理的语义层和参考资料获取可追踪、经审查的数据答案。
- **领域负责人 / 指标所有者**：定义规范指标、裁决歧义、批准文档和纠正修复，并查看评测趋势。

### 2.2 次要用户

- **业务提问者**：不需要理解底层 Schema，但应能看到口径、局限、来源和新鲜度。
- **平台 / 数据治理人员**：配置 Workspace、外部 Codebase、工具和权限边界，审核 PII 与访问策略。

## 3. 问题陈述

仅把 Claude 连接到数据仓库会制造“精确但可能错误”的答案。v1 必须同时解决：

1. **概念—实体歧义**：同一术语可能对应多个表、字段、指标、粒度和过滤规则。
2. **数据陈旧**：模型、Schema、业务定义、文档和 Skill 会以不同速度变化。
3. **检索失败**：正确资料可能存在，但缺乏结构化路由时 Agent 仍无法正确使用。
4. **跨目录上下文风险**：业务 Codebase 能补充语境，也可能带来错误定义、越界读取、
   指令注入、PII 和执行风险。[用户约束 + 设计推论]
5. **验证不足**：SQL 可运行不代表实体、数据或结论正确，且静默失败不能被完全消除。

## 4. 产品目标

- **G-01**：提供以 Claude Code 为运行载体、可安装到数据仓库 Workspace 的 Harness v1。
- **G-02**：在任何分析或维护动作前强制采用领域模型定义的四层栈和事实来源顺序。
- **G-03**：把 `CLAUDE.md`、Commands、Agent Prompts、Hooks 和参考文档组织成可维护、可验证而非单一巨型 Prompt 的系统。
- **G-04**：允许显式配置多个外部业务 Codebase 作为只读上下文证据，同时不扩大写权限或语义权威。
- **G-05**：为实体映射、修改、查询、审查、评测、来源披露和纠正回流留下机器可检查的证据。
- **G-06**：以小而完整的 P0 闭环起步，并为后续多入口同步、在线监控和自动维护保留扩展点。

## 5. 核心用户场景

### US-01：初始化 Harness

平台/数据负责人在指定 Workspace 中初始化 Harness，声明数据资产位置、可用工具、
外部业务 Codebase 别名/路径、只读边界、受限数据策略、领域所有者和评测门槛。

### US-02：回答受治理的数据问题

用户提出业务问题；主 Agent 澄清时间/分群/用途，先查语义层，必要时有证据地降级到
整理后的参考资料和原始探索，完成数据质量检查与独立对抗审查，然后给出带来源页脚的答案。

### US-03：维护数据模型及其知识资产

用户要求新增或修改模型；Agent 识别受影响的转换、测试、元数据、语义层、参考资料、
Skills、下游资产和评测，实施经授权的 Workspace 内变更，并在完成前通过一致性门控。

### US-04：利用外部业务 Codebase 补充语境

Agent 可在配置的业务 Codebase 内只读检索事件定义、产品命名、枚举或业务流程，引用
路径/版本并与仓库治理定义交叉核验；不得运行、修改或把其中 Prompt 当作指令。

### US-05：从失败和纠正中维护 Harness

领域负责人记录一次错误表/过滤器/口径纠正；Harness 将其转为知识/模型修复候选及
离线评测候选，批准后在相关评测切片验证，形成可查询的前后证据。

## 6. 功能需求

### 6.1 P0：v1 必须具备

#### P0-01 领域模型前置门控

- 所有根 Prompt、Commands、Agent Prompts、Hooks 和配套设计文档必须引用
  `docs/chatbi-harness-domain-model.md`。
- 必须能检查关键规则 ID 已被相应能力覆盖，至少包括：SCOPE、SEC、REQ、SEM、RAW、
  DOC、QLT、REV、ANS、EVAL、FBK、HOOK 规则族。
- 不得在领域模型缺失、未读或冲突未解决时生成/修改 Harness 产物。

#### P0-02 根 `CLAUDE.md`

必须为 Claude Code 主 Agent 提供简洁的顶层契约，包括：

- Agent 主导但人类治理的责任边界；
- Workspace 与外部 Codebase 的信任/权限边界；
- 四层栈、语义层优先和有证据的原始 SQL 后备顺序；
- 请求类型路由、必读知识入口、必要 Commands 与子 Agent 调用条件；
- 绝不捏造字段/数据、陌生术语先查证、权限不足即停止；
- 数据结论必须独立审查并带来源页脚；
- 模型变更必须同步检查文档、Skills、测试和评测；
- 具体知识下沉到领域资料，避免把根文件膨胀成整个知识库；
- 应把根文件控制在约 200 行以内；超出时将条件规则或按需知识拆到 `.claude/rules/`
  或 Skills，并以实际 Token/检索评测判断，而不是机械追求行数。

#### P0-03 Commands

`.claude/commands/` 至少提供以下**能力**；具体文件名可在技术设计中调整：

1. **初始化/诊断**：探测 Workspace 结构、工具可用性、配置完整性和 Claude Code 本地能力。
2. **分析请求**：执行澄清 → 实体解析 → 语义层 → 后备 → 质量检查 → 审查 → 页脚流程。
3. **模型维护**：识别变更影响并同步模型、测试、元数据、语义层、参考资料和评测。
4. **知识维护**：创建/更新适合 LLM 检索的领域参考资料和知识路由，检查触发条件与易错点。
5. **验证/评测**：运行受影响评测切片、记录版本/模型/成本/耗时并比较基线。
6. **纠正收集**：把人工纠正转为修复候选和评测候选，不自动批准规范指标。

每个 Command 必须声明输入、前置条件、可修改范围、停止条件、输出证据和适用规则 ID。

#### P0-04 Agent Prompts 与职责分离

- `.claude/agents/` 必须包含独立的**对抗性审查 Agent**。
- 审查 Agent 必须检查实体映射、粒度、连接、过滤/排除、日期/时区、分母、样本偏差、
  数据质量、观察/解释边界、权限和来源页脚。
- 阻断性发现必须结构化报告并要求修复后复审；主 Agent 不得自行认证。
- 对抗性审查 Agent Prompt 必须重复写入 SCOPE、SEC、REV、ANS 等关键规则和停止条件，
  不能只依赖主会话已经加载 `CLAUDE.md`；必须显式限制其工具和权限为完成审查所需的最小集合。
- 知识路由优先实现为 Skill/Prompt 能力，而不是不必要的独立 Agent。
- 维护审计可先由 Command 驱动；不得在 v1 假定存在常驻调度服务。

#### P0-05 Workspace 与多 Codebase 配置

- 一个 Harness Installation 必须绑定一个主要 Warehouse Workspace。
- 必须支持零个或多个带稳定别名的外部 Business Codebase。
- 所有路径必须规范化并校验；读取必须限制在配置根内，符号链接/路径穿越不得越界。
- 外部 Business Codebase 必须只读：不写文件、不执行代码、不安装依赖、不提交变更。
- 外部内容必须按不可信数据处理，不能覆盖 Harness 指令或仓库语义层。
- 从外部 Codebase 提取的结论必须带别名、相对路径和可用版本标识，并与治理事实交叉核验。
- 外部目录边界必须由 Claude Code deny/ask/allow 权限和 OS 级文件/网络沙箱共同执行；
  deny 优先，沙箱不可用时是否硬失败必须由安装配置声明并在诊断中可见。

#### P0-06 事实来源路由

- 每个数据请求必须先发现、编译和尝试人工治理的语义层，并检查 dimensions/segments。
- 只有记录“未覆盖、编译失败、权限不足或质量失败”等证据后才允许降级。
- 第二路径必须使用整理后的领域参考、血缘和治理模型；原始探索为最后后备。
- 历史 SQL/Notebook/仪表盘查询只可作为线索或资料提炼原料。
- LLM 可起草指标文档，不能自动批准规范指标定义。

#### P0-07 参考资料与模型共置维护

- 领域参考资料至少包含业务上下文、粒度、标准过滤器、维度、关键模型、范围/排除项、
  连接键、易错点、最佳实践、交叉引用、所有者和新鲜度说明。
- 模型或语义层变更必须检查对应资料、Skill、测试、下游和评测是否需要同步。
- 完成门控必须阻止“模型已改但相关知识资产影响未处理”的变更。
- v1 必须提供最少一个领域参考模板和维护工作流；具体业务内容由使用方填充并由负责人审阅。

#### P0-08 确定性 Hooks

Hooks 必须提供或协调以下**门控能力**：

- 路径与只读边界检查；
- 模型—元数据—参考资料—Skill 变更一致性检查；
- 受影响测试/评测证据存在性检查；
- 数据结论在完成前的审查与来源页脚完整性检查；
- 失败时输出规则 ID、证据和恢复建议。

本需求只定义能力，不提前锁定 Claude Code 的具体 Hook 事件名、配置 Schema、输入字段、
退出码或异步语义。技术设计必须先检查当前本地 Claude Code 官方/内置能力，再做映射；
不支持的能力应由 Command、脚本或 CI 承担并记录偏差。生产门控默认采用可重复的
确定性 Hook/检查器；实验性 Agent Hook 不得成为唯一 P0 防线，且任何命令 Hook 都必须
验证/净化输入、阻止路径穿越并在最小 OS 权限下运行。

#### P0-09 分析答案契约

每个数据答案必须包含：

- 被回答的问题、时间范围、实体/分群口径；
- 方法、来源层级、关键过滤/包含/排除项和分母；
- 数据新鲜度/最大日期、完整性或异常检查结果；
- 观察与解释的明确区分，以及局限；
- 对抗性审查状态和轮次；
- 来源页脚：`语义层 | 整理后的参考/治理模型 | 原始探索`、置信度、审查、
  新鲜度、模型所有者；
- 原始探索或新鲜度未知时的复核提示。

#### P0-10 离线评测与纠正闭环

- 必须支持人工验证的高频问题与长尾问题用例。
- 数字型评测必须锚定快照/稳定事实，或评分查询与实体选择以防漂移。
- 作为评测 ground truth 的问答/SQL 必须在该次运行中从运行时示例、检索语料和 Prompt
  中隔离；同一条样例不得同时提示被测 Agent 又用于证明它答对，防止评测泄漏。
- 每次运行必须记录 suite/Skill 版本、Git SHA（若可用）、模型 ID、逐断言结果、Token 和耗时。
- 评测门槛必须可配置，不能把博客经验值硬编码为普遍标准。
- 纠正记录必须同时生成知识/模型修复候选和评测候选，经人类批准后合并。
- 输出不得宣称评测通过等于绝对正确；必须保留静默失败声明。

#### P0-11 文档交付

除 Prompt/Command/Agent/Hook 本体外，v1 必须包含：

- 安装与初始化；
- 配置 Workspace、外部 Codebase、工具、权限、PII 策略、所有者和评测门槛；
- 分析与维护工作流；
- 规则 ID 到产物/门控的追踪矩阵；
- 参考资料编写规范和模板；
- 审查发现、来源页脚、评测用例、纠正记录的格式；
- 故障排查、限制和安全说明；
- Claude Code 版本/能力探测结果与任何兼容性降级。

### 6.2 P1：首版后优先增强

- **P1-01 多领域路由**：按领域拆分知识资料和评测切片，支持交叉引用和所有者路由。
- **P1-02 变更影响图**：利用血缘、引用和文件映射更精准地计算受影响资料/评测。
- **P1-03 PR 粒度消融**：对 Skill/规则变更自动比较前后准确率、Token 和延迟。
- **P1-04 纠正性语言采集适配器**：从已获授权的沟通渠道导入纠正候选；默认不连接外部系统。
- **P1-05 使用信号仪表盘**：跟踪语义层解决比例、纠正比例和离线准确率趋势。
- **P1-06 多 Workspace 配置档案**：同一 Harness 分发包支持多个独立安装，但每次会话仍有单一主要 Workspace。
- **P1-07 负面实验登记**：记录无收益/负收益方案，阻止重复实验。
- **P1-08 标准数据工具适配器**：为支持 MCP 的语义层/血缘/健康度工具提供受限适配器，按只读发现、查询、可变更命令分组授权，而不是把整个工具服务器一次性授予 Agent。

### 6.3 P2：可选演进

- **P2-01 多入口同步**：将规范 Skill 同步到其他 IDE、托管应用、MCP 资源或聊天入口。
- **P2-02 定时维护 Agent**：周期扫描漂移和纠正，自动起草修复但保留人类批准。
- **P2-03 领域级受限 Agent**：针对强隔离/高敏领域提供权限受限的专用 Agent。
- **P2-04 常驻 KPI 核验**：核心指标每日与官方仪表盘或稳定基线做合理性检查。
- **P2-05 可视化运营面板**：展示评测、漂移、审查成本与知识维护队列。

## 7. 非功能要求

### 7.1 正确性与可验证性

- **NFR-COR-01**：关键结论必须可追溯到文件、语义实体、查询和验证证据。
- **NFR-COR-02**：规则门控应可重复；相同输入与相同仓库版本应产生一致的来源选择和检查项。
- **NFR-COR-03**：任何效果声称必须由本项目评测支持，不能沿用博客数字作为保证。
- **NFR-COR-04**：评测必须防止 ground truth 泄漏，并能区分“运行时示例命中”与“未见样例泛化”。

### 7.2 安全与隐私

- **NFR-SEC-01**：最小权限、拒绝优先，不自动认证或提权。
- **NFR-SEC-02**：外部 Codebase 全程只读并按不可信内容处理，防止路径穿越、符号链接越界和指令注入。
- **NFR-SEC-03**：Prompt、日志、评测和纠正记录不得持久化未经授权的密钥或 PII。
- **NFR-SEC-04**：命令执行目标、工作目录和可修改路径必须显式可审计。
- **NFR-SEC-05**：模型层、Agent 工具层、Claude Code 权限层与 OS 沙箱形成纵深防御；
  Prompt 约束不能被当作文件、命令、网络或数据库权限的技术执行机制。

### 7.3 可维护性

- **NFR-MNT-01**：Prompt 保持分层；根契约稳定，领域事实位于可独立更新的参考资料。
- **NFR-MNT-02**：规则与产物之间必须有追踪矩阵，避免修改后形成“孤儿规则”。
- **NFR-MNT-03**：模板、示例和检查器应支持新增领域而无需复制整个 Harness。
- **NFR-MNT-04**：文档只保留仍有收益的约束，支持删除过时脚手架。

### 7.4 可移植性与兼容性

- **NFR-PORT-01**：除安装配置外不得硬编码本机绝对路径。
- **NFR-PORT-02**：领域规则不依赖某个 UI；Claude Code 专属映射集中到适配层和安装文档。
- **NFR-PORT-03**：必须记录已验证的 Claude Code 版本/能力；未知版本应给出诊断结果而非静默运行。

### 7.5 性能与成本

- **NFR-PERF-01**：评测记录 Token 与耗时，允许以准确率—成本—延迟三者比较方案。
- **NFR-PERF-02**：不得仅为降低延迟跳过 P0 对抗性审查；若将来放宽，必须有消融证据和明确风险接受。
- **NFR-PERF-03**：知识检索应先路由到少量相关资料，避免无界扫描所有历史 SQL。

### 7.6 可用性与解释性

- **NFR-UX-01**：失败信息必须包含规则 ID、原因、证据和下一步，不要求用户猜测恢复动作。
- **NFR-UX-02**：非数据专家能够从答案页脚判断来源层级、新鲜度、所有者和是否需要复核。
- **NFR-UX-03**：高风险/受限/定义不清请求必须提供精确澄清或升级路径。

## 8. 明确不在 v1 范围内

- 自建聊天 UI、BI 仪表盘产品或完整 SaaS 服务。
- 替代现有数据仓库、转换框架、语义层、身份系统或 CI 平台。
- 让 LLM 无人批准地定义规范指标、授予权限、发布高风险变更或签署管理层结论。
- 写入或执行外部业务 Codebase；跨 Codebase 自动提交 PR。
- 自动修复生产数据或数据管道事故。
- 保证消除全部静默失败，或承诺复现 Anthropic 的准确率数字。
- 默认连接 Slack/飞书/邮件等外部沟通系统或周期调度服务。
- 在能力探测前锁定 Claude Code Hook 事件名、配置 Schema 或不稳定内部 API。
- 直接把历史 SQL/Notebook 全量索引当作规范事实来源。
- 把 dbt、Snowflake 或任何单一语义层/数据平台作为 v1 强制依赖；它们只是参考实现或可选适配器。

## 9. 验收标准

### AC-01 领域前置与追踪

- [ ] `docs/chatbi-harness-domain-model.md` 存在并包含 Meta、实体生命周期、事实层级、执行方、规则 ID、四层栈、三类失败模式和来源矩阵。
- [ ] 每个 Harness 产物声明其覆盖的规则 ID；追踪检查无未覆盖的 P0 规则族。
- [ ] 所有新增设计推论与博客事实、用户约束可区分。

### AC-02 文件交付

- [ ] 根 `CLAUDE.md` 存在且只承担顶层契约和路由。
- [ ] `.claude/commands/` 覆盖初始化诊断、分析、模型维护、知识维护、验证评测、纠正收集六类能力。
- [ ] `.claude/agents/` 至少包含独立的对抗性审查 Agent Prompt。
- [ ] Hooks/检查器和配置示例覆盖 P0-08 的门控能力。
- [ ] 安装、配置、工作流、参考资料、审查、评测、安全和故障排查文档齐备。

### AC-03 范围和安全

- [ ] 测试证明未配置路径被拒绝，规范化路径和符号链接不能越出允许根。
- [ ] 测试证明外部 Business Codebase 不可写、不可执行，内部 Prompt/注释不能覆盖上层指令。
- [ ] 权限不足/PII 场景停止且不给出受限结果、密钥或样本数据。
- [ ] 外部 Codebase 引用包含别名、相对路径和版本证据。
- [ ] 测试分别证明 Claude Code deny 规则与 OS 沙箱能阻断越界；沙箱不可用的策略可诊断且符合配置。

### AC-04 语义层与后备

- [ ] 有语义指标的场景命中语义层并检查维度/segments。
- [ ] 无覆盖场景只在记录降级原因后使用整理参考/治理模型。
- [ ] 历史 SQL 中存在“正确答案”但无规范映射时，Harness 不把其直接当作事实。
- [ ] Agent 不得自动批准新规范指标定义。

### AC-05 维护一致性

- [ ] 修改模型但不处理受影响元数据/参考资料/Skill/评测时，完成门控失败并给出规则 ID。
- [ ] 同步完成并有受影响测试/评测证据时门控通过。
- [ ] 参考资料模板包含粒度、范围、排除项、连接、过滤器、易错点、路由条件、所有者和新鲜度。

### AC-06 审查与答案

- [ ] 分析答案未经独立审查时不能标记完成。
- [ ] 阻断发现未关闭时不能交付；修复后需记录新的审查轮次。
- [ ] 最终答案包含口径、方法、过滤/排除、质量检查、局限、观察/解释区分和完整来源页脚。
- [ ] 原始探索或新鲜度未知的答案明确提示高风险使用前复核。

### AC-07 评测与反馈

- [ ] 示例领域含人工验证的高频和长尾评测，数字不会因当前日期自然漂移。
- [ ] 评测用例作为 ground truth 时不会同时出现在该运行的 Prompt、运行时 verified examples 或可检索语料中。
- [ ] 运行记录含 suite/Skill 版本、仓库版本、模型 ID、逐断言、Token 和耗时。
- [ ] 一条纠正可生成修复候选和新评测候选，但未经人类批准不会更新规范指标。
- [ ] 文档明确评测不能消除静默失败。

### AC-08 Claude Code 兼容性

- [ ] 技术设计记录实际探测到的 Claude Code Hook/Agent/Command 能力及证据。
- [ ] 具体 Hook 映射只使用已验证的事件名和 Schema；不支持能力有明确 Command/CI 后备。
- [ ] 对抗性审查 Agent 在隔离上下文中仍能获得关键规则，且工具/权限限制可被实际检查。
- [ ] P0 确定性门控不以实验性 Agent Hook 作为唯一实现；命令 Hook 通过恶意输入和路径穿越测试。
- [ ] Harness 在文档声明的 Claude Code 版本上完成一次端到端演练。

### AC-09 质量门槛

- [ ] 全部自动检查和测试通过。
- [ ] 使用至少五个压力场景覆盖概念歧义、陈旧、检索失败、外部 Codebase 信任边界和 PII/权限。
- [ ] 技术设计、实际文件清单、规则追踪矩阵与测试报告一致。

## 10. 风险与缓解

| ID | 风险 | 影响 | 初步缓解 |
| --- | --- | --- | --- |
| R-01 | “Agent 主导”被实现成无人治理 | 错误指标或高风险变更自动发布 | 人类指标所有者、权限负责人和高风险签字作为硬边界 |
| R-02 | 根 Prompt 膨胀 | 检索变差、维护困难、规则冲突 | 根契约 + 知识路由 + 按领域参考资料 |
| R-03 | 外部 Codebase 内容注入指令 | 越界执行、数据泄露、规则覆盖 | 只读、内容即数据、路径沙箱、引用与交叉核验 |
| R-04 | Claude Code Hook 能力与假设不符 | 门控不可实现或静默失效 | 技术设计阶段本地能力探测；Command/CI 后备；记录版本 |
| R-05 | 没有现成语义层 | P0 分析路径无法达到最高可信层 | 明确缺口、只允许有证据降级；不伪造语义层；优先建立小型人工治理层 |
| R-06 | 文档与模型持续漂移 | 准确率快速退化 | 共置、变更影响检查、受影响评测、纠正闭环 |
| R-07 | 对抗审查成本和延迟较高 | 使用体验或预算受压 | 记录 Token/耗时，后续用固定评测做消融；P0 不跳过 |
| R-08 | 评测覆盖不足或基准漂移 | 绿色评测给出虚假安全感 | 高频 + 长尾 + 纠正用例；快照/查询评分；持续校准 |
| R-09 | 业务 Codebase 与仓库定义冲突 | Agent 选择错误业务语义 | T1/T2 优先，冲突披露并交领域负责人裁决 |
| R-10 | 静默失败 | 错误结论未被发现地使用 | 页脚、高风险人签、核心 KPI 常驻评测（后续）、明确非保证 |
| R-11 | 评测样例同时作为运行时提示 | 准确率被泄漏夸大，无法证明泛化 | ground truth 隔离；分别报告 seen/unseen 结果 |
| R-12 | MCP/CLI 工具集同时包含只读与可变更能力 | Agent 越权修改模型或仓库对象 | 工具分组、默认禁用可变更工具、最小权限和显式批准 |
| R-13 | 子 Agent 未加载主会话关键约束 | 审查遗漏安全/来源规则或错误放行 | 关键规则写入 Agent Prompt；隔离上下文验收 |

## 11. 待定问题（Grill 清单）

以下 15 项全部是**用户确认点**，无法从博客、官方参考项目或当前目标可靠推断。
每项保留推荐默认值供逐项确认；推荐值不是组织事实。在未确认前，技术设计必须将其
标为“暂用推荐默认值”，不得伪造成用户已决定，也不得用其他隐含假设替代。

1. **Warehouse Workspace 采用什么数据技术栈？**
   - 推荐：Harness 核心保持工具无关，通过配置声明转换命令、测试命令、语义层和查询适配器；v1 选择一个“示例适配器”做端到端验收。
   - 原因：博客不指定具体产品，过早锁定会削弱可移植性。

2. **v1 的主要工作模式是“分析问答”“仓库开发维护”，还是两者同优先？**
   - 推荐：两者都为 P0，但以同一领域模型贯通；先确保分析闭环，再让维护命令以评测证明不会破坏该闭环。
   - 原因：用户目标强调开发维护，博客证据主要来自分析准确率，二者不能丢掉任一侧。

3. **允许 Agent 在 Workspace 内自动写到什么程度？**
   - 推荐：允许生成候选变更和运行本地验证；规范指标、权限策略、生产发布及破坏性迁移必须人类批准。
   - 原因：与人类负责指标定义和高风险签字一致。

4. **外部 Business Codebase 如何配置和版本化？**
   - 推荐：使用稳定别名、规范化绝对根路径和可选 Git revision；产物引用只保存别名 + 相对路径 + revision，不写死机器路径。
   - 原因：兼顾安装期定位、报告可移植性和追踪性。

5. **是否允许读取 Workspace/Codebase 的 Git 历史？**
   - 推荐：默认允许只读当前工作树和本地 Git 元数据；深历史检索按需启用并受范围限制。
   - 原因：历史有助于理解变更，但无界检索会增加噪声和敏感信息暴露。

6. **查询与数据访问通过哪些工具执行？**
   - 推荐：配置“托管连接优先、CLI 后备、均不可用则停止认证请求”的适配顺序；不在顶层 Prompt 硬编码工具名。
   - 原因：直接沿用博客附录的能力顺序并保持可移植。

7. **PII 分类和受限数据策略由谁提供？**
   - 推荐：必须由组织现有治理/用户配置提供，Harness 只执行；缺少策略的敏感请求拒绝结果输出。
   - 原因：Harness 不具备自行判断组织法律和权限边界的权威。

8. **对抗性审查是否覆盖所有数据答案？**
   - 推荐：P0 全覆盖；记录成本和延迟后再用消融证据讨论低风险快速路径。
   - 原因：博客附录将其设为强制，且本项目尚无证据支持放宽。

9. **评测的首个领域、基线问题和发布门槛是什么？**
   - 推荐：选择一个有明确指标所有者、规范仪表盘和有限模型范围的领域；由负责人给出 20—40 个起始用例和门槛，数字只是初始建议而非固定要求。
   - 原因：需要真实组织数据校准，不能复制 Anthropic 的经验阈值。

10. **纠正信息的 v1 输入渠道是什么？**
    - 推荐：先使用本地结构化 Markdown/JSON 记录和显式 Command；外部聊天扫描放到 P1。
    - 原因：符合当前本地 Markdown 工作流，避免未经授权连接沟通系统。

11. **Hook 失败是阻断还是告警？**
    - 推荐：SCOPE/SEC、未审查答案、缺失必需同步和失败 P0 评测为阻断；潜在文档影响但证据不充分时告警并要求显式处置。
    - 原因：在正确性与误报之间建立可解释边界。

12. **需要支持哪些操作系统和 Claude Code 版本？**
    - 推荐：先以当前本地环境和实际安装版本为 v1 基线，记录能力探测；脚本遵循可移植 shell 约束，再决定扩展矩阵。
    - 原因：当前没有可靠版本/平台需求，不应预设 Hook Schema。

13. **“多业务 Codebase”是否可能包含嵌套或重叠路径？**
    - 推荐：v1 拒绝重叠根和指向 Workspace 内部的外部别名，避免信任层级歧义。
    - 原因：路径身份和读写策略否则可能冲突。

14. **高风险答案如何定义？**
    - 推荐：至少包含管理层/董事会材料、受监管/PII 领域、财务核心指标、原始探索和新鲜度未知；允许组织配置扩展。
    - 原因：博客明确提到管理层签字和权限/隐私权衡。

15. **是否需要把 `CONTEXT.md`/ADR 纳入最终 Harness？**
    - 推荐：领域术语稳定后生成根 `CONTEXT.md`；仅对难以逆转、非显然且有真实权衡的决策创建 ADR。
    - 原因：当前阶段只允许两个产物；领域模型已先承担术语合同，后续可按项目工作流补齐。

## 12. 相似项目 / 官方参考实现调研

调研日期：2026-07-21。仅采用厂商官方文档或官方 GitHub 仓库；以下三者分别提供
“Claude Code Harness 载体”“开放的数据工程 Agent 工具/Skills”“语义优先且可评测的
托管分析产品”三个互补视角，不把任何一个产品的能力等同于本 Harness 的完整目标。

### 12.1 Claude Code 原生扩展与安全模型（官方参考）

**核心能力与组织方式。** Claude Code 官方将常驻项目约束放在 `CLAUDE.md`，把按路径
规则放在 `.claude/rules/`，把按需知识和可复用工作流放在 Skills；官方建议根
`CLAUDE.md` 保持在 200 行以内。Skills 与 Subagents 分工不同：Skill 在主上下文提供
知识/工作流，Subagent 使用独立上下文完成聚焦任务；Custom Commands 已包含在 Skills
体系中，已有 `.claude/commands/*.md` 仍兼容。
[官方功能选择指南](https://code.claude.com/docs/en/features-overview)
[官方 Skills / Custom Commands 文档](https://code.claude.com/docs/en/slash-commands)
[官方术语表](https://code.claude.com/docs/en/glossary)

**运行与集成。** 自定义 Subagent 使用带 YAML frontmatter 的 Markdown Prompt，可限制
tools、permission mode、max turns、Skills、MCP servers 和 scoped Hooks；Subagent 拥有
隔离上下文，因此关键规则应放进 Agent Prompt，而不是假设主对话上下文自然完整继承。
[官方 Subagents 文档](https://code.claude.com/docs/en/sub-agents)
[官方配置排错：子 Agent 关键规则](https://code.claude.com/docs/en/debug-your-config)

**治理、权限与 Hooks。** Claude Code 权限按 deny → ask → allow 评估，外部目录可通过
additional directories 暴露，但目录加入只扩展文件访问，不把它变成配置根；符号链接
同时检查链接与真实目标。官方把权限与 OS 级文件/网络沙箱定义为互补的纵深防御。
Hooks 可位于项目设置、插件、Skill 或 Agent 生命周期；官方提示 command Hook 以当前
系统用户权限运行，必须净化输入和阻止路径穿越，而 Agent Hook 仍属实验性、生产默认
优先 command Hook。
[官方 Permissions 文档](https://code.claude.com/docs/en/permissions)
[官方 Sandboxing 文档](https://code.claude.com/docs/en/sandboxing)
[官方 Hooks 指南](https://code.claude.com/docs/en/hooks-guide)
[官方 Hooks 安全说明](https://code.claude.com/docs/en/hooks#security-considerations)

**验证机制。** Hooks 能在工具调用、完成和子 Agent 生命周期等边界执行确定性命令、
模型判断或 Agent 验证，但不同 Hook 类型具有不同阻断语义和限制；例如事后 Hook 无法
撤销已经发生的动作。因此本 Harness 仍需在技术设计阶段按实际安装版本做事件/Schema
能力探测，而不能只凭 Prompt 保证安全。
[官方 Hooks Reference](https://code.claude.com/docs/en/hooks)

**优势。** 与用户指定的 v1 载体完全一致；原生支持 Prompt 分层、独立审查者、权限、
沙箱、Hooks 和 MCP，能把领域规则落到多个执行层。

**缺口。** Claude Code 只提供通用 Harness 原语，不提供语义层优先、规范指标、数据
新鲜度、来源页脚、离线数据评测或纠正闭环；这些仍必须由本项目的 46 条领域规则定义。

### 12.2 dbt MCP Server + dbt Agent Skills（官方开源参考）

**核心能力。** dbt 官方 MCP Server 为 Agent 提供 dbt Core、Fusion 和 Platform 上下文；
其工具覆盖语义层指标/维度/实体发现、编译 SQL、指标查询，以及模型、来源新鲜度、
exposure、血缘、测试和模型健康度发现。这证明“语义层入口 + 血缘/健康度后备 + 受控
CLI”可以通过标准 Agent 工具接口组合。
[dbt 官方 MCP Server 仓库](https://github.com/dbt-labs/dbt-mcp)

**Prompt/Skill 组织与可移植性。** dbt 官方 Agent Skills 把分析工程、单元测试、
Semantic Layer、自然语言指标问答、Mesh 治理、故障排查、MCP 配置和 CLI 执行拆成
独立 Skills；它们遵循 Agent Skills 格式，并声明可安装到 Claude Code 及多种其他 Agent。
仓库还提供 Skill 变化的 A/B 评测入口。
[dbt 官方 Agent Skills 仓库](https://github.com/dbt-labs/dbt-agent-skills)
[dbt 官方 Skills 评测目录](https://github.com/dbt-labs/dbt-agent-skills/tree/main/evals)

**治理与权限。** dbt MCP 同时暴露只读发现、查询和可能修改模型/warehouse objects 的
CLI 工具；官方明确提醒只有在信任客户端并理解影响时才允许 CLI 能力。其 Skills 还覆盖
dbt Mesh 的 contracts、access、groups、versions，但权限最终仍由 dbt/warehouse 凭证与
Agent 客户端共同约束。
[dbt MCP 工具清单与 CLI 警告](https://github.com/dbt-labs/dbt-mcp#tools)

**验证机制。** dbt 能暴露 compile、build、test、source freshness、model health 和
lineage；Agent Skills 提供单元测试工作流及 Skill A/B 评测。这些适合验证数据工程变更，
但官方项目并未证明其默认提供本 Harness 要求的独立对抗性分析审查、来源页脚、纠正
回流或防 ground-truth 泄漏的领域评测闭环。

**运行/集成。** MCP 可接入 Claude/Cursor 等客户端；Skills 可作为 Claude Code 插件或
Agent Skills 包安装。相较硬编码 dbt 命令，这种接口适配更可移植，但 dbt 项目与已配置
凭证仍是大多数数据能力的前置条件。

**优势。** 与“Agent 开发维护数据仓库”高度接近，提供真实的模型、测试、语义层、血缘、
健康度和文档工具；分拆的 Skills 也验证了程序性知识模块化方向。

**缺口。** 绑定 dbt 生态；工具服务器本身并不建立本项目的 Workspace/外部 Codebase
信任差异，也不自动保证语义层强制优先、人类指标所有权或所有答案经独立审查。

### 12.3 Snowflake Cortex Analyst + Semantic Views（官方产品参考）

**核心能力与语义层优先。** Cortex Analyst 用 Semantic Views 描述业务实体、维度、事实、
指标和关系，并以丰富元数据、规范计算、预定义连接和 verified examples 提高 text-to-SQL
一致性。其 Routing Mode 明确优先使用 semantic SQL，只在语义视图不能满足时回退标准
SQL，和本领域模型的 T1 → T2/T3 原则高度一致。
[Cortex Analyst 官方文档](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst)
[Semantic Views 官方概览](https://docs.snowflake.com/en/user-guide/views-semantic/overview)
[Routing Mode 官方文档](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/cortex-analyst-routing-mode)

**治理与权限。** Semantic Views 是原生 Schema 对象，支持 RBAC、privileges、tags 和
public/private access modifiers；底层 row access/masking policies 可传播到语义视图。
官方同时提醒 sample values 属于元数据，可能不受 masking policy 保护，这对本 Harness
的 PII 元数据处理是直接风险信号。
[Semantic View YAML / Tags / Access Modifiers](https://docs.snowflake.com/en/user-guide/views-semantic/semantic-view-yaml-spec)
[Semantic Views 安全最佳实践](https://docs.snowflake.com/en/user-guide/views-semantic/best-practices-dev)

**验证机制。** Verified Query Repository 保存自然语言问题、预期 SQL、验证人和时间；
Cortex Analyst Evaluations 比较生成 SQL 与 verified query 结果，跟踪准确率、回归和延迟。
更关键的是，被选作 ground truth 的 verified query 会在该次评测的临时语义视图中移除，
避免它同时指导生成，从而降低评测泄漏；官方也明确提醒相对日期会造成 ground truth 漂移。
[Verified Query Repository](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository)
[Cortex Analyst Evaluations](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations)

**反馈与人类治理。** Cortex Analyst 可基于查询历史/使用数据建议 verified queries、filters
和 metrics，但建议不会自动生效，需用户 Accept/Edit/Dismiss。这与“LLM 起草、人类批准
指标定义”的边界一致。
[Semantic Model/View Suggestions](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/verified-query-suggestions)

**运行/集成与 Prompt 组织。** 产品通过 Snowsight、SQL 和 REST API 使用语义视图，并
允许在语义定义中设置 SQL generation / question categorization custom instructions；它
不是以仓库内 `CLAUDE.md`、Commands、Subagents、Hooks 组成的开发 Harness。
[Cortex Analyst REST API](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/rest-api)

**优势。** 给出了规范语义对象、细粒度治理、verified queries、回归评测和 human-in-loop
建议的完整产品化实例；尤其强化了评测隔离与 PII 元数据风险。

**缺口。** 绑定 Snowflake；主要解决 SQL 可回答的问题，不负责跨业务 Codebase 的仓库
开发维护、Prompt/Hook 组织、独立对抗审查、入口无关的来源页脚和跨平台适配。

### 12.4 横向比较

| 维度 | Claude Code 原生扩展 | dbt MCP + Agent Skills | Snowflake Cortex Analyst | 本 Harness v1 取向 |
| --- | --- | --- | --- | --- |
| 核心定位 | 通用编码 Agent Harness 原语 | Agent 数据工程/语义层工具与 Skills | 语义视图驱动 text-to-SQL 产品 | Agent 主导仓库开发、维护、分析与治理闭环 |
| 运行/集成 | CLI、项目配置、Skills、Subagents、Hooks、MCP | MCP + dbt 项目/Platform + 多客户端 Skills | Snowsight、SQL、REST、Snowflake 对象 | Claude Code v1；工具适配器可替换 |
| 语义层优先 | 不内置 | 有明确 Semantic Layer 工具，但顺序由 Agent/Harness 控制 | Routing Mode 原生优先语义 SQL | SEM-001 强制，降级需证据 |
| 治理/权限 | deny/ask/allow、managed settings、sandbox | dbt contracts/access + warehouse/MCP 凭证 | RBAC、tags、masking/row policies、access modifiers | 人类指标所有权 + 路径/工具/数据纵深权限 |
| 验证 | 通用 Hook 和测试编排能力 | compile/build/test/freshness/health + Skill A/B | verified queries、准确率/回归/延迟、holdout 隔离 | 数据质量 + 独立审查 + 离线评测 + 纠正闭环 |
| Prompt/Agent 组织 | 原生分层最完整 | 模块化 Agent Skills | 模型 custom instructions，非代码 Harness | 根契约 + Commands/Skills + 独立审查 Agent + Hooks |
| 可移植性 | Claude Code/Agent SDK 生态 | Skills 跨 Agent，数据能力偏 dbt | Snowflake 专属 | 领域规则入口无关，v1 适配 Claude Code |
| 主要优势 | 与目标载体一致、权限和扩展原语完备 | 仓库开发/语义/血缘工具最接近目标 | 语义治理与评测产品化最成熟 | 组合三者优势并补足 Anthropic ChatBI 反馈闭环 |
| 主要缺口 | 无数据领域治理 | 无完整答案审查/来源/反馈合同 | 无跨 codebase 开发 Harness | 需要自行实现、验证并维护这些组合约束 |

### 12.5 需求影响摘要

#### Keep（保持）

- 保持 P0-01 的领域模型硬前置和 46 条规则不变；竞品能力不能削弱规则。
- 保持 P0-02 的根契约 + 按需知识分层、P0-04 独立审查 Agent、P0-06 语义层优先、
  P0-07 模型/文档共置、P0-08 确定性门控及 P0-10 评测/纠正闭环。
- 保持 NFR-PORT-02 的产品中立领域层：Claude Code 是 v1 载体，dbt/Snowflake 只是适配参考。

#### Add（新增）

- P0-04：关键安全与审查规则必须进入 Subagent 自身 Prompt，并限制 tools/permissions。
- P0-05 与 NFR-SEC-05：Claude Code 权限和 OS 沙箱双层执行，Prompt 不充当权限机制。
- P0-08：生产 P0 门控不以实验性 Agent Hook 为唯一实现；command Hook 必须安全处理输入。
- P0-10、NFR-COR-04、AC-07：ground truth 与运行时示例隔离，分别衡量 seen/unseen 能力。
- P1-08：标准 MCP 数据工具适配器按只读发现/查询/可变更操作分组授权。
- R-11—R-13：登记评测泄漏、工具过度授权和 Subagent 规则缺失风险。

#### Change（调整）

- P0-02 将“根 Prompt 保持简洁”具体化为约 200 行的默认预算，但以 Token 和检索评测
  为最终依据，不把行数变成脱离效果的机械门槛。
- AC-03/AC-08 增加权限与沙箱分别验证、隔离子 Agent 规则验证、Hook 恶意输入验证。
- 第 11 节的 15 项推荐值保持不变，但统一明确为等待用户确认的默认值，不能当成组织事实。

#### Avoid（避免）

- 避免把全部参考知识放进 `CLAUDE.md`，或为每项知识都创建 Subagent。
- 避免一次性授予 MCP/CLI 的所有可变更工具；避免只靠 Prompt 约束外部目录写入。
- 避免让同一 verified example 同时指导运行并充当评测答案；避免复制 Anthropic/Snowflake
  的准确率数字作为本项目承诺。
- 避免因某一产品已有能力而跳过来源页脚、独立审查、纠正回流或人类指标所有权。

#### Out-of-scope（继续排除）

- v1 不自建 BI UI、不替代语义层/仓库/身份/CI，不自动连接外部沟通系统。
- v1 不强制采用 dbt 或 Snowflake，也不复制其完整托管平台；只实现适配接口与 Harness 约束。
- v1 不支持外部 Business Codebase 写入，不允许 LLM 自动批准规范指标或高风险发布。

## 13. 需求来源

- `docs/anthropic-self-service-data-analytics-with-claude-zh.md`：ChatBI 方法、失败模式、四层栈、Skill 模板、验证和反馈实践。
- `docs/chatbi-harness-domain-model.md`：本项目规范术语、实体、执行方、规则 ID 和来源追踪。
- 用户目标：Claude Code v1、主要交付类型、Warehouse Workspace 及多个业务 Codebase 读取范围、先领域梳理后实现。
- `docs/agents/domain.md`：单上下文领域文档约定。
- Claude Code、dbt Labs 和 Snowflake 的官方文档/官方 GitHub：第 12 节逐项链接的参考分析。

STATUS: REQUIREMENTS_FINALIZED
