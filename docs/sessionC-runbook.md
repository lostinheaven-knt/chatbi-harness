# SessionC Runbook:`/chatbi-build-from-requirement` 实测

> 在 chatbi-ws 端到端实测第 8 个命令。单元测试 + build 验证(629 绿、
> `validate_domain_contract` PASS、build-product.sh 干净)只是离线验证;本
> runbook 实测 live 推导 + 串 maintain-model + 交接 analyze 这条路径,它
> 此前**未被执行过**。commit+push 前先跑这个。

## 0. 已同步到 chatbi-ws 的内容

test-agent 已重建部署产品(`/Users/admin/Downloads/workspace/chatbi`,8 命令)。
chatbi-ws 已有 `.claude/`(sessionB 时用 install.sh 装的),所以 install.sh
的 `.claude` 存在预检会 FAIL。改为手工同步新增 + 修改文件(保留 local config
+ bootstrap 状态):

- **新增**:`.claude/commands/chatbi-build-from-requirement.md`、
  `.claude/skills/chatbi-build/SKILL.md`、
  `.claude/lib/chatbi_harness/build_plan.py`、
  `.claude/schemas/build-plan.schema.json`
- **修改**:`.claude/lib/chatbi_harness/bootstrap.py`(加 read_source_inventory +
  merge_source_inventories)、`.claude/lib/chatbi_harness/__init__.py`(导出)、
  `.claude/skills/chatbi-bootstrap/SKILL.md`(## Layers stub)、
  `.claude/skills/chatbi-maintenance/SKILL.md`(读 § Layers + append_model_registry)、
  `CLAUDE.md`(+1 路由行)
- **保留**:`.claude/chatbi-harness.local.json`(path_bindings / cli_adapters.mysql)、
  `.chatbi/bootstrap/source_inventory.json`
- 蓝图 `docs/org/data-warehouse-blueprint.md` 的 `## Layers` 段填了 v1 默认跨层
  矩阵(之前是空 header)。

import 已验证:`PYTHONPATH=chatbi-ws/.claude/lib python3 -c "from chatbi_harness import build_plan, ..."` -> OK。

## 1. chatbi-ws 现状

已建的模型(sessionB 及之后):
- ODS:`ods_agent_session`、`ods_creator_profile`、`ods_auth_account`
- DWD:`dwd_session_creator_detail`
- DWS:`dws_function_usage_daily`
- semantic(T1):`core_function_adoption_rate`(指标 A)

`model_registry.json` **不存在** - 这些模型是在 registry 功能落地前建的。
首次 `read_model_registry` 返回 `()`(空 registry,不是错误)。

## 2. 启动 sessionC

```
cd /Users/admin/Downloads/workspace/chatbi-ws
export CHATBI_PYTHON=/opt/homebrew/bin/python3   # sessionB 用的那个
claude
```

## 3. `/chatbi-init` - 确认 8 命令就绪

预期:8 命令路由就绪,无 BLOCKED。新路由 `/chatbi-build-from-requirement`
应该出现。

## 4. 复用路径(模型已建,T1 已覆盖)

```
/chatbi-build-from-requirement 验证指标A(核心业务功能使用率)能否回答日维度的功能使用情况
```

预期行为:
- **Step 1**:agent 读蓝图 § Metrics + § Layers + source_inventory +
  model_registry(空 `()`)+ 通过 `select_adapter` 发现 T1 覆盖(找到
  `core_function_adoption_rate`)。
- **Step 2**:推导"T1 已覆盖,无需新建 DWD/DWS,复用现有"。
- **Step 4**:交接 `/chatbi-analyze` -> T1 查询 -> 独立审查(REV-001) ->
  带 provenance footer 的答案。

这条路径**不依赖** model_registry(T1 发现靠 `select_adapter`)。

## 5. 建造路径 - 验证推导 + chaining

### 5.1 需求文本

```
/chatbi-build-from-requirement 分析不同认证方式(auth_type)的创作者在各核心业务功能(scene 0-8)下每天的会话活跃度(会话数 + 活跃创作者数),对比不同认证方式用户的功能使用强度差异。
```

### 5.2 为什么这个需求能验证建造路径

现有模型**覆盖不了**这个需求:
- `dwd_session_creator_detail` 只 join `ods_agent_session` + `ods_creator_profile`,**没接 `ods_auth_account`**,缺 `auth_type` 维度。
- `dws_function_usage_daily` 粒度是 `scene × ds`,**没 `auth_type` 维度**。
- 指标 A/B 都不含认证方式维度。

所以 agent 应推导出要建**新** DWD + 新 DWS(或扩展现有 - agent 自行判断),触发完整建造链。

### 5.3 预期 agent 行为

- **Step 1**:读蓝图 § Metrics(指标 B 使用强度,但缺 auth_type 维度)+ § Layers(v1 跨层矩阵)+ source_inventory(`t_plg_creator_auth_account` 存在)+ 现有模型(dwd/dws 都缺 auth_type)+ `select_adapter`(T1 不覆盖 auth_type 维度)。
- **Step 2 推导**(agent 推理,不写死):
  - 现有 DWD/DWS 不能复用(缺 auth_type)。
  - 建新 DWD:join `ods_agent_session` + `ods_creator_profile` + `ods_auth_account`,带 `auth_type`。
  - 建新 DWS:`auth_type × scene × ds` 聚合 `session_count` + `distinct_active_creators`。
  - ODS 都已建(`ods_auth_account` 在) -> **不扩源**。
  - 跨层:DWS->DWD->ODS,符合 `## Layers` v1 矩阵。
  - 调 `validate_build_plan` + `validate_layer_dependency`(fail-closed)。
- **Step 3**:按依赖序(ODS 已在 -> DWD -> DWS)串 `/chatbi-maintain-model` 逐个建。每个 sync gate + stop_gate pass 后 `append_model_registry` 写 `model_registry.json`。
- **Step 4**:交接 `/chatbi-analyze` 出答案(或建 semantic 指标再交接)。

### 5.4 关注点(反馈给我)

1. **known_models 问题(关键,验证改进 A)**:新 DWD 依赖现有 `ods_agent_session` / `ods_creator_profile` / `ods_auth_account`,但这 3 个模型没进 `model_registry.json`(之前建时没 registry 功能)。`validate_build_plan(known_models=空)` 会把"依赖现有模型"判成 SCOPE-001 跨计划边界 -> `GateError`。
   - **改进 A 修前跑**:预期卡在 Step 2 `validate_build_plan` SCOPE-001。这正好确认问题。
   - **改进 A 修后跑**:Step 1 扫 `models/` 补 `known_models` -> validate 过 -> 走到 Step 3 chaining。
2. **推导逻辑**:join 3 表 + 按 auth_type 聚合是否从需求 + 蓝图 § Metrics 推导(不写死在 prompt)?
3. **跨层校验**:`validate_layer_dependency` 是否过(DWS->DWD->ODS 符合 v1 矩阵)?agent 是否读蓝图 `## Layers` 解析成 `LayerRule`?
4. **串 maintain-model**:join/聚合逻辑传进 change-request;sync gate pass 后是否触发 `append_model_registry`?`model_registry.json` 是否被创建(从空 -> 2 条新模型)?
5. **扩展现有 vs 新建**:agent 是扩展现有 `dwd_session_creator_detail` 加 auth_type,还是新建独立 DWD?两种都合理,看 agent 怎么判断 + 是否说明理由。

## 6. 扩源路径 - 验证 ODS 缺表 + 人批 + 完整 ODS->DWD->DWS->ADS

5.1 的需求 ODS 都已建,不触发扩源。这个 case 要走**扩源**:需求要的源表不在
source_inventory -> agent 标记 extend-source -> STOP 要人批 -> 人批后 bootstrap
增量 introspect 新表 -> 完整 ODS->DWD->DWS->ADS。

### 6.1 demo 准备(为了完整链路)

chatbi-ws 的 source_inventory 是 public 库全量 introspect(125 张),public 库的
表都在。要触发扩源,跑前手动在 public 库加一张"新源表"(模拟生产库新加、还没
纳入 source_inventory),**不重新 bootstrap**:

```sql
-- 在 public 库执行(root@127.0.0.1:3306,db public)
CREATE TABLE t_plg_creator_withdraw_record (
  withdraw_id   BIGINT PRIMARY KEY,
  creator_id    BIGINT NOT NULL,
  amount        DECIMAL(10,2) NOT NULL,
  withdraw_time DATETIME NOT NULL,
  status        VARCHAR(32) NOT NULL  -- e.g. pending/approved/rejected
);
-- 插几行 demo 数据(creator_id 用现有的 15 个)
```

这样 source_inventory(旧,125 张)没有 `t_plg_creator_withdraw_record`,agent 查到
缺表 -> 扩源。

**如果不想改 public 库**:跳过 demo 准备,demo 到扩源 STOP(agent 标记
extend-source + 要人批)即验证门控;完整链路(bootstrap 增量 introspect + 建模型)
需要这表存在。

### 6.2 需求文本

```
/chatbi-build-from-requirement 分析创作者的功能使用强度(会话数)与提现收入的关系:高使用创作者是否对应高提现收入?需要创作者的提现记录数据。
```

### 6.3 为什么这个需求能验证扩源 + 完整链路

- 提现数据不在现有任何 ODS/DWD/DWS/semantic 模型。
- source_inventory(125 张 t_plg_creator_*)没有提现表(除非 6.1 加了)-> ODS 缺表
  -> 扩源。
- 人批 + bootstrap 增量 introspect 后,完整链路:ODS(新源)-> DWD(join 会话+
  创作者+提现)-> DWS(按 scene×ds×withdraw_status 聚合)-> ADS(高使用高提现清单)。

### 6.4 预期 agent 行为

- **Step 1**:`read_source_inventory` 搜"提现/withdraw" -> 没有(除非 6.1 加了)。
  蓝图 § Metrics 无提现。`collect_known_models` = 现有 6(+5.1 建的 2)个模型。
- **Step 2 扩源识别**:推导要建 `ods_withdraw_record`,但 source_inventory 没有对应
  源表 -> 标记 `requires_human_approval=True` extend-source
  (`human_approval.approved=False`)。
- **validate_build_plan**:extend-source 条目(`approved=False`)触发 GateError(Q1
  extend-source 门,SCOPE-001/SEC-001/RAW-003/HOOK-004)。**STOP 要人批**。agent 不
  自行扩源(不能发明源表 RAW-003,不能自扩源边界 SCOPE-001/SEC-001)。
- **人批后**:agent 调 bootstrap 增量 introspect(public 库
  `t_plg_creator_withdraw_record`,用现有 mysql adapter,scoped
  `--execute=SELECT...INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME='t_plg_creator_withdraw_record'`)
  -> `merge_source_inventories`(旧 + 新,碰撞 raise GateError)-> source_inventory
  加这表。
- **Step 2 推导完整计划**(扩源后):
  1. `ods_withdraw_record`(ODS,新源表 1:1 view,ds=DATE(withdraw_time))
  2. `dwd_session_withdraw_detail`(DWD,join `ods_agent_session` +
     `ods_creator_profile` + `ods_withdraw_record` on creator_id,会话+提现明细,
     grain 每会话一行)
  3. `dws_withdraw_usage_daily_by_withdraw`(DWS,按 scene × ds × withdraw_status
     聚合 session_count + withdraw_amount)
  4. `ads_high_usage_high_withdraw`(ADS,高使用高提现创作者清单,应用层汇总)
- **validate_build_plan**(`known_models`=现有 + 新 plan,extend-source 现在已批
  `approved=True`)+ **validate_layer_dependency**(ADS->DWS->DWD->ODS,符合 v1 矩阵)。
- **Step 3**:串 maintain-model 按 ODS->DWD->DWS->ADS 序建。每个 sync gate +
  stop_gate pass 后 `append_model_registry`。
- **Step 4**:交接 analyze(7 字段)。

### 6.5 关注点(反馈给我)

1. **扩源识别**:agent 是否查 source_inventory 没有提现表 -> 标记 extend-source
   (`requires_human_approval=True`)?
2. **Q1 extend-source 门**:`validate_build_plan` 是否因 `approved=False` 触发
   GateError -> STOP 要人批?
3. **人批后 bootstrap 增量**:agent 是否调 `merge_source_inventories`(增量
   introspect 新表)?source_inventory 是否更新(125 -> 126 张)?
4. **完整链路**:ODS -> DWD -> DWS -> ADS 是否都建?跨层校验(ADS->DWS->DWD->ODS)
   是否过?
5. **registry**:是否写 4 条新模型(`ods_withdraw_record` /
   `dwd_session_withdraw_detail` / `dws_withdraw_usage_daily_by_withdraw` /
   `ads_high_usage_high_withdraw`)?注意 stop_gate 需 eval run(EVAL-004),可能没写
   (同 5.1 的 fail-closed)。
6. **推导逻辑**:join/聚合是否从需求 + 蓝图推导(不写死)?

## 7. 扩 ODS 路径 - 建新 ODS + DWD + DWS(不扩源,ODS->DWD->DWS)

5.1 是扩 DWD(column)+ 新 DWS(ODS 都已建)。第 6 章是扩源(source_inventory 没有
源表,你没那表只到 STOP)。这个 case 是**建新 ODS**:需求要的源表在 source_inventory
里(public 库 125 张之一),但**没建 ODS**。agent 推导建新 ODS -> DWD -> DWS。**不扩源**
(source_inventory 有),能完整跑。

### 7.1 需求文本

```
/chatbi-build-from-requirement 分析创作者的功能使用强度(会话数)与额度消耗(credits used)的关系:高使用创作者是否对应高额度消耗?需要额度消耗记录(trade_used_record)。
```

### 7.2 为什么这个需求能验证扩 ODS(建新 ODS + DWD + DWS)

- `t_plg_trade_used_record`(额度消耗)在 source_inventory(125 张之一),但**没建
  ODS**。
- C2 谱系文档(`blaze-plg-creator-table-lineage.md`)描述 trade_used_record 为"消耗
  主记录",`session_id -> agent_session`(作线索,SCOPE-002 候选非权威,不作事实来源)。
- 现有 DWD(`dwd_session_creator_detail`)没接 trade_used_record。
- 所以建新 ODS + 新 DWD + 新 DWS。**不扩源**(source_inventory 有
  t_plg_trade_used_record)。
- 完整链路:ODS(新)-> DWD(新)-> DWS(新)。

### 7.3 预期 agent 行为

- **Step 1**:`read_source_inventory` 查 trade_used_record -> 在(125 张之一)。C2 谱系
  文档作线索(`session_id->agent_session`,SCOPE-002 候选非权威)。`collect_known_models`
  = 现有模型(ods_agent_session 等 + 5.1 建的)。
- **Step 2 推导**:
  - 现有 DWD/DWS 不能复用(没接 trade_used_record)。
  - 建新 ODS:`ods_trade_used_record`(源表 t_plg_trade_used_record,
    ds=DATE(create_time))。
  - 建新 DWD:`dwd_session_trade_detail`(join `ods_agent_session` +
    `ods_trade_used_record` on session_id,会话+额度消耗明细,grain 每会话一行)。
  - 建新 DWS:`dws_trade_usage_daily`(按 scene × ds 聚合 session_count +
    credits_used_amount)。
  - **不扩源**(source_inventory 有 t_plg_trade_used_record,无需 extend-source)。
  - `validate_build_plan`(`known_models`=现有 + 新 plan)。新 DWD 依赖
    `ods_agent_session`(现有,`collect_known_models` 扫到)+ `ods_trade_used_record`(新,
    在 plan)。过。
  - `validate_layer_dependency`:DWD->{ods,dim}(`ods_trade_used_record` 是 ods),
    DWS->{dwd,dim}。过。
- **Step 3**:串 maintain-model 按 ODS->DWD->DWS 序建。每个 sync gate + stop_gate pass
  后 `append_model_registry`。
- **Step 4**:交接 analyze(7 字段)。

### 7.4 关注点(反馈给我)

1. **不扩源识别**:agent 是否识别 `t_plg_trade_used_record` 在 source_inventory(125 张
   之一)-> 建新 ODS,而非扩源?**对比第 6 章扩源**(提现表不在 source_inventory)。
2. **完整链路**:ODS -> DWD -> DWS 是否都建?跨层校验(DWD->{ods,dim},
   DWS->{dwd,dim})是否过?
3. **known_models**:新 DWD 依赖 `ods_agent_session`(现有,`collect_known_models` 扫到)
   + `ods_trade_used_record`(新,在 plan)。`validate_build_plan` 过(不 SCOPE-001)?
4. **C2 谱系作线索**:agent 是否正确引用 C2 文档(`session_id->agent_session`)作候选线索
   (SCOPE-002,非权威),而非当作事实来源?
5. **registry**:是否写 3 条新模型(`ods_trade_used_record` / `dwd_session_trade_detail` /
   `dws_trade_usage_daily`)?注意 stop_gate 需 eval run(EVAL-004),可能没写(同 5.1)。
6. **推导逻辑**:join(session_id)+ 聚合(credits_used_amount)从需求 + 蓝图推导(不写死)?

## 8. 关注点(反馈给我)

1. **agent Step 1 怎么发现现有模型?**
   - 复用路径:靠 `select_adapter` 发现 T1(不依赖 registry)- 应该能跑通。
   - 建造路径:如果推导的 DWD 依赖现有 `ods_agent_session`,而 `known_models`
     (来自空 registry)不含它,`validate_build_plan` 可能抛 SCOPE-001
     `GateError`。**如果卡在这**,反馈给我 - 修复方案是让 SKILL Step 1 也扫
     `models/{ods,dwd,dws,dim,ads}/` 来补 `known_models`(比只读 registry 鲁棒,
     因为 registry 可能滞后于实际模型)。
2. **蓝图 `## Layers` v1 矩阵**:是否被正确读 + 解析成 `LayerRule`?
   (ods->{}, dwd->{ods,dim}, dws->{dwd,dim}, ads->{dws,dim}, dim->{})。
3. **推导逻辑**:join/聚合是否从需求 + 蓝图 § Metrics 推导(不写死)?
4. **交接 analyze**:T1 是否覆盖,能否出带 footer 的答案?
5. **串 maintain-model**(建造路径):sync gate pass 后是否触发
   `append_model_registry`?`model_registry.json` 是否被创建?

## 9. 反馈

把命令输出 / agent 行为 / 报错贴给我。我在这修,重新同步到 chatbi-ws,重测。
live smoke 干净了再 commit+push。
