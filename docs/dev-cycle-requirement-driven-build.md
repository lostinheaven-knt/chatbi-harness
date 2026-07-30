# Dev Cycle: 需求驱动建造工作流 (`/chatbi-build-from-requirement`)

> Status: ACTIVE (legacy step 7.a). Dependency-driven batch plan for the 8th
> command + supporting thin lib / bootstrap / maintain-model / product-doc
> changes. Module order follows `docs/orchestrator-state.md:25-32`. API
> contracts in `docs/technical-design-requirement-driven-build.md`; per-module
> change list in `docs/modification-requirement-driven-build.md`.
>
> Open point 6 decision (CONFIRMED, overrides technical-design §2.7 v1
> simplification): `validate_build_plan` gets `known_models: frozenset[str]`
> in v1. SCOPE-001 cross-plan-boundary check (dep points to a pre-existing
> model outside the plan) is lifted into the lib, not left to the SKILL.

## Cycle 1: bootstrap incremental introspect (Module 3)

- 范围: `bootstrap.py` 加 `read_source_inventory(path) -> SourceInventory` +
  `merge_source_inventories(base, extra) -> SourceInventory`。Q4 放 bootstrap.py
  (producer also reads, mirrors impact.py 既有 build+validate 模式)。
- 依赖: 无 (复用现有 `SourceInventory`/`SourceTable`/`SourceColumn` dataclass)。
- 交付: 两个新函数 + `__all__` 更新。absent policy 不对称:
  `read_source_inventory` absent -> GateError (bootstrap 前置缺失);
  `read_model_registry` absent -> () (首次建造空 registry, Module 2)。
- 预计文件: `harness/.claude/lib/chatbi_harness/bootstrap.py` (MODIFY)。
- 验证点: `read_source_inventory` round-trip `SourceInventory.to_dict()`;
  absent/malformed -> GateError; `merge_source_inventories` union by name,
  collision -> GateError, inputs not mutated, schema_version stays 1。

## Cycle 2: thin lib `build_plan.py` + schema + exports (Module 2)

- 范围: `build_plan.py` 新增 (ModelEntry/HumanApproval/CrossLayerException/
  LayerRule/BuildPlan dataclass frozen-slots + `build_model_entry` 工厂 +
  `read_model_registry` + `validate_build_plan` + `validate_layer_dependency`
  + `append_model_registry`) + `build-plan.schema.json` + `__init__.py` 导出。
- 依赖: Cycle 1 (import `read_source_inventory`/`merge_source_inventories`
  from `.bootstrap`)。复用 `gates._sanitize_text`/`GateError`/`GateDecision`,
  `evidence._get_schema`/`_validate_against_schema`, `impact._CHANGE_KINDS`,
  `harness_state` 原子写纪律 (inline, not direct call -- path constrained)。
- 交付: 7 个 build_plan 公共名 + 2 个 bootstrap 名 (Q4) 从 `__init__.py` 导出。
  `validate_build_plan` 签名含 `known_models: frozenset[str] = frozenset()`
  (open point 6 决策)。拓扑检查: 每个 upstream_dep 要么在 plan.models 内
  (拓扑有序, DOC-002/HOOK-004), 要么在 known_models 内 (计划外预存模型);
  两者都不在 -> GateError (SCOPE-001, evidence_ref
  `build-plan:scope:<name>:<dep>`)。
- 预计文件: `harness/.claude/lib/chatbi_harness/build_plan.py` (ADD),
  `harness/.claude/schemas/build-plan.schema.json` (ADD),
  `harness/.claude/lib/chatbi_harness/__init__.py` (MODIFY)。
- 验证点: frozen-slots `AttributeError` on setattr; `to_dict` round-trip 过
  schema; `read_model_registry` absent -> (), present -> parse + 每条过
  `build_model_entry`, malformed/tampered -> GateError;
  `validate_build_plan` PASS + 5 raise (topology/alias/SEM-003 一致性/
  Q1 extend-source 门/SCOPE-001 known_models 跨计划边界);
  `validate_layer_dependency` PASS + 跨层拒绝 + 例外不抛;
  `append_model_registry` create + idempotent on (name,created_rev) +
  atomic temp+rename 0o600 + 不 mutate entry。

## Cycle 3: blueprint `## Layers` stub (Module 5)

- 范围: `chatbi-bootstrap/SKILL.md` Step 8 ADD `## Layers` header + 骨架
  (层级顺序 ODS->DWD->DWS->ADS, DIM 独立; 不跨层规则; 例外需显式记录进
  `ModelEntry.cross_layer_exception` DOC-002, 不进蓝图 Q2)。注意 Step 8 现在
  只有 `## Tooling` + `## Metrics`, 这是 ADD 第三个 header, 非填占位。
- 依赖: 无 (纯文档骨架, 声明式领域知识)。
- 交付: Step 8 stub 创建逻辑加 `## Layers` 段。
- 预计文件: `harness/.claude/skills/chatbi-bootstrap/SKILL.md` (MODIFY Step 8)。
- 验证点: `## Layers` header 存在; 骨架含层级顺序 + 不跨层规则 + 例外记录位置;
  实际规则留空给 operator 填 (DOC-001, 同 `## Metrics` 占位 posture)。

## Cycle 4: new command + SKILL (Module 1)

- 范围: `commands/chatbi-build-from-requirement.md` (镜像 chatbi-bootstrap.md:
  frontmatter + 0-5 段 + Rules) + `skills/chatbi-build/SKILL.md` (镜像
  chatbi-bootstrap/SKILL.md: 4 步流程映射 0-5 段)。
- 依赖: Cycle 2 (lib surface named primitives); Cycle 3 (blueprint § Layers
  input for Step 2 cross-layer); Cycle 5 (Module 4 append registry for Step 3)。
- 交付: 第 8 个命令 + runbook, 信任层 = orchestrator (derive + chain +
  human-in-loop + handoff, 不写受治理内容/不答问/不批指标)。
- 预计文件: `harness/.claude/commands/chatbi-build-from-requirement.md` (ADD),
  `harness/.claude/skills/chatbi-build/SKILL.md` (ADD)。
- 验证点: 0-5 段结构齐; Rules 复用现有 rule ID (46 不变); derivation 明确为
  agent reasoning, lib 只读+校验; 受保护点 (SEM-003 4 enum + extend-source
  flag + SEC-001/ANS-003 + REV-001/002/003) 与 feature-flow §6 表一致。

## Cycle 5: maintain-model writes registry + reads § Layers (Module 4)

- 范围: `chatbi-maintenance/SKILL.md` §3 读块扩读 § Layers (同 § Metrics 模式,
  缺则问 operator 不发明 META-003) + sync gate + stop_gate pass 后调
  `append_model_registry` (新步骤)。
- 依赖: Cycle 2 (`ModelEntry`, `append_model_registry`)。
- 交付: maintain-model 建模成功后记 registry (derived evidence, 不进 config
  schema); 读 § Layers 拿跨层依赖规则。
- 预计文件: `harness/.claude/skills/chatbi-maintenance/SKILL.md` (MODIFY §3 + new step)。
- 验证点: append 只在 sync gate + stop_gate pass 后 (DOC-004/HOOK-001 fail-closed);
  § Layers 缺则问 operator 不发明; registry 是 derived evidence 不进
  `chatbi-harness.schema.json`。

## Cycle 6: product integration (Module 6)

- 范围: `build-product.sh` (+1 命令到循环 + import canary 加
  `chatbi_harness.build_plan` + 注释 7->8); `harness/CLAUDE.md` (+1 路由行,
  验 <200, 当前 114 行); `harness/product-README.md` (Seven->Eight + 表格行);
  `harness/docs/harness/installation.md` + `README.md` (各一段, VERIFIED OFFLINE
  framing)。
- 依赖: Cycle 4 (new command exists)。
- 交付: 产品 build 含第 8 命令; 文档计数更新; 无 leak。
- 预计文件: `build-product.sh` (MODIFY), `harness/CLAUDE.md` (MODIFY),
  `harness/product-README.md` (MODIFY), `harness/docs/harness/installation.md`
  (MODIFY), `harness/docs/harness/README.md` (MODIFY)。
- 验证点: `build-product.sh` canary import OK; CLAUDE.md <200 行;
  `validate_domain_contract` 仍 PASS (46 规则不变, 契约产物只 +1 路由行);
  product-README 计数 "Eight"; installation/README VERIFIED OFFLINE framing。

## Cycle 7: tests (Module 7)

- 范围: `tests/harness/test_build_plan.py` ADD (frozen-slots/to_dict round-trip/
  read_model_registry absent+parse+reject/read_source_inventory round-trip/
  validate_build_plan PASS+5 raise 含 known_models SCOPE-001/
  validate_layer_dependency PASS+跨层拒绝+例外不抛/append_model_registry
  create+idempotent+atomic+不mutate); `test_bootstrap.py` MODIFY (merge+
  read_source_inventory+增量 path); `test_maintenance.py` MODIFY (append-after-
  sync-pass); `test_contract.py` MODIFY (required_routes 加 build-from-requirement
  + 补 bootstrap 缺口); `test_e2e.py` MODIFY (six_commands->eight_commands +
  加 build-from-requirement + 补 bootstrap)。
- 依赖: Cycle 1-6 全部完成。
- 交付: 566+ 全绿 (新测试 additive); `validate_domain_contract` PASS。
- 预计文件: `tests/harness/test_build_plan.py` (ADD),
  `tests/harness/test_bootstrap.py` (MODIFY),
  `tests/harness/test_maintenance.py` (MODIFY),
  `tests/harness/test_contract.py` (MODIFY),
  `tests/harness/test_e2e.py` (MODIFY)。
- 验证点: `python3 -B -m unittest discover -s tests/harness` 全绿;
  `validate_domain_contract` PASS; 无新 rule ID; `chatbi-harness.schema.json` 未改。

STATUS: ACTIVE
