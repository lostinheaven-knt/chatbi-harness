# ChatBI Harness Cycle 1 Feature Flow (v2)

STATUS: CODE_AS_READ on 2026-07-22. Every call chain below is read from the
checked-in source in this development directory. Line references are
`file:line`. Where an entry or behavior does not exist in code, this document
says so explicitly instead of inventing a chain. This is not a restatement of
`docs/technical-design.md`; it records what the code actually does in Cycle 1.

## Changelog from v1

This v2 corrects three documentation inaccuracies identified in
`docs/optimization-checklist-v1.md` (P1-1). No code changed; all `file:line`
references are identical to v1.

- §0: added `docs/harness/README.md` to the source-file list (delivered as
  Task 5, 211 lines, `STATUS: CODE_AS_READ on 2026-07-22`).
- §9 gap 10: `docs/harness/README.md` is now present; no longer a gap. v1 was
  written before the README was delivered.
- §10.3: file inventory count corrected 42 -> 45 (README now included; the raw
  `find` also counts 5 workspace-tooling files under `.claude/` that are not
  Cycle 1 deliverables). Harness docs count corrected 3 -> 5.

All other sections are unchanged from v1.

## 0. Scope of this document

Cycle 1 delivers one vertical slice: a governed root contract, an explicit
`/chatbi-init` diagnostic, a shared/local configuration loader, realpath-based
path identity checks, a unified pass/warn/block gate model, and a thin
`SessionStart` hook that reuses the same diagnostic core. Analysis, maintenance,
evaluation, correction, real sandbox enforcement, real adapter connections, and
real Claude E2E are out of Cycle 1 scope and are listed as design gaps in
section 9.

The flows below are read from these source files:

- `CLAUDE.md`, `CONTEXT.md`, `.claude/rules/00-domain-contract.md`,
  `.claude/rules/10-security.md`, `.claude/rules/20-completion.md`
- `.claude/commands/chatbi-init.md`
- `.claude/lib/chatbi_harness/{__init__,config,paths,gates,diagnostics}.py`
- `.claude/hooks/session_diagnose.py`, `.claude/hooks/session_diagnose`,
  `.claude/hooks/python_binding_launcher.py`
- `.claude/settings.json`
- `tests/harness/test_{config,paths,gates,contract,hooks,diagnostics}.py`
- `docs/harness/README.md` (Cycle 1 harness documentation: entry point, scope,
  document map)

## 1. Flow A: Root contract load and static validation

### Entry

Claude Code loads `CLAUDE.md` as the workspace root contract. The contract
itself directs further loading:

- `CLAUDE.md:17-19` instructs loading `CONTEXT.md` for stable vocabulary and
  `.claude/rules/` for detailed conditions, and to load domain references only
  when their route applies.
- `CLAUDE.md:4-7` makes `docs/chatbi-harness-domain-model.md` a hard
  precondition: if it is missing, unreadable, or conflicts, the Agent must stop.

### Call chain

```text
CLAUDE.md:4-7   (read domain model before changing any Harness artifact)
  -> CLAUDE.md:17-19   (load CONTEXT.md + .claude/rules/*)
     -> CONTEXT.md:1-52   (stable entity vocabulary, no procedures)
     -> .claude/rules/00-domain-contract.md:1-42   (REQ/SEM/RAW/SRC/DOC/PORT conditions)
     -> .claude/rules/10-security.md:1-29           (SCOPE/SEC conditions)
     -> .claude/rules/20-completion.md:1-48         (QLT/REV/ANS/EVAL/ABL/FBK/HOOK conditions)
```

The deterministic check that enforces this contract shape lives in the library,
not in prose:

```text
gates.py:170-233  validate_domain_contract(workspace_root)
  -> gates.py:173-180   domain model file must exist, else block HOOK-004
  -> gates.py:181-208   for each artifact in gates.py:30-36 (_CONTRACT_ARTIFACTS):
       gates.py:186-192    missing artifact -> block HOOK-004
       gates.py:194-200    CLAUDE.md > 200 lines -> block HOOK-004
       gates.py:201-207    sanitize_text mismatch (secret/path) -> block HOOK-004
       gates.py:208        collect referenced rule IDs
  -> gates.py:212-219   unknown rule IDs not in domain model -> block HOOK-004
  -> gates.py:220-227   governed rule IDs missing from contract -> block HOOK-004
  -> gates.py:228-233   pass HOOK-004
```

### Branch conditions

- The 5 contract artifacts are hard-coded at `gates.py:30-36`: `CLAUDE.md`,
  `CONTEXT.md`, and the three rules files. No other file counts as a contract
  artifact.
- Rule ID extraction uses `gates.py:26-29 _RULE_ID`, which matches only
  `SCOPE|SEC|REQ|SEM|RAW|SRC|DOC|PORT|QLT|REV|ANS|EVAL|ABL|FBK|HOOK - \d{3}`.
  `META-001..009` and the `FM-*` failure modes are governing context, not
  gate-enforced rule IDs; they are intentionally excluded from the 46-count
  executable set.

### Data flow

`validate_domain_contract` reads each artifact's text, redacts it with
`gates.py:39-45 _sanitize_text`, and compares the redacted text to the original.
If they differ, the artifact contained a secret, query value, or absolute path
and the contract fails closed. The union of rule IDs found across the 5
artifacts must equal the set found in the domain model.

### Error handling

Every failure returns a `GateDecision` with `status="block"`, `rule_ids`,
sanitized `evidence_refs`, `reason`, and `recovery` (`gates.py:108-123`). No
raw file content or machine path reaches the decision because `_sanitize_text`
runs in `GateDecision.__post_init__` (`gates.py:62-72`). Unexpected exceptions
become a block via `gates.py:153-167 fail_closed`.

### Evidence

`tests/harness/test_contract.py:41-89` asserts the checked-in contract passes,
contains exactly 46 governed rule IDs, is covered 1:1 by the contract
artifacts, and `CLAUDE.md` is within 200 lines. `test_contract.py:90-171`
covers unknown-rule, missing-coverage, over-budget, machine-path/secret, and
missing-domain-model failures, all fail-closed and sanitized.

## 2. Flow B: `/chatbi-init` explicit diagnostic command

### Entry

`.claude/commands/chatbi-init.md` is the Claude Code slash command. Its
frontmatter (`chatbi-init.md:1-4`) declares the description and argument hint.
The body is the procedure an Agent follows.

### Call chain

```text
.claude/commands/chatbi-init.md:38-42   (procedure step 1)
  Agent calls chatbi_harness.run_init_diagnostic(shared_path, local_path)
    -> .claude/lib/chatbi_harness/__init__.py:9-10   (re-exports run_init_diagnostic)
       -> diagnostics.py:384-682  run_init_diagnostic(shared_config, local_config, ...)
```

The command explicitly forbids passing a Claude executable unless the user
confirmed its exact absolute path (`chatbi-init.md:38-42`); otherwise the
`claude_executable` keyword is omitted and only the system allowlist is used
(see Flow E).

### Branch conditions

- `chatbi-init.md:49-56` stop conditions: `BLOCKED` on contract/config/path
  failure, missing login/sandbox/owner/PII/threshold/adapter, or any unexpected
  probe failure converted to fail-closed. Cycle 1 `PASS` does not set
  `production_ready=true` (`chatbi-init.md:54-55`).
- `chatbi-init.md:58-62` output evidence: one JSON object with `schema_version`,
  `status`, `production_ready`, `checks`, `capabilities`, `path_references`,
  `pending_configuration`, `recovery_actions`. No credentials, PII, raw command
  output, or unnecessary absolute paths.

### Data flow

The command is a procedure description; it performs no computation itself. All
behavior is in `run_init_diagnostic` (Flow C). The command's role is to declare
inputs, preconditions, allowed changes, stop conditions, and rule IDs
(`chatbi-init.md:64-67`).

### Error handling

Errors surface as the diagnostic's structured checks (Flow C). The command
requires the Agent to present the exact top-level status and, if writing
`.chatbi/diagnostic.json`, to write the same `result.to_json()` returned to the
user (`chatbi-init.md:43-47`).

### Evidence

`tests/harness/test_diagnostics.py:470-503` asserts the command document
contains the required headings, the `claude_executable=confirmed_claude_path`
keyword contract, the `..`/symlink/absolute input restrictions, and the
PASS/WARN/BLOCKED status vocabulary.

## 3. Flow C: `run_init_diagnostic` core pipeline

### Entry

`diagnostics.py:384-390 run_init_diagnostic(shared_config, local_config=None, *,
capability_probe=None, claude_executable=None) -> DiagnosticResult`

### Call chain (ordered, fail-closed at each stage)

```text
diagnostics.py:394-405   STAGE 1 domain contract
  diagnostics.py:395       workspace_root = Path.cwd().resolve(strict=True)
  diagnostics.py:396       domain_decision = validate_domain_contract(workspace_root)  [Flow A]
  diagnostics.py:403       append DiagnosticCheck("domain_contract", domain_decision)
  diagnostics.py:404-405   if block -> return DiagnosticResult(checks) early

diagnostics.py:407-434   STAGE 2 configuration input path
  diagnostics.py:407-411   _validate_configuration_path(workspace_root, shared_config, "shared")
  diagnostics.py:412-418   if shared ok and local given: _validate_configuration_path(..., "local")
  diagnostics.py:419-421   if any path blocked -> append "configuration_path", return early
  diagnostics.py:422-434   if shared unresolved -> fail_closed, return early

diagnostics.py:436-463   STAGE 3 effective configuration
  diagnostics.py:437       config = load_effective_config(validated_shared, validated_local)  [Flow D]
  diagnostics.py:438-440   except GateError -> append "configuration", return early
  diagnostics.py:441-452   except Exception -> fail_closed, return early
  diagnostics.py:453-463   append "configuration" pass

diagnostics.py:464-500   STAGE 4 portable path references
  diagnostics.py:465-468   aliases = workspace id + sorted codebase aliases
  diagnostics.py:470-473   for each alias: resolve_path_reference(config, alias=alias, target=".")  [Flow E]
  diagnostics.py:474-476   except GateError -> append "paths", return early
  diagnostics.py:477-489   except Exception -> fail_closed, return early
  diagnostics.py:490-500   append "paths" pass

diagnostics.py:501-535   STAGE 5 capability probe
  diagnostics.py:502-517   if capability_probe is None:
       diagnostics.py:503-509  forbidden_roots = workspace + all bound business roots
       diagnostics.py:510-513  _discover_claude_executable(forbidden_roots, claude_executable)
       diagnostics.py:514-517  probe_local_capabilities(claude_executable=discovered, safe_path=safe_path)
  diagnostics.py:518-522   else: call injected probe, force evidence_source="synthetic"
  diagnostics.py:523-535   except Exception -> fail_closed "capability_probe", return early

diagnostics.py:537-681   STAGE 6 capability checks (only reached if probe succeeded)
  diagnostics.py:560-581   claude_version  (pass if 2.1.216, warn if other, block if unavailable)
  diagnostics.py:582-590   claude_doctor   (pass only if doctor_status=="pass")
  diagnostics.py:591-599   claude_login    (pass only if logged_in is True)
  diagnostics.py:600-608   sandbox         (pass only if sandbox_available is True)
  diagnostics.py:610-627   adapters        (pass only if configured adapters subset of available)
  diagnostics.py:629-639   governance_owner(pass only if default_domain_owner or metrics owner set)
  diagnostics.py:641-651   pii_policy      (pass only if pii_policy_ref AND restricted_disclosure set)
  diagnostics.py:652-664   release_threshold (pass only if threshold and threshold_owner set)
  diagnostics.py:666-681   revision_evidence (pass; content_sha256 or git_sha noted)

diagnostics.py:682   return DiagnosticResult(checks, path_references, capabilities)
```

### Branch conditions (key)

- **Early return on first blocking stage.** Stages 1-4 each return immediately on
  a block, so a domain-contract failure never reaches config loading, and a
  config failure never reaches path resolution. This is visible at
  `diagnostics.py:404-405`, `419-421`, `438-440`/`451-452`, `474-476`/`488-489`.
- **Capability probe injection.** When `capability_probe` is provided (tests,
  offline fixtures), real subprocess probing is skipped
  (`diagnostics.py:518-522`). The snapshot's `evidence_source` is forced to
  `"synthetic"` (`diagnostics.py:522`) so synthetic evidence can never be
  mistaken for a local probe.
- **Claude version logic** (`diagnostics.py:560-581`): unavailable -> block
  (HOOK-002); exactly `2.1.216` -> pass; any other version -> warn, never pass.
- **Doctor/login/sandbox** all block on `None`/non-pass, never silently pass
  (`diagnostics.py:582-608`).

### Data flow

`Path.cwd()` is the single source of the Workspace root
(`diagnostics.py:395`). The diagnostic never reads an absolute root from
configuration; `workspace.root` is schema-locked to `"."` (`schema:36`). Bound
business roots come from `config["path_bindings"]` and are resolved to real
paths only to build the `forbidden_roots` set for executable discovery
(`diagnostics.py:503-509`); they are not persisted in the output. The output is
assembled by `DiagnosticResult.to_dict` (`diagnostics.py:359-373`).

### Error handling

- `GateError` from config or paths carries an already-sanitized blocking
  `GateDecision` and is appended directly (`diagnostics.py:438-440`,
  `474-476`).
- Any other `Exception` becomes a `fail_closed` block with HOOK-004 (and
  stage-specific rule IDs) at `diagnostics.py:441-452`, `477-489`, `523-535`.
- `production_ready` is hard-coded `False` (`diagnostics.py:336-339`) with the
  inline comment that Cycle 1 has no closed-loop proof for governed policy,
  sandbox, or adapters. No code path can set it true in Cycle 1.
- `status` aggregates checks: block present -> `BLOCKED`; else warn present ->
  `WARN`; else `PASS` (`diagnostics.py:327-334`).

### Evidence

`tests/harness/test_diagnostics.py:82-101` (missing domain contract blocks
before config), `103-146` (missing/unsafe config paths blocked and sanitized),
`148-247` (valid install reaches portable path diagnostics, alias ordering is
deterministic), `249-295` (missing capabilities explicitly blocked), `297-340`
(ready install reports PASS or version WARN, production_ready still false),
`342-421` (probe timeout/nonzero never reports available, no raw output leaks),
`423-468` (unconfirmed PATH cannot supply the Claude probe executable).

## 4. Flow D: Configuration load, schema subset, and cross-field validation

### Entry

`config.py:385-388 load_effective_config(shared_path, local_path=None) ->
EffectiveConfig`

### Call chain

```text
config.py:391             _load_json(shared_path, "shared")
  -> config.py:86-144       read bytes; size limit 256 KiB (config.py:17,96-102);
                             reject duplicate keys (config.py:77-83,109-115),
                             non-UTF-8 (config.py:116-122), malformed JSON
                             (config.py:123-129), non-finite numbers
                             (config.py:130-136), non-object top level
                             (config.py:137-143)
config.py:392-401         reject local-only fields (path_bindings, cli_adapters) in shared
config.py:402-408         _contains_matching_string(data, (_ABSOLUTE_PATH, _SECRET_VALUE))
                             -> reject machine paths/secrets in shared (SEC-003, PORT-001)
config.py:409             _load_json(local_path, "local") if local given
config.py:410-417         reject unknown local fields (only path_bindings, cli_adapters allowed)
config.py:418-426         reject secret values / secret argv in local
config.py:427-428         merge local path_bindings + cli_adapters into shared data
config.py:429             _validate_effective_data(data)
  -> config.py:267-276     _validate_schema against .claude/schemas/chatbi-harness.schema.json
  -> config.py:278-287     protected actions mandatory (SEM-003)
  -> config.py:288-294     runtime.fail_if_sandbox_unavailable must be true (SEC-001)
  -> config.py:295-308     release_threshold requires threshold_owner (EVAL-004)
  -> config.py:309-322     fixture mode cannot be production fallback (PORT-001)
  -> config.py:324-333     business codebase path_ref values unique (SCOPE-001, PORT-001)
  -> config.py:334-350     path_bindings must reference declared path_refs and be absolute
config.py:430             return EffectiveConfig._from_dict(data)  (frozen, read-only)
```

### Schema subset actually implemented

The schema file self-documents its implemented keyword subset at
`schema:6-18` (`x-implemented-keywords`): `type`, `required`, `properties`,
`additionalProperties`, `enum`, `pattern`, `propertyNames`, `items`,
`minItems`, `uniqueItems`, `minimum`. The validator is
`config.py:203-263 _validate_schema`; it does not implement the full JSON
Schema standard and the schema description (`schema:5`) says so explicitly.

### Branch conditions

- Shared config may not contain `path_bindings` or `cli_adapters`
  (`config.py:392-401`); local config may contain only those two keys
  (`config.py:410-417`). This enforces the shared/local layer split.
- `_ABSOLUTE_PATH` (`config.py:19-25`) matches POSIX absolute, Windows drive,
  UNC, forward-slash UNC, and `file:` URIs. `_SECRET_VALUE` (`config.py:26-29`)
  matches `api_key|token|password|secret = value` and `sk-`/`pk-` prefixed
  tokens. Shared config is scanned for both; local config for secrets only
  (local may legitimately hold absolute path bindings).
- `_contains_secret_argv` (`config.py:174-186`) rejects `--token`,
  `--api-key`, `--password`, `--secret` and their `_file` variants as CLI argv,
  so a secret flag cannot be split from its value.

### Data flow

Merged data is frozen recursively via `config.py:147-152 _freeze`
(`MappingProxyType` for dicts, tuples for lists) so the returned
`EffectiveConfig` (`config.py:353-382`) is read-only. `to_dict`/`to_json`
(`config.py:372-382`) thaw a copy for serialization. `to_json` uses
`allow_nan=False` and sorted keys for stable output.

### Error handling

All failures raise `GateError` built by `config.py:60-74 _config_gate_error`,
each carrying rule IDs (HOOK-004 plus the domain rule), a sanitized
`evidence_ref` like `config:shared`/`config:schema`/`config:local`, a reason,
and a recovery. No absolute path or secret value reaches the decision (verified
by `test_config.py` assertions that canary strings are absent from
`decision.to_json()`).

### Evidence

`tests/harness/test_config.py:61-90` (checked-in examples + 8 invalid fixtures
all rejected), `92-105` (missing config), `121-143` (non-finite numbers),
`145-167` (invalid alias), `169-192` (duplicate path_ref), `218-312` (machine
paths/secrets/UNC/file-URI rejected while HTTPS allowed), `314-363` (missing
field, schema_version, unknown field), `365-428` (size limit, malformed JSON,
non-UTF-8, duplicate key), `449-510` (local merge cannot mutate shared
policy), `529-656` (env names accepted, secret values/flags rejected),
`720-845` (protected actions, sandbox policy, threshold owner, fixture mode).

## 5. Flow E: Path identity and portable references

### Entry

`paths.py:355-360 resolve_path_reference(config, *, alias, target) ->
PortablePathReference`

### Call chain

```text
paths.py:363             roots = _configured_roots(config)
  -> paths.py:147-159      workspace root = Path.cwd() resolved; each codebase root from path_bindings
  -> paths.py:160-170      unconfigured codebase root (no binding) -> block
  -> paths.py:171-188      pairwise overlap check by resolved components (==, is_relative_to)
paths.py:364-371         unknown alias -> block
paths.py:373-389         absolute target (POSIX or Windows) -> block; ".." in parts -> block
paths.py:390-398         per-component _reject_symlink (paths.py:192-231):
                            broken symlink -> block; symlink escaping root -> block; any symlink -> block
paths.py:399-425         candidate.resolve(strict=True); must stay is_relative_to(root)
paths.py:426-450         inspect target type; for directories, rglob + _reject_symlink every child
paths.py:451             relative_path = resolved.relative_to(root).as_posix()
paths.py:452             git_revision = _git_revision(root, relative_path) if target is a file
paths.py:453-462         revision = git_revision or _content_sha256(resolved_target)
paths.py:463-468         return PortablePathReference(alias, relative_path, revision, revision_kind)
```

### Branch conditions

- Roots are compared by resolved path components, never by string prefix
  (`paths.py:174-178`). `foo` and `foobar` do not overlap
  (`test_paths.py:488-516`).
- A configured codebase root that is a symlink is rejected at
  `paths.py:100-107` inside `_resolve_root` (`paths.py:81-125`), before any
  target is examined.
- Git is only invoked through a trusted allowlist: `paths.py:31-58
  _resolve_trusted_git` resolves `git` only from absolute components of
  `os.defpath`, verifies it is a real executable file, and caches the result.
  An external `git` on `PATH` cannot supply the executable
  (`test_paths.py:975-1021`).

### Data flow

`PortablePathReference` (`paths.py:128-144`) carries only `alias`,
`relative_path`, `revision`, `revision_kind`. No absolute root is stored.
`revision_kind` is `"git_sha"` only when `_git_revision` returned a clean
tracked commit (`paths.py:287-352`: HEAD verified, file tracked, no
diff-files/diff-index/untracked changes); otherwise `"content_sha256"`
(`paths.py:248-284`). For directories, `_content_sha256` walks `rglob("*")`,
skips `.git`, frames files with their content hash and directories with their
relative name, and re-checks the directory identity afterward to detect
mid-scan mutation (`paths.py:277-284`).

### Error handling

All failures go through `paths.py:61-78 _path_error`, which sanitizes the alias
(`paths.py:70`: an alias not matching `_ALIAS` becomes `invalid-alias`) and
builds a block decision with rule IDs `("SCOPE-001", "PORT-001", "HOOK-004")`
(overridden for unconfigured roots). Evidence refs are of the form
`path:{alias}:{location}:{category}` and never contain the absolute root or
canary (`test_paths.py` asserts this for every negative case).

### TOCTOU boundary

`paths.py:1-5` documents that Cycle 1 is a point-in-time check. The module is
re-callable, but continuous per-operation revalidation before each tool use is
explicitly deferred to Cycle 2. `test_paths.py:735-783` covers a target
unreadable mid-resolution and a file removed during the git probe, both
fail-closed.

### Evidence

`tests/harness/test_paths.py:105-134` (absolute target), `136-166` (traversal),
`168-197` (missing target), `199-303` (symlink escape, internal symlink,
broken symlink), `305-341` (nested directory symlink escape), `343-399`
(unknown/malformed alias sanitized), `401-486` (identical/ancestor/descendant
roots rejected; `foo`/`foobar` do not overlap), `518-641` (root symlink,
non-directory, missing, unconfigured roots), `688-733` (portable stable
content reference round-trip), `785-839` (directory content hash stability and
change detection), `841-973` (clean git target uses commit SHA; dirty/untracked
falls back to content hash; fsmonitor not invoked), `975-1021` (external git
cannot supply the executable).

## 6. Flow F: Unified gate model (GateDecision / GateError)

### Entry

All Cycle 1 failures and successes are expressed as `GateDecision`
(`gates.py:52-141`). Blocking decisions are raised as `GateError`
(`gates.py:143-150`).

### Call chain

```text
gates.py:74-123    GateDecision.pass_ / warn / block  (constructors)
gates.py:62-72     __post_init__: enforce status in {pass,warn,block},
                     _unique rule_ids/evidence_refs, _sanitize_text reason/recovery/evidence
gates.py:39-45     _sanitize_text: redact URL queries, named secrets, bearer tokens,
                     prefixed secrets, POSIX absolute paths, Windows absolute paths
gates.py:143-150   GateError(decision): requires status=="block"; message = decision.to_json()
gates.py:153-167   fail_closed(error, ...): any unexpected exception -> block HOOK-004
gates.py:125-140   to_dict / to_json: stable sorted serialization
```

### Branch conditions

- A `GateError` constructed from a non-block decision raises `ValueError`
  (`gates.py:147-148`); the exception boundary only carries blocking decisions.
- Sanitization is mandatory in `__post_init__`, so even a caller that forgets
  to sanitize gets redacted output. If redaction changes the text, the original
  sensitive value is gone from the serialized form.

### Data flow

`rule_ids` and `evidence_refs` are de-duplicated preserving first-seen order
(`gates.py:48-49 _unique`, applied at `gates.py:65-70`). `to_json`
(`gates.py:134-140`) uses `ensure_ascii=False`, compact separators, and sorted
keys, so identical decisions serialize identically.

### Error handling

This is the error model. `fail_closed` (`gates.py:153-167`) is the single
escape hatch for unexpected exceptions across config, paths, diagnostics, and
the hook. It never returns pass or warn.

### Evidence

`tests/harness/test_gates.py:17-25` (unknown status rejected), `27-38`
(exception sanitized, no secret/path leak), `40-52` (GateError exposes only its
decision), `53-76` (paths/secrets/URL queries redacted), `78-93` (pass/warn
explicit), `95-114` (stable serialization with de-duplication).

## 7. Flow G: SessionStart hook (settings -> shell -> binding validator -> Python entry)

### Entry

`.claude/settings.json:1-15` maps the `SessionStart` event. The only matcher is
`startup|resume|clear|compact` (`settings.json:5`); the only command is the
Workspace-relative `.claude/hooks/session_diagnose` (`settings.json:9`). There
are no other Hook events mapped.

### Call chain

```text
.claude/settings.json:3-13   SessionStart matcher -> command ".claude/hooks/session_diagnose"
  -> .claude/hooks/session_diagnose:9-11   (shell) verify /usr/bin/python3 executable,
                                            launcher file exists and is not a symlink
  -> .claude/hooks/session_diagnose:13     exec /usr/bin/python3 -B -I .claude/hooks/python_binding_launcher.py
     -> python_binding_launcher.py:115-143 main()
        -> python_binding_launcher.py:117          workspace_root = Path.cwd().resolve(strict=True)
        -> python_binding_launcher.py:118          _load_business_roots(workspace_root)  (lines 50-91)
        -> python_binding_launcher.py:119          _validated_python(workspace_root, business_roots)  (lines 94-112)
        -> python_binding_launcher.py:120-123      hook file must be a real regular file (no symlink)
        -> python_binding_launcher.py:133-137      os.execve(python, [python, "-B", "-I", hook], safe_env)
           -> .claude/hooks/session_diagnose.py:192-229 main()
              -> session_diagnose.py:194-195   workspace_root + _validate_event(_read_event(), root)
              -> session_diagnose.py:197-201   run_init_diagnostic(shared, local if exists)  [Flow C]
              -> session_diagnose.py:202-217   build output JSON, write stdout, exit 0
              -> session_diagnose.py:219-229   HookInputError / Exception -> exit 2 sanitized
```

### Branch conditions

**Binding validator (`python_binding_launcher.py`):**
- `CHATBI_PYTHON` env must be set, absolute, no `..`, a real executable regular
  file, and must resolve outside both the Workspace and every configured
  Business root (`python_binding_launcher.py:94-112`). Any failure writes the
  fixed block JSON (`python_binding_launcher.py:14-26`) to stderr and exits 2.
- Business roots are loaded from `.claude/chatbi-harness.local.json` only
  (`python_binding_launcher.py:50-91`): the local config must not be a symlink,
  must be under 256 KiB, and each binding must be an absolute real directory
  that is not itself a symlink. Symlinked or relative roots fail closed.
- The hook file itself must not be a symlink (`python_binding_launcher.py:121-123`).
- The shell launcher has no PATH fallback: it uses only `/usr/bin/python3`
  (`session_diagnose:9,13`). Missing or non-executable `/usr/bin/python3` fails
  before any Python runs (`session_diagnose:4-7`).

**Event validation (`session_diagnose.py:104-174 _validate_event`):**
- Input is one JSON object (`session_diagnose.py:105-110`).
- Required fields (`session_diagnose.py:40-42`): `session_id`, `transcript_path`,
  `cwd`, `hook_event_name`, `source`, `model`. Missing any -> block
  (`session_diagnose.py:111-117`).
- Optional fields (`session_diagnose.py:43`): `permission_mode`, `agent_id`,
  `agent_type`. Any other field -> block (`session_diagnose.py:118-124`).
- `transcript_path` must be an absolute path with no `..` and bounded length
  (`session_diagnose.py:136-147`); it is validated but never read.
- `hook_event_name` must equal `SessionStart` (`session_diagnose.py:148-153`).
- `source` must be in `{startup, resume, clear, compact}`
  (`session_diagnose.py:154-160`).
- `cwd` must exactly equal the resolved Workspace root string
  (`session_diagnose.py:167-173`); a symlink-spelled cwd is rejected.
- stdin is capped at 64 KiB (`session_diagnose.py:39,73-79`); duplicate JSON
  keys are rejected (`session_diagnose.py:59-69,89`).

### Data flow

The hook never accepts a config path or executable from the event payload
(`test_hooks.py:504-528` confirms `shared_config`/`claude_executable` fields are
rejected as unknown fields). It hard-codes `.claude/chatbi-harness.json` as
shared and uses `.claude/chatbi-harness.local.json` only if it exists
(`session_diagnose.py:197-201`). The output adds a hook envelope
(`schema_version`, `hook_event_name`, `source`, `chatbi_commands_available`,
`diagnostic`) around the diagnostic (`session_diagnose.py:202-208`).
`chatbi_commands_available = diagnostic.status != "BLOCKED"`
(`session_diagnose.py:206`): a blocked diagnostic marks ChatBI commands
unavailable but still returns exit 0, so an ordinary non-ChatBI conversation is
not permanently blocked.

### Error handling

- Library import failure (`session_diagnose.py:15-36`): writes a block JSON
  with `HOOK-001, HOOK-004` to stderr and exits 2, with no traceback.
- `HookInputError` (`session_diagnose.py:51-56,183-189`): becomes a block with
  `SEC-003, HOOK-001, HOOK-004` and a category-specific evidence ref, written
  to stderr, exit 2 (`session_diagnose.py:219-220`).
- Any other `Exception` (`session_diagnose.py:221-229`): `fail_closed` with
  `SEC-003, HOOK-001, HOOK-004`, exit 2.
- stderr is bounded (`test_hooks.py:356` asserts `len(stderr) <= 512`); canary
  secrets and absolute workspace paths never appear in stdout or stderr.

### Evidence

`tests/harness/test_hooks.py:129-158` (settings command never resolves Python
from inherited PATH), `160-237` (invalid Python bindings fail before any
interpreter executes), `238-307` (business root aliases resolved before Python
executes), `309-329` (valid event returns one diagnostic without blocking the
session), `331-358` (invalid serialization/oversized input fail closed),
`360-412` (doctor nonzero/timeout never report available), `414-445` (library
import exception fails closed without a traceback), `447-492` (settings and
compatibility docs document only the verified contract), `494-542` (event shape
and workspace identity fail closed).

## 8. Cross-cutting: sanitization and fail-closed guarantees

Across all flows, every public decision passes through `GateDecision`
sanitization (`gates.py:62-72`), and every unexpected exception passes through
`fail_closed` (`gates.py:153-167`). The redaction patterns
(`gates.py:12-25`) cover POSIX absolute paths under common roots
(`/Users`, `/private`, `/tmp`, `/var`, `/home`, `/opt`, `/etc`, `/root`),
Windows absolute paths, URL query strings, named secrets, bearer tokens, and
prefixed secrets. No Cycle 1 code path returns a raw exception message, raw
subprocess output, or an absolute machine root to the user.

The probe layer retains only normalized fields: `probe_local_capabilities`
(`diagnostics.py:104-209`) truncates combined stdout+stderr to 8192 chars,
extracts only a semver version string, and classifies doctor status into
`{pass, not_logged_in, timeout, unavailable, error}`. Raw command output,
environment values, and absolute executable paths are not persisted in the
`CapabilitySnapshot` (`diagnostics.py:76-88`).

## 9. Known design gaps (Cycle 1 does not implement these)

These are read from code comments, explicit `False` returns, and the
compatibility/installation docs. They are future hard gates, not current
behavior, and must not be treated as implemented.

1. **`production_ready` is always false.** `diagnostics.py:336-339` hard-codes
   `False` with the comment that Cycle 1 has no closed-loop proof for governed
   policy, sandbox, or adapters. No code path sets it true.
2. **No continuous TOCTOU enforcement.** `paths.py:1-5` states Cycle 1 is a
   point-in-time check; per-operation revalidation before each tool use is
   Cycle 2.
3. **Real Claude `SessionStart` Hook not yet E2E triggered.**
   `docs/harness/compatibility.md:35-46` records that the contract is an offline
   subprocess test; a real logged-in Claude process has not loaded these project
   settings or triggered SessionStart. Login, keychain, sandbox, and managed
   policy are not verified locally.
4. **No real sandbox enforcement.** The diagnostic checks whether
   `sandbox_available` is true (`diagnostics.py:600-608`) and requires
   `fail_if_sandbox_unavailable=true` in config (`config.py:288-294`), but no OS
   sandbox is actually run. Sandbox execution is Cycle 2.
5. **No managed/CLI adapter implementation.** `available_adapters` in
   `CapabilitySnapshot` is always `()` from a local probe
   (`diagnostics.py:201-209`); the adapters check (`diagnostics.py:610-627`)
   blocks unless configured adapters are reported available, which cannot happen
   in Cycle 1 without an injected synthetic probe. Real adapter connections are
   Cycle 2.
6. **No external Codebase actual read.** `resolve_path_reference` validates
   identity and produces portable references but does not read file contents for
   analysis. Codebase read-for-context is Cycle 2.
7. **No PreToolUse write/execute blocking.** Only `SessionStart` is mapped
   (`settings.json:3-13`). Write/execute gate hooks are Cycle 2.
8. **No analysis, maintenance, evaluation, or correction commands.** The root
   contract routes them (`CLAUDE.md:72-79`) but only `/chatbi-init` is
   installed. Analysis/review is Cycle 3; model/knowledge maintenance is Cycle
   4; evaluation/correction is Cycle 5.
9. **Claude login unverified in the current environment.** The local probe
   reports `not_logged_in` or `unavailable` when no confirmed executable or
   login exists; the `claude_login` check blocks accordingly
   (`diagnostics.py:591-599`).
10. **`docs/harness/README.md` delivered (no longer a gap).**
    `docs/harness/README.md` is present (211 lines, substantive content: v1
    goals, Cycle 1 entry point, later-cycle status, hard boundaries, and a
    document map; header `STATUS: CODE_AS_READ on 2026-07-22`). v1 of this
    document listed it as absent; that was written before the README was
    delivered. The 5 files under `docs/harness/` are README, compatibility,
    configuration, installation, and rule-traceability.

## 10. Validation evidence

All commands were run from the Workspace root of this development directory
on 2026-07-22 using `python3 -B` (no bytecode written). `rg` was available;
equivalent `grep` is noted where relevant.

### 10.1 Targeted Cycle 1 tests

Command:
```text
python3 -B -m unittest tests.harness.test_config tests.harness.test_paths tests.harness.test_hooks
```
Actual result:
```text
................................................................
----------------------------------------------------------------------
Ran 64 tests in 12.523s

OK
```

### 10.2 Full discovery suite

Command:
```text
python3 -B -m unittest discover -s tests/harness -p 'test_*.py'
```
Actual result:
```text
.....................................................................................
----------------------------------------------------------------------
Ran 85 tests in 11.508s

OK
```

### 10.3 File inventory

Command:
```text
find CLAUDE.md CONTEXT.md .claude docs/harness tests/harness -type f -print
```
Actual result: 45 files listed. The Cycle 1 deliverables are all present (40
files): `CLAUDE.md`, `CONTEXT.md`, 3 rules, `chatbi-init.md`, `settings.json`,
`session_diagnose` + `session_diagnose.py` + `python_binding_launcher.py`, 4
lib modules + `__init__.py`, schema, shared/example/local-example configs, 9
config fixtures, 5 harness docs (README, compatibility, configuration,
installation, rule-traceability), 7 test files. `docs/harness/README.md` is
now present (211 lines, delivered as Task 5; this corrects the gap 10 reported
in v1 of this document). The remaining 5 files are workspace orchestration
tooling outside Cycle 1 scope (`.claude/agents/*` agent definitions,
`.claude/commands/orchestrate.md`, `.claude/SKILL-PATHS.md`). No `__pycache__`
or `*.pyc` residue was found (`find -name __pycache__ -o -name '*.pyc'`
returned nothing).

### 10.4 Secret / machine-path scan

Command:
```text
rg -n '/Users/|BEGIN .*PRIVATE KEY|api[_-]?key|token\s*[:=]' CLAUDE.md CONTEXT.md .claude docs/harness
```
Actual result (one match):
```text
.claude/fixtures/config/embedded-secret.json:4:  "business_codebases": {"source_app": {"description": "api_key=TEST_SECRET_CANARY_NOT_A_CREDENTIAL", ...}}
```
Interpretation: this is a deliberate negative-test fixture
(`embedded-secret.json`) whose canary value is literally
`TEST_SECRET_CANARY_NOT_A_CREDENTIAL`. It exists to verify that
`load_effective_config` rejects embedded secrets (`test_config.py:77-90` lists
it among the invalid fixtures). It is not a real secret, not a credential, and
not a machine path. No `/Users/` or `BEGIN ... PRIVATE KEY` match occurred.
Equivalent `grep -rnE` produces the same single hit. **Verdict: PASS** (no real
secret or machine path leaked).

### 10.5 Rule ID coverage scan

Command:
```text
rg -o '[A-Z]{2,5}-[0-9]{3}' CLAUDE.md CONTEXT.md .claude docs/harness | sort -u
```
Actual result: 46 unique rule IDs, exactly matching the 46 governed rule IDs in
`docs/chatbi-harness-domain-model.md`. A direct `diff` between the domain
model's rule set and the artifacts' rule set returned no differences
(`EXACT MATCH: 46/46`). No fabricated rule IDs (e.g. `SCOPE-999`) and no
missing rule IDs. The authoritative per-artifact coverage assertion is
`tests/harness/test_contract.py:47-89`, which requires the 5 contract artifacts
to cover all 46 governed IDs and passes as part of the 85-test suite.

### 10.6 CLAUDE.md line budget

`wc -l CLAUDE.md` returned 112 lines, within the ~200-line budget enforced by
`gates.py:194-200` and asserted by `test_contract.py:75`.
