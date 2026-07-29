# SessionB Runbook: 本地数仓 demo via `/chatbi-bootstrap`

> 在 sessionA(dev,`chatbi-cc-dev`)里建好了 `/chatbi-bootstrap`。本 runbook 指导你在
> sessionB(workspace,`/Users/admin/Downloads/workspace/chatbi-ws`,由 `install.sh` 从产品
> `chatbi/` 装出)里实际跑通:用 `/chatbi-bootstrap`
> 在你的本地 MySQL 上 scaffold 数仓 -> 用 `/chatbi-maintain-model` 建 ODS -> 用
> `/chatbi-analyze` 出"核心业务功能使用情况分析表"。
>
> **sessionB 是受治理的 agent**(46 规则、7 命令、SessionStart hook 生效)。**你 = human owner**:
> 定标准、审批受保护动作(指标口径/生产发布/破坏性迁移,SEM-003)。agent 起草,你把关。
>
> 日期:2026-07-27。harness 状态:AS_BUILT,563 测试绿,`/chatbi-bootstrap` 已 live-smoke 验证
> (真连 127.0.0.1:3306,`dw` 建好,`public`=125 表)。

## 0. 你手上的环境

| 项 | 值 |
| --- | --- |
| 源 MySQL | `127.0.0.1:3306`,db `public`,user `root`,**无密码**,MySQL 9.7.1,125 表 |
| mysql CLI | `/opt/homebrew/bin/mysql`(live smoke 已确认) |
| Python 3.10+ | `/opt/homebrew/bin/python3`(3.14) |
| 产品(build artifact,rebuildable) | `/Users/admin/Downloads/workspace/chatbi` |
| demo workspace(install.sh 目标,sessionB 跑这里) | `/Users/admin/Downloads/workspace/chatbi-ws` |
| 业务参考文档(Business Codebase) | `/Users/admin/Downloads/workspace/fypro_all_app/docs/`(3 个 md) |
| 目标数仓 DB | `dw`(bootstrap 自动 `CREATE DATABASE IF NOT EXISTS`) |
| demo 分析目标 | "核心业务功能使用情况分析表"(基于 `creator-web-functional-flow.md`) |
| MVP 表 | `t_plg_creator_agent_session` + `t_plg_creator_creator_profile` + `t_plg_creator_auth_account` |

---

## 1. Pre-flight(终端 1,开 sessionB 之前)

### 1.0 把产品装进专用 workspace(一次性,必做)

`chatbi/` 是 `build-product.sh` 的产物--每次 sessionA rebuild 都会 `rm -rf chatbi/` 重建。
**不要直接在 `chatbi/` 里跑 sessionB**,否则你累积的运行态(`.chatbi/`、dbt 模型、local
config、source_inventory)会在下次 rebuild 时被清空。用 `install.sh` 把产品装进一个专用
workspace:

```sh
/Users/admin/Downloads/workspace/chatbi/install.sh /Users/admin/Downloads/workspace/chatbi-ws
```

install.sh 会:预检(专用空根、无 `.claude`/`CLAUDE.md` 冲突)-> 拷贝 `.claude/`/`docs/`/
`CLAUDE.md`/`CONTEXT.md`/`e2e-state.py`/`README.md` -> 校验 3.10+ python 在边界外 -> 打印
`export CHATBI_PYTHON=...` -> 打印下一步。它**不**改你的 shell rc;`CHATBI_PYTHON` 你自己
export(见 2.1)。

> 也可装到 `/tmp/chatbi-e2e`(你之前指定的实测目录),但 /tmp 重启可能清空,演示用建议持久
> 化的 `chatbi-ws`。`/tmp/chatbi-e2e` 留给 6.3 的 throwaway hook 测试。

**之后所有 sessionB 操作都在 `chatbi-ws/` 里,不在 `chatbi/`。** 1.1-1.4 的预检里,1.2 查
产品(源),1.4 改 workspace(目标)。

### 1.1 MySQL 在跑 + 可连

```sh
brew services list | grep mysql          # 应是 started
mysql -h 127.0.0.1 -P 3306 -u root -e "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='public'"
# 期望: 125
```

### 1.2 产品里有 `/chatbi-bootstrap`

```sh
ls /Users/admin/Downloads/workspace/chatbi/.claude/commands/chatbi-bootstrap.md
ls /Users/admin/Downloads/workspace/chatbi/.claude/skills/chatbi-bootstrap/SKILL.md
ls /Users/admin/Downloads/workspace/chatbi/.claude/lib/chatbi_harness/bootstrap.py
# 三个都应存在
```

### 1.3 确认 mysql + python 绝对路径

```sh
command -v mysql     # 期望 /opt/homebrew/bin/mysql
/opt/homebrew/bin/python3 --version   # 期望 3.1x
```

### 1.4 把 fypro 文档声明为 Business Codebase(推荐,Part D 要用)

`/chatbi-bootstrap` 不能新建 `business_codebases` 条目(那是受治理的 shared config 改动,SCOPE-001/PORT-001)。你**手动**在 shared config 里声明别名,bootstrap 只负责把别名解析成绝对路径写进 local config。

编辑 `/Users/admin/Downloads/workspace/chatbi-ws/.claude/chatbi-harness.json`,把 `"business_codebases": {}` 改成:

```json
"business_codebases": {
  "fypro_docs": {
    "description": "Creator business semantics, table lineage, functional flow docs",
    "path_ref": "fypro_docs_root",
    "read_mode": "adapter",
    "git_history": "metadata_only"
  }
},
```

> 只加这一个字段,别动 `adapters`/`governance`/`evaluation`/`runtime`/`workspace`。

### 1.5 装 dbt-mysql(Part D 建模用,可后装)

**不要装进 venv_fastmcp(Python 3.14)**--dbt 依赖 `mashumaro`,在 3.14 上必崩
(`UnserializableField: ... Optional[str] ... not serializable`,mashumaro 的 codegen
不兼容 3.14 的 typing 内省)。dbt 稳到 3.13。用一个**独立的 Python 3.12 venv**:

```sh
brew install python@3.12                                                       # 一次性
/opt/homebrew/bin/python3.12 -m venv /Users/admin/Downloads/workspace/venv_dbt
/Users/admin/Downloads/workspace/venv_dbt/bin/pip install -U pip dbt-mysql
/Users/admin/Downloads/workspace/venv_dbt/bin/dbt --version   # 期望: dbt-core 1.7.19 + mysql 1.7.0
```

之后所有 `dbt` 命令:要么用全路径 `/Users/admin/Downloads/workspace/venv_dbt/bin/dbt`,
要么先 `source /Users/admin/Downloads/workspace/venv_dbt/bin/activate` 再用 `dbt`。
**dbt 的 Python(3.12)和 harness 的 `CHATBI_PYTHON`(3.14)互不冲突**--harness lib
不用 mashumaro,3.14 没问题;dbt 单独 3.12。agent 调 `dbt` 就是跑个命令。

> 如果你只想先跑通 bootstrap(Part 3),这步可以跳过,Part D 之前再装。

---

## 2. 启动 sessionB

### 2.1 开 sessionB

```sh
cd /Users/admin/Downloads/workspace/chatbi-ws
export CHATBI_PYTHON=/opt/homebrew/bin/python3     # 必须在启动 claude 的同一个 shell 里 export
claude
```

> **关键**:`CHATBI_PYTHON` 必须在启动 `claude` 的那个 shell 里 export,SessionStart hook 才能继承。
> 没设的话 SessionStart 直接 fail-closed block(SCOPE-001/SEC-001/PORT-001/HOOK-004)。

### 2.2 SessionStart hook 触发

`claude` 启动时,`.claude/hooks/session_diagnose` 会跑,产出一个诊断 JSON。期望:
- `status: "pass"`(CHATBI_PYTHON 已设 + 契约可读)。
- `production_ready: false` 是**正常的**--人工 hard-gate(owner/PII 策略/sandbox/adapter 闭环)没闭合,FBK-003 不伪造。

如果 `status: "block"`:看 `recovery`,通常是 `CHATBI_PYTHON` 没设或路径不对。

### 2.3 跑 `/chatbi-init` 拿基线诊断

在 sessionB 里输入:

```
/chatbi-init
```

期望:一个 JSON,**`status: "BLOCKED"` 是正常的**(本地 demo 必然 BLOCKED,别卡在这)。
`production_ready: false` 是 Cycle 1 预期(FBK-003,不伪造)。pass 的是 `domain_contract`/
`configuration`/`paths`/`revision_evidence`;block 的是:

- **探测/环境限制**(`claude_login`/`sandbox`/`adapters`/`claude_version` warn)--配置不可修。
  Cycle 1 已知缺口(probe 不验 adapter,`available_adapters` 恒空)+ probe 正则按 2.1.216 校准,
  2.1.217 的 doctor 输出里 login/sandbox 标记没识别到。bootstrap 用 mysql CLI,不靠这些,忽略。
- **人类治理项**(`governance_owner`/`pii_policy`/`release_threshold`)--shared config 里是 null,
  `/chatbi-init` 不许改。你在 `/chatbi-analyze`(Part 5)前自己填 `governance.owners.default_domain_owner`
  (你自己)即可;pii_policy/release_threshold 是生产门,demo 留 null。

> **关键:`/chatbi-init` BLOCKED 不挡 `/chatbi-bootstrap`。** bootstrap 的 gate 是
> `load_effective_config`(已 pass),不读 init 诊断状态。看到 BLOCKED 直接进 Part 3。

如果 agent 问"claude 可执行文件路径"或"写入待办清单?"--前者确认
`/Users/admin/.local/bin/claude`(让 claude_version/claude_doctor 过/warn),后者**跳过**。

---

## 3. 跑 `/chatbi-bootstrap`(scaffold 数仓)

### 3.1 调用

在 sessionB 里输入(推荐用 prose 描述,agent 会抽取字段):

```
/chatbi-bootstrap 127.0.0.1 3306 root public
```

参数顺序(host port user source_db)。**不传 credential-env-name** = 本地无密码 root(`credential_env_names: []`)。**不传 business-codebase-alias** 先跑通基础;想顺便绑 fypro 文档,加第 6 个参数 `fypro_docs`(前提是 1.4 已声明)。

> 也可以纯 prose:`/chatbi-bootstrap` 然后说"源 MySQL 127.0.0.1:3306,user root 无密码,源库 public,目标 dw 库;business codebase 别名 fypro_docs"。

### 3.2 agent 会走的 9 步(你要配合的关键点)

agent 加载 `skills/chatbi-bootstrap/SKILL.md`,按 9 步执行。**你会被拦下来的地方**:

| 步 | agent 做 | 你要做的 |
| --- | --- | --- |
| 1 | 校验 host/port/user/source_db + 凭据处理 | 确认连接信息(无密码 root 即可) |
| **2** | **问你确认 mysql 绝对路径**(Risk #1,镜像 `/chatbi-init` 确认 claude_executable) | **明确确认 `/opt/homebrew/bin/mysql`**。不确认 = `cli_allowlist` 空 = `resolve_executable` fail-closed,流程停。prose 里给的路径**不算**确认,要你显式说"确认"。 |
| 3 | 写 `.claude/chatbi-harness.local.json`(`cli_adapters.mysql` + 可选 `path_bindings`) | 若加了 fypro_docs,确认其绝对路径 `/Users/admin/Downloads/workspace/fypro_all_app/docs` |
| 4 | shared config `adapters.query` 追加 `cli:mysql`(幂等) | 无 |
| 5 | `load_effective_config` 校验(schema + secret 扫描 + path-binding) | 无;若 `GateError` 看 `recovery` |
| 6 | 预检 `dw` 表数(risk#4)-> `CREATE DATABASE IF NOT EXISTS dw` | 无;若 `dw` 已有表会 WARN(不 clobber) |
| 7 | introspect `public` -> `.chatbi/bootstrap/source_inventory.json` | 无 |
| 8 | scaffold `dbt_project.yml` + `models/{ods,dwd,dws,dim}/`(空) + stub `docs/org/data-warehouse-blueprint.md` | 无 |
| 9 | 出 hand-off 报告:表数、dw 状态、config 路径、inventory 路径 | 看报告,确认表数=125 |

### 3.3 验证产出

在**另一个终端**核:

```sh
cd /Users/admin/Downloads/workspace/chatbi-ws

# (a) local config 写了 cli_adapters.mysql
cat .claude/chatbi-harness.local.json | grep -A3 mysql

# (b) shared config 注册了 cli:mysql
grep "cli:mysql" .claude/chatbi-harness.json

# (c) dw 库建好了
mysql -h 127.0.0.1 -P 3306 -u root -e "SHOW DATABASES LIKE 'dw'"

# (d) source_inventory.json 产出,125 表
ls -la .chatbi/bootstrap/source_inventory.json
/opt/homebrew/bin/python3 -c "import json;d=json.load(open('.chatbi/bootstrap/source_inventory.json'));print('tables:',len(d.get('tables',[])))"

# (e) dbt scaffold
ls dbt_project.yml models/ods models/dwd models/dws models/dim
```

全部通过 = bootstrap 成功。**此时还没有任何 ODS/DWD/DWS 模型**(只有空目录 + inventory)。下一步 `/chatbi-maintain-model` 建 ODS。

---

## 4. 建 ODS via `/chatbi-maintain-model`(3 表 MVP)

### 4.1 为什么这 3 张表

demo 目标是"核心业务功能使用情况分析表"。`t_plg_creator_agent_session.scene` 枚举(0-8)直接把 AI 会话映射到核心功能(0=New Chat、1=建站、2=Project、3=DNA、6=Dashboard、7=Growth Plan、8=AI Studio)--这是天然的"功能类型"轴。配 `creator_profile`(分母:已 onboarding 的 creator)和 `auth_account`(注册维度),3 表就够跑通 T1 指标。

### 4.2 dbt profiles 配置(建模型前一次性做)

bootstrap 只 scaffold 了 `dbt_project.yml` 骨架。建模型前补 profiles:

```sh
mkdir -p ~/.dbt
cat > ~/.dbt/profiles.yml <<'YML'
dw:
  target: dev
  outputs:
    dev:
      type: mysql
      server: 127.0.0.1
      port: 3306
      schema: dw
      username: root
      password: ""
      threads: 1
YML
```

> profile 名必须和 `dbt_project.yml` 里的 `profile:` 一致。bootstrap scaffold 出的
> `dbt_project.yml` 是 `profile: dw`(project name 也叫 `dw`),所以 profiles.yml 顶层
> 用 `dw:`。**别用 `chatbi_dw`**,否则 `dbt debug` 报 `Could not find profile named 'dw'`。

**跑 dbt 前先激活 venv_dbt**(见 1.5;dbt 不能用 3.14):
`source /Users/admin/Downloads/workspace/venv_dbt/bin/activate`

验证:

```sh
cd /Users/admin/Downloads/workspace/chatbi-ws
dbt debug --profiles-dir ~/.dbt     # 期望: Connection test passed
```

> dbt 走的是它自己的 MySQL 连接(不走 harness 的 `cli:mysql` adapter)。harness 治理的是**模型变更流程**(agent 起草 + 评审 + 你审批),dbt 是执行引擎。两者不冲突。

### 4.3 建 ODS:逐表调 `/chatbi-maintain-model`

**第 1 张:`t_plg_creator_agent_session`(核心事实表)**

在 sessionB 输入:

```
/chatbi-maintain-model
Create ODS model from source_inventory for table t_plg_creator_agent_session.
- Source: public.t_plg_creator_agent_session (AI session event log; scene enum 0-8 maps to core business function per fypro_docs business semantics doc).
- Target: models/ods/ods_agent_session.sql (dbt model, 1:1 with source, typed columns, add ds partition column = DATE(create_time)).
- Context: this is the central "function usage" fact table for the 核心业务功能使用情况 analysis.
```

agent 会:
1. `change_kind = model`,target = `cli:mysql`/`ods_agent_session`。
2. `build_impact_manifest`(影响:metadata/semantic/reference/tests/downstream/eval)。
3. 起草 `models/ods/ods_agent_session.sql`(candidate)。
4. `dbt test`(若你配了 schema test)+ 独立评审(adversarial-reviewer,11 维度)。
5. **不是受保护动作**(ODS 不是指标口径),无需你 approve 指标;但 stop gate 要求 sync 完整。
6. 通过 -> 模型落库,`dw.ods_agent_session` 可查。

**第 2 张:`t_plg_creator_creator_profile`(维度 + 分母)**

```
/chatbi-maintain-model
Create ODS model for t_plg_creator_creator_profile.
- Source: public.t_plg_creator_creator_profile (creator identity + onboarding_status; defines "onboarded creator" denominator).
- Target: models/ods/ods_creator_profile.sql.
- Note: onboarding_status=1 is the denominator for adoption-rate metrics; flag it in the model comment.
```

**第 3 张:`t_plg_creator_auth_account`(注册维度)**

```
/chatbi-maintain-model
Create ODS model for t_plg_creator_auth_account.
- Source: public.t_plg_creator_auth_account (account/login identity; account_id hub, 1:1 to creator via creator_profile.account_id).
- Target: models/ods/ods_auth_account.sql.
```

### 4.4 验证 ODS

```sh
cd /Users/admin/Downloads/workspace/chatbi-ws
dbt run --select ods_agent_session ods_creator_profile ods_auth_account   # 建 dw 里的表
dbt test --select ods_agent_session ods_creator_profile ods_auth_account  # 跑测试
mysql -h 127.0.0.1 -P 3306 -u root -e "SELECT COUNT(*) FROM dw.ods_agent_session"
```

---

## 5. DWD/DWS + 语义层 + `/chatbi-analyze`

### 5.1 DWD(明细,join agent_session + creator_profile)

```
/chatbi-maintain-model
Create DWD model dwd_session_creator_detail: join ods_agent_session (session_id, scene, creator_id, create_time) with ods_creator_profile (creator_id, onboarding_status) to attach onboarding status to each session. Target: models/dwd/dwd_session_creator_detail.sql. Grain: one row per session.
```

### 5.2 DWS(汇总:按 scene × 日)

```
/chatbi-maintain-model
Create DWS model dws_function_usage_daily: aggregate dwd_session_creator_detail by scene and ds (DATE(create_time)). Columns: scene, ds, session_count, distinct_active_creators. Target: models/dws/dws_function_usage_daily.sql. Grain: one row per (scene, ds).
```

### 5.3 语义层 T1 指标(人审批,SEM-003)

```
/chatbi-maintain-model
Define semantic metric core_function_adoption_rate:
- Numerator: COUNT(DISTINCT creator_id) from dwd_session_creator_detail WHERE scene = <X> AND onboarding_status = 1 AND ds in <period>.
- Denominator: COUNT(DISTINCT creator_id) from ods_creator_profile WHERE onboarding_status = 1.
- Axis: scene (0-8).
This is a canonical metric definition - REQUEST HUMAN OWNER APPROVAL (SEM-003).
```

agent 起草口径,**会停下等你 approve**(SEM-003,受保护动作)。你确认口径后,指标进语义层,T1 可查。

### 5.4 `/chatbi-analyze` 出分析表

```
/chatbi-analyze
Question: 核心业务功能(AI 会话 scene 0-8)的使用情况如何?给出各功能的采纳率 + 日均使用强度。
Time range: 全量数据(本地 demo 数据稀疏,用全量)。
Entity: agent_session (by scene).
Segment: 已 onboarding 的 creator (onboarding_status=1).
Actor: 我(human owner).
Purpose: 验证 agent 维护数仓的端到端分析闭环。
Supported decision: 是否扩展 demo 到更多功能/线上。
```

agent 走 5 层流:
1. Layer 1 clarify(字段齐了就不停)。
2. Layer 2 T1 语义层查询(`core_function_adoption_rate` + 日均强度)。
3. 独立评审(adversarial-reviewer,SHA 绑定)。
4. review gate + stop gate(PASS 才交付)。
5. 出**带 provenance footer 的分析表**(16 字段:question/time_range/entity/method/source_tier=T1/filters/denominator/quality/limitations/review_round/freshness/owner/confidence/provenance_refs)。

期望产出:一张按 scene 分的表(每个核心功能的采纳率 + 日均会话数),带完整 provenance。

---

## 6. 验证 + 排错

### 6.1 5 个治理闭环是否跑通

| 闭环 | 在哪步验 | 通过标志 |
| --- | --- | --- |
| 建模闭环 | Part 4.3 | ODS 模型经评审 + sync gate 落库 |
| 语义层闭环 | Part 5.3 | 指标定义经你 approve(SEM-003)后可查 |
| 分析闭环 | Part 5.4 | `/chatbi-analyze` 出带 provenance 的表 |
| 纠错闭环 | (可选) `/chatbi-correction` 吃一条纠错 | 产出 fix candidate + eval case |
| 边界闭环 | 见 6.3 | PreToolUse/Stop gate block 越界写 |

### 6.2 常见问题

| 症状 | 原因 | 修 |
| --- | --- | --- |
| SessionStart `status: block` | `CHATBI_PYTHON` 未设/路径错 | 在启动 claude 的 shell 里 `export CHATBI_PYTHON=/opt/homebrew/bin/python3` |
| bootstrap Step 2 卡住 | 你没显式确认 mysql 路径 | 明确说"确认 /opt/homebrew/bin/mysql" |
| bootstrap `GateError: invalid-python-binding` 或 secret | local config 里混进了密码值或机器路径 | 删掉密码值,只留 env 变量名 |
| `resolve_executable` fail-closed | cli_allowlist 空 | Step 2 没确认 mysql 路径 |
| `dbt --version` 崩 `mashumaro.exceptions.UnserializableField` | dbt 装进了 Python 3.14 venv | 改用 venv_dbt(Python 3.12,见 1.5);dbt 不能用 3.14 |
| dbt `dbt debug` 连不上 | profiles.yml 密码/端口错 | 确认 server/port/schema=dw/username=root/password="" |
| dbt `Could not find profile named 'dw'` | profiles.yml 顶层 profile 名 ≠ dbt_project.yml 的 `profile:` | profiles.yml 用 `dw:`(scaffold 的 dbt_project.yml 是 `profile: dw`),见 4.2 |
| dbt run 报源表找不到 / source 报 `database` 不允许 | dbt-mysql adapter 的 source 用 `database: public` 不生效--MySQL 的"库"在 dbt 里映射成 `schema`,adapter 不允许 source 单独设 `database` | schema.yml 的 source 改成 `schema: public`(去掉 `database:`);target 侧同理 `schema: dw`。见 4.3 agent 已修的 ods_agent_session 案例 |
| `CREATE DATABASE` 失败 | MySQL 没起或 root 权限 | `brew services restart mysql` |
| `/chatbi-analyze` 降级到 T2/T3 | T1 指标没建或没 approve | 先完成 Part 5.3(SEM-003 审批) |

### 6.3 边界闭环(可选,需 live hook)

出厂 `settings.json` 只注册 SessionStart(安全默认,避免热重载死锁)。前 4 闭环靠 command/skill 程序性治理就够。**边界闭环**(agent 越界写 -> PreToolUse/Stop 硬 block)需注册 live hook:

- **别在 sessionB 主会话注册**(阻塞型 hook 热重载 settings.json 可能死锁,我之前踩过)。
- 在 `/tmp/chatbi-e2e` 这种 throwaway workspace 注册全 6 hook,按 `docs/harness/e2e-checklist.md` 的 6-hook 块,验越界 block 后删掉。

---

## 7. 这份 runbook 不做的事(诚实)

- **性能验证**:MySQL 是 OLTP,DWS/ADS 聚合在真实数据量下慢且不代表 Hive/StarRocks 性能。性能到线上/StarRocks 验。
- **live hook 注册**:sessionB 保持 SessionStart-only;边界闭环在 throwaway workspace 单独验(6.3)。
- **非 MySQL 引擎**:v1 只支持 MySQL。StarRocks 因 MySQL 协议兼容,`cli:mysql` 大致能跑但未验;Hive 需另写 adapter(未来)。
- **生产就绪**:`production_ready` 全程 `false`,直到人工 hard-gate(owner/PII 策略/sandbox/adapter 闭环)闭合(FBK-003,不伪造)。
- **全量 125 表 ODS**:本 runbook 只建 3 表 MVP。其余 122 表按需用 `/chatbi-maintain-model` 逐个建(或未来补 `--generate-ods` 批量)。

---

## 8. 一页速查

```sh
# 1. pre-flight
brew services list | grep mysql
mysql -h 127.0.0.1 -P 3306 -u root -e "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA='public'"  # 125

# 2. install product into dedicated workspace (one-time)
/Users/admin/Downloads/workspace/chatbi/install.sh /Users/admin/Downloads/workspace/chatbi-ws

# 3. sessionB (in chatbi-ws, NOT chatbi)
cd /Users/admin/Downloads/workspace/chatbi-ws
export CHATBI_PYTHON=/opt/homebrew/bin/python3
claude
# in sessionB: /chatbi-init  (baseline)

# 4. bootstrap (confirm mysql path when asked: /opt/homebrew/bin/mysql)
/chatbi-bootstrap 127.0.0.1 3306 root public

# 5. dbt setup (outside sessionB, another terminal; dbt needs Python 3.12, NOT 3.14)
brew install python@3.12
/opt/homebrew/bin/python3.12 -m venv /Users/admin/Downloads/workspace/venv_dbt
/Users/admin/Downloads/workspace/venv_dbt/bin/pip install -U pip dbt-mysql
source /Users/admin/Downloads/workspace/venv_dbt/bin/activate
# write ~/.dbt/profiles.yml (see 4.2), then: dbt debug

# 6. build ODS (in sessionB, one per table)
/chatbi-maintain-model   # ods_agent_session, then ods_creator_profile, then ods_auth_account

# 7. DWD + DWS + semantic metric (approve metric when asked, SEM-003)
/chatbi-maintain-model   # dwd_session_creator_detail, dws_function_usage_daily, core_function_adoption_rate

# 8. analyze
/chatbi-analyze          # 核心业务功能使用情况分析表 with provenance
```
