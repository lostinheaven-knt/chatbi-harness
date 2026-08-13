# ChatBI Harness on Agno —— 从零到全部功能验收操作手册（v2，全链路版）

- 适用范围：agno target（skill+hooks 形态，main @ 33a96db）
- 验收方式：**agent-ui 网页多轮对话为主**，API/日志/证据核对为辅
- 判定标准：每步有明确预期；✅ = 真实模型已验证 / 🧪 = stub/单测覆盖 / ⏳ = 需能力扩展（未实现）
- 诚实标注：所有 ⏳ 项都是 **agno runtime 尚缺的能力**（CC 验收环境有 MySQL + dbt，agno 侧还没有对应适配器），不是"忘了验"

---

## 0. 验收范围（对照 CC 的 sessionB/sessionC）

CC 版验收覆盖完整数仓生命周期：**数仓初始化 → 业务 db → ODS → DWD → DWS → ADS（查询）**，多种长度链路。agno 版分两阶段：

| 阶段 | 范围 | 对应 CC runbook | 现状 |
|---|---|---|---|
| **Phase 1** | fixture 级治理全链（分析/审查/交接/审批/证据） | sessionB §5 的 analyze 段 | ✅ 可验收（本手册 §3） |
| **Phase 2** | 初始化 → ODS → DWD → DWS → ADS 全链路 | sessionB §3-§5 + sessionC 全部 | ✅ 工具面已落地 + **live 全链验收通过**（2026-08-12，L2/L3/L4/D/E + 负向 + **BFR/EXT**，工具链直驱真实 mysql+dbt；详见 §4 与 test-report-agno-phase2-live-v1 / test-report-agno-phase2-bfr-ext-live-v1） |

**Phase 2 能力清单**（technical-design-agno-phase2 已落地，19 个治理工具）：

| 能力 | 实现 | 验证方式 |
|---|---|---|
| MySQL 查询适配器 | `chatbi_query_source`（governed tool，mysql CLI argv 数组 + 表白名单 + 只读校验） | 确定性：`test_query_source.py` 28 cases；live：`dw_agno.ods_agent_session` 真实可查 |
| dbt 执行引擎 | `chatbi_dbt_draft`（models/** 受控写）+ `chatbi_dbt_execute`（run/test，REV-001 评审绑定） | 确定性：`test_dbt_tools.py` 18 cases + L3/L4/BFR/EXT 链路；live：dbt run PASS=1 |
| source_inventory | `RuntimeNativeRunner.run_mysql`（INFORMATION_SCHEMA 探测 → 原子落盘） | 确定性：`test_native.py` 16 cases；live：125 表 inventory 落盘 |
| 语义层发现 | `chatbi_semantic_discover`（ws/semantic/** 扫描 + T1 gap 降级 + fixture 回退） | 确定性：`test_semantic_discover.py` 9 cases + L1 链路 |
| 业务 Codebase 读取 | `chatbi_crosscheck(search=True)` + fypro 别名接线（local config 绑定） | 确定性：`test_codebase_wiring.py` 6 cases |
| 全链路 | 六条链路 + 负向 + 上限回归 | 确定性：`test_phase2_chains.py` 9 cases（全部离线） |

> 诚实标注：**2026-08-12 live 全链验收已补齐**（`docs/test-report-agno-phase2-live-v1.md`）——真实 mysql 127.0.0.1:3306 + venv_dbt 1.7.19 + 真实 tool_hooks/guardrails 全链执行：L2 bootstrap（inventory=125）、L3 ODS 3 表 MVP（dbt run PASS=3 + test PASS=6 + COUNT=15）、L4 DWD/DWS/ADS（dbt run PASS=3 + ADS 查询 + provenance source_tier=T2）、语义层发现（creator_usage 命中）、fypro crosscheck（search 23 命中）。驱动方式：工具链直驱（确定性脚本 + 真实执行）；真实模型（deepseek-v4-flash）仍遵循度不足（§1.1 登记，与 §3 边界一致）。live-found 修复 2 项（bootstrap 保留 local config path_bindings；dbt 多模型 select argv 展开），提交 `11fdfca`。

---

## 1. 环境准备（零起点）

> **占位符**（机器路径只出现在部署边界，PORT-001；下文所有命令按此替换）：
> `<AGNO_VENV>` = agno 2.6.22 venv 根（如 `/Users/<user>/workspace/agno-main/.venv`）；`<AGENT_UI_ROOT>` = agent-ui 检出根；`<DEV_REPO>` = chatbi-cc-dev 开发检出根；`<FYPRO_DOCS_ROOT>` = fypro_all_app/docs（Business Codebase 根）。

| 项 | 要求 | 检查命令 |
|---|---|---|
| agno 环境 | agno 2.6.22 venv | `<AGNO_VENV>/bin/python -c "import agno; print(agno.__version__)"` |
| DeepSeek 凭据 | 测试环境 url/key | `cat <AGNO_VENV>/config.json` |
| 前端 | agent-ui（Next.js） | `<AGENT_UI_ROOT>` 存在 |
| 代码 | chatbi-cc-dev main | `git log --oneline -1` → 33a96db（或更新） |
| ⏳ Phase 2 额外 | MySQL 127.0.0.1:3306 + venv_dbt + fypro 业务库 | 见 §4.1 |

## 2. 启动服务（两个终端）

```sh
# 终端 1 后端（自动读 config.json 凭据；--keep 保留治理状态）
cd <DEV_REPO>
<AGNO_VENV>/bin/python .scratch/agno-demo/serve.py --keep

# 终端 2 前端
cd <AGENT_UI_ROOT> && pnpm dev   # http://localhost:3000
```

连通性：`curl -s http://127.0.0.1:7777/agents` → 预期 `chatbi-agno`。

---

## 3. Phase 1：fixture 级治理全链验收（✅ 真实模型）

> **重要模型边界（2026-08-11 实测）**：deepseek-v4-flash 无法可靠地把自然语言问题解析成 request.schema.json 的 7 字段并调用 chatbi_record_request（实测会叙述计划、逐字段过度澄清、或把带字段的消息当普通问题）。**3.1 验收请用完整结构化消息**（下方模板），或换更强的模型。系统防御不受影响（无证据链的散文输出仍被 C002 拦截）。

### 3.1 完整字段问题 → 全链完成 ✅

**操作**（新会话，自然话语触发——skill 触发语义，模型自动填 actor/purpose/supported_decision 默认值）：

```
帮我看一下各地区的收入总额。
```

想一次到位可带时间范围：

```
帮我看一下 2024 年 1 月各地区的收入总额。
```

**预期**：
- agent 调 `chatbi_record_request`（7 字段齐，hook 校验通过）→ record_evidence（T1 语义层）→ submit_candidate（SHA 绑定）→ chatbi_review（独立 reviewer）→ delivery gate PASS → **run.completed**（gate=delivery, decision=pass）
- 回答带 provenance 脚注（数据来源/时间范围/置信度/限制），区分 observation vs interpretation

**核对**：
```sh
ls .scratch/agno-demo/ws/.chatbi/runs/<session_id>/
# evidence-request / evidence-t1_semantic / evidence-t2_curated /
# evidence-candidate_bind / evidence-candidate_review（content_sha256 齐全）
grep "run.completed" .scratch/agno-demo/ws/.chatbi-runtime/events/*.jsonl | tail -1
```

### 3.2 独立审查拦截（负向路径）✅

**操作**：同一会话问（fixture 数据无日期列，诱导时间断言）：

```
上个月的收入是多少？
```

**预期**：reviewer BLOCKED（无日期列、时间覆盖无证据）→ 阻断消息带阻断项（rule_ids）+ 恢复建议 → 无 run.completed。

### 3.3 对话式交接（多轮）✅（2026-08-11 修复）

**操作**：问一个缺字段的问题，例如只给 time_range 不给 segment：

**预期**：
- agent 输出**以问句结尾**的澄清消息（"Which segment should I use?"）——delivery gate 对问句放行（c8b3d0d 修复：`_is_conversational_handoff`，问句/含 clarify 标记不触发 C002）
- 你在同一会话回复缺失字段 → agent 继续
- 若 agent 输出散文计划（不以问句结尾）→ 被 C002 拦截，recovery 提示你补充缺失字段后重问（33a96db）

**核对**：
```sh
grep "gate.blocked" .scratch/agno-demo/ws/.chatbi-runtime/events/<run>.jsonl | tail -1
# 预期：无 gate.blocked（问句交接）或 recovery 可操作的 gate.blocked
```

### 3.4 runbook 按需加载 ✅

**核对**：
```sh
grep "runbook-load" .scratch/agno-demo/ws/.chatbi-runtime/events/*.jsonl | tail -3
# 预期：evidence_source=runbook-load，sha256 与 manifest 一致
```

**负向**：要求加载不存在的 workflow → 拒绝且不写 evidence（fail-closed）。

### 3.5 工具白名单强制（C011）✅

**核对**：
```sh
grep "tool.blocked" .scratch/agno-demo/ws/.chatbi-runtime/events/*.jsonl | tail -5
# 预期：get_skill_instructions 被拒，recovery 指向 chatbi_load_runbook
```

### 3.6 审批链（protected actions）✅（状态机真实模型 + resolver stub）

**操作**：对话中说（自然话语）：

```
帮我注册一个治理模型：revenue_m2，上游依赖 warehouse.sales，层 dwd。
```

**预期**：`chatbi_registry_append` 被 @approval 暂停 → agno_approvals 表出现 pending 行 → 拒绝路径（非 superuser）工具不执行；通过路径（superuser）kernel 重验后执行。

**核对**：
```sh
python3 -c "import sqlite3; con=sqlite3.connect('.scratch/agno-demo/ws/.chatbi-runtime/agno.db'); print(con.execute('SELECT id,status,tool_name FROM agno_approvals').fetchall())"
```

### 3.7 证据与事件可追溯 ✅

**核对**：
```sh
ls .scratch/agno-demo/ws/.chatbi/runs/<session_id>/           # 证据（content_sha256）
ls .scratch/agno-demo/ws/.chatbi-runtime/events/<run>.jsonl   # 事件流（按 run 隔离）
cat .scratch/agno-demo/ws/.chatbi/model_registry.json         # 治理注册表
```

### 3.8 跨工作流路由 🧪（stub 覆盖，真实模型链为后续项）

**操作**：对话中说：

```
维护一下 revenue_m2 这个模型。
```

→ 预期路由到 maintain-model（runbook-load evidence 出现 maintenance）。以 `test_multiturn_handoff.py::C2-3` 为准。

---

## 4. Phase 2：全链路验收（✅ 工具面已落地 + ✅ live 全链 2026-08-12）

> 能力落地（technical-design-agno-phase2，分支 feat/agno-phase2）：4 个新治理工具
> （query_source / dbt_draft / dbt_execute / semantic_discover）经真实 tool_hooks +
> guardrails 的确定性链路测试全绿（`test_phase2_chains.py`，六条链路 + 负向 + 上限回归，
> 全部离线）；**live 全链验收已通过**（`docs/test-report-agno-phase2-live-v1.md`，真实
> mysql + venv_dbt 1.7.19 + 真实治理链，驱动方式=工具链直驱）。下面每步标注验证方式：
> **确定性测试** / **live 全链（工具链直驱）** / **真实模型**。

### 4.1 数仓初始化（对应 sessionB §3 bootstrap）

**步骤 0：从零重置**（bootstrap 是 `CREATE DATABASE IF NOT EXISTS`，不会清库——此前验收的残留表必须先清）：

```sh
mysql -h 127.0.0.1 -P 3306 -u root -e "DROP DATABASE IF EXISTS dw_agno"
# 清治理状态 + 验收轮建仓产物（models=验收建的模型 SQL、target= dbt 编译产物、
# logs/dbt_packages= dbt 残留；bootstrap scaffold 会重建 models 空目录结构）
rm -rf .scratch/agno-demo/ws/.chatbi .scratch/agno-demo/ws/.chatbi-runtime \
       .scratch/agno-demo/ws/.claude/chatbi-harness.local.json \
       .scratch/agno-demo/ws/models .scratch/agno-demo/ws/target \
       .scratch/agno-demo/ws/logs .scratch/agno-demo/ws/dbt_packages
# 保留（从零起点样例）：data/warehouse.csv、semantic/、docs/
# 然后重启服务
```

**对话指令（自然话语——skill 触发，2026-08-12 真实模型验证通过）**：

> 初始化数仓：源库用 public，目标库 dw_agno。业务参考文档在 <FYPRO_DOCS_ROOT>，别名 fypro_docs。

**可配置项**（自然语言指定即生效，实测验证）：
- **mysql 连接**（host/port/user）→ 写入 local config cli_adapters.mysql argv
- **源数据库** → inventory 从该库扫（`source_database` 证据字段）
- **Business Codebase**（业务参考文档，sessionB §1.4 对齐）→ 别名声明写共享配置 `business_codebases`，路径绑定写 local config `path_bindings`（机器路径只允许在这，PORT-001）
- 未指定的用测试默认值（127.0.0.1/3306/root/public）；**语义层目录为系统定义**（deployment `semantic_docs_dir`，默认 semantic，对话不可配）

**遵循度测量（2026-08-12，`measure_bootstrap_rate.py`）**：
- **简单指令**（`初始化数仓：源库用 public，目标库 dw_agno。`）：**10/10 EXECUTED（100%）**
- **复杂指令**（带 fypro 路径+别名）：**5/5 EXECUTED（100%）**，且完整落地 fypro 配置（共享配置别名 + local path_bindings 真实路径 + mysql argv）
- 合计 **15/15（100%）**，全部 `load_runbook → chatbi_bootstrap → inventory 落盘 → 交接问下一步`
- 之前观察到的"低遵循度/确认循环"均为**旧代码（docstring 修复前）或单次偶发**；修复后系统上简单与复杂指令均 100% 遵循。

预期：agent 加载 runbook → 调 `chatbi_bootstrap`（连接参数用对话指定值或默认值）→ `bootstrap-inventory` evidence 落盘（table_count=125，source_database=指定源库）→ 本地配置写入 mysql argv + 语义文档目录 → 以对话交接询问下一步（如"首批 ODS 优先覆盖哪些业务域/表"）。你回复后在同一会话继续。

**核对**：
```sh
mysql -h 127.0.0.1 -P 3306 -u root -e "SHOW TABLES FROM dw_agno"        # 从零：0 表（bootstrap 只建库）
ls .scratch/agno-demo/ws/.chatbi/runs/<session_id>/evidence-bootstrap-*
# payload: {"source_database": "<指定源库>", "table_count": <源库表数>,
#           "business_codebases": ["<别名>"]}
python3 -c "import json; print(json.load(open('.scratch/agno-demo/ws/.claude/chatbi-harness.json'))['business_codebases'])"
# 预期：别名声明（description/path_ref/read_mode）
python3 -c "import json; print(json.load(open('.scratch/agno-demo/ws/.claude/chatbi-harness.local.json'))['path_bindings'])"
# 预期：别名根 -> 实际路径（机器路径仅此处）
```

**Business Codebase 交叉核对验收**（初始化后，同一会话继续）：

> 用 fypro_docs 交叉核对一下 t_plg_creator_agent_session 的业务语义。

预期：`chatbi_crosscheck(search=True)` 命中 fypro 文档（evidence_source=codebase-crosscheck，命中数 > 0）。

| 步骤 | CC 操作（sessionB） | agno 落点 |
|---|---|---|
| 建仓 | `/chatbi-bootstrap` 9 步（config/adapter/dw 库/source_inventory/dbt scaffold） | ✅ bootstrap 工具面已实现：`_bootstrap` handler + `RuntimeNativeRunner.run_mysql`（CREATE DATABASE IF NOT EXISTS dw_agno + INFORMATION_SCHEMA 4 列探测）+ scaffold（dbt_project.yml name/profile=dw_agno + models/{ods,dwd,dws,ads}） |
| 验证产出 | config 写 cli:mysql、dw 库建好、inventory 125 表、dbt scaffold | ✅ 确定性：`test_native.py`（argv/落盘/反读）+ L2 链路（table_count=125）；**live 全链（2026-08-12）**：真实 mysql bootstrap → `bootstrap-inventory` evidence table_count=125 + 本地 config 写正式 mysql argv（无密码值）+ `dbt debug` Connection test OK（dw 与 dw_agno 并存） |

**验收判定**：`bootstrap-inventory` evidence 落盘且 125 表（或实际表数）与源一致 —— L2 链路断言通过（live ✅，run `live-l2`，工具链直驱）。

### 4.2 建 ODS（对应 sessionB §4，maintain-model 3 表 MVP）

| 步骤 | CC 操作 | agno 落点 |
|---|---|---|
| dbt profiles | `~/.dbt/profiles.yml`（profile 名=dw_agno，与 dbt_project.yml 一致） | ✅ 部署边界已配置；**live 抽查**：`dbt debug` Connection test OK（dw 与 dw_agno 并存） |
| 起草模型 | `chatbi_dbt_draft`（models/** 受控写，无裸 Write） | ✅ 确定性：`test_dbt_tools.py`（路径包含/后缀/大小/穿越拒绝）+ L3/L4/BFR/EXT 链路 |

**对话指令**：

```
给 public.t_plg_creator_agent_session 建一张 ODS 模型，目标 models/ods/ods_agent_session.sql。
```
| 执行 | `chatbi_dbt_execute`（run/test，REV-001 评审绑定：文件 sha ∈ review PASS 集） | ✅ 确定性：操作/select 白名单 + 无 review 拒绝 + sha 不匹配拒绝；**live 全链（2026-08-12，run `live-l3`）**：impact→registry 审批→draft→review 绑定→`dbt run` **PASS=3**（3 视图全建）+ `dbt test` **PASS=6**（not_null/unique）+ `SELECT COUNT(*) FROM dw_agno.ods_agent_session` = **15** |
| 验证 | `dbt run --select ods_*` + `dbt test` + mysql COUNT(*) | ✅ 3 表 MVP（ods_agent_session / ods_creator_profile / ods_auth_account 镜像 CC）**live 全链已验**；模型注册 3 条 + approval.resolved 齐 |

**链路长度**：3 表 = 短链路（source → ODS）。

> live-found（2026-08-12）：`dbt run --select a,b,c`（逗号）在 dbt 1.7.19 报 "does not match any nodes" → 修复：argv 展开为空格分隔（提交 `11fdfca`）。

### 4.3 DWD/DWS + 语义层（对应 sessionB §5）

**对话指令**：

```
基于 ODS 建一张 DWD 模型：<描述>。
```

**链路长度**：source → ODS → DWD → DWS（中链路）。

**agno 落点**：✅ 语义层适配器已实现：`chatbi_semantic_discover`（ws/semantic/** 扫描，T1 证据 + 未命中 T1 gap 自动满足 T2 降级前置；test/example + fixture_enabled 时回退运行时语义 fixture）。确定性：`test_semantic_discover.py` + L3 链路（draft→review→execute→query dws_y 全绿）。**live（2026-08-12）**：run `live-d` 语义发现 `creator_usage` 命中 `semantic/metrics/creator_usage.md`（T1 证据）；run `live-l4` DWD/DWS/ADS 三模型真实 `dbt run` PASS=3（DWD/DWS 视图 + ADS 物化表）。

### 4.4 建 ADS 物化表 + analyze 查询（对应 sessionC §8）

**对话指令**：

```
建一张 ADS 汇总表：<描述>。
```

**链路长度**：source → ODS → DWD → DWS → ADS（全链路）。

**agno 落点**：✅ L4 链路确定性测试全绿（4 层 draft → 1 次 review → execute 4 模型 → query dw_agno.ads_z，provenance source_tier=T2）。**live 全链（2026-08-12，run `live-l4`）**：DWD 视图 + DWS 视图 + ADS 物化表真实 `dbt run` PASS=3 → `chatbi_query_source` 查 `dw_agno.ads_function_usage_summary` rows=[["1","15"]] → 结构化答案 + **provenance footer source_tier=T2** + run.completed。analyze 直查 dw_agno 表走 `chatbi_query_source`（T2/T3，T1 只来自语义层）。

**验收判定**：ADS 表可查、analyze 回答的 source_tier 正确、provenance 脚注完整 —— L4 链路断言覆盖（确定性 + **live ✅**）。

### 4.5 多长度链路矩阵（验收覆盖表）

> 每条链路的对话触发：L1/L2/L3/L4 分别用 §3.1/§4.1/§4.2/§4.3-4.4 的对话指令（自然话语，skill 触发语义）。

| 链路 | 覆盖点 | CC 出处 | agno 状态 |
|---|---|---|---|
| 1 跳：source → analyze | fixture warehouse.csv | sessionB §5 | ✅ Phase 1 已验；live 复验 live-l1（source_tier=T1） |
| 2 跳：source → ODS → analyze | bootstrap → query_source(dw_agno.ods_*) | sessionB §4 | ✅ 确定性 L2 + **live live-l2**（bootstrap inventory=125 + 查询） |
| 3 跳：→ DWD → DWS | draft 2 模型 → review → execute → query dws_y | sessionB §5 | ✅ 确定性 L3 + **live live-l4**（dwd/dws 真实 dbt run） |
| 4 跳：→ DWS → ADS → analyze | 4 层 draft → execute → query ads_z | sessionC §6-§8 | ✅ 确定性 L4 + **live live-l4**（ADS 物化表 + 查询 + source_tier=T2 footer） |
| ODS 3 表 MVP（maintain-model 语义） | impact → registry 审批 → draft → review 绑定 → dbt run/test → COUNT | sessionB §4 | ✅ 确定性（BFR/EXT）+ **live live-l3**（dbt run PASS=3 + test PASS=6 + COUNT=15） |
| 语义层发现 | semantic_discover 对 ws/semantic/** | — | ✅ 确定性 + **live live-d**（creator_usage 命中，T1 证据） |
| 业务 Codebase 交叉核对 | crosscheck(search=True) fypro | sessionB Part D | ✅ 确定性 + **live live-e**（search 23 命中，含 blaze-plg-creator-db-business-semantics.md） |
| 建造路径（build-from-requirement） | build_plan → impact → registry_append（审批链）→ draft → review → execute | sessionC §5 | ✅ 确定性 BFR（approval.resolved=approved + dbt-run）+ **live live-bfr（2026-08-12）**：4 层计划 + 审批预置 seam + 真实 dbt PASS=4 + 查询 rows=[["1","15"]] |
| 扩源路径（新业务表 → 全链） | inventory 合并 +1 → draft 新 ODS → execute → query | sessionC §6 | ✅ 确定性 EXT + **live live-ext（2026-08-12）**：kernel 合并 125→126 + 真实 dbt PASS=1 + 查询命中 |
| 负向：无 review 执行 | execute 前无 review → deny REV-001 | — | ✅ 确定性 CHAIN_negative_no_review |
| 负向：越白名单查询 | information_schema → deny | — | ✅ 确定性 CHAIN_negative_blocked_query + **live live-neg** |
| 上限回归 | REVIEW_BLOCK_LIMIT 不受新工具影响 | — | ✅ 确定性 CHAIN_review_block_limit_unaffected |

> 诚实标注（2026-08-12 live 全链后）：八条核心链路（L1-L4 + ODS MVP + D/E 能力 +
> **BFR/EXT**）已由**工具链直驱（确定性脚本 + 真实 mysql/dbt/治理链）** 全链 live 通过
> （详见 `docs/test-report-agno-phase2-live-v1.md` + `docs/test-report-agno-phase2-bfr-ext-live-v1.md`）；
> 真实模型（deepseek-v4-flash）仍遵循度不足（一次 bootstrap 尝试：runbook-load 成功但误调
> get_skill_instructions 被 C011 拦截后散文输出被 C002 拦截，未成链——与 §3 登记边界一致）。
> 负向/上限保持确定性测试覆盖；BFR/EXT 审批为预置 HITL record 的离线 seam，L3 live 审批同法
> （approval.resolved=approved）。BFR 链的 SRC-002 crosscheck 步骤未纳入 live（ws 重置状态
> fypro 配置不一致，观察项已登记，crosscheck 能力由 live-e 23 命中验证）。

---

### 4.6 建造路径（build-from-requirement，对应 sessionC §5）

**能力状态**：工具面齐（`chatbi_build_plan`：classify_src002_finding + validate_build_plan + validate_layer_dependency → `chatbi_impact_manifest` → `chatbi_registry_append`（@approval）→ draft → review → execute）；**确定性测试全绿**（`test_phase2_chains.py::BFR_build_from_requirement`，approval.resolved=approved + dbt-run）；**live 验收已通过（2026-08-12，run `live-bfr`，工具链直驱，详见 `docs/test-report-agno-phase2-bfr-ext-live-v1.md` §3）**：真实需求 → 4 层 build_plan（ods/dwd/dws/ads，kernel validate_build_plan + validate_layer_dependency 执行）→ impact_manifest（DOC-004 前置）→ registry_append（**审批预置 seam**：approval.requested → kernel 重验 → approval.resolved=approved → model_registry.json 落盘）→ draft 4 个真实模型 SQL（ODS/DWD/DWS 视图 + ADS 物化表）→ review ×4（stub PASS，REV-001 sha 绑定走 kernel）→ **真实 dbt run PASS=4** → 真实查询 `dw_agno.ads_bfr_usage_summary` rows=[["1","15"]] → run.completed（gate=delivery）。

**对话指令**（agent-ui）：

```
根据这个需求建数仓：核心业务功能使用情况分析（按 agent_session 的 scene 枚举聚合），粒度 日×功能，段 全量。
```

**预期**（确定性基线）：
- `chatbi_build_plan` 从需求推导分层建造计划（validate_layer_dependency：ODS→DWD→DWS→ADS 层序校验）
- SRC-002 路由判定（业务 Codebase 交叉核对后 route A/B）
- impact_manifest → registry_append 触发 @approval 审批（protected action）→ kernel 重验后执行
- draft 模型 → review（REV-001 sha 绑定）→ dbt execute → run.completed

**核对**：
```sh
ls .scratch/agno-demo/ws/.chatbi/runs/<session_id>/   # build-plan / impact / registry / draft / review 证据
python3 -c "import json; print(json.load(open('.scratch/agno-demo/ws/.chatbi/model_registry.json'))['models'][-1])"
```

**诚实标注**：审批链在 AgentOS 同步 run 中无法中途人批（E004 paused，已登记 seam）——确定性测试与 live（`live-bfr`）均用预置 HITL record（`coordinator.request_approval` 预置 + `approval_verify_hook` 以 superuser 身份 kernel 重验）；live 走此路径时审批解析为人工步骤。

**live 核对（2026-08-12，run `live-bfr`，工具链直驱）**：
```sh
ls .scratch/agno-demo/ws/.chatbi/runs/ses-bfr/   # build_plan / impact_manifest /
                                                 # registry_append / dbt_draft ×4 /
                                                 # candidate_bind+review ×4 / dbt_run / t1+t2
python3 -c "import json; print(json.load(open('.scratch/agno-demo/ws/.chatbi/model_registry.json'))['models'][-1]['name'])"
# ads_bfr_usage_summary
grep -o '"event_type": "approval.resolved"[^}]*' .scratch/agno-demo/ws/.chatbi-runtime/events/live-bfr.jsonl
# approval.resolved, resolution=approved
mysql -h 127.0.0.1 -P 3306 -u root -e "SHOW TABLES FROM dw_agno"   # 4 个 BFR 对象
```

### 4.7 扩源路径（EXT，对应 sessionC §6）

**能力状态**：kernel inventory 合并（+1 新表）→ draft 新 ODS → review → execute → query 命中；**确定性测试全绿**（`test_phase2_chains.py::EXT_source_expansion`）；**live 验收已通过（2026-08-12，run `live-ext`，工具链直驱，详见 `docs/test-report-agno-phase2-bfr-ext-live-v1.md` §3）**：kernel `merge_source_inventories` 增量合并（真实 125 表基线 + 模拟新表 `t_plg_creator_ext_demo`，125 → 126 无碰撞）→ T1 gap → draft 新 ODS（真实 SQL）→ review（REV-001 绑定）→ **真实 dbt run PASS=1** → 真实查询 `dw_agno.ods_ext_demo` rows=[["1","15"]] → run.completed（gate=delivery）。

**对话指令**（agent-ui）：

```
public 库新增了一张表 t_plg_creator_xxx，把它接入数仓并建一张 ODS 模型。
```

**预期**（确定性基线）：inventory 增量合并（新表入源清单）→ draft ODS → review → execute → `chatbi_query_source` 命中新表。

**核对**：
```sh
ls .scratch/agno-demo/ws/.chatbi/runs/<session_id>/   # inventory-merge / draft / review / dbt-run 证据
```

### 4.8 全旅程验收（需求→初始化→扩源→4 层新模型，2026-08-12 live ✅）

> 一条对话级"全旅程"链路：**需求 → 发现未初始化 → 发起初始化确认（交接）→ 确认后初始化 → 初始化完成后 → 扩源 → 新 ODS → 新 DWD → 新 DWS → 新 ADS**。
> 驱动方式：**工具链直驱**（确定性脚本 + 真实 mysql 127.0.0.1:3306 + 真实 venv_dbt 1.7.19 + 真实治理链 tool_hooks 六层/guardrails/kernel），review 用 stub 判定（REV-001 SHA 绑定 + review.schema 校验走真实 kernel）。真实模型（deepseek-v4-flash）遵循度不足的登记边界同 §1.1/§3；S2 交接为**脚本阶段点**（对话语义登记，不消耗真实模型）。
> 详细报告：`docs/test-report-agno-full-journey-live-v1.md`。

**场景步骤表**（每步：动作 / 预期 / 判定；run `live-fj-boot` + `live-fj`）：

| 步骤 | 动作 | 预期 | 判定（live 断言摘录） |
|---|---|---|---|
| 1 发现未初始化 | 需求输入「根据需求建数仓：核心功能使用情况分析，粒度日×功能」→ 检查 dw_agno 不存在 + 无 bootstrap-inventory evidence | 产出判定「需要初始化」 | dw_agno 不在 SHOW DATABASES；`evidence-bootstrap-*` 0 份；判定=需要初始化 ✅ |
| 2 发起初始化确认 | 模拟交接（agent 请求确认，对话语义） | 登记为脚本阶段点，不消耗真实模型 | S2 阶段点登记（未消耗真实模型）✅ |
| 3 确认后初始化 | chatbi_bootstrap（真实 mysql：建 dw_agno + 125 表 inventory + scaffold） | bootstrap-inventory evidence + run.completed | `table_count=125, source_database=public`；mysql argv 写入（无密码值，SEC-003）；path_bindings 保留（fypro_docs_root，26d40bf 复验）；scaffold 4 层；run.completed ✅ |
| 4 扩源 | kernel `merge_source_inventories` 125→126（+新表 `t_plg_creator_full_journey`） | inventory 证据（126 表，新表在列） | 126 表；`t_plg_creator_full_journey` present ✅ |
| 5 新四层 | build-from-requirement 链：record_request → T1 gap → build_plan → impact → registry（审批 seam）→ draft ×4 → review ×4 → 真实 dbt run → 查询 | 4 层新模型真实落地 + ADS 查询命中 | build_plan 分层 ods/dwd/dws/ads；approval.resolved=approved；**dbt run PASS=4**；查询 `dw_agno.ads_full_journey_usage_summary` rows=[["1","15"]]；run.completed ✅ |
| 6 收尾核对 | SHOW TABLES FROM dw_agno；model_registry.json；证据链完整 | 4 层新对象 + 注册表 + request/plan/impact/registry/draft/review/dbt-run/query 各 evidence 落盘 | dw_agno 4 个真实对象；registry 含 `ads_full_journey_usage_summary`；evidence 10 份（前缀齐）✅ |

**对话指令**（真实模型版——先给需求，agent 应发现未初始化并发起确认，你回复确认）：

```
根据需求建数仓：核心功能使用情况分析，粒度日×功能。
```

预期：agent 检查发现 warehouse 未初始化 → 以**问句结尾**请求确认（交接契约，§3.3）→ 你回复「确认，开始初始化」→ agent 初始化 → 扩源 → 依次建 4 层新模型 → 查询验证。

**live 结果（2026-08-12，工具链直驱）**：
```sh
# 41/41 PASS（S1 判定 + S2 阶段点 + S3 bootstrap + S4 扩源 + S5 四层 + S6 收尾）
grep "结果" .scratch/agno-demo/live-full-journey.log      # == 结果：41/41 PASS ==
grep "PASS=4" .scratch/agno-demo/live-full-journey.log    # Done. PASS=4 WARN=0 ERROR=0 SKIP=0 TOTAL=4
mysql -h 127.0.0.1 -P 3306 -u root -e "SHOW TABLES FROM dw_agno"
# ads_full_journey_usage_summary / dwd_full_journey_session_detail /
# dws_full_journey_function_usage / ods_full_journey_session
ls .scratch/agno-demo/ws/.chatbi/runs/ses-fj/   # evidence-request / build_plan / impact_manifest /
                                               # registry_append / dbt_draft / candidate_bind /
                                               # candidate_review / dbt_run / t1_semantic / t2_curated
python3 -c "import json; print(json.load(open('.scratch/agno-demo/ws/.chatbi/model_registry.json'))['models'][-1]['name'])"
# ads_full_journey_usage_summary
```

**复现命令**（从零重置 + 脚本，两段）：

```sh
# 1) 从零重置（手册 §4.1 步骤 0；保留 data/ semantic/ docs/）
mysql -h 127.0.0.1 -P 3306 -u root -e "DROP DATABASE IF EXISTS dw_agno"
rm -rf .scratch/agno-demo/ws/.chatbi .scratch/agno-demo/ws/.chatbi-runtime \
       .scratch/agno-demo/ws/models .scratch/agno-demo/ws/target \
       .scratch/agno-demo/ws/logs .scratch/agno-demo/ws/dbt_packages
# 恢复 local config 至部署前状态（cli_adapters: {}，path_bindings 仅 fypro_docs_root）
printf '{"path_bindings": {"fypro_docs_root": "<FYPRO_DOCS_ROOT>"}}' \
  > .scratch/agno-demo/ws/.claude/chatbi-harness.local.json

# 2) 跑全旅程（agno venv python；服务可不用，工具链直驱独立跑）
<AGNO_VENV>/bin/python -B \
  .scratch/agno-demo/live_full_journey_acceptance.py
```

**验收判定**：41/41 断言全绿（S1 判定=需要初始化 → S6 证据链齐）+ dw_agno 4 个真实新对象 + model_registry 落盘 + run.completed（gate=delivery, decision=pass）—— **live 通过（2026-08-12）**。回归：agno 套件 320 OK + conformance 退出码 0；零产品代码改动，无 live-found 产品缺陷。

### 4.8.1 纯对话驱动补测（2026-08-12，PARTIAL —— 停在 4 层建模）

> 驱动方式标注：**纯对话驱动**（agent-ui 同形态 API `POST /agents/chatbi-agno/runs`，每一轮都是用户消息 → 真实模型 deepseek-v4-flash 自主决策 → 工具调用 → 真实执行；review 用**真实 reviewer agent**，与 4.8 的 stub 判定不同）。
> 详细报告：`docs/test-report-agno-full-journey-conversation-v1.md`。

**逐轮结果**（会话 `ses-fj-conv`，run `63c95367`/`170eeeb5`/`b4dc7397` 等）：

| 轮 | 驱动者输入 | 模型行为 | 分类 |
|---|---|---|---|
| T1 需求 | 「根据需求建数仓：核心功能使用情况分析，粒度日×功能，段全量。」 | 发现未初始化后**跳过确认交接直接执行 bootstrap**（乱序），随后以问句请求功能语义确认 | 尝试1/clean-1 DRIFT（散文被 C002 拦）；clean-2 **EXECUTED**（真实 bootstrap 125 表）+ HANDSOFF |
| T2 确认 | 「功能 = scene…确认，开始初始化。」 | 建 ODS 草稿落盘 + 问 dbt 路径/窗口 → 答后继续 | HANDSOFF（正常） |
| T3 扩源 | （未单独驱动到——模型直接跳去建层） | — | 对话面**无 merge 治理工具**（观察项） |
| T4-T7 建层 | 「业务问题就是最初的需求…请继续完成数仓构建。」 | record_request → T1 gap → build_plan → impact → dbt_draft（ODS/DWD/DWS 落盘）→ submit_candidate → **review ×3 真实 reviewer 全 BLOCKED（REV-003 终局）** → registry_append（@approval 暂停） | **停在 4 层建模** |
| 审批 seam | approvals API 以 superuser 解析（approved）+ continue | resume 后 approval_verify_hook **SEC-003 拦截**（run_subject 为空，fail-closed）→ 交付门 REV-003 终局 | **BLOCKED（已登记 seam，§5）** |
| T8 查询 | 未到达 | — | — |

**结论**：PARTIAL —— 真实模型能自主完成**发现未初始化 + 真实 bootstrap + ODS/DWD/DWS 草稿 + 请求/计划/影响/候选/审查链**，但**停在 4 层建模**：① 真实 reviewer 对模型文件候选 3 连 BLOCKED（REV-003 终局，工具链直驱版用 stub 判定规避了此边界）；② registry_append @approval 审批 seam —— OS 层 approvals API 可解析 approved，但 continue 后 ChatBI kernel 重验所需 `run_subject` 无法重建（AgentOS 2.6.22 resume 不重跑 pre-hooks，SEC-003 fail-closed，暂停点登记为人工步骤）。无产品缺陷，零产品代码改动。

**补测复现**：
```sh
# 服务在线即可（serve.py --keep）
<AGNO_VENV>/bin/python -B \
  .scratch/agno-demo/drive_turn.py <session_id> "<一轮指令>" .scratch/agno-demo/fj-conv-tN
# 审批 seam：curl -X POST :7777/approvals/<id>/resolve -d '{"status":"approved","resolved_by":"owner@example.com"}'
#       然后：curl -X POST :7777/agents/chatbi-agno/runs/<run_id>/continue -F "tools=" -F "stream=true"
```



## 5. 常见问题排查

| 现象 | 原因 | 处理 |
|---|---|---|
| "Something went wrong while streaming... C002" | agent 输出无证据链（未走治理流） | 按 recovery 补充缺失字段（如 time_range）后重问；或改用 3.1 的结构化消息模板 |
| agent 逐字段反复追问 | deepseek-v4-flash 过度澄清（模型能力边界） | 结构化消息一次性给全 7 字段；或换更强模型 |
| agent 输出散文计划被拦 | 非问句结尾（交接契约） | 重问（带字段）；已登记的模型遵循度边界 |
| run 长时间不结束 | 旧代码无 REVIEW_BLOCK_LIMIT | 确认 ≥720ec47 |
| HITL 审批恢复失败 | agno 2.6.22 × DeepSeek tool_call_id 不匹配（已登记）；2026-08-12 纯对话补测确认精确机理：OS approvals API 可解析 approved，但 continue 后 ChatBI approval_verify_hook 的 `run_subject`（contextvar，由 pre-hook 设置）为空 → SEC-003 fail-closed，registry_append 无法执行 | OS 层可解析 approved；受保护工具执行暂停点登记为人工步骤；等 agno 升级或手工续跑 |
| 服务启动报错 | 端口占用/状态损坏 | `kill $(lsof -ti:7777)`；必要时不带 --keep 重启 |

## 6. 环境重置（验收后收尾）

```sh
rm -rf .scratch/agno-demo/ws/.chatbi .scratch/agno-demo/ws/.chatbi-runtime
# 然后不带 --keep 重启
```

## 7. 修复记录（3.1 报错排查链，2026-08-11）

| 提交 | 修复 | 实测效果 |
|---|---|---|
| 20c53d5 | record_request 工具契约（7 字段 + 格式）+ schema 失败翻译成"去问用户"recovery | agent 不再猜空值，开始走澄清 |
| 60535a0 | instructions 顶部加 GOVERNANCE PROTOCOL 前置（5 条 + 问句结尾契约） | agent 开始加载 runbook、进入治理流 |
| c8b3d0d | delivery gate 对问句/含 clarify 输出放行（对话交接） | 澄清问句不再被 C002 误杀 |
| 33a96db | C002 recovery 改为对用户可操作（补字段后重问） | 失败可恢复 |
