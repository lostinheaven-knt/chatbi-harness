# 测试清单 v1

> 来源：`docs/dev-cycle-1.md` Task 6 第 4 步 + §8 测试矩阵（行 303–312 / 355–366），
> 覆盖 `tests/harness/` 下 6 个模块共 85 个自动化用例 + 4 类静态合同检查。
> 每条 checkbox 后标注对应测试模块/用例或静态命令。执行结果见 `docs/test-report-v1.md`。

## 用例编号约定

- `SC-*` 静态合同（Static Contract）
- `CF-*` 配置单元（Config）
- `PT-*` 路径单元（Path）
- `GT-*` Gate 单元（Gate）
- `HK-*` Hook contract
- `DG-*` 离线纵向（DiaGnostics / init 纵向）
- `CP-*` 兼容性（ComPatibility）

---

## 1. 静态合同（SC）

### 1.1 自动化（`tests/harness/test_contract.py::DomainContractTests`，7 项）

- [x] SC-001 `test_checked_in_contract_is_valid` — checked-in 根契约经 `validate_domain_contract` 通过，rule_ids=(HOOK-004,)。
- [x] SC-002 `test_checked_in_contract_covers_governed_rules_and_root_responsibilities` — 领域模型 46 条 governed 规则与 CLAUDE/CONTEXT/.claude/rules/docs/harness 引用集合一致；CLAUDE.md ≤200 行；含域模型链接、人类责任、evidence only、四层栈、T1/T2/T3、independent、provenance、model or semantic change、prose 不等于 enforcement、6 条 `/chatbi-*` 路由。
- [x] SC-003 `test_unknown_rule_reference_fails_closed` — 未知规则 ID（SCOPE-999）fail-closed block，reason 含规则号，recovery 引向 domain model。
- [x] SC-004 `test_missing_governed_rule_coverage_fails_closed` — governed 规则未覆盖时 block，evidence_refs=(contract:rule-coverage,)，且不泄漏夹具中的 canary secret/机器路径。
- [x] SC-005 `test_root_contract_over_line_budget_fails_closed` — CLAUDE.md 超 200 行 block，evidence_refs=(contract:CLAUDE.md,)。
- [x] SC-006 `test_contract_with_machine_path_or_secret_fails_closed` — `/Users/...`、`api_key=...`、`?token=...` 三类 unsafe 值 block 且 to_json 不含原值。
- [x] SC-007 `test_missing_domain_model_fails_closed` — 缺域模型 block，evidence_refs=(contract:domain-model,)，reason 含 missing。

### 1.2 静态命令检查

- [x] SC-008 文件清单完整：`find CLAUDE.md CONTEXT.md .claude docs/harness tests/harness -type f -print` 输出 43 个文件，含根契约、.claude/{rules,hooks,lib,schemas,commands,fixtures,agents}、docs/harness 4 文档 + README + rule-traceability、tests/harness 6 测试模块 + `__init__.py`。
- [x] SC-009 规则 ID 集合 46/46 一致：`grep -rhoE '[A-Z]{2,5}-[0-9]{3}' CLAUDE.md CONTEXT.md .claude docs/harness | sort -u` = 46 条 governed 规则；与 `docs/chatbi-harness-domain-model.md` 的 governed 子集一致（域模型另含 9 条 META-001..009 meta 规则，不计入 governed）。
- [x] SC-010 secret/机器路径扫描：`grep -rnE '/Users/|BEGIN .*PRIVATE KEY|api[_-]?key|token[[:space:]]*[:=]' CLAUDE.md CONTEXT.md .claude docs/harness` 唯一命中 `.claude/fixtures/config/embedded-secret.json` 中的测试 canary `TEST_SECRET_CANARY_NOT_A_CREDENTIAL`（测试夹具非真实凭证，且被 `CF-001` 验证为 GateError 拒绝）。判 PASS。
- [x] SC-011 `test_hooks.test_settings_and_compatibility_document_only_the_verified_contract` — settings.json 仅含 `hooks.SessionStart`，command=`.claude/hooks/session_diagnose` 无 `..`/`python3`/机器路径/secret；launcher 可执行；compatibility/installation 文档含 VERIFIED OFFLINE / NOT YET EXERCISED / PRODUCTION BLOCKER / no PATH fallback / real paths / fixed OS / explicit confirmation 等约束，且不含 "fixture is production"。
- [x] SC-012 `test_diagnostics.test_init_command_and_minimal_docs_state_the_current_contract` — `/chatbi-init` 命令文档含 Input/Preconditions/Allowed changes/Stop conditions/Output evidence/Rules 六节，含 `[confirmed-claude-executable]`、`claude_executable=confirmed_claude_path`、Workspace-relative、`..`/symlink 禁令、PASS/WARN/BLOCKED；installation/configuration 文档含 VERIFIED OFFLINE / content_sha256，不含 "production certified"。

---

## 2. 配置单元（CF）— `tests/harness/test_config.py::EffectiveConfigTests`，34 项

### 2.1 parse / schema 基础

- [x] CF-001 `test_checked_in_examples_and_config_fixtures_are_executable_contracts` — checked-in shared/example/local.example 与 9 个 fixture（valid-minimal 通过；missing-field/duplicate-key/unknown-field/invalid-alias/absolute-shared-path/embedded-secret/owner-threshold-conflict/production-fixture-fallback 全部 GateError）。
- [x] CF-002 `test_missing_shared_config_fails_closed` — 缺 shared config block，evidence=(config:shared,)，不泄漏路径。
- [x] CF-003 `test_top_level_config_must_be_a_json_object` — 顶层非 object block。
- [x] CF-004 `test_non_finite_json_numbers_fail_closed` — NaN/±Inf 在 release_threshold block，不泄漏 NaN/Infinity。
- [x] CF-005 `test_missing_required_nested_field_fails_closed` — 删 workspace.id block。
- [x] CF-006 `test_schema_version_other_than_one_fails_closed` — schema_version=2 block。
- [x] CF-007 `test_unknown_shared_field_fails_closed_against_declared_schema` — 未知字段 surprise block，recovery 含 remove。
- [x] CF-008 `test_config_larger_than_256_kib_fails_closed_before_parsing` — >262144 字节 block，reason 含 256。
- [x] CF-009 `test_malformed_json_fails_closed_without_parser_details` — 截断 JSON block，reason 含 json，不泄漏路径。
- [x] CF-010 `test_non_utf8_config_fails_closed_without_exposing_bytes` — 非 UTF-8 block，reason 含 utf-8，不泄漏 \xff。
- [x] CF-011 `test_duplicate_json_key_fails_closed_with_gate_error` — 重复键 block，recovery 含 unique。

### 2.2 codebase schema / cross-field

- [x] CF-012 `test_invalid_codebase_alias_fails_closed` — 非法 alias block，recovery 含 lowercase。
- [x] CF-013 `test_codebase_path_reference_must_be_unique` — path_ref 重复 block，rule_ids=(SCOPE-001,PORT-001,HOOK-004)。
- [x] CF-014 `test_codebase_shape_and_read_mode_are_schema_validated` — read_mode=execute block，reason 含 adapter。
- [x] CF-015 `test_all_protected_actions_are_mandatory` — 少一个 protected_action block，rule_ids=(SEM-003,HOOK-004)。
- [x] CF-016 `test_sandbox_failure_policy_cannot_be_disabled` — fail_if_sandbox_unavailable=False block，rule_ids=(SEC-001,HOOK-004)。
- [x] CF-017 `test_release_threshold_requires_an_explicit_owner` — 有 threshold 无 owner block，rule_ids=(EVAL-004,HOOK-004)。
- [x] CF-018 `test_release_threshold_rejects_a_blank_owner` — owner 空白 block。
- [x] CF-019 `test_release_threshold_honors_declared_numeric_minimum` — threshold=-0.01 block，reason 含 minimum。
- [x] CF-020 `test_fixture_enabled_flag_is_required_by_schema` — 删 fixture_enabled block。
- [x] CF-021 `test_fixture_mode_cannot_be_a_production_adapter_fallback` — fixture 作为生产 fallback block，rule_ids=(PORT-001,HOOK-004)，recovery 含 isolate。

### 2.3 安全值拒绝

- [x] CF-022 `test_shared_config_rejects_machine_paths_and_secret_values` — 绝对路径/`/Users/`/`C:/`/`C:\`/UNC/`api_key=` block，不泄漏原值。
- [x] CF-023 `test_shared_config_rejects_forward_slash_unc_paths` — `//server/share` block，reason 含 machine path。
- [x] CF-024 `test_shared_config_rejects_file_uri_without_rejecting_https` — `file:///` block 且 `https://` 放行。

### 2.4 merge / subset schema / local 约束

- [x] CF-025 `test_valid_shared_config_loads_as_deterministic_read_only_effective_config` — 两次加载 to_json 一致；path_bindings={}；写尝试抛 TypeError（只读）。
- [x] CF-026 `test_optional_local_bindings_merge_without_mutating_shared_policy` — local path_bindings/cli_adapters 合并生效，shared protected_actions 不变。
- [x] CF-027 `test_local_config_cannot_override_shared_or_protected_policy` — local 试图改 workspace block，rule_ids=(SEM-003,HOOK-004)，recovery 含 path_bindings/cli_adapters。
- [x] CF-028 `test_shared_config_rejects_local_only_fields_instead_of_dropping_them` — shared 出现 path_bindings/cli_adapters block，rule_ids=(PORT-001,HOOK-004)。
- [x] CF-029 `test_local_config_accepts_env_names_but_rejects_secret_values` — argv 含 `--token=super-secret` block，不泄漏 secret，recovery 含 environment variable。
- [x] CF-030 `test_local_argv_cannot_split_secret_flag_from_its_value` — `--token <value>` 拆分 block。
- [x] CF-031 `test_local_argv_rejects_credential_file_flags` — `--token-file`/`--api-key_file=`/`--password-file`/`--secret_file=` 全 block，不泄漏。
- [x] CF-032 `test_local_credential_environment_names_follow_declared_pattern` — env name 不符 pattern block。
- [x] CF-033 `test_local_path_binding_must_be_absolute_and_declared` — 未声明 alias block，recovery 含 absolute/declared。
- [x] CF-034 `test_declared_local_path_binding_cannot_be_relative` — 声明 alias 但值相对 block，不泄漏相对路径。

---

## 3. 路径单元（PT）— `tests/harness/test_paths.py::PathIdentityTests`，21 项

### 3.1 target 校验（realpath / 穿越 / symlink / missing）

- [x] PT-001 `test_absolute_target_is_rejected_without_disclosure` — 绝对 target block，evidence=path:warehouse:target:absolute，不泄漏路径/secret-canary。
- [x] PT-002 `test_parent_traversal_is_rejected_even_when_it_returns_inside_root` — `nested/../model.sql` block，evidence=path:warehouse:nested/../model.sql:traversal。
- [x] PT-003 `test_missing_target_fails_closed_with_relative_location` — missing target block，evidence=...:missing。
- [x] PT-004 `test_internal_symlink_cannot_escape_the_configured_root` — 内部 symlink 指向外部 block，evidence=...:symlink-escape。
- [x] PT-005 `test_internal_symlink_is_rejected_even_when_target_stays_inside` — 内部 symlink 即使不逃逸也 block，evidence=...:symlink。
- [x] PT-006 `test_broken_symlink_target_fails_closed` — broken symlink block，evidence=...:broken-symlink。
- [x] PT-007 `test_directory_reference_rejects_nested_symlink_escape` — 目录引用内含逃逸 symlink block，evidence=path:warehouse:models/linked.txt:symlink-escape。
- [x] PT-008 `test_unreadable_target_is_a_sanitized_gate_error` — PermissionError 与 git probe 中途删除竞态都 fail-closed，evidence=...:unreadable，不泄漏 secret-canary。

### 3.2 alias / 根重叠

- [x] PT-009 `test_unknown_alias_fails_closed` — 未知 alias block，evidence=path:unknown_alias:alias:unknown。
- [x] PT-010 `test_malformed_unknown_alias_is_sanitized` — 恶意 alias（含路径）净化为 path:invalid-alias:alias:unknown，不泄漏。
- [x] PT-011 `test_identical_workspace_and_codebase_roots_are_rejected` — workspace==codebase root block，evidence=path:billing_app:root:overlap。
- [x] PT-012 `test_ancestor_or_descendant_roots_are_rejected_by_components` — codebase-in-workspace 与 workspace-in-codebase 双向 block（按 components 比较非字符串前缀）。
- [x] PT-013 `test_similar_foo_and_foobar_roots_do_not_overlap` — foo 与 foobar 不误判重叠，正常返回 reference。
- [x] PT-014 `test_codebase_root_symlink_is_rejected` — codebase root 是 symlink block，evidence=...:root:symlink。
- [x] PT-015 `test_codebase_root_must_be_a_directory` — codebase root 是文件 block，evidence=...:root:not-directory。
- [x] PT-016 `test_missing_codebase_root_fails_closed_without_leaking_path` — missing root block，evidence=...:root:missing。
- [x] PT-017 `test_unconfigured_codebase_root_fails_closed_with_recovery` — codebase 声明但 local 未绑定 block，rule_ids=(SCOPE-001,PORT-001,HOOK-004)，recovery 含 path binding。

### 3.3 reference 可移植性 / revision

- [x] PT-018 `test_external_file_has_portable_stable_content_reference` — 外部文件返回 alias/relative_path/content_sha256，两次 to_json 一致，不泄漏绝对路径。
- [x] PT-019 `test_directory_content_hash_is_stable_and_changes_with_content` — 目录 content_sha256 稳定，内容变则 hash 变；同字节不同目录 shape hash 不同。
- [x] PT-020 `test_clean_git_target_uses_commit_revision` — 干净 git 目标用 git_sha=HEAD；脏/未跟踪/ignored 目录退 content_sha256；fsmonitor 不被触发；无 index.lock。
- [x] PT-021 `test_external_root_cannot_supply_the_git_executable` — 外部 root 的 `./git` 不被调用（PATH 注入防护），返回 content_sha256。

---

## 4. Gate 单元（GT）— `tests/harness/test_gates.py::GateDecisionTests`，6 项

- [x] GT-001 `test_decision_rejects_an_unknown_status` — status="allow" 抛 ValueError。
- [x] GT-002 `test_unexpected_exception_becomes_a_sanitized_block` — `fail_closed` 把异常转 block，不泄漏 secret/机器路径。
- [x] GT-003 `test_gate_error_exposes_only_its_block_decision` — `GateError(decision)` 的 str==to_json，且 .decision 即原 decision。
- [x] GT-004 `test_public_output_redacts_paths_secrets_and_url_queries` — block 输出对 /Users/、?token=、api_key=、password=、C:\ 路径统一替换 [REDACTED_PATH]/[REDACTED_SECRET]/[REDACTED_QUERY]。
- [x] GT-005 `test_pass_and_warn_are_explicit_public_outcomes` — `pass_`/`warn` 显式 status。
- [x] GT-006 `test_block_decision_serializes_required_fields_stably` — block 序列化去重 rule_ids/evidence_refs，sort_keys 稳定。

> Gate fail-closed 行为另由 SC-003..007、CF-* block 用例、PT-* block 用例、HK fail-closed 用例共同覆盖。

---

## 5. Hook contract（HK）— `tests/harness/test_hooks.py::SessionStartHookTests`，9 项

### 5.1 SessionStart good/bad/malicious JSON

- [x] HK-001 `test_valid_event_returns_one_diagnostic_without_blocking_the_session` — 合法 event exit=0、stdout 一条 diagnostic、status=BLOCKED、production_ready=False（设计预期）、不泄漏 transcript-secret-canary 与 workspace 路径。
- [x] HK-002 `test_invalid_serialization_and_oversized_input_fail_closed` — 非 UTF-8/截断 JSON/重复 key/超 64KiB 四类恶意输入 exit=2、stdout 空、stderr JSON block 含 HOOK-004、≤512B、不泄漏 secret-canary/路径。
- [x] HK-003 `test_event_shape_and_workspace_identity_fail_closed` — 非 object/缺 source/错 event/unknown shared_config/绝对 claude_executable/错 source 类型/cwd 不匹配/cwd 穿越/cwd symlink 9 类形状错误全 block。

### 5.2 Python 绑定 / PATH 安全

- [x] HK-004 `test_settings_command_never_resolves_python_from_inherited_path` — settings command 不从 PATH 解析 python（fake python3 不被调用），exit=0。
- [x] HK-005 `test_invalid_python_bindings_fail_before_any_interpreter_executes` — CHATBI_PYTHON missing/relative/non-executable/workspace/business_root 五类绑定全 block，evidence=hook:session-start:python-binding，且 fake python 不执行。
- [x] HK-006 `test_business_root_aliases_are_resolved_before_python_executes` — parent_symlink/root_symlink/dot_component/duplicate_separator/platform_canonical_alias 五种 business root 拼写统一解析并 block 业务侧 python，不执行 marker。

### 5.3 库加载 / doctor / 文档合同

- [x] HK-007 `test_library_import_exception_fails_closed_without_a_traceback` — lib 加载抛异常 exit=2 block，rule_ids=(HOOK-001,HOOK-004)，stderr 无 traceback、无 secret、无路径。
- [x] HK-008 `test_doctor_nonzero_and_timeout_never_report_chatbi_available` — doctor 非 0（not_logged_in）与 timeout 两种都 status=BLOCKED、chatbi_commands_available=False、doctor check=block、不泄漏 secret-canary。
- [x] HK-009 `test_settings_and_compatibility_document_only_the_verified_contract` — 见 SC-011（同用例，静态合同视角）。

---

## 6. 离线纵向（DG）— `tests/harness/test_diagnostics.py::InitDiagnosticTests`，8 项

- [x] DG-001 `test_missing_domain_contract_blocks_before_configuration` — 缺域模型在 config 之前 block，单 check domain_contract，evidence=contract:domain-model，不泄漏 workspace。
- [x] DG-002 `test_missing_configuration_is_a_structured_blocked_result` — 缺 shared config 给 domain_contract+configuration 两 check；外部/相对穿越/symlink config 全 block 于 configuration_path，不泄漏 secret-canary/绝对路径。
- [x] DG-003 `test_valid_install_reaches_portable_path_diagnostics` — 合法安装跑到 paths check=pass，path_references 顺序=[warehouse, alpha_app, zeta_app]（按 shared 声明顺序），reordered shared 产生相同 to_json（顺序无关），warehouse reference content_sha256 64 位，不泄漏 workspace。
- [x] DG-004 `test_missing_production_capabilities_are_explicitly_blocked` — 能力缺失时 BLOCKED，blocked_ids 含 claude_version/claude_doctor/claude_login/sandbox/adapters/governance_owner/pii_policy/release_threshold，pending_configuration=sorted(blocked_ids)，有 recovery_actions，不泄漏 workspace。
- [x] DG-005 `test_ready_install_can_report_pass_or_compatible_version_warning` — ready 能力 PASS（production_ready 仍 False，设计预期）； newer 版本 WARN，claude_version check=warn recovery 含 compatibility。
- [x] DG-006 `test_local_probe_times_out_and_never_returns_raw_command_output` — probe 用绝对可执行、capture_output=True、shell=False；timeout/非 0/无 login 证据三种 runner 都不泄漏 secret-canary/绝对 canary，doctor_status=timeout/not_logged_in/pass 对应。
- [x] DG-007 `test_unconfirmed_path_cannot_supply_the_claude_probe_executable` — untrusted bin 的 claude 不被调用（marker 不创建）；shutil.which 抛异常 fail-closed 于 capability_probe check，不泄漏 secret-canary。
- [x] DG-008 `test_init_command_and_minimal_docs_state_the_current_contract` — 见 SC-012（同用例，静态合同视角）。

---

## 7. 兼容性（CP）— 复用上述用例的兼容性视角

- [x] CP-001 version probe — `DG-005`/`DG-006` 验证 claude_version 提取与 WARN 兼容阈值；`HK-008` 验证 doctor 返回非 0 时 version 仍可取但不报 available。
- [x] CP-002 doctor probe — `HK-008`（not_logged_in/timeout）、`DG-006`（timeout/not_logged_in/pass）、`DG-007`（which 异常）覆盖 doctor_status 五态：pass/warn-ish/timeout/not_logged_in/unavailable。
- [x] CP-003 platform probe — `SC-011` 验证 compatibility.md 含 "fixed OS"、"real paths"、"no PATH fallback"；`HK-004`/`HK-005` 验证无 PATH fallback、必须显式 CHATBI_PYTHON。
- [x] CP-004 git probe — `PT-020`（干净 git 用 git_sha、脏退 content_sha256、fsmonitor 不触发）、`PT-021`（外部 root 不供应 git 可执行）覆盖 git 探测与降级；`DG-003` 验证无 git 时 content_sha256 可用。
- [x] CP-005 登录/沙箱不冒充 verified E2E — `HK-001`/`DG-004`/`DG-005` 验证 production_ready 硬编码 False（Cycle 1 设计预期）；`SC-011` 验证文档含 NOT YET EXERCISED / PRODUCTION BLOCKER，不含 "fixture is production"。

---

## 执行命令汇总（与 dev-cycle-1.md §Task 6 一致）

```text
python3 -B -m unittest tests.harness.test_config tests.harness.test_paths tests.harness.test_hooks
python3 -B -m unittest discover -s tests/harness -p 'test_*.py'
find CLAUDE.md CONTEXT.md .claude docs/harness tests/harness -type f -print
grep -rnE '/Users/|BEGIN .*PRIVATE KEY|api[_-]?key|token[[:space:]]*[:=]' CLAUDE.md CONTEXT.md .claude docs/harness
grep -rhoE '[A-Z]{2,5}-[0-9]{3}' CLAUDE.md CONTEXT.md .claude docs/harness | sort -u
```

## 统计

- 自动化用例：85（test_contract 7 + test_config 34 + test_paths 21 + test_gates 6 + test_hooks 9 + test_diagnostics 8）
- 静态检查条目：12（SC-008..SC-012 命令/文档 + 各模块 fail-closed 静态视角）
- 合计清单条目：85 自动化 + 4 静态命令（SC-008/009/010 + 复用）= 见报告汇总
