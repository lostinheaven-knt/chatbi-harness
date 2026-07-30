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

## 6. 关注点(反馈给我)

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

## 7. 反馈

把命令输出 / agent 行为 / 报错贴给我。我在这修,重新同步到 chatbi-ws,重测。
live smoke 干净了再 commit+push。
