# Feature Flow: 需求驱动建造工作流 (requirement-driven build) v1

> Status: **AS_BUILT** (legacy step 7.b). This doc was originally SCANNED
> (legacy step 2: pre-implementation capability map). It has been updated after
> implementation to reflect the as-built code. The design sections (§1-§9) are
> still accurate as design reference; the AS_BUILT appendix at the end maps the
> design to actual file paths + line numbers. New code references cite the
> as-built harness source under `harness/`.

## 0. 一句话定位

`/chatbi-analyze` 只查询（T1->T2->T3 降级），需求需要**新模型**时它 STOP
（`chatbi-analyze.md:194-207`、`chatbi-runbook/SKILL.md:108-127`）。新工作流闭合这个
缺口：拿到需求 -> 推导要造哪些 DWD/DWS/ADS -> 串 `/chatbi-maintain-model` 逐个建 ->
齐备后回到 `/chatbi-analyze` 出答案。**它本身不写受治理模型内容、不批指标、不答问**，
是 analyze 与 maintain-model 之间的桥接编排器。

## 1. 现有命令能力地图（8 个命令）

build 产物里目前 8 个命令（`build-product.sh:35-39`）。下表是各命令的输入/输出/信任层，
重点行（analyze、maintain-model、bootstrap、build-from-requirement）展开。

| 命令 | 做什么 | 输入 | 输出 | 信任层 | 关键文件 |
| --- | --- | --- | --- | --- | --- |
| `/chatbi-init` | 诊断安装就绪度 | shared/local config + confirmed claude exe | `diagnostic.json` (PASS/WARN/BLOCKED) | 只读诊断 | `commands/chatbi-init.md`、`diagnostics.py` |
| `/chatbi-bootstrap` | 从零搭本地 Warehouse 脚手架 | MySQL 连接 + 可选 codebase alias | local+shared config、`dw` DB、`source_inventory.json`、dbt 脚手架、蓝图 stub | **INFRA SETUP only**（不造受治理模型） | `commands/chatbi-bootstrap.md`、`bootstrap.py`、`skills/chatbi-bootstrap/SKILL.md` |
| `/chatbi-build-from-requirement` | 推导建造计划 + 串 maintain-model + 交接 analyze | 需求文本 + 粒度/分段 | 建造计划 + 每模型 maintain-model footer + analyze 交接 | **orchestrator**（推导+串接+人在环+交接，不写受治理内容/不答问/不批指标） | `commands/chatbi-build-from-requirement.md`、`build_plan.py`、`skills/chatbi-build/SKILL.md` |
| `/chatbi-maintain-model` | 单模型建造/维护 | model-change-request | 受治理模型 + ImpactManifest + 同步证据 | 候选写入；受保护动作 STOP | `commands/chatbi-maintain-model.md`、`impact.py`、`skills/chatbi-maintenance/SKILL.md` |
| `/chatbi-maintain-knowledge` | 知识参考 lint/起草 | reference path/request | lint 通过的 route-ready 参考 | 候选；不批指标 | `commands/chatbi-maintain-knowledge.md`、`knowledge.py` |
| `/chatbi-analyze` | 5 层降级查询出答案 | request.schema.json (7 字段) | answer + provenance footer (16 字段) | 只查询；不建造；不自认证 | `commands/chatbi-analyze.md`、`skills/chatbi-runbook/SKILL.md`、`evidence.py` |
| `/chatbi-evaluate` | 离线评测套件 | evaluation-request | EvaluationRun（seen/unseen、token、latency） | 隔离评分；不批门槛 | `commands/chatbi-evaluate.md`、`evaluator.py` |
| `/chatbi-correction` | 结构化纠正记录 | correction-request | fix_candidate + eval_case_candidate（默认 owner_approved=false） | 候选；不自动合并 | `commands/chatbi-correction.md`、`evaluator.py:build_correction_record` |

### 1.1 `/chatbi-analyze` 5 层降级（新工作流要桥接的"查询侧"）

`chatbi-analyze.md:55-103` + `chatbi-runbook/SKILL.md:33-127`：

1. **Layer 1 Clarify**（REQ-001/002/003/004）：`entity/time_range/segment/supported_decision`
   缺失或歧义即 STOP 问最小澄清；多义词查业务上下文显式解析，不猜（RAW-003）。
2. **Layer 2 T1 语义层**（SEM-001/002）：先发现并编译语义层 metric/dimension/segment；
   覆盖则查询并记 `EvidenceEntry(source_tier="T1")`。
3. **Layer 3 T2 整理参考**（RAW-001，证据背书降级）：**仅当记录了 T1 gap**（覆盖缺失/
   编译失败/权限不足/新鲜度失败）才回退；从明确文档定 model/grain/join/filter。
4. **Layer 4 T3 原始探索**（RAW-002/003，高风险）：**仅当记录了 T2 gap** 才回退；强制
   高风险复核警告（ANS-003）。
5. **Layer 5 独立审查 + 门控**（REV-001/002/003）：`compute_candidate_sha` 绑定候选 ->
   派发 `adversarial-reviewer` 子 agent（`review.schema.json` 11 维覆盖）->
   `subagent_review_gate.py` 校验 PASS+SHA 匹配 -> `stop_gate.py` 校验无未关发现 ->
   交付带 16 字段 provenance footer 的答案。

**关键停点（`chatbi-analyze.md:194-207`）**：T1 覆盖无法确定即 STOP（不静默降级）；
需求需要新模型时，analyze 没有建造路径，只能停在"语义层未覆盖/模型缺失"gap。**这就是
新工作流要闭合的缺口**：analyze 停下 -> 新工作流接管造缺失模型 -> 回 analyze 出答案。

### 1.2 `/chatbi-maintain-model` 单模型建造流程（新工作流要串接的"建造侧"）

`chatbi-maintain-model.md` + `chatbi-maintenance/SKILL.md`：

1. **Classify**（SKILL §1）：`change_kind` ∈ model/column/semantic/reference/Skill/
   downstream/eval；target 是逻辑别名。受保护动作（`approve_metric`/
   `change_access_policy`/`production_publish`/`destructive_migration`）即 STOP 要人批
   （SEM-003）。
2. **读蓝图**（SKILL §0 + §3）：跑 dbt 前读 `docs/org/data-warehouse-blueprint.md`
   § Tooling（dbt 可执行路径）；起草 ODS/DWD/DWS 前读 § Metrics（哪些表是 fact/dim、
   哪些列是分子/分母、功能轴）——用于标注列角色，不让 operator 每次重述。§ Metrics 缺
   即问 operator，不发明列角色。
3. **Build impact manifest**（SKILL §2，`impact.py:138-230` `build_impact_manifest`）：
   覆盖 metadata/semantic/reference/Skill/tests/downstream/eval/code；记
   `evidence_state`(sufficient/missing/uncertain) 与 `p0_eval_failed`；missing/uncertain
   显式记录不降级为空占位。
4. **Produce candidates**（SKILL §3）：每个 `change_required=True` 的资产产出候选变更，
   应用后标 `synced=True`。
5. **Sync gate**（SKILL §4，DOC-004）：任何 `change_required` 但 `synced=False` ->
   blocking drift -> `stop_gate` 失败不交付；全同步 + 测试/eval 存在 + 证据充分 + 无 P0
   失败 -> 通过；不确定是否同步完 -> fail-closed 不交付。
6. **PostToolUse record**（SKILL §5，`posttool_impact.py`）：只记录 manifest 与 drift
   标记，**不回滚**。
7. **Footer**（SKILL §8）：change_kind、target、affected assets(synced/unsynced)、
   evidence_state、p0_eval_failed、protected_action、review round、owner、freshness。

**信任边界**：可起草候选代码/元数据/参考/eval；**不可**批指标/访问策略/生产发布/破坏性
迁移（SEM-003）。单模型交付受 `stop_gate` + sync gate 约束。

### 1.3 `/chatbi-bootstrap` 与 source_inventory（新工作流的"源现状"输入）

`bootstrap.py:200-250` 的 `SourceInventory`/`SourceTable`/`SourceColumn`（frozen-slots
dataclass）是 bootstrap introspect 源 `public` schema 后写到
`.chatbi/bootstrap/source_inventory.json` 的产物（`chatbi-bootstrap/SKILL.md:160-178`）。
`schema_version: 1`，含 tables(name)/columns(name, data_type, is_primary_key)。
**这是新工作流"读源现状"的契约面**——它不重新 introspect，直接读这个 JSON。

## 2. 新工作流在 harness 里的位置（两方案取舍）

新工作流是 analyze（查询）与 maintain-model（单模型建造）之间的**编排层**。两种落法：

### 方案 A：新增第 8 个命令 `/chatbi-build-from-requirement`（推荐）

形态对齐 bootstrap：command（`commands/chatbi-build-from-requirement.md`）+ SKILL
（`skills/chatbi-build/SKILL.md`）+ 可选薄 lib（`lib/chatbi_harness/build_plan.py`）。

**优点**：
- 信任边界干净。新命令是**编排器**：推导建造计划、串 maintain-model、人在受保护点批、
  齐备后交给 analyze。它本身不写受治理模型内容（maintain-model 干）、不答问（analyze
  干）、不批指标（人干）。与 bootstrap"infra setup only"是同一种"窄信任层"模式。
- analyze 的 5 层降级契约不被污染。analyze 的合同是"产带 footer 的答案"，停点是"需要
  新模型即 STOP"（SEM-001/RAW-001）。若让它建造，停点要变"需要新模型 -> 建造"，破坏
  降级语义与 candidate_sha 审查门（审查门是给分析候选的，不是给建造计划的）。
- 与现有 command-per-responsibility 模式一致（init/bootstrap/maintain-model/analyze
  各管一摊），`CLAUDE.md` 路由表 +1 行即可。
- 受保护动作边界（4 个 enum）保持不变。

**代价**：第 8 个命令 -> `build-product.sh:36-38` 命令循环 +1、`product-README.md` "Seven"
->"Eight"、`harness/CLAUDE.md` 路由表 +1 行（仍 <200 行预算，`gates.py:194-200`）。

### 方案 B：扩展 `/chatbi-analyze` 加 "build-then-answer" 模式

**优点**：用户"问一个问题"单入口（UX 简单）。

**缺点**（不推荐）：
- 破坏 analyze 信任层。analyze 是**只查询**（T1->T2->T3 降级，永不建造），其停点之一就是
  "需求需要新模型 -> STOP"（`chatbi-analyze.md:206-207`）。扩展成建造违背 SEM-001/RAW-001
  降级语义。
- 混两份合同。analyze 交付 16 字段 provenance footer（`provenance.schema.json`）；建造
  交付 ImpactManifest（`impact-manifest.schema.json`）+ sync gate。一份命令背两份合同。
- candidate_sha 审查门（`subagent_review_gate.py`）是绑分析候选的，建造计划没有等价物。
- 5 层流程膨胀，违背 DOC-005（删减而非加长）。

**结论**：采用方案 A。下文步骤序列按方案 A 写。新命令的信任层 = **编排器**：推导 +
串接 + 人在环 + 交接，不亲自写受治理内容、不亲自答问。

## 3. 新工作流步骤序列（4 步细化）

每步标注：读什么 / 推导什么 / 调哪个现有命令 / 人在哪批 / 触发治理规则。
输入是一个"需求"（比 analyze 的 7 字段 request 更宽：可能是"造好能回答 X 的指标体系"，
而非"回答 X"）。

### Step 1 — 拿需求 + 读现状（纯读，不写）

**读什么**：
- 需求文本（业务问题 + 期望粒度/维度/分段）。
- 蓝图 `docs/org/data-warehouse-blueprint.md`：§ Metrics（设计意图：哪些表 fact/dim、
  分子/分母、功能轴、已规划的 `DWD needed`/`DWS needed`，见 `data-warehouse-blueprint.md:45-104`）、
  § Layers（跨层引用规则，见 §4）、§ Tooling（dbt 路径，maintain-model 跑 dbt 前要读）、
  § Source（源库 + source_inventory 路径）。
- `.chatbi/bootstrap/source_inventory.json`（源表/列/PK/类型，`bootstrap.py:217-250`）。
- 现有模型注册（**当前是 GAP，见 §5**）：扫 `models/{ods,dwd,dws,dim}/` 目录 + 读蓝图，
  或读一个由 maintain-model 维护的 `.chatbi/model_registry.json`。
- 受治理语义层现状（T1 覆盖）：通过 `select_adapter`（managed->cli->fixture，
  `adapters/__init__.py:495-718`）发现已有 metric/dimension/segment。

**推导什么**：
- 需求->指标口径候选（分子/分母/维度/分段/粒度）。多义词显式解析（REQ-002），不猜
  （RAW-003）。多团队不同定义则列候选问人，不合并折中（REQ-004）。
- 现状覆盖判断：T1 已覆盖？现有 DWD/DWS 可复用？还是缺模型？

**调谁**：无（纯读 + 推导）。

**人在哪批**：需求歧义即 STOP 问最小澄清（REQ-001/002），与 analyze Layer 1 同语义。

**治理规则**：REQ-001/002/003/004、RAW-003、SEM-001（先查 T1）、SCOPE-001（读不越界）、
SEC-003/PORT-001（输出不带绝对路径/密钥）、DOC-001（蓝图是受治理共置参考）。

### Step 2 — 推导建造计划（agent 推理，不写死 prompt）

**核心设计边界**（`orchestrator-state.md:31-33`）：join/聚合逻辑由 agent 从需求 + 数仓
现状 + 蓝图 Metrics+Layers **推导**，**不**写死在 prompt 里。lib 只提供"读"（inventory、
registry、蓝图段落）与"校验计划形状"，不做推导。

**推导什么**：
1. 能否复用现有 DWS/DWD？能 -> 直接进 Step 4。
2. 不能 -> 要从 ODS 造哪些 DWD（join 逻辑：哪些 ODS 表 join、join key、grain）？
   哪些 DWS（聚合逻辑：按哪些维度聚合、度量公式）？推导依据 = 蓝图 § Metrics 的
   `DWD needed`/`DWS needed` 段（如 `data-warehouse-blueprint.md:85-95` 已写明
   `dwd_session_creator_detail` 的 join 与 grain、`dws_function_usage_daily` 的聚合列）+
   § Layers 的跨层引用规则（ADS->DWS->DWD->ODS，不跨层）+ source_inventory（源表/列存在性）。
3. ODS 缺表（source_inventory 里没有对应源表）-> **标记要扩源**（源边界，人批）。
4. 出建造计划：有序模型列表（ODS 先 -> DWD -> DWS -> ADS 后），每个模型带 change_kind、
   target、依赖的上游模型、推导出的 join/聚合摘要、是否触及受保护动作。

**调谁**：无（推导）。可调薄 lib `validate_build_plan`（**新 GAP**，见 §5）校验计划形状
（跨层依赖有序、target 是逻辑别名、不越 Workspace）。

**人在哪批**：
- **源边界扩源**：ODS 缺表要扩源 -> STOP 要人批（SCOPE-001/SEC-001；agent 不能发明源表
  RAW-003，不能自扩源边界）。人确认新源在范围内并授权 bootstrap 重新 introspect 或手工
  补 source_inventory。
- **指标口径**：推导出的分子/分母/分段口径 -> STOP 要指标所有者批（SEM-003 `approve_metric`；
  agent 可起草口径文档，不可定稿）。

**治理规则**：SEM-003（指标口径人批）、SCOPE-001/SEC-001（源边界人批）、RAW-003（不捏造
表/字段/join）、DOC-002（模型元数据含粒度/范围/join key/分层）、PORT-001（target 用逻辑
别名）、META-003（声明式知识在蓝图，程序性在 SKILL）、HOOK-004（计划校验 fail-closed）。

### Step 3 — 串 `/chatbi-maintain-model` 逐个建（人 在 受保护点批）

**调谁**：对建造计划里每个模型，按依赖序（ODS->DWD->DWS->ADS）调
`/chatbi-maintain-model`。每次调用走 maintain-model 的完整流程：classify -> 读蓝图
§ Tooling/§ Metrics -> `build_impact_manifest` -> 产出候选 -> sync gate（DOC-004）->
`stop_gate` -> footer。maintain-model 内部读蓝图 § Metrics 拿列角色（`chatbi-maintenance/SKILL.md:46-52`），
新工作流在 Step 2 推导的 join/聚合摘要作为 change-request 的输入传进去。

**人在哪批**（每个模型的受保护点）：
- 指标定义起草 -> 人批才定稿（SEM-003 `approve_metric`）。
- 访问策略变更 -> 人批（SEM-003 `change_access_policy`）。
- 生产发布 -> 人批（SEM-003 `production_publish`）。
- 破坏性迁移 -> 人批（SEM-003 `destructive_migration`）。
- 高风险（executive/regulated/PII/core_finance）-> 人签字（SEC-001/ANS-003）。
- **常规 ODS/DWD/DWS/ADS 建造 + join/聚合推导 = agent**（`CONTEXT.md:9-12`、
  `orchestrator-state.md:31`），不需人批每个模型。

**建造计划持久化**：用 `harness_state.write_state`（`harness_state.py:100-123`）把计划
与每步进度写到 `.chatbi/runs/<session_id>/build_plan.json`，断点可续。每模型 maintain-model
产出的 ImpactManifest 聚合进计划（复用 `impact.py` 的 `ImpactManifest` 形状）。

**治理规则**：DOC-004（sync gate）、SEM-003（4 类受保护动作）、SEC-001/ANS-003（高风险
签字）、REV-001（若建造触及分析候选则审查门）、HOOK-001/004（`stop_gate`/`posttool_impact`
确定性门）、FBK-003（评测通过不是绝对正确性保证）。

### Step 4 — 齐备 -> 建 ADS / 走 `/chatbi-analyze` 出答案

**调谁**：
- 若需求要 ADS 层（面向应用的汇总表），最后串一个 `/chatbi-maintain-model` 建 ADS
  （ADS 依赖 DWS，跨层规则见 §4）。
- 模型齐备后，**交给 `/chatbi-analyze`** 出答案。analyze 此时 T1 语义层已覆盖（新造的
  metric/dimension 已通过 maintain-model 的 semantic change_kind 落到语义层），走 Layer 2
  T1 编译查询 -> Layer 5 独立审查 -> 交付带 footer 的答案。

**关键闭合点**：analyze 原本"需要新模型即 STOP"（`chatbi-analyze.md:206-207`）的缺口，
由新工作流 Step 1-3 造齐模型后，analyze 在 Step 4 不再 STOP，直接 T1 出答案。这把
"查询侧停点"与"建造侧入口"接上了。

**人在哪批**：答案交付前走 analyze 的独立审查门（`adversarial-reviewer` + `subagent_review_gate`，
REV-001/002/003）；高风险用途人签字（ANS-003）。

**治理规则**：SEM-001（T1 优先）、REV-001/002/003（独立审查）、ANS-001/002/003（footer
与高风险警告）、QLT-001（新鲜度/完整性）、FBK-003。

## 4. 跨层引用规则该进蓝图哪一段

### 现状

`data-warehouse-blueprint.md:35-43` 的 `## Layers` 段只有 4 个空 header（ODS/DWD/DWS/
DIM），**没有任何跨层引用规则文字**。`## Metrics` 段（`:45-104`）有设计意图（fact/dim、
分子/分母、`DWD needed`/`DWS needed`），但那是指标设计，不是跨层依赖约束。`## Lineage`
段（`:105`）也是空的。

harness lib 里**没有**跨层引用校验（grep `cross.?layer`/`lineage` 命中的都是 adapter 的
源表 lineage 发现，`adapters/__init__.py:343/490`、`adapters/fixture.py:248`，不是 DW
跨层 ADS->DWS->DWD->ODS 校验）。

### 建议

跨层引用规则（ADS->DWS->DWD->ODS，不跨层；DIM 可被 DWD/DWS 引用）写进蓝图的
**`## Layers` 段**，与 § Metrics 同套路：agent 读（`chatbi-maintenance/SKILL.md:46-52`
已示范"读 § Metrics"的模式，加一句"读 § Layers 拿跨层依赖规则"）。理由：

- 跨层规则是**声明式领域知识**（"指标是什么/模型怎么分层"），按 META-003 属于蓝图，
  不属于程序性 SKILL。写进 SKILL 会变成易过期的硬编码（违背 PORT-001/META-003）。
- 与 Tooling 段（operator 指引）、Metrics 段（设计意图）同处一份 companion doc，符合
  DOC-001 共置原则。
- bootstrap stub 已创建 `## Layers` header（`chatbi-bootstrap/SKILL.md:191-203`），只需
  在 stub 里补一段占位说明 + 由 operator/领域负责人填实际规则。

**建议的 Layers 段内容骨架**（写到 blueprint stub + 由人填）：
- 层级顺序：ODS（贴源）-> DWD（明细，join ODS）-> DWS（汇总，聚合 DWD）-> ADS（应用，
  汇总 DWS）。DIM 独立维度层，可被 DWD/DWS/ADS 引用。
- 不跨层引用：ADS 只依赖 DWS/DIM，不直接读 DWD/ODS；DWS 只依赖 DWD/DIM，不直接读 ODS；
  DWD 只依赖 ODS/DIM。
- 例外需显式记录（哪个模型跨层 + 理由 + 人批），进模型元数据（DOC-002 的分层字段）。

**不新增 governed rule**（见 §7）：跨层不跨层是 DW 设计约束，属蓝图声明式知识，不是
新的治理规则 ID。

## 5. 现有 lib 可复用能力 + 缺口

### 5.1 可复用（不重造）

| 能力 | 位置 | 新工作流怎么用 |
| --- | --- | --- |
| `SourceInventory`/`SourceTable`/`SourceColumn` | `bootstrap.py:200-250` | Step 1 读源现状（直接读 source_inventory.json，不重新 introspect） |
| `build_impact_manifest` + `ImpactManifest` + `AffectedAsset` | `impact.py:59-230` | Step 3 每个模型的 maintain-model 调用产出 manifest；新工作流聚合进建造计划（asset_kind 已含 `code`/`metadata`/`semantic` 等 8 类，`impact.py:34-37`） |
| `load_effective_config` + `EffectiveConfig` | `config.py:354-430` | 读配置 + 复跑所有门（schema/secret/path-binding/protected-actions） |
| `select_adapter` + `CliAdapter` | `adapters/__init__.py:495-718`、`317-463` | Step 1 发现 T1 语义层现状；Step 3 若要跑 `dbt list` 枚举现有模型，按 bootstrap option (a) 的 `--execute=<SQL>` 模式（`chatbi-bootstrap/SKILL.md:121-159`） |
| `policy.decide` + `PolicyRequest` | `policy.py:88-174` | 受保护动作检查（SEM-003：agent actor + protected_action -> block；`policy.py:107-118`） |
| `harness_state.write_state`/`read_state` | `harness_state.py:100-123`、`63-97` | 持久化建造计划与进度到 `.chatbi/runs/<sid>/build_plan.json`，断点续 |
| `compute_candidate_sha` | `evidence.py:163-178` | 给建造计划算 SHA（若建造计划要走审查/版本绑定） |
| `gates.fail_closed` + `GateError` + `GateDecision` | `gates.py:143-167`、`52-140` | fail-closed 异常边界（HOOK-004） |
| `knowledge.lint_reference` | `knowledge.py:68-114` | 若建造触及受治理参考文档，走 lint |
| `validate_domain_contract` | `gates.py:170-233` | 不被新工作流调用，但它约束"不能随便加规则"（见 §7） |

### 5.2 缺口（需新增，但都是薄层）

**缺口 1：没有模型注册（model registry）。**
`chatbi-harness.schema.json` 的 EffectiveConfig 只有 workspace/business_codebases/
adapters/governance/evaluation/runtime/path_bindings/cli_adapters（`:20-185`），**没有
models 字段**。Step 1 要"读现有模型注册"目前只能扫 `models/{ods,dwd,dws,dim}/` 目录 +
读蓝图，没有结构化清单。

- **建议**：加一个 `.chatbi/model_registry.json`，由 `/chatbi-maintain-model` 每次成功
  建模后追加（模型名、层、上游依赖、change_kind、created_rev、owner）。它是
  maintain-model 的副产物（像 source_inventory 是 bootstrap 的副产物），作为新工作流
  Step 1 的契约面。薄 lib：`build_plan.py` 里加 `read_model_registry(path)` +
  `ModelEntry` dataclass（frozen-slots，对齐 `SourceInventory` 风格）。
- **不进 config schema**：registry 是 derived evidence（运行时产物，`.chatbi/` 下），
  不是 governed config，避免改 schema（`additionalProperties: false`，`chatbi-harness.schema.json:186`）。

**缺口 2：没有跨层引用校验。**
lib 没有"ADS->DWS->DWD->ODS 不跨层"的校验（见 §4）。

- **建议**：规则写进蓝图 `## Layers` 段（声明式）；薄 lib 加
  `validate_layer_dependency(plan, layer_rules)` 校验建造计划的依赖序不跨层。layer_rules
  从蓝图 Layers 段解析（agent 读 + 传给 lib，或 lib 解析 markdown）。这是确定性校验，
  属 HOOK-001 确定性门范畴，不做开放式推理。
- **替代**：不写 lib，让 agent 读蓝图 Layers 段后自行校验（同 maintain-model 读 Metrics
  段的模式）。代价是没确定性兜底。**推荐写薄 lib**，因为跨层依赖是可机械校验的。

**缺口 3：没有需求->建造计划推导器。**
这是 agent 推理（join/聚合逻辑从需求+现状+蓝图推导），**不是确定性 lib**——任务明确
要求"推导不写死 prompt"。

- **建议**：不写推导器 lib。lib 只提供：① 读（registry/inventory/蓝图段落，缺口 1+2
  的薄层）；② 校验计划形状（`validate_build_plan`：跨层依赖有序、target 逻辑别名、不
  越 Workspace、受保护动作标记齐全）。推导本身在 SKILL 里描述步骤，agent 跑。
- 可选：加 `BuildPlan` dataclass + `build-plan.schema.json`（对齐
  `impact-manifest.schema.json` 风格），让计划有可校验形状，便于持久化与审查。

**缺口 4：没有"扩源"受保护动作表达。**
源边界扩源（ODS 缺表要加新源）不是 `protected_actions` enum 里的 4 个之一
（`chatbi-harness.schema.json:42-49`、`config.py:34-41` 的 `_REQUIRED_PROTECTED_ACTIONS`）。
它是 SCOPE-001/SEC-001/RAW-003 的"stop and escalate"——agent 不能自扩源边界，必须人批。
- **建议**：不把 `extend_source` 加进 `protected_actions` enum（那会改 schema +
  `_REQUIRED_PROTECTED_ACTIONS`，是 governed config 变更）。扩源作为建造计划里一个
  `requires_human_approval=True` 的标记项，新工作流 STOP 要人批，用 SCOPE-001/SEC-001
  作 rule_ids。这与 correction 的 `owner_approved=false`（SEM-003 默认不批）同模式。

## 6. 受保护动作清单（必须人批）

来自 `chatbi-harness.schema.json:42-49` 的 enum + `config.py:34-41` + `CONTEXT.md` +
`CLAUDE.md:21-27`。

| 受保护点 | 规则 | 表达方式 | 新工作流在哪步 |
| --- | --- | --- | --- |
| 指标口径批准（定义/批准 canonical metric） | SEM-003（`approve_metric`） | `policy.decide` actor=agent -> block（`policy.py:107-118`）；maintain-model `change_kind=semantic` 起草但人批 | Step 2 推导口径、Step 3 maintain-model 起草 |
| 访问策略变更 | SEM-003（`change_access_policy`） | 同上 | Step 3 若模型触及访问策略 |
| 生产发布 | SEM-003（`production_publish`） | 同上 | Step 3/4 发布点 |
| 破坏性迁移 | SEM-003（`destructive_migration`） | 同上 | Step 3 若要破坏性改表 |
| 源边界扩源（ODS 缺表加新源） | SCOPE-001/SEC-001/RAW-003 | 建造计划标记 `requires_human_approval`，STOP 要人批（**不进 protected_actions enum**，见 §5 缺口 4） | Step 2 推导出 ODS 缺表 |
| 高风险签字（executive/regulated/PII/core_finance） | SEC-001/ANS-003 | `policy._is_high_risk` warn 要人签字（`policy.py:82-85`、`139-153`）；`high_risk_classes` enum（`chatbi-harness.schema.json:120-133`） | Step 4 答案交付 + Step 3 高风险数据 |
| 答案独立审查通过 | REV-001/002/003 | `adversarial-reviewer` + `subagent_review_gate` + `stop_gate`（agent 不能自认证，META-008） | Step 4 analyze 交付前 |

**agent 可做（不需人批）**：常规 ODS/DWD/DWS/ADS 建造、join/聚合逻辑推导、候选代码/
元数据/参考/eval 起草、跑验证、汇总证据、串接 maintain-model（`CONTEXT.md:9-12`、
`CLAUDE.md:21-27`）。

## 7. 与现有 46 规则的关系（默认复用，不新增）

**结论：46 规则不变，不新增规则 ID。** 新工作流全部复用现有规则。

**为什么不能随便加规则**：`gates.py:170-233` 的 `validate_domain_contract` 强制：
- 域模型 `docs/chatbi-harness-domain-model.md` 里 `grep` 出的 46 个 rule ID 必须在
  契约产物（CLAUDE.md/CONTEXT.md/三个 rules 文件）里全部被引用（`gates.py:220-227`
  missing_rule_ids -> block）。
- 契约产物里引用的 rule ID 必须都在域模型里（`gates.py:212-219` unknown_rule_ids ->
  block）。
- CLAUDE.md 不超 200 行（`gates.py:194-200`）。

加一个新规则 ID 要同时改域模型 + CLAUDE.md + rules 文件（governed 变更），且新规则要
塞进 200 行预算。新工作流不需要这个——它的每个受保护点都已有规则覆盖：

| 新工作流关切 | 复用的现有规则 |
| --- | --- |
| 建造要同步下游 | DOC-004（sync gate，`impact.py`、`chatbi-maintenance/SKILL.md:54-60`） |
| 指标口径人批 | SEM-003 |
| 源边界人批 | SCOPE-001/SEC-001/RAW-003 |
| 不捏造表/字段/join | RAW-003 |
| 推导依据是声明式蓝图 | META-003（声明式 vs 程序性分离）、DOC-001（共置） |
| 模型元数据含分层/join key | DOC-002 |
| 删减而非加长 | DOC-005 |
| target 用逻辑别名 | PORT-001 |
| fail-closed | HOOK-004 |
| 答案独立审查 | REV-001/002/003、META-008 |
| 高风险签字 | SEC-001/ANS-003 |
| 评测非绝对正确 | FBK-003 |
| 需求澄清 | REQ-001/002/003/004 |
| T1 优先 | SEM-001/002 |

**唯一可能误判为"新规则"的**：跨层不跨层（ADS->DWS->DWD->ODS）。但这是 DW 设计约束，
属蓝图 `## Layers` 段的声明式知识（见 §4），不是 governed rule ID。agent 读蓝图遵守，
不新增规则。所以 46 还是 46，`validate_domain_contract` 继续通过。

## 8. 影响的文件（实现时才动，本次只扫描）

实现新工作流（方案 A）预计动：
- **新增**：`commands/chatbi-build-from-requirement.md`、`skills/chatbi-build/SKILL.md`、
  `lib/chatbi_harness/build_plan.py`（薄：`read_model_registry`/`validate_build_plan`/
  `validate_layer_dependency`/`BuildPlan` dataclass）、
  `schemas/build-plan.schema.json`、`tests/harness/test_build_plan.py`。
- **改**：`lib/chatbi_harness/__init__.py`（导出）、`build-product.sh:36-38`（+1 命令）、
  `harness/CLAUDE.md` 路由表 +1 行、`harness/product-README.md`（"Seven"->"Eight"）、
  `harness/.claude/skills/chatbi-bootstrap/SKILL.md` Step 8（blueprint stub 补 `## Layers`
  段占位说明）、`chatbi-maintenance/SKILL.md`（maintain-model 建模后写 model_registry.json
  + 读 § Layers）、`docs/harness/installation.md` + `docs/harness/README.md`。
- **不改**：`chatbi-harness.schema.json`（registry 是 derived evidence 不进 config）、
  域模型 + 三个 rules 文件（不加规则）、`protected_actions` enum（扩源不进 enum）。

## 9. 待确认（给 orchestrator/plan-agent）

1. **命令名**：`/chatbi-build-from-requirement` 还是更短的 `/chatbi-plan-build` 或
   `/chatbi-build`？需 orchestrator 定。
2. **model_registry.json 落点**：`.chatbi/model_registry.json`（derived evidence）vs
   写进 dbt project 的 `meta`？建议前者（不耦合 dbt，对齐 source_inventory 模式）。
3. **建造计划是否走独立审查**：Step 2 的计划要不要像 analyze 候选那样过
   `adversarial-reviewer` + candidate_sha？还是只在 Step 4 答案过审查？倾向后者（计划
   是过程产物，审查落在最终答案与每个模型的 sync gate）。
4. **扩源闭环**：ODS 缺表时，是回到 `/chatbi-bootstrap` 重新 introspect 新源，还是人
   工补 source_inventory？前者更干净但要 bootstrap 支持增量 introspect（当前是一次性
   全量，`chatbi-bootstrap/SKILL.md:160-178`）。

STATUS: AS_BUILT

---

## 10. AS_BUILT appendix (implementation mapping)

The design sections above (§1-§9) were written as a pre-implementation scan.
This appendix maps the design to the actual as-built code (legacy step 7.b).

### 10.1 New files created

| File | Purpose | Key lines |
| --- | --- | --- |
| `harness/.claude/lib/chatbi_harness/build_plan.py` | Thin deterministic lib: dataclasses + factory + readers + validators + append | `ModelEntry` `:97`, `build_model_entry` `:192`, `read_model_registry` `:319`, `validate_build_plan` `:382`, `validate_layer_dependency` `:455`, `append_model_registry` `:503` |
| `harness/.claude/schemas/build-plan.schema.json` | JSON Schema contract for `validate_build_plan` (mirrors `impact-manifest.schema.json`) | full file |
| `harness/.claude/commands/chatbi-build-from-requirement.md` | Slash command (orchestrator trust layer, mirrors `chatbi-bootstrap.md` structure) | full file |
| `harness/.claude/skills/chatbi-build/SKILL.md` | Runbook (4-step flow: read -> derive -> chain maintain-model -> hand off to analyze) | full file |
| `tests/harness/test_build_plan.py` | 49 tests: frozen-slots, to_dict round-trip, factory validation, read_model_registry, read_source_inventory, validate_build_plan (incl. known_models SCOPE-001), validate_layer_dependency, append_model_registry | full file |
| `docs/dev-cycle-requirement-driven-build.md` | Dev cycle plan (7 cycles, dependency-driven) | full file |

### 10.2 Modified files

| File | Change |
| --- | --- |
| `harness/.claude/lib/chatbi_harness/bootstrap.py` | ADD `read_source_inventory(path)` `:253` + `merge_source_inventories(base, extra)` `:380` + `json`/`Path`/`Any` imports; UPDATE `__all__` |
| `harness/.claude/lib/chatbi_harness/__init__.py` | ADD build_plan import block (7 names) + bootstrap names (`read_source_inventory`, `merge_source_inventories`); UPDATE `__all__` (alphabetic) |
| `harness/.claude/skills/chatbi-bootstrap/SKILL.md` | Step 8: ADD `## Layers` header + skeleton (layer order, no-cross-layer rule, exceptions -> `ModelEntry.cross_layer_exception`) |
| `harness/.claude/skills/chatbi-maintenance/SKILL.md` | §3: ADD read `## Layers` block (META-003); §4: ADD `append_model_registry` after sync gate + stop_gate pass |
| `build-product.sh` | Command loop: 7 -> 8 commands `:35-38`; import canary: + `chatbi_harness.build_plan` `:60-63` |
| `harness/CLAUDE.md` | Routing table: +1 row (Build from a requirement) `:76`; 114 lines total (< 200 budget) |
| `harness/product-README.md` | "Seven" -> "Eight" `:3`; +1 table row; +1 install note |
| `harness/docs/harness/installation.md` | ADD § "Build from a requirement" (VERIFIED OFFLINE framing) |
| `harness/docs/harness/README.md` | ADD §2.5 `/chatbi-build-from-requirement` (lib surface VERIFIED OFFLINE; live chaining NOT YET EXERCISED) |
| `tests/harness/test_bootstrap.py` | ADD `ReadSourceInventoryTests` + `MergeSourceInventoriesTests` (merge union, collision, round-trip, incremental path) |
| `tests/harness/test_maintenance.py` | ADD `AppendModelRegistryAfterSyncGateTests` (sync gate pass -> append; fail -> no append) |
| `tests/harness/test_contract.py` | `required_routes`: + `/chatbi-bootstrap` + `/chatbi-build-from-requirement` (closes pre-existing gap) |
| `tests/harness/test_e2e.py` | `test_six_commands` -> `test_eight_commands`; + `chatbi-bootstrap.md` + `chatbi-build-from-requirement.md` |

### 10.3 Open point 6 resolution (known_models)

`validate_build_plan` signature (as-built, `build_plan.py:382-387`):
```python
def validate_build_plan(
    plan: BuildPlan,
    layer_rules: tuple[LayerRule, ...],
    known_models: frozenset[str] = frozenset(),
) -> None:
```
The SCOPE-001 cross-plan-boundary check is in the lib (not the SKILL): a dep
that is neither in `plan.models` nor in `known_models` -> `GateError`
(SCOPE-001, evidence_ref `build-plan:scope:<name>:<dep>`). The SKILL passes
`known_models = {m.name for m in read_model_registry(...)}`.

### 10.4 Test results

- Baseline: 566 tests (565 pass + 1 skip).
- As-built: 629 tests (628 pass + 1 skip). +63 new tests, all additive.
- `validate_domain_contract`: PASS (46 rules unchanged, CLAUDE.md 114 < 200).
- `build-product.sh`: import OK, no canary leak, no dev-only file leak.
