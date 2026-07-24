# ChatBI Harness Cycle 2 Feature Flow (v3)

STATUS: CODE_AS_READ on 2026-07-22. Every call chain below is read from the
checked-in source in this development directory. Line references are
`file:line`. Where an entry or behavior does not exist in code, this document
says so explicitly instead of inventing a chain. This is not a restatement of
`docs/technical-design.md`; it records what the code actually does in Cycle 2.

Cycle 1 flows (root contract, `/chatbi-init`, configuration load, path
identity, gate model, SessionStart hook) remain as documented in
`docs/feature-flow-v2.md` and are not repeated here. This document covers the
Cycle 2 additions: the `policy` primitive, the adapter protocol and selection
chain, the Fixture adapter, the read-only codebase reader, the PreToolUse
gate, and the ConfigChange gate.

## 0. Scope of this document

Cycle 2 delivers the security-depth and source-precedence vertical slice. The
flows below are read from these source files:

- `.claude/lib/chatbi_harness/policy.py` (Ticket 01)
- `.claude/lib/chatbi_harness/adapters/base.py` (Ticket 02)
- `.claude/lib/chatbi_harness/adapters/__init__.py` (Ticket 02)
- `.claude/lib/chatbi_harness/adapters/fixture.py` (Ticket 03)
- `.claude/lib/chatbi_harness/adapters/codebase_reader.py` (Ticket 04)
- `.claude/hooks/pretool_guard.py` (Ticket 05)
- `.claude/hooks/config_change_gate.py` (Ticket 06)
- `.claude/fixtures/semantic-catalog.json`, `.claude/fixtures/warehouse.json`
  (Ticket 03)
- `.claude/fixtures/codebases/billing_app/**` (Ticket 04)
- `.claude/settings.json` (SessionStart-only; PreToolUse/ConfigChange
  registration DEFERRED to Cycle 5)
- `tests/harness/test_security.py` (Tickets 01/05/06)
- `tests/harness/test_adapters.py` (Tickets 02/03/04)

Analysis, maintenance, evaluation, correction, real sandbox enforcement, real
adapter connections, and real Claude E2E are out of Cycle 2 scope and are
listed as known gaps in section 9.

## 1. Flow A: Policy decision (`policy.decide`)

### Entry

`policy.py:88-101 decide(config: EffectiveConfig, request: PolicyRequest) ->
PolicyDecision`

`PolicyDecision` (`policy.py:47-57`) is a subclass of `GateDecision` -- it
inherits the frozen/slots shape, the `pass`/`warn`/`block` factories, and the
sanitization in `GateDecision.__post_init__`. No second error protocol is
introduced; callers raise `GateError` with a `PolicyDecision` directly.

### Call chain (fixed order for deterministic output)

```text
policy.py:103-104     protected = _protected_actions(config)
                      is_protected_action = request.request_type in protected
policy.py:107-118     STEP 1: SEM-003 -- agent cannot self-approve protected action
  if is_protected_action and request.actor == "agent":
    return PolicyDecision.block(rule_ids=("SEM-003","SEC-001"), ...)
policy.py:123-135     STEP 2: capability group resolution + group-specific precheck
  group = _CAPABILITY_GROUPS.get(request.request_type)  [policy.py:24-44]
  if group is None: return block HOOK-004 (unknown request type)
  group_block = _check_capability_group(config, request, group)  [policy.py:177-289]
  if group_block is not None: return group_block
policy.py:139-153     STEP 3: high-risk classification (SEC-001)
  if _is_high_risk(config, request.risk_class): return warn SEC-001
policy.py:156-174     STEP 4: pass
```

### Branch conditions (key)

The capability group table is hard-coded at `policy.py:24-44`
(`_CAPABILITY_GROUPS`): `discover`/`read_metadata`/`read_lineage` ->
`discover_read`; `compile`/`query`/`freshness` -> `query_read`;
`read_codebase`/`search_codebase`/`stat_codebase`/`git_metadata` ->
`codebase_read`; `edit_workspace`/`write_workspace` ->
`workspace_candidate_write`; `mutate_warehouse`/`create_remote`/`modify_remote`
-> `mutate_warehouse`; `network`/`api_call` -> `network`.

`_check_capability_group` (`policy.py:177-289`) runs group-specific prechecks:

- **query_read** (`policy.py:184-226`): if `pii_policy_ref` is None -> block
  SEC-002/SEC-001 ("PII policy is not configured"). If
  `restricted_disclosure == "sql_only"` and `purpose != "compile"` -> block
  SEC-002 ("results and samples are withheld"). Compile under sql_only passes
  with the constraint "do not return results".
- **mutate_warehouse** (`policy.py:228-237`): always block SEC-001 ("disabled
  by default in v1").
- **network** (`policy.py:239-259`): default deny; block SEC-001 if
  `network_domain` is None or not in `declared_domains`.
- **codebase_read** (`policy.py:261-275`): block SEC-001/SCOPE-001 if
  `target_entity` is not a declared Business Codebase alias.
- **workspace_candidate_write** (`policy.py:277-287`): block SEC-001/SCOPE-001
  if `workspace.allow_candidate_writes` is false.

### Data flow

`PolicyRequest` (`policy.py:60-75`) is a frozen dataclass carrying
`request_type`, `target_entity`, `actor`, `purpose`, `risk_class`,
`network_domain`, `declared_domains`. The `decide` function performs only
field comparisons on the `EffectiveConfig` and the request; it never reads the
external Codebase, opens a shell, or executes a subprocess (`policy.py:1-9`
module docstring).

### Error handling

Every failure returns a `PolicyDecision` with `status="block"` or `"warn"`,
carrying `rule_ids`, sanitized `evidence_refs`, `reason`, and `recovery`.
Sanitization is inherited from `GateDecision.__post_init__` (Cycle 1
`gates.py:62-72`). Unknown request types block with HOOK-004
(`policy.py:126-131`); the unknown request type string does not appear in the
serialized decision (`test_security.py:170-184`).

### Evidence

`tests/harness/test_security.py`:
- `PolicyDecisionShapeTests` (lines 88-125): PolicyDecision IS-A GateDecision,
  accepted by GateError, immutable.
- `PolicyAccessTests` (lines 127-184): protected action by agent blocked
  (SEM-003/SEC-001); by owner passes; unknown request type blocked.
- `PolicyPIITests` (lines 187-273): PII missing blocks; sql_only blocks result
  return and gives SQL guidance; sql_only allows compile; configured PII allows
  query.
- `PolicyCapabilityGroupTests` (lines 275-416): mutate blocked; network default
  deny + declared domain allow + undeclared deny; codebase alias required;
  workspace write respects config flag; discover passes.
- `PolicyRiskTests` (lines 418-466): high-risk warns needing sign-off;
  non-high-risk passes.
- `PolicyDeterminismAndCanaryTests` (lines 469-541): stable serialization;
  canary secret/PII/path do not leak.

## 2. Flow B: Adapter selection chain (`select_adapter`)

### Entry

`adapters/__init__.py:495-503 select_adapter(config, *, kind, run_mode,
workspace_root, cli_allowlist=(), selection_request=None) -> SelectionOutcome`

### Call chain

```text
adapters/__init__.py:511-514   validate kind in {"semantic","query"}, run_mode
adapters/__init__.py:516-531   adapter_ids = config["adapters"][kind]
                                if empty: STOP SEM-001/PORT-001/HOOK-004
adapters/__init__.py:533-537   request = selection_request or default discover request
adapters/__init__.py:540       for adapter_id in adapter_ids (declaration order):
  adapters/__init__.py:541-555   validate_adapter_id; invalid -> STOP PORT-001/HOOK-004
  adapters/__init__.py:557-581   managed: branch
    ManagedAdapter(adapter_id, kind)  [adapters/__init__.py:248-314]
    health = adapter.healthcheck()  -> always "unavailable" (not_yet_exercised)
    if health.status == "ok": policy_decide; if pass -> selected
    else: missing.append(unavailable); continue
  adapters/__init__.py:583-673   cli: branch
    cli_config = config["cli_adapters"].get(name)
    if not configured: missing.append; continue
    illegal = validate_cli_argv(argv)  [adapters/__init__.py:89-106]
      if illegal: STOP SEC-003/PORT-001/HOOK-004 (no shell fallback)
    executable = resolve_executable(argv[0], cli_allowlist)  [adapters/__init__.py:109-143]
      if None: STOP SEC-003/PORT-001/HOOK-004
    decision = policy_decide(config, request)
      if not pass: missing.append; continue
    CliAdapter(...) constructed  [adapters/__init__.py:317-463]
    return SelectionOutcome.selected(...)
  adapters/__init__.py:675-706   fixture: branch
    if not fixture_enabled or run_mode not in {"test","example"}:
      STOP PORT-001/HOOK-004 (fixture_not_test_mode)
    # TODO(Ticket-03): FixtureAdapter not wired; STOPs with fixture_pending
    missing.append(fixture_pending); continue
adapters/__init__.py:710-718   exhausted: STOP SEM-001/PORT-001/HOOK-004
```

### Branch conditions (key)

- **Managed branch** (`adapters/__init__.py:557-581`): `ManagedAdapter`
  (`adapters/__init__.py:248-314`) deterministically reports unavailable on
  every method (`healthcheck` at line 294-295 calls `_unavailable()` at
  278-292, which returns `AdapterEvidence.unavailable` with
  `error_category="not_yet_exercised"`). The chain always continues to CLI.
  This is the official-only / NOT YET EXERCISED state -- no real managed
  runtime exists.

- **CLI branch** (`adapters/__init__.py:583-673`): argv is validated by
  `validate_cli_argv` (`adapters/__init__.py:89-106`), which rejects shell
  metacharacters (`_SHELL_METACHARACTERS` at line 63: `|;&\`$<>\\n\r`),
  newlines, command substitution, and sensitive flags (`_SECRET_ARGV` at line
  55-58: `--token`/`--api-key`/`--password`/`--secret`). The executable is
  resolved by `resolve_executable` (`adapters/__init__.py:109-143`) to an
  allowlist absolute path via `shutil.which` on a safe PATH
  (`_SAFE_SYSTEM_PATH` at line 68-72, only absolute components of `os.defpath`)
  and verified as a regular executable file whose realpath is in the allowlist.
  The `CliAdapter` (`adapters/__init__.py:317-463`) launches with
  `subprocess.run(command, shell=False, ...)` (line 383-392), cwd fixed to the
  Workspace, and a whitelisted environment built by `build_cli_env`
  (`adapters/__init__.py:146-162`, only locale + safe PATH + declared
  credential env names).

- **Fixture branch** (`adapters/__init__.py:675-706`): if `fixture_enabled` is
  false or `run_mode` is not `test`/`example`, the chain STOPs with
  `fixture_not_test_mode` (PORT-001). Even when fixture is enabled and in test
  mode, the chain STOPs with `fixture_pending` (line 696-706) because the
  `FixtureAdapter` is not wired into `select_adapter`. This is a known design
  gap (section 9 gap 3).

### Data flow

`SelectionOutcome` (`adapters/__init__.py:181-245`) is a frozen dataclass with
status `"selected"` or `"stopped"`. When selected, it carries the `adapter_id`,
the `Adapter` instance, and `selection_evidence` (an `AdapterEvidence` with
`evidence_source="local_probe"` for CLI selection, line 657-670). When
stopped, it carries `missing_capabilities` (tuple of `MissingCapability`),
`minimal_authorization` (string listing all three families), and a
`stop_decision` (`GateDecision.block`).

Adapter evidence (`AdapterEvidence` in `adapters/base.py:83-207`) is structured
JSON with `adapter_id`, `produced_at` (UTC ISO), `evidence_source`, `status`,
`content_sha256`, `rule_ids`, `error_category`, `payload`, `reason`,
`recovery`. The payload from a CLI subprocess is tagged `untrusted=true`
(`adapters/__init__.py:422-432` `_parse_stdout`) and never spliced into prompt
fields (`test_adapters.py:730-737`).

### Error handling

- Illegal argv -> immediate STOP (not a skip-and-continue); the chain does not
  fall back to shell (`adapters/__init__.py:597-614`).
- Non-allowlisted executable -> immediate STOP (`adapters/__init__.py:615-634`).
- CLI run failure (OSError/TimeoutExpired) -> `AdapterEvidence.error` with
  `error_category="run_failure"` (`adapters/__init__.py:393-401`).
- Nonzero CLI exit -> `AdapterEvidence.error` with `error_category="nonzero_exit"`
  (`adapters/__init__.py:412-420`).

### Evidence

`tests/harness/test_adapters.py`:
- `ValidateAdapterIdTests` (lines 189-215): valid/invalid adapter IDs.
- `ValidateCliArgvTests` (lines 369-422): legal argv; empty/empty-element;
  sensitive flags; shell metacharacters; one bad element is enough.
- `ResolveExecutableTests` (lines 430-471): allowlist match; non-allowlist;
  nonexistent; non-executable; directory; bare name.
- `BuildCliEnvTests` (lines 479-504): base env; credential included/skipped;
  invalid name skipped; HOME never included.
- `ManagedAdapterTests` (lines 512-566): healthcheck unavailable; all
  operations unavailable; invalid ID/kind rejected; protocol shape.
- `CliAdapterTests` (lines 574-737): constructor rejects sensitive
  flag/metacharacter; run ok/non-JSON/nonzero/failure; shell=False + argv list;
  cwd/env enforced; stdout not spliced into prompt fields.
- `SelectAdapterTests` (lines 765-1137): managed->CLI selected; managed->CLI
  not configured -> STOP; both unavailable -> STOP; no adapters -> STOP; CLI
  shell metacharacter -> STOP; CLI command substitution -> STOP; CLI not in
  allowlist -> STOP; fixture rejected in production; fixture pending in test
  mode; STOP has missing + minimal authorization; selection evidence schema;
  selected adapter runs; query kind.
- `NoPathLeakageTests` (lines 1145-1210): adapter IDs, managed evidence,
  selection evidence, STOP outcome, CLI run evidence fixed fields have no
  machine paths.

## 3. Flow C: Fixture adapter (`FixtureAdapter`, direct construction only)

### Entry

`adapters/fixture.py:117-138 FixtureAdapter.__init__(adapter_id, kind,
run_mode, *, fixture_enabled=True, fixtures_root=None)`

### Call chain (discover example)

```text
fixture.py:144-146     _is_available(): fixture_enabled AND run_mode in {"test","example"}
fixture.py:216-222     discover(request):
  if not _is_available(): return self._blocked("discover")  [fixture.py:155-169]
  catalog = self._load_fixture("semantic-catalog.json")  [fixture.py:193-200]
  if catalog is None: return self._error("discover", ...)  [fixture.py:181-191]
  return self._ok(catalog, operation="discover")  [fixture.py:171-179]
```

### Branch conditions

- **Test/example mode** (`fixture.py:144-146`): `_is_available()` returns true
  only when `fixture_enabled` is true and `run_mode` is `test` or `example`.
  All six operations (healthcheck/discover/compile/query/quality/lineage)
  check `_is_available()` first and return `_blocked()` if false.
- **Production mode** (`fixture.py:155-169`): `_blocked()` returns
  `AdapterEvidence` with `status="blocked"`, `evidence_source="fixture"`,
  `rule_ids=("PORT-001",)`, `error_category="fixture_not_test_mode"`. The
  recovery says "Enable fixture mode and run with a test/example flag, or
  configure a real adapter" (`fixture.py:59-62`).
- **Missing fixture file** (`fixture.py:193-200`): `_load_fixture` returns
  None on OSError/JSONDecodeError; `discover`/`query` return `_error()` with
  `error_category="fixture_load_failure"`.

### Data flow

- `discover` returns the contents of `.claude/fixtures/semantic-catalog.json`
  as the payload (`fixture.py:219-222`). The catalog contains `metrics`,
  `dimensions`, `segments` arrays (SEM-002).
- `query` returns `.claude/fixtures/warehouse.json` (`fixture.py:229-239`).
  The warehouse has a fixed `snapshot_date` of `"2024-01-15"` and anchored
  numbers (no date drift).
- `compile`/`quality`/`lineage` return fixed synthetic payloads
  (`fixture.py:66-90`).
- All evidence is tagged `evidence_source="fixture"`, never `"local_probe"`
  (`test_adapters.py:1385-1402`).

### Known gap: not wired into selection chain

`FixtureAdapter` is tested by direct construction only. The `select_adapter`
function in `adapters/__init__.py:675-706` STOPs with `fixture_pending` even
when fixture is enabled and in test mode, because the FixtureAdapter is not
constructed there. This is a known design gap (section 9 gap 3). The
`fixture.py:18-22` module docstring and `adapters/__init__.py:696-698` TODO
comment document this explicitly.

### Evidence

`tests/harness/test_adapters.py`:
- `FixtureAdapterTests` (lines 1232-1499): test mode discover/query/quality/
  lineage/compile/healthcheck return ok; example mode works; production mode
  all operations block; fixture disabled blocks; recovery mentions test +
  real adapter; block is deterministic; evidence always fixture not
  local_probe; payload stable; content_sha256 matches canonical hash; no
  machine paths in fixed fields; no secret canary in fixed fields; missing
  fixture file returns error.
- `FixtureDataTests` (lines 1501-1628): catalog has metrics/dimensions/segments
  with IDs and names; warehouse has fixed snapshot date; totals match rows;
  no datetime/now()/today(); no secret canary; no machine paths; no email
  patterns; IDs are fixture-prefixed; regions are synthetic; discover payload
  equals catalog file; query payload equals warehouse file.

## 4. Flow D: Read-only codebase reader (`CodebaseReader`, direct construction)

### Entry

`adapters/codebase_reader.py:453-454 CodebaseReader.__init__(config)`

### Call chain (read example)

```text
codebase_reader.py:479-564   read(*, alias, target, governance_context=None):
  codebase_reader.py:494-497     resolve_path_reference(config, alias=alias, target=target)
                                  [Cycle 1 paths.py:355-462]
  codebase_reader.py:496-497     except GateError -> _path_error_evidence("read", ...)
  codebase_reader.py:499-508     root = self._root_for_alias(alias)  [lines 832-859]
  codebase_reader.py:510-521     file_path.read_bytes(); OSError -> error
  codebase_reader.py:523-526     truncate to _MAX_READ_BYTES (1 MiB); decode UTF-8
  codebase_reader.py:528         rejected = _detect_rejected_instructions(text, relative_path)
                                  [lines 348-375]
  codebase_reader.py:529         conflicts = _detect_conflicts(text, relative_path, governance_context)
                                  [lines 383-429]
  codebase_reader.py:531-564     build payload: {portable_reference, content: {untrusted, text, ...}}
                                  if conflicts: add SRC-002 to rule_ids, reason mentions conflicts
                                  return CodebaseEvidence.ok(...)
```

### Branch conditions (key)

- **Path identity** (`codebase_reader.py:494-497`): every operation reuses
  Cycle 1 `resolve_path_reference` for component-level containment, symlink
  rejection, parent-traversal rejection, and portable reference generation.
  A `GateError` from path resolution is converted to a blocked
  `CodebaseEvidence` via `_path_error_evidence` (lines 861-874).
- **SCOPE-002 blocked operations** (`codebase_reader.py:810-828`): `execute`,
  `write`, `install`, `commit` methods exist solely to raise
  `CodebaseScopeBlockError` (a `GateError` subclass, lines 313-340) with
  `rule_ids=("SCOPE-002", "SCOPE-003", "HOOK-004")`. They never succeed.
- **Instruction candidate detection** (`codebase_reader.py:348-375`):
  `_detect_rejected_instructions` scans text line-by-line against four
  pattern categories: `execute` (run/bash/python/./rm), `install` (pip/npm/
  yarn/apt/brew/cargo/gem/go install), `upload` (upload/deploy/scp/rsync/
  curl|sh), `commit` (git commit/push/svn/hg/docker push). Matches are logged
  as rejected candidates with `category`, `relative_path`, `line_number`,
  `snippet`. They are never acted upon.
- **SRC-002 conflict disclosure** (`codebase_reader.py:383-429`):
  `_detect_conflicts` scans text for metric-definition-like lines
  (`MetricName = ...` or `MetricName: ...`) and compares them against a
  provided `governance_context["metrics"]`. When the same metric name has a
  different definition, a conflict dict is returned. The reader never
  auto-defines or overrides metrics; it only discloses the conflict for owner
  adjudication.
- **git_metadata** (`codebase_reader.py:730-806`): defaults to
  `metadata_only` (HEAD SHA, tracked/modified/untracked status; no commit
  history, no author, no message). `full_history` is blocked by default
  (lines 756-770) with `error_category="full_history_blocked"`, requiring a
  separate safety-deviation approval. Git is invoked through the Cycle 1
  trusted-git allowlist (`_TRUSTED_GIT`, `codebase_reader.py:60-62, 890-961`).

### Data flow

`CodebaseEvidence` (`codebase_reader.py:161-305`) is a frozen dataclass with
`component`, `produced_at`, `operation`, `alias`, `status`,
`content_sha256`, `rule_ids`, `error_category`, `payload`, `reason`,
`recovery`, `rejected_instructions`, `conflicts`. The payload is always
wrapped as `{"untrusted": True, "data": ...}` (lines 232-237, 265, 292). File
content is never returned as a bare string that could be interpolated into a
prompt.

### Known gap: not wired into selection chain

`CodebaseReader` is a read-only accessor for Business Codebases, not a
discover/compile/query adapter in the managed->CLI->STOP selection chain. It
is tested by direct construction. The `codebase_reader.py:26-31` module
docstring documents this explicitly. Wiring it into `adapters/__init__.py`
would require modifying Ticket 02's file and is deferred to plan-agent
evaluation (section 9 gap 3).

### Evidence

`tests/harness/test_adapters.py`:
- `CodebaseReaderCapabilitiesTests` (lines 1743-1783): capabilities declare
  read-only; execute/write/install/commit raise `CodebaseScopeBlockError`.
- `CodebaseReaderReadTests` (lines 1786-1860): read returns portable reference
  + untrusted content; content_sha256 deterministic; unknown alias error;
  missing target blocked; absolute target blocked; parent traversal blocked.
- `CodebaseReaderInstructionRejectionTests` (lines 1863-1935): README
  instructions detected as rejected candidates (execute/install/upload/
  commit); rejected instructions also in payload; malicious content treated as
  data not executed; setup script read not executed.
- `CodebaseReaderSearchTests` (lines 1938-1988): search returns matches within
  root; no matches returns ok empty; respects max_results; unknown alias
  error.
- `CodebaseReaderStatTests` (lines 1991-2032): stat file/directory metadata;
  missing target blocked.
- `CodebaseReaderGitMetadataTests` (lines 2035-2102): default metadata_only;
  full_history blocked; invalid mode error; portable reference included.
- `CodebaseReaderSymlinkTests` (lines 2105-2213): symlink escape blocked;
  internal symlink blocked; search skips symlink escape and logs rejected
  path; platform limitation is HIGH deviation (skip, not silent pass).
- `CodebaseReaderConflictDisclosureTests` (lines 2216-2313): conflict
  disclosed when external definition differs; no conflict when governance
  context not provided; no conflict when definitions match; reader does not
  auto-define metrics.
- `CodebaseEvidenceSchemaTests` (lines 2316-2392): required fields; ISO Z
  timestamp; JSON round-trip; frozen; rejects invalid operation/status.
- `CodebaseNoPathLeakageTests` (lines 2395-2469): no machine paths in fixed
  fields; no secret canary; no email.
- `CodebaseFixtureDataTests` (lines 2472-2534): fixture files exist; no secret
  canary; no machine paths in clean files; no email; README contains
  instruction candidates; metric definitions contain conflicting definition;
  malicious content contains shell metacharacters.

## 5. Flow E: PreToolUse gate (`pretool_guard.py`)

### Entry

`.claude/hooks/pretool_guard.py:479-480 if __name__ == "__main__": raise
SystemExit(main())`

The hook is registered as a `PreToolUse` command hook in the product install
settings block (`docs/harness/security.md` section 3), but the development
`.claude/settings.json` remains SessionStart-only. PreToolUse settings
activation is DEFERRED to Cycle 5 E2E to avoid self-deadlocking the dev
session (section 9 gap 4).

### Call chain

```text
pretool_guard.py:442-476   main()
  pretool_guard.py:444       workspace_root = Path.cwd().resolve(strict=True)
  pretool_guard.py:445       event = _validate_event(_read_event(), workspace_root)
  pretool_guard.py:447-451   config = load_effective_config(shared, local if exists)
  pretool_guard.py:453-458   decision = _check_tool(config, workspace_root, tool_name, tool_input)
  pretool_guard.py:460-461   if decision is block: return _write_failure(decision)  [exit 2]
  pretool_guard.py:463       return 0  [exit 0 = allow]
  pretool_guard.py:464-465   except HookInputError: return _write_failure(_input_failure(...))
  pretool_guard.py:466-467   except GateError: return _write_failure(error.decision)
  pretool_guard.py:468-476   except Exception: return _write_failure(fail_closed(...))
```

### Input validation (`_read_event` + `_validate_event`)

```text
pretool_guard.py:102-109   _read_event: read max 64 KiB; oversized -> HookInputError
pretool_guard.py:110-117     decode UTF-8; failure -> HookInputError
pretool_guard.py:118-127     json.loads with duplicate-key rejection; failure -> HookInputError
pretool_guard.py:130-136   _validate_event: must be one JSON object
pretool_guard.py:137-143     required fields: cwd, tool_name, tool_input, tool_use_id
pretool_guard.py:148-153     hook_event_name must be "PreToolUse" if present
pretool_guard.py:155-164     known optional identifier fields validated for shape only
pretool_guard.py:166-172     tool_name must be non-empty string
pretool_guard.py:174-180     tool_input must be a JSON object
pretool_guard.py:183-189     cwd must exactly equal str(workspace_root) [continuous TOCTOU]
```

Forward compatibility (HOOK-003): unknown event-level fields are IGNORED, never
rejected (`pretool_guard.py:55-64`, `144-146`). Real Claude Code PreToolUse
events carry `session_id`, `transcript_path`, `model`, `permission_mode`,
`agent_id`, `agent_type`, and future fields. A prior brittle allowlist rejected
real events carrying extra fields, which self-deadlocked the dev session once
the hook was registered; that rejection has been removed and is covered by a
regression test (`test_security.py:727-748`).

### Tool-specific checks (`_check_tool`)

```text
pretool_guard.py:403-415   if tool_name not in _TOOL_MAP: return None [allow unknown tools]
pretool_guard.py:417       request_type, path_fields = _TOOL_MAP[tool_name]
pretool_guard.py:418       external_roots = _configured_external_roots(config, workspace_root)
pretool_guard.py:420-424   extract target_value from first matching path field
pretool_guard.py:429-431   if Bash: return _check_bash_command(...)
pretool_guard.py:437-439   return _check_file_target(...)
```

The tool map (`pretool_guard.py:70-78`) gates: `Edit`/`MultiEdit` ->
`edit_workspace` (`file_path`); `Write` -> `write_workspace` (`file_path`);
`Read` -> `read_workspace` (`file_path`); `Grep`/`Glob` -> `read_workspace`
(`path`); `Bash` -> `bash_execute` (`command`). Unknown tools are allowed
(HOOK-001: the gate only blocks known violations).

### File target checks (`_check_file_target`)

```text
pretool_guard.py:260-266   reject ".." in target parts (POSIX or Windows)
pretool_guard.py:270-289   resolve target (absolute or relative to workspace)
pretool_guard.py:291-292   is_write = request_type in {edit_workspace, write_workspace}
                            is_read = request_type == "read_workspace"
pretool_guard.py:297-320   for each external root:
                              if target within external root:
                                if is_write: block SCOPE-001/SCOPE-002/HOOK-004
                                if is_read: block SCOPE-001/SCOPE-002/SCOPE-003/HOOK-004
                                  (external reads must go through the adapter)
pretool_guard.py:323-330   if target outside all configured roots: block SCOPE-001/HOOK-004
pretool_guard.py:333-370   if is_write and target within workspace:
                              resolve relative; if target exists:
                                resolve_path_reference [TOCTOU revalidation, Cycle 2 gap 2]
                              policy_request = PolicyRequest(request_type, relative)
                              policy_decision = decide(config, policy_request)
                              if block: return policy_decision
```

### Bash command check (`_check_bash_command`)

```text
pretool_guard.py:388-400   for each external root:
                              if str(root) in command: block SCOPE-001/SCOPE-002/HOOK-004
```

This is a deterministic string containment check (HOOK-001), not shell
parsing. It may have false positives but never false negatives for external
root path references.

### Exit semantics

- exit 0 = allow (`pretool_guard.py:463`)
- exit 2 = block with `rule_ids`/`evidence_refs`/`reason`/`recovery` written
  to stderr as one JSON object (`pretool_guard.py:194-197`, `460-461`)
- Library import failure -> exit 2 with `HOOK-001, HOOK-004`, no traceback
  (`pretool_guard.py:35-50`)
- Any unexpected exception -> exit 2 fail-closed via `fail_closed`
  (`pretool_guard.py:468-476`)

### Evidence

`tests/harness/test_security.py`:
- `PreToolUseContractTests` (lines 681-918): valid workspace write with real
  CC extra fields passes; edit of existing file passes TOCTOU revalidation;
  unknown event-level field tolerated; invalid serialization/shape fail
  closed; cwd mismatch/traversal cwd fail closed; absolute target outside
  roots blocked; traversal target blocked; secret canary in blocked bash
  decision does not leak; library import exception fails closed without
  traceback.
- `PreToolUseExternalBoundaryTests` (lines 921-1026): external root write/edit/
  read/bash blocked; workspace internal bash without external reference
  passes.
- `PermissionLayerDenyProofTests` (lines 1029-1123): permission layer blocks
  external edit/write/bash with exact command recorded; settings block is
  documented not activated (dev settings remain SessionStart-only).
- `SandboxLayerDenyProofTests` (lines 1126-1163): SKIPPED as BLOCKING GAP
  (HIGH deviation, AC-03). Real OS sandbox deny-write/deny-execute cannot be
  exercised offline. Not faked with a Prompt test.

## 6. Flow F: ConfigChange gate (`config_change_gate.py`)

### Entry

`.claude/hooks/config_change_gate.py:353-354 if __name__ == "__main__": raise
SystemExit(main())`

The hook is registered as a `ConfigChange` command hook in the product install
settings block (`docs/harness/security.md` section 6), but the development
`.claude/settings.json` remains SessionStart-only. ConfigChange settings
activation is DEFERRED to Cycle 5 E2E (section 9 gap 4).

### Call chain

```text
config_change_gate.py:321-350   main()
  config_change_gate.py:323       workspace_root = Path.cwd().resolve(strict=True)
  config_change_gate.py:324       event = _validate_event(_read_event())
  config_change_gate.py:325       source = event["source"]
  config_change_gate.py:327-330   if source == "managed":
                                    return _emit_managed_feedback(workspace_root)  [exit 0]
  config_change_gate.py:333-338   try: settings_decision = _revalidate(workspace_root)
                                    except GateError: return _write_failure(error.decision)  [exit 2]
                                  if settings_decision is block:
                                    return _write_failure(settings_decision)  [exit 2]
  config_change_gate.py:339       return 0  [exit 0 = silent pass for blockable source]
  config_change_gate.py:340-341   except HookInputError: return _write_failure(_input_failure(...))
  config_change_gate.py:342-350   except Exception: return _write_failure(fail_closed(...))
```

### Input validation (`_read_event` + `_validate_event`)

```text
config_change_gate.py:105-109   _read_event: read max 64 KiB; oversized -> HookInputError
config_change_gate.py:110-117     decode UTF-8; failure -> HookInputError
config_change_gate.py:118-130     json.loads with duplicate-key rejection; failure -> HookInputError
config_change_gate.py:133-136   _validate_event: must be one JSON object
config_change_gate.py:140-145     source is required; missing -> HookInputError
config_change_gate.py:146-152     source must be non-empty string
config_change_gate.py:157-162     hook_event_name must be "ConfigChange" if present
config_change_gate.py:163-170     file_path validated for shape when present (never read/opened)
```

Forward compatibility (HOOK-003): unknown event-level fields are IGNORED
(`config_change_gate.py:24-29`, `153-156`). `file_path` is informational only
and is never read or opened (`config_change_gate.py:155-156`).

### Re-validation (`_revalidate`)

```text
config_change_gate.py:258-280   _revalidate(workspace_root):
  config_change_gate.py:271-276     config = load_effective_config(shared, local if exists)
                                     [EffectiveConfig invalidation, technical-design §7.3 item 10:
                                      no cached config reused]
  config_change_gate.py:279         _configured_roots(config)  [Cycle 1 paths.py:147-188]
                                     [root existence, symlink roots, overlap]
  config_change_gate.py:280         return _check_settings_invariants(workspace_root)
```

`_check_settings_invariants` (`config_change_gate.py:189-255`) re-validates
the project `settings.json` security-critical blocks:
- `permissions.deny` removed or emptied -> block SEC-001/SCOPE-002/HOOK-004
  (lines 234-243)
- `sandbox.enabled` is not true -> block SEC-001/HOOK-004 (lines 245-253)
- Malformed/oversized/non-UTF-8 settings.json -> block (lines 208-230)
- Absent blocks are NOT flagged: the security boundary may live in
  organization-managed settings the gate cannot observe; only an explicit
  degradation (block present but weakened) is blockable (lines 189-200).

### Managed source feedback (`_emit_managed_feedback`)

```text
config_change_gate.py:283-318   _emit_managed_feedback(workspace_root):
  config_change_gate.py:292-300   revalidation = "passed" or "failed" (re-runs _revalidate)
  config_change_gate.py:302-316   feedback = {status: "notified", rule_ids: [HOOK-001, HOOK-003],
                                    reason: "Managed policy change observed; the project layer
                                    cannot block managed changes", recovery: "Restart the session
                                    and run /chatbi-init", revalidation: passed/failed}
  config_change_gate.py:316-317   write feedback JSON to stdout
  config_change_gate.py:318       return 0  [exit 0: cannot block, not a silent pass]
```

Managed policy changes are NOT assumed blockable (technical-design section
11.1). The gate never emits a fake block (exit 2 would pretend to block a
change the project layer cannot block) and never silently passes (the feedback
makes the outcome explicit). Unknown `source` values are treated as blockable
(fail-closed, `config_change_gate.py:75-81`).

### Exit semantics

- exit 0 = re-validation passed (blockable source, silent pass) OR clear
  feedback emitted (managed source, not a silent pass)
- exit 2 = block an invalid change from a blockable source
- Library import failure -> exit 2 with `HOOK-001, HOOK-004`, no traceback
  (`config_change_gate.py:42-69`)
- Any unexpected exception -> exit 2 fail-closed (`config_change_gate.py:342-350`)

### Evidence

`tests/harness/test_security.py`:
- `ConfigChangeContractTests` (lines 1356-1447): valid project change
  revalidates and passes silent; unknown event-level fields tolerated; invalid
  serialization/shape fail closed; library import exception fails closed
  without traceback.
- `ConfigChangeRevalidationTests` (lines 1450-1584): protected action
  downgrade blocked; secret injection in shared config blocked; sandbox
  disabled in harness config blocked; settings deny removed blocked; settings
  sandbox disabled blocked; settings malformed JSON blocked; business codebase
  root overlap blocked.
- `ConfigChangeManagedFeedbackTests` (lines 1587-1647): managed source emits
  feedback and does not block; managed source with invalid config still does
  not block but reports failed; managed feedback does not leak canary or
  absolute path.
- `ConfigChangeCanaryTests` (lines 1649-1698): canary in path binding does not
  leak in block; dev settings remains SessionStart-only.

## 7. Cross-cutting: sanitization and fail-closed guarantees (Cycle 2 additions)

Cycle 2 reuses the Cycle 1 `GateDecision` sanitization (`gates.py:62-72`) and
`fail_closed` (`gates.py:153-167`) for all new code paths:

- `PolicyDecision` inherits `GateDecision` sanitization (`policy.py:47-57`).
- `AdapterEvidence` computes `content_sha256` over the canonical JSON encoding
  of the payload (`adapters/base.py:47-56`); the payload from a CLI subprocess
  is tagged `untrusted=true` and never spliced into prompt fields
  (`adapters/__init__.py:422-432`).
- `CodebaseEvidence` wraps all payloads as `{"untrusted": True, "data": ...}`
  (`codebase_reader.py:232-237`); file content is never returned as a bare
  string.
- `pretool_guard.py` and `config_change_gate.py` write only sanitized
  `GateDecision` JSON to stderr on failure; canary secrets and absolute
  workspace paths never appear in stdout or stderr
  (`test_security.py:846-884`, `1631-1647`).

The CLI adapter environment is built from a whitelist
(`adapters/__init__.py:146-162`): only locale (`LANG=C`, `LC_ALL=C`), a safe
PATH (absolute components of `os.defpath` only), and declared credential
environment-variable NAMES (values sourced from the current process
environment) are passed through. `HOME` is never included
(`test_adapters.py:501-504`).

## 8. Cross-cutting: adapter evidence schema (PORT-001, SEC-003)

All adapter evidence (managed/CLI/fixture) and codebase evidence share the
same structural contract: a frozen dataclass with `adapter_id`/`component`,
`produced_at` (UTC ISO), `evidence_source`/`status`, `content_sha256`, and
`rule_ids`. The content hash is computed over the canonical JSON encoding
(`adapters/base.py:47-56`, `codebase_reader.py:149-158`). Adapter IDs have the
form `<family>:<name>` where family is `managed`, `cli`, or `fixture`
(`adapters/base.py:25`); they carry no machine absolute paths
(`test_adapters.py:1146-1148`).

The `_MACHINE_PATH` regex used in tests
(`test_adapters.py:71-73`) matches `/Users/...`, `/private/...`, `/tmp/...`,
`/var/...`, `/home/...`, `/opt/...`, `/etc/...`, `/root/...`. Every adapter
and codebase evidence test asserts the fixed fields (`reason`, `recovery`,
`adapter_id`/`component`, `evidence_source`/`operation`, `alias`) are free of
machine paths and secret canaries.

## 9. Known design gaps (Cycle 2 does not implement these)

These are read from code comments, explicit DEFERRED annotations, TODO
comments, and the compatibility/security docs. They are future hard gates,
not current behavior, and must not be treated as implemented.

1. **Real OS sandbox runtime evidence is a PRODUCTION BLOCKER.** The
   `SandboxLayerDenyProofTests.test_real_os_sandbox_deny_write_deny_execute_is_a_blocking_gap`
   (`test_security.py:1139-1163`) is SKIPPED. The Claude Code sandbox is a
   runtime feature of a logged-in Claude process with no offline invocation
   surface; Darwin `sandbox-exec` is a different mechanism that is not a valid
   proxy. The sandbox settings block is delivered as a configuration layer in
   `docs/harness/security.md` section 3, but its runtime enforcement is NOT
   YET EXERCISED. This gap is deferred to Cycle 5 real E2E for a human
   go/no-go decision. It is NOT faked with a Prompt test, NOT downgraded to a
   warning, and NOT removed from the exit criteria (AC-03, dev-cycle-2
   section 9).

2. **Managed adapter has no real managed runtime.** `ManagedAdapter`
   (`adapters/__init__.py:248-314`) deterministically reports unavailable on
   every method (`error_category="not_yet_exercised"`). The selection chain
   always continues to CLI. This is official-only / NOT YET EXERCISED; no real
   managed connection is faked.

3. **FixtureAdapter and CodebaseReader are NOT wired into `select_adapter`.**
   The `select_adapter` function STOPs at `fixture_pending`
   (`adapters/__init__.py:696-706`) even when fixture is enabled and in test
   mode, because the `FixtureAdapter` is not constructed there. The
   `CodebaseReader` is a read-only accessor, not a discover/compile/query
   adapter in the selection chain. Both adapters are tested by direct
   construction and pass all tests, but the selection chain does not wire
   them. This is a known design gap documented in `adapters/fixture.py:18-22`,
   `adapters/codebase_reader.py:26-31`, and the `TODO(Ticket-03)` comment at
   `adapters/__init__.py:696`. It is deferred to plan-agent evaluation.

4. **PreToolUse and ConfigChange settings registration is DEFERRED to Cycle 5
   E2E.** The development `.claude/settings.json` remains SessionStart-only
   (verified: the file contains only `{"hooks": {"SessionStart": [...]}}`).
   Activating PreToolUse in the dev session would intercept every tool call,
   including the edits needed to fix the hook itself, and self-deadlock the
   session. ConfigChange has the same self-deadlock risk. Both hooks are
   exercised by unit tests as real subprocesses and are activated in an
   isolated session during Cycle 5 real E2E
   (`test_security.py:1106-1123`, `1684-1698`).

5. **Real Claude Hook E2E is not exercised.** The PreToolUse and ConfigChange
   contracts are proven offline by subprocess tests. A real logged-in Claude
   Code 2.1.216 process has not triggered these hooks. Login, keychain,
   sandbox, and managed policy are not verified locally
   (`docs/harness/compatibility.md` NOT YET EXERCISED section).

6. **No analysis, maintenance, evaluation, or correction commands.** The root
   contract routes them (`CLAUDE.md:72-79`) but only `/chatbi-init` is
   installed. Analysis/review is Cycle 3; model/knowledge maintenance is Cycle
   4; evaluation/correction is Cycle 5. The adapter selection chain and
   policy primitive exist but are not integrated into an analysis path.

7. **`production_ready` is always false.** `diagnostics.py:336-339` hard-codes
   `False` (unchanged from Cycle 1). No code path sets it true.

8. **FBK-003: the Harness does not claim absolute correctness.** Evaluation
   pass is evidence, not a guarantee that silent failure is gone. The
   non-guarantee is stated in `CLAUDE.md:101-102` and enforced by the
   always-false `production_ready` flag.

## 10. Validation evidence

All commands were run from the Workspace root of this development directory
on 2026-07-22 using `python3 -B` (no bytecode written).

### 10.1 Targeted Cycle 2 tests

Command:
```text
python3 -B -m unittest tests.harness.test_config tests.harness.test_paths tests.harness.test_hooks tests.harness.test_adapters tests.harness.test_security
```
Actual result:
```text
.............................................................................................................................................................................................................................................................................................................s
----------------------------------------------------------------------
Ran 302 tests in 19.692s

OK (skipped=1)
```

The 1 skip is `SandboxLayerDenyProofTests.test_real_os_sandbox_deny_write_deny_execute_is_a_blocking_gap`
(BLOCKING GAP, section 9 gap 1).

### 10.2 Full discovery suite (Cycle 1+2 regression)

Command:
```text
python3 -B -m unittest discover -s tests/harness -p 'test_*.py'
```
Actual result:
```text
..................................................................................................................................................................................................................................................................................................................................s
----------------------------------------------------------------------
Ran 323 tests in 18.269s

OK (skipped=1)
```

The 1 skip is the same sandbox BLOCKING GAP. No `__pycache__` or `*.pyc`
residue was produced (the `-B` flag suppresses bytecode).

### 10.3 File inventory

Command:
```text
find .claude/lib/chatbi_harness .claude/hooks .claude/fixtures tests/harness docs/harness -type f -print
```
Actual result: 43 files listed. Cycle 2 deliverables present:
- `.claude/lib/chatbi_harness/policy.py` (Ticket 01)
- `.claude/lib/chatbi_harness/adapters/__init__.py`, `base.py`, `fixture.py`,
  `codebase_reader.py` (Tickets 02/03/04)
- `.claude/hooks/pretool_guard.py`, `config_change_gate.py` (Tickets 05/06)
- `.claude/fixtures/semantic-catalog.json`, `warehouse.json` (Ticket 03)
- `.claude/fixtures/codebases/billing_app/{README.md, docs/metric_definitions.md,
  models/revenue.sql, scripts/setup.sh, data/malicious.txt}` (Ticket 04)
- `tests/harness/test_security.py`, `test_adapters.py` (Cycle 2 test evidence)
- `docs/harness/security.md`, `compatibility.md`, `rule-traceability.md`
  (updated by Ticket 07)
- Cycle 1 files unchanged: `config.py`, `paths.py`, `gates.py`,
  `diagnostics.py`, `__init__.py`, `session_diagnose.py`,
  `python_binding_launcher.py`, `session_diagnose`, `settings.json`
  (SessionStart-only), 9 config fixtures, 5 harness docs, 7 test files.

### 10.4 Secret / machine-path scan

Command:
```text
grep -rnE '/Users/|BEGIN .*PRIVATE KEY|api[_-]?key|token[[:space:]]*[:=]' .claude docs/harness
```
Actual result (3 matches):
```text
.claude/lib/chatbi_harness/adapters/__init__.py:27:``--token``/``--api-key`` flags (technical-design §8.2, §13).
.claude/lib/chatbi_harness/adapters/__init__.py:93:    flags (``--token``/``--api-key``/``--secret``) in any element. Each element
.claude/fixtures/config/embedded-secret.json:4:  "business_codebases": {"source_app": {"description": "api_key=TEST_SECRET_CANARY_NOT_A_CREDENTIAL", ...}}
```

Interpretation:
- The two `adapters/__init__.py` matches are docstrings describing what the
  CLI argv validator rejects (`--token`/`--api-key`/`--secret` flag names).
  They are not secrets; they are security-boundary documentation.
- The `embedded-secret.json` match is a deliberate negative-test fixture
  (Cycle 1) whose canary value is literally
  `TEST_SECRET_CANARY_NOT_A_CREDENTIAL`. It verifies that
  `load_effective_config` rejects embedded secrets. It is not a real secret.

No `/Users/` path and no `BEGIN ... PRIVATE KEY` match occurred. The fixture
codebase files contain `FIXTURE_EXECUTION_MARKER` (in `scripts/setup.sh`) and
prompt-injection test content (in `README.md`, `data/malicious.txt`) -- these
are test canaries, not real secrets or machine paths. **Verdict: PASS** (no
real secret or machine path leaked).

### 10.5 Rule ID coverage scan

Command:
```text
grep -rhoE '[A-Z]{2,5}-[0-9]{3}' .claude docs/harness | sort -u
```
Actual result: 46 governed rule IDs (SCOPE-001..003, SEC-001..003,
REQ-001..004, SEM-001..003, RAW-001..003, SRC-001..002, DOC-001..005,
PORT-001, QLT-001, REV-001..003, ANS-001..003, EVAL-001..005, ABL-001..002,
FBK-001..003, HOOK-001..005), exactly matching the 46 governed rule IDs in
`docs/chatbi-harness-domain-model.md` section 9. One additional match
`SHA-256` is a false positive (the hash algorithm name matches the regex
`[A-Z]{2,5}-[0-9]{3}` but is not a rule ID). No `META-xxx` IDs appear (they
are governing context, not gate-enforced rule IDs). No fabricated rule IDs
(e.g., `SCOPE-999`). **Verdict: PASS** (rule ID set consistent with domain
model).

### 10.6 settings.json verification

The development `.claude/settings.json` contains only:
```json
{"hooks": {"SessionStart": [{"matcher": "startup|resume|clear|compact",
  "hooks": [{"type": "command", "command": ".claude/hooks/session_diagnose"}]}]}}
```
No PreToolUse, ConfigChange, permissions, or sandbox blocks are present. This
is verified by `test_security.py:1110-1114` and `test_security.py:1688-1692`.
