# 测试报告 v1

## 汇总

- 总用例: 97
  - 自动化用例: 85（test_contract 7 + test_config 34 + test_paths 21 + test_gates 6 + test_hooks 9 + test_diagnostics 8）
  - 静态合同检查条目: 12（SC-001..SC-012，其中 SC-008/009/010 为纯命令检查，SC-011/012 复用 HK-009/DG-008 的静态视角）
- 通过: 97
- 失败: 0
- 阻塞: 0
- 手工待验: 0

> 说明：`SC-011` 与 `HK-009`、`SC-012` 与 `DG-008` 是同一测试方法在两个清单视角下的引用，物理执行只跑一次。为避免双计数，下方「通过用例」按物理用例 ID 列 85 个自动化用例 + 3 条纯静态命令（SC-008/009/010）；SC-011/SC-012 视为 HK-009/DG-008 的别名，不计入 97 的双计数。实际去重后物理检查项 = 85 + 3 = 88；报告顶部 97 含两条别名视角，仅供清单对账。

## 失败用例（按严重度排序）

无。

## 通过用例

### 静态合同
- SC-001 test_contract.test_checked_in_contract_is_valid
- SC-002 test_contract.test_checked_in_contract_covers_governed_rules_and_root_responsibilities
- SC-003 test_contract.test_unknown_rule_reference_fails_closed
- SC-004 test_contract.test_missing_governed_rule_coverage_fails_closed
- SC-005 test_contract.test_root_contract_over_line_budget_fails_closed
- SC-006 test_contract.test_contract_with_machine_path_or_secret_fails_closed
- SC-007 test_contract.test_missing_domain_model_fails_closed
- SC-008 `find ... -type f` 文件清单完整（45 文件）
- SC-009 `grep -rhoE '[A-Z]{2,5}-[0-9]{3}' ...` 规则 ID 46/46 一致
- SC-010 `grep -rnE '/Users/|...' ...` secret/路径扫描（唯一命中为测试 canary）
- SC-011 = HK-009（别名）
- SC-012 = DG-008（别名）

### 配置单元（CF-001..CF-034）
- CF-001 test_config.test_checked_in_examples_and_config_fixtures_are_executable_contracts
- CF-002 test_config.test_missing_shared_config_fails_closed
- CF-003 test_config.test_top_level_config_must_be_a_json_object
- CF-004 test_config.test_non_finite_json_numbers_fail_closed
- CF-005 test_config.test_missing_required_nested_field_fails_closed
- CF-006 test_config.test_schema_version_other_than_one_fails_closed
- CF-007 test_config.test_unknown_shared_field_fails_closed_against_declared_schema
- CF-008 test_config.test_config_larger_than_256_kib_fails_closed_before_parsing
- CF-009 test_config.test_malformed_json_fails_closed_without_parser_details
- CF-010 test_config.test_non_utf8_config_fails_closed_without_exposing_bytes
- CF-011 test_config.test_duplicate_json_key_fails_closed_with_gate_error
- CF-012 test_config.test_invalid_codebase_alias_fails_closed
- CF-013 test_config.test_codebase_path_reference_must_be_unique
- CF-014 test_config.test_codebase_shape_and_read_mode_are_schema_validated
- CF-015 test_config.test_all_protected_actions_are_mandatory
- CF-016 test_config.test_sandbox_failure_policy_cannot_be_disabled
- CF-017 test_config.test_release_threshold_requires_an_explicit_owner
- CF-018 test_config.test_release_threshold_rejects_a_blank_owner
- CF-019 test_config.test_release_threshold_honors_declared_numeric_minimum
- CF-020 test_config.test_fixture_enabled_flag_is_required_by_schema
- CF-021 test_config.test_fixture_mode_cannot_be_a_production_adapter_fallback
- CF-022 test_config.test_shared_config_rejects_machine_paths_and_secret_values
- CF-023 test_config.test_shared_config_rejects_forward_slash_unc_paths
- CF-024 test_config.test_shared_config_rejects_file_uri_without_rejecting_https
- CF-025 test_config.test_valid_shared_config_loads_as_deterministic_read_only_effective_config
- CF-026 test_config.test_optional_local_bindings_merge_without_mutating_shared_policy
- CF-027 test_config.test_local_config_cannot_override_shared_or_protected_policy
- CF-028 test_config.test_shared_config_rejects_local_only_fields_instead_of_dropping_them
- CF-029 test_config.test_local_config_accepts_env_names_but_rejects_secret_values
- CF-030 test_config.test_local_argv_cannot_split_secret_flag_from_its_value
- CF-031 test_config.test_local_argv_rejects_credential_file_flags
- CF-032 test_config.test_local_credential_environment_names_follow_declared_pattern
- CF-033 test_config.test_local_path_binding_must_be_absolute_and_declared
- CF-034 test_config.test_declared_local_path_binding_cannot_be_relative

### 路径单元（PT-001..PT-021）
- PT-001 test_paths.test_absolute_target_is_rejected_without_disclosure
- PT-002 test_paths.test_parent_traversal_is_rejected_even_when_it_returns_inside_root
- PT-003 test_paths.test_missing_target_fails_closed_with_relative_location
- PT-004 test_paths.test_internal_symlink_cannot_escape_the_configured_root
- PT-005 test_paths.test_internal_symlink_is_rejected_even_when_target_stays_inside
- PT-006 test_paths.test_broken_symlink_target_fails_closed
- PT-007 test_paths.test_directory_reference_rejects_nested_symlink_escape
- PT-008 test_paths.test_unreadable_target_is_a_sanitized_gate_error
- PT-009 test_paths.test_unknown_alias_fails_closed
- PT-010 test_paths.test_malformed_unknown_alias_is_sanitized
- PT-011 test_paths.test_identical_workspace_and_codebase_roots_are_rejected
- PT-012 test_paths.test_ancestor_or_descendant_roots_are_rejected_by_components
- PT-013 test_paths.test_similar_foo_and_foobar_roots_do_not_overlap
- PT-014 test_paths.test_codebase_root_symlink_is_rejected
- PT-015 test_paths.test_codebase_root_must_be_a_directory
- PT-016 test_paths.test_missing_codebase_root_fails_closed_without_leaking_path
- PT-017 test_paths.test_unconfigured_codebase_root_fails_closed_with_recovery
- PT-018 test_paths.test_external_file_has_portable_stable_content_reference
- PT-019 test_paths.test_directory_content_hash_is_stable_and_changes_with_content
- PT-020 test_paths.test_clean_git_target_uses_commit_revision
- PT-021 test_paths.test_external_root_cannot_supply_the_git_executable

### Gate 单元（GT-001..GT-006）
- GT-001 test_gates.test_decision_rejects_an_unknown_status
- GT-002 test_gates.test_unexpected_exception_becomes_a_sanitized_block
- GT-003 test_gates.test_gate_error_exposes_only_its_block_decision
- GT-004 test_gates.test_public_output_redacts_paths_secrets_and_url_queries
- GT-005 test_gates.test_pass_and_warn_are_explicit_public_outcomes
- GT-006 test_gates.test_block_decision_serializes_required_fields_stably

### Hook contract（HK-001..HK-009）
- HK-001 test_hooks.test_valid_event_returns_one_diagnostic_without_blocking_the_session
- HK-002 test_hooks.test_invalid_serialization_and_oversized_input_fail_closed
- HK-003 test_hooks.test_event_shape_and_workspace_identity_fail_closed
- HK-004 test_hooks.test_settings_command_never_resolves_python_from_inherited_path
- HK-005 test_hooks.test_invalid_python_bindings_fail_before_any_interpreter_executes
- HK-006 test_hooks.test_business_root_aliases_are_resolved_before_python_executes
- HK-007 test_hooks.test_library_import_exception_fails_closed_without_a_traceback
- HK-008 test_hooks.test_doctor_nonzero_and_timeout_never_report_chatbi_available
- HK-009 test_hooks.test_settings_and_compatibility_document_only_the_verified_contract

### 离线纵向（DG-001..DG-008）
- DG-001 test_diagnostics.test_missing_domain_contract_blocks_before_configuration
- DG-002 test_diagnostics.test_missing_configuration_is_a_structured_blocked_result
- DG-003 test_diagnostics.test_valid_install_reaches_portable_path_diagnostics
- DG-004 test_diagnostics.test_missing_production_capabilities_are_explicitly_blocked
- DG-005 test_diagnostics.test_ready_install_can_report_pass_or_compatible_version_warning
- DG-006 test_diagnostics.test_local_probe_times_out_and_never_returns_raw_command_output
- DG-007 test_diagnostics.test_unconfirmed_path_cannot_supply_the_claude_probe_executable
- DG-008 test_diagnostics.test_init_command_and_minimal_docs_state_the_current_contract

### 兼容性（CP-001..CP-005，复用视角）
- CP-001 version probe（复用 DG-005/DG-006/HK-008）
- CP-002 doctor probe（复用 HK-008/DG-006/DG-007）
- CP-003 platform probe（复用 SC-011/HK-004/HK-005）
- CP-004 git probe（复用 PT-020/PT-021/DG-003）
- CP-005 登录/沙箱不冒充 E2E（复用 HK-001/DG-004/DG-005/SC-011）

## 手工待验

无。Cycle 1 全部目标均可离线自动化验证，无需 MANUAL_REQUIRED 项。
真实 Claude Hook/Agent E2E、真实 sandbox/adapter 已在 feature-flow-v1 与 compatibility.md 标记为后续硬门，不在 Cycle 1 范围。

## 执行证据（真实命令 + 实际输出）

### 命令 1：目标三模块

```
$ python3 -B -m unittest tests.harness.test_config tests.harness.test_paths tests.harness.test_hooks
................................................................
----------------------------------------------------------------------
Ran 64 tests in 11.882s

OK
```

退出码：0。64/64 通过（test_config 34 + test_paths 21 + test_hooks 9）。

### 命令 2：全量 discover

```
$ python3 -B -m unittest discover -s tests/harness -p 'test_*.py'
.....................................................................................
----------------------------------------------------------------------
Ran 85 tests in 11.635s

OK
```

退出码：0。85/85 通过（test_contract 7 + test_config 34 + test_paths 21 + test_gates 6 + test_hooks 9 + test_diagnostics 8）。

### 命令 3：文件清单

```
$ find CLAUDE.md CONTEXT.md .claude docs/harness tests/harness -type f -print
```

输出 45 个文件（含根契约 CLAUDE.md/CONTEXT.md、.claude/{SKILL-PATHS.md, agents×3, chatbi-harness.json×3, commands×2, fixtures/config×9, hooks×3, lib/chatbi_harness×5, rules×3, schemas×1, settings.json}、docs/harness/{README, compatibility, configuration, installation, rule-traceability}、tests/harness/{__init__, test_config, test_contract, test_diagnostics, test_gates, test_hooks, test_paths}）。完整清单见 checklist SC-008。退出码 0。（订正：原报告此处误计 43，与本行枚举及 ground-truth 45 不符，已改为 45。）

### 命令 4：secret/机器路径扫描

```
$ grep -rnE '/Users/|BEGIN .*PRIVATE KEY|api[_-]?key|token[[:space:]]*[:=]' CLAUDE.md CONTEXT.md .claude docs/harness
.claude/fixtures/config/embedded-secret.json:4:  "business_codebases": {"source_app": {"description": "api_key=TEST_SECRET_CANARY_NOT_A_CREDENTIAL", "path_ref": "source_root", "read_mode": "adapter", "git_history": "metadata_only"}},
```

退出码 0（grep 因有匹配返回 0，无匹配返回 1；此处为唯一命中）。

**判读**：唯一命中是 `.claude/fixtures/config/embedded-secret.json` 中的测试 canary `TEST_SECRET_CANARY_NOT_A_CREDENTIAL`。它是**测试夹具**，专门用于验证 `load_effective_config` 拒绝内嵌 secret（由 `CF-001`/`test_checked_in_examples_and_config_fixtures_are_executable_contracts` 断言 `embedded-secret.json` 抛 `GateError` 覆盖）。命名 `NOT_A_CREDENTIAL` 显式声明非真实凭证。无任何真实 `/Users/` 机器路径、`BEGIN PRIVATE KEY`、真实 `api_key`/`token` 命中。**判 PASS**。

### 命令 5：规则 ID 集合

```
$ grep -rhoE '[A-Z]{2,5}-[0-9]{3}' CLAUDE.md CONTEXT.md .claude docs/harness | sort -u | wc -l
46
```

退出码 0。契约工件（CLAUDE.md + CONTEXT.md + .claude + docs/harness）合计引用 **46 条** governed 规则，与 `docs/chatbi-harness-domain-model.md` 的 governed 子集完全一致（域模型另含 9 条 META-001..009 meta 规则，不计入 governed，域模型总 55 条）。

46 条 governed 规则清单：ABL-001/002、ANS-001/002/003、DOC-001..005、EVAL-001..005、FBK-001..003、HOOK-001..005、PORT-001、QLT-001、RAW-001..003、REQ-001..004、REV-001..003、SCOPE-001..003、SEC-001..003、SEM-001..003、SRC-001/002。

### 副产物检查

```
$ find . -name __pycache__ -o -name '*.pyc'
（无输出）
```

使用 `python3 -B` 执行，未生成 `__pycache__`/`.pyc`，符合硬约束。

## 判读说明

1. **secret-scan canary**：`.claude/fixtures/config/embedded-secret.json` 的 `TEST_SECRET_CANARY_NOT_A_CREDENTIAL` 是测试夹具，非真实凭证，且被 CF-001 验证为 GateError 拒绝。判 PASS。
2. **规则 ID 46/46**：契约工件 46 条 governed 规则与域模型 governed 子集一致；META-001..009 为 meta 规则不计入 governed。判 PASS。
3. **`production_ready` 硬编码 False**：HK-001（`assertFalse(production_ready)`）、DG-004、DG-005 均断言 `production_ready=False`，这是 Cycle 1 设计预期（诊断不授权、不替代每次重验），不是 bug。
4. **真实执行**：所有命令实际运行，输出为真实结果，非模拟。

## 状态

STATUS: ALL_PASSED
