# ChatBI Harness Rule Traceability (Cycle 2)

STATUS: CODE_AS_READ on 2026-07-22. Each of the 46 governed executable rules
from `docs/chatbi-harness-domain-model.md` section 9 is traced below to a
concrete implementation evidence reference (file:line or test case) or to the
planned later cycle. Rule-family summaries are not used as a substitute for
per-rule evidence. The 9 META principles (section 3) and the 3 failure modes
(section 4) are governing context, not gate-enforced rule IDs; they are noted
at the end and do not require per-item implementation evidence.

Evidence conventions:
- `IMPLEMENTED (Cycle N)` = a real code path or test enforces this rule now.
  Cycle 2 evidence may build on Cycle 1 evidence; the highest cycle that
  delivered the current enforcement is cited.
- `PARTIAL (Cycle N)` = a real gate enforces a declared aspect; the runtime
  enforcement of the full rule is a later cycle.
- `PLANNED: Cycle N` = no implementation yet; routed in contract only.

## 9.1 Scope and security

### SCOPE-001 — reads/writes/exec limited to configured Workspace; external only via configured Business Codebase
- Evidence: `config.py:324-350` (path_ref uniqueness + path_bindings must be
  absolute and declared); `paths.py:147-189` (`_configured_roots` resolves
  workspace + codebase roots, rejects overlap); `paths.py:364-389` (alias and
  target validation); `diagnostics.py:250-309` (`_validate_configuration_path`
  rejects absolute/traversal/symlink/escape config inputs).
- Tests: `tests/harness/test_paths.py:105-134` (absolute target rejected),
  `136-166` (traversal), `401-486` (overlap), `644-686` (unconfigured root);
  `tests/harness/test_config.py:658-688` (binding must be absolute+declared).
- Status: **IMPLEMENTED (Cycle 2)**. Cycle 1 delivered init-time boundary
  enforcement; Cycle 2 adds continuous per-operation enforcement via the
  PreToolUse gate (`pretool_guard.py:442-476` revalidates path identity on
  every tool call, closing feature-flow-v2 section 9 gap 2; `pretool_guard.py:183-189`
  cwd must match Workspace; `pretool_guard.py:297-330` external roots deny-
  write/deny-read, targets outside all roots blocked; `pretool_guard.py:349-357`
  existing write targets re-resolved; `policy.py:261-275` codebase_read requires
  declared alias). Tests: `test_security.py:808-844`, `945-1011`.

### SCOPE-002 — Business Codebase read-only in v1; no edit/execute/install/commit
- Evidence: schema locks `read_mode` to `{"adapter"}` only
  (`.claude/schemas/chatbi-harness.schema.json:63`); `CLAUDE.md:33-35`;
  `.claude/rules/10-security.md:11-13`; `chatbi-init.md:20` ("Never execute
  files discovered there").
- Tests: `tests/harness/test_config.py:194-216` (`read_mode: "execute"`
  rejected by schema); `tests/harness/test_security.py:945-1011` (external
  write/edit/bash blocked by PreToolUse); `tests/harness/test_adapters.py:1756-1783`
  (codebase_reader capabilities read-only; execute/write/install/commit raise
  `CodebaseScopeBlockError`).
- Cycle 2 evidence: `pretool_guard.py:297-305` (external root write/Edit
  blocked SCOPE-001/SCOPE-002/HOOK-004); `pretool_guard.py:391-398` (Bash
  referencing external root blocked); `codebase_reader.py:810-828`
  (execute/write/install/commit raise `CodebaseScopeBlockError`);
  `codebase_reader.py:460-475` (capabilities declare execute/write/install/
  commit as False).
- Status: **IMPLEMENTED (Cycle 2)**. Cycle 1 schema-locked `read_mode` to
  `adapter` only; Cycle 2 adds tool-layer deny-write/deny-execute via
  PreToolUse and the codebase_reader's read-only interface.

### SCOPE-003 — external content not instructions; cross-boundary citation = alias + relative path + revision
- Evidence: `CLAUDE.md:14-15,36-37`; `paths.py:128-144` (`PortablePathReference`
  carries only `alias`, `relative_path`, `revision`, `revision_kind`);
  `CONTEXT.md:18-19`.
- Tests: `tests/harness/test_paths.py:688-733` (portable reference round-trip,
  no absolute root in output).
- Status: **IMPLEMENTED (Cycle 2)**. Cycle 1 delivered portable citation
  generation; Cycle 2 adds runtime instruction-ignore enforcement:
  `codebase_reader.py:479-564` (read wraps content as `untrusted=true`);
  `codebase_reader.py:348-375` (instruction candidates detected and logged as
  rejected, never acted upon); `pretool_guard.py:306-320` (direct external
  reads blocked -- must use the adapter). Tests: `test_adapters.py:1799-1823`
  (read returns untrusted content), `1863-1935` (instructions detected as
  rejected), `982-996` (external read blocked, recovery says "use adapter").

### SEC-001 — check access/PII before action; stop if insufficient; no privilege escalation
- Evidence: `config.py:288-294` (`fail_if_sandbox_unavailable` must be true);
  `diagnostics.py:591-599` (`claude_login` check blocks if not logged in);
  `diagnostics.py:600-608` (`sandbox` check blocks if unavailable);
  `python_binding_launcher.py:94-112` (confirmed Python binding must resolve
  outside Workspace and Business roots).
- Tests: `tests/harness/test_config.py:737-752` (sandbox policy cannot be
  disabled); `tests/harness/test_hooks.py:160-237` (invalid Python bindings
  fail before execution); `tests/harness/test_diagnostics.py:249-295`
  (missing capabilities blocked).
- Status: **IMPLEMENTED (Cycle 2)**. Cycle 1 delivered init-time capability
  and config gates; Cycle 2 adds the per-action deterministic access precheck
  primitive `policy.decide` (`policy.py:88-174`): PII policy missing -> block
  SEC-002; mutate_warehouse -> block; network default deny; codebase_read
  requires declared alias; workspace write respects config flag; high-risk
  class -> warn requiring sign-off. The PreToolUse gate calls `policy.decide`
  for workspace writes (`pretool_guard.py:362-369`). Tests: `test_security.py:127-466`
  (policy access/PII/capability/risk tests). Analysis-path integration
  (calling `policy.decide` before every query) is Cycle 3.

### SEC-002 — restricted data disclosure policy; SQL-only mode returns no results/samples
- Evidence: schema `restricted_disclosure` enum `["sql_only", null]`
  (`schema:103-106`); `diagnostics.py:641-651` (`pii_policy` check requires both
  `pii_policy_ref` and `restricted_disclosure` to be set).
- Tests: `tests/harness/test_diagnostics.py:249-295` (unconfigured PII policy
  is a blocked check).
- Status: **IMPLEMENTED (Cycle 2)**. Cycle 1 declared the disclosure mode and
  verified it is configured; Cycle 2 adds the deterministic disclosure policy
  primitive in `policy.decide` (`policy.py:184-226`): PII policy missing ->
  block SEC-002/SEC-001; `sql_only` with `purpose != "compile"` -> block
  SEC-002 ("results and samples are withheld"); `sql_only` with
  `purpose == "compile"` -> pass ("do not return results"). Tests:
  `test_security.py:190-273` (PII missing blocks; sql_only blocks result return
  and gives SQL guidance; sql_only allows compile; configured PII allows
  query). Analysis-path integration (calling `policy.decide` before every
  query) is Cycle 3.

### SEC-003 — no PII/secrets in logs/evals/corrections; prefer structure/hashes/aggregates
- Evidence: `gates.py:39-45` (`_sanitize_text` redacts secrets/paths in every
  decision); `config.py:402-408` (reject secrets+absolute paths in shared
  config); `config.py:418-426` (reject secrets in local config);
  `paths.py:61-78` (sanitized path error refs); `session_diagnose.py:183-189`
  (sanitized hook input failures).
- Tests: `tests/harness/test_gates.py:53-76` (redaction of paths/secrets/URLs);
  `tests/harness/test_config.py:218-312` (machine paths/secrets rejected);
  `tests/harness/test_hooks.py:331-358` (no secret leak on bad input);
  `tests/harness/test_paths.py` (every negative case asserts canary absent).
- Status: **IMPLEMENTED (Cycle 1)**, reinforced by Cycle 2. Sanitization is
  mandatory in `GateDecision.__post_init__` and secret rejection covers
  shared+local config. Cycle 2 adds: adapter evidence payloads tagged
  `untrusted=true` and hashed (`adapters/base.py:47-56`,
  `adapters/__init__.py:422-432`); codebase_reader wraps all content as
  untrusted data (`codebase_reader.py:531-540`); PreToolUse and ConfigChange
  sanitize canary secrets in all failure output (`test_security.py:846-884`,
  `1631-1647`).

## 9.2 Request clarification and entity resolution

### REQ-001 — determine request type, business question, time range, segment, decision before query
- Evidence (routing only): `.claude/rules/00-domain-contract.md:9-12`;
  `CLAUDE.md:72-79` routes `/chatbi-analyze` as the expected route.
- Status: **PLANNED: Cycle 3**. No analysis command is implemented in Cycle 1;
  the contract declares the route and the rule.

### REQ-002 — resolve polysemous terms via business context; no guessing
- Evidence (routing only): `.claude/rules/00-domain-contract.md:11-12`;
  `CLAUDE.md:60`.
- Status: **PLANNED: Cycle 3**.

### REQ-003 — Entity Resolution records selected entity, grain, filters, exclusions, rejected alternatives
- Evidence (routing only): `.claude/rules/00-domain-contract.md:11-12`.
- Status: **PLANNED: Cycle 3**.

### REQ-004 — multiple team definitions -> present candidates, ask context
- Evidence (routing only): `.claude/rules/00-domain-contract.md:13-14`.
- Status: **PLANNED: Cycle 3**.

## 9.3 Source selection and execution

### SEM-001 — discover semantic layer first; fallback only after proven gap/failure
- Evidence (routing only): `.claude/rules/00-domain-contract.md:18-20`;
  `CLAUDE.md:56-58`.
- Cycle 2 evidence: `adapters/__init__.py:495-718` (`select_adapter` selection
  chain managed->CLI->STOP); `adapters/base.py:210-239` (`Adapter` protocol
  with `discover`/`compile`/`query`); when no adapter is usable, STOP with
  SEM-001/PORT-001/HOOK-004 and `missing_capabilities` (`adapters/__init__.py:710-718`).
- Tests: `tests/harness/test_adapters.py:765-1137` (managed->CLI selected;
  managed->CLI not configured -> STOP; both unavailable -> STOP; no adapters
  -> STOP; STOP carries SEM-001 and minimal authorization).
- Status: **PARTIAL (Cycle 2)**. The adapter discover/compile protocol and
  the managed->CLI->STOP selection chain are implemented and tested. The
  analysis-path integration (calling `select_adapter` during analysis,
  recording source-routing evidence, falling back with proof) is Cycle 3.

### SEM-002 — semantic discovery checks metrics, dimensions, segments; no hand-written WHERE for existing segments
- Evidence (routing only): `.claude/rules/00-domain-contract.md:18-19`.
- Cycle 2 evidence: `adapters/fixture.py:216-222` (`FixtureAdapter.discover`
  returns `semantic-catalog.json` as evidence); `.claude/fixtures/semantic-catalog.json`
  contains `metrics`, `dimensions`, `segments` arrays (SEM-002 contract);
  `adapters/base.py:210-239` (`Adapter.discover` returns `AdapterEvidence`
  with structured payload).
- Tests: `tests/harness/test_adapters.py:1515-1545` (catalog has metrics/
  dimensions/segments with IDs and names), `1614-1628` (discover payload
  equals catalog file).
- Status: **PARTIAL (Cycle 2)**. The fixture catalog evidence contract
  defines metrics/dimensions/segments and is tested. The `Adapter` protocol
  returns structured discover evidence. However, the protocol does not
  enforce SEM-002 validation on every `discover()` payload (no schema
  assertion that all three are present for non-fixture adapters). Analysis-
  path enforcement (checking discovery results contain all three before
  proceeding, no hand-written WHERE for existing segments) is Cycle 3.

### SEM-003 — LLM may draft metric docs but cannot create/approve canonical definitions; humans confirm
- Evidence: `CLAUDE.md:25-27`; `.claude/rules/00-domain-contract.md:26-28`;
  `config.py:278-287` (the four protected actions including `approve_metric`
  are mandatory and cannot be removed); schema `protected_actions` enum
  includes `approve_metric` (`schema:43-48`); `chatbi-init.md:66`.
- Tests: `tests/harness/test_config.py:720-735` (all protected actions
  mandatory; removing one blocks); `tests/harness/test_config.py:485-510`
  (local config cannot override protected policy).
- Status: **IMPLEMENTED (Cycle 1)** for the protection gate, reinforced by
  Cycle 2. `approve_metric` is a non-disableable protected action and local
  config cannot weaken it. Cycle 2 adds `policy.decide` (`policy.py:107-118`)
  which blocks agent self-approval of protected actions with SEM-003/SEC-001
  ("Agent cannot self-approve protected action; drafting is not approval").
  Tests: `test_security.py:130-149` (protected action by agent blocked).
  Runtime prevention during the analysis path (the analysis command calling
  `policy.decide` before approving) is Cycle 3.

### RAW-001 — raw SQL is fallback only; record semantic gap evidence; use curated references
- Evidence (routing only): `.claude/rules/00-domain-contract.md:20-21`;
  `CLAUDE.md:57-59`.
- Status: **PLANNED: Cycle 3**.

### RAW-002 — no bypassing semantic layer for custom dates/joins/convenience
- Evidence (routing only): `.claude/rules/00-domain-contract.md:22-23`.
- Status: **PLANNED: Cycle 3**.

### RAW-003 — no fabricating tables/fields/data; use explicit joins/denominators/filters/time
- Evidence (routing only): `.claude/rules/00-domain-contract.md:24`;
  `CLAUDE.md:60-61`.
- Status: **PLANNED: Cycle 3**.

### SRC-001 — historical SQL/notebooks/dashboards are candidate clues only, not correctness proof
- Evidence (routing only): `.claude/rules/00-domain-contract.md:24`;
  `CLAUDE.md:59`.
- Status: **PLANNED: Cycle 3**. Historical-SQL-as-clue is enforced on the
  analysis path (Cycle 3); the underlying historical SQL/notebook artifacts may
  also be curated during Cycle 4 knowledge maintenance, but the rule's
  enforcement point (treating them as candidate clues, not correctness proof)
  is the analysis path.

### SRC-002 — external Codebase interpretation cross-checked against governed facts; conflicts -> domain owner
- Evidence (routing only): `.claude/rules/00-domain-contract.md:25-26`;
  `CONTEXT.md:42-43`.
- Cycle 2 evidence: `codebase_reader.py:383-429` (`_detect_conflicts` scans
  external content for metric-definition-like lines and compares them against
  a provided `governance_context["metrics"]`; conflicts are disclosed with
  `metric_name`, `external_definition`, `governance_definition`,
  `relative_path`, `line_number`); `codebase_reader.py:544-553` (when
  conflicts are found, SRC-002 is added to `rule_ids`, reason mentions
  conflicts, recovery says "Request the domain owner to adjudicate; do not
  auto-define or override metrics"); `codebase_reader.py:2291-2313` (reader
  does not auto-define metrics -- governance context is not modified).
- Tests: `tests/harness/test_adapters.py:2229-2313` (conflict disclosed when
  external definition differs; no conflict when governance context not
  provided; no conflict when definitions match; reader does not auto-define
  metrics).
- Status: **IMPLEMENTED (Cycle 2)**. The codebase_reader discloses conflicts
  between external content and governance definitions for owner adjudication.
  It never auto-defines or overrides metrics. The rule's enforcement point
  (cross-checking external interpretations against governed facts) is
  delivered as a deterministic primitive.

## 9.4 Documentation and maintenance

### DOC-001 — model knowledge co-located or atomic change mechanism; model changes assess doc impact
- Evidence (routing only): `.claude/rules/00-domain-contract.md:31-33`.
- Status: **PLANNED: Cycle 4** (model/knowledge maintenance).

### DOC-002 — governed metadata: descriptions, grain, scope, values, filters, exclusions, join keys, lineage, owner, layer
- Evidence (routing only): `.claude/rules/00-domain-contract.md:34-36`.
- Status: **PLANNED: Cycle 4**.

### DOC-003 — references state "use for"/"do not use for" + pitfalls
- Evidence (routing only): `.claude/rules/00-domain-contract.md:37`.
- Status: **PLANNED: Cycle 4**.

### DOC-004 — model/semantic changes trigger Hook/CI checks on related Skills/refs/evals/downstream; blocking drift fails completion
- Evidence (routing only): `.claude/rules/20-completion.md:46-47`;
  `CLAUDE.md:98-100`.
- Status: **PLANNED: Cycle 4**. DOC-004 (model-semantic changes trigger
  Hook/CI checks on related Skills/refs/evals/downstream; blocking drift
  fails completion) is a Cycle 4 rule. Cycle 2 delivered the Hook
  infrastructure (PreToolUse/ConfigChange thin entries + GateError reuse +
  settings mapping pattern) that is the prerequisite for Cycle 4's PostToolUse
  impact check, but DOC-004 itself (the impact check) is not implemented.
  Only `SessionStart` is mapped in the dev `settings.json`; PreToolUse and
  ConfigChange settings are DEFERRED to Cycle 5 E2E.

### DOC-005 — remove obsolete scaffolding when evidence supports
- Evidence (routing only): `.claude/rules/00-domain-contract.md:39`.
- Status: **PLANNED: Cycle 4**.

### PORT-001 — no hardcoded machine absolute paths or single-entry namespace; config resolves logical aliases to paths
- Evidence: `config.py:402-408` (reject absolute paths in shared config);
  `config.py:324-350` (path_ref/path_bindings logical resolution);
  `paths.py:128-144` (portable references carry no absolute root);
  `gates.py:19-25,39-45` (redact absolute paths in all decisions);
  `python_binding_launcher.py:94-112` (confirmed executable outside roots);
  `CLAUDE.md:36-37`; `chatbi-init.md:66`.
- Tests: `tests/harness/test_config.py:218-312` (machine paths rejected);
  `tests/harness/test_paths.py:688-733` (portable reference, no absolute root);
  `tests/harness/test_gates.py:53-76` (path redaction).
- Status: **IMPLEMENTED (Cycle 1)**, reinforced by Cycle 2. No shared artifact
  may contain a machine absolute path; all citations are alias + relative path
  + revision. Cycle 2 adds: adapter IDs have the form
  `<family>:<name>` with no machine paths (`adapters/base.py:25`,
  `test_adapters.py:1146-1148`); adapter evidence and codebase evidence fixed
  fields are free of machine paths (`test_adapters.py:1145-1210`,
  `2395-2469`); CLI adapter environment uses only a safe PATH
  (`adapters/__init__.py:68-72`); adapter selection evidence tagged
  `evidence_source="local_probe"` never carries machine paths
  (`test_adapters.py:1155-1178`).

## 9.5 Validation and delivery

### QLT-001 — candidate answer checks freshness/completeness/anomalies; correct SQL does not fix bad data
- Evidence (routing only): `.claude/rules/20-completion.md:10-11`;
  `CLAUDE.md:87`.
- Status: **PLANNED: Cycle 3** (data quality checks).

### REV-001 — every candidate data conclusion needs independent adversarial reviewer; main Agent cannot self-certify
- Evidence (routing only): `CLAUDE.md:88-90`; `.claude/rules/20-completion.md:12-13`.
- Status: **PLANNED: Cycle 3** (adversarial review subagent).

### REV-002 — review covers entity/grain/joins/filters/dates/denominator/sample/quality/observation-vs-interpretation/provenance
- Evidence (routing only): `.claude/rules/20-completion.md:14-17`.
- Status: **PLANNED: Cycle 3**.

### REV-003 — blocking findings must be fixed and re-reviewed; cannot silently accept
- Evidence (routing only): `.claude/rules/20-completion.md:18-19`;
  `CLAUDE.md:90`.
- Status: **PLANNED: Cycle 3**.

### ANS-001 — final answer distinguishes observation from interpretation; discloses method/filters/limitations
- Evidence (routing only): `CLAUDE.md:91-94`; `.claude/rules/20-completion.md:20-22`.
- Status: **PLANNED: Cycle 3**.

### ANS-002 — every answer has provenance footer: source tier, review status/round, freshness, owner, confidence
- Evidence (routing only): `CLAUDE.md:91-94`; `.claude/rules/20-completion.md:20-22`.
- Status: **PLANNED: Cycle 3**.

### ANS-003 — raw exploration/unknown freshness -> high-risk recheck warning; executive material needs human sign-off
- Evidence (routing only): `CLAUDE.md:95-97`; `.claude/rules/20-completion.md:21-23`.
- Status: **PLANNED: Cycle 3**.

## 9.6 Evaluation and feedback

### EVAL-001 — each released domain has human-verified offline Q&A/query evaluation
- Evidence (routing only): `.claude/rules/20-completion.md:26`; `CLAUDE.md:101`.
- Status: **PLANNED: Cycle 5** (offline evaluation).

### EVAL-002 — anchor cases to snapshots/stable facts or score query/entity selection
- Evidence (routing only): `.claude/rules/20-completion.md:27`.
- Status: **PLANNED: Cycle 5**.

### EVAL-003 — Evaluation Run records suite/Skill version, Git SHA, model ID, assertions, tokens, latency
- Evidence (routing only): `.claude/rules/20-completion.md:28`.
- Status: **PLANNED: Cycle 5**.

### EVAL-004 — release threshold configurable and owner-approved; no hardcoded vendor benchmark
- Evidence: `config.py:295-308` (a set `release_threshold` requires a non-blank
  `threshold_owner`); schema `release_threshold` `minimum: 0`
  (`schema:140`); `diagnostics.py:652-664` (`release_threshold` check blocks
  unless both threshold and owner are configured); `chatbi-init.md:66`.
- Tests: `tests/harness/test_config.py:754-807` (threshold requires owner,
  blank owner rejected, numeric minimum enforced);
  `tests/harness/test_config.py:121-143` (non-finite threshold rejected).
- Status: **IMPLEMENTED (Cycle 1)**. No vendor benchmark is hardcoded; the
  threshold is optional but, if set, requires an explicit human owner.

### EVAL-005 — semantic-covered cases assert semantic-layer use; offline accuracy ~100% but no claim of eliminating online errors
- Evidence (routing only): `.claude/rules/20-completion.md:31`.
- Status: **PLANNED: Cycle 5**.

### ABL-001 — meaningful changes fix eval set, change one component, record diff/cost/latency
- Evidence (routing only): `.claude/rules/20-completion.md:32-33`.
- Status: **PLANNED: Cycle 5**.

### ABL-002 — retain concise negative experiment list
- Evidence (routing only): `.claude/rules/20-completion.md:34`.
- Status: **PLANNED: Cycle 5**.

### FBK-001 — corrections structured-collected into weekly/cycle review; track semantic-layer ratio and corrective language ratio
- Evidence (routing only): `.claude/rules/20-completion.md:35`.
- Status: **PLANNED: Cycle 5** (correction feedback loop).

### FBK-002 — each accepted Correction Record proposes repair candidate AND evaluation case candidate, human-approved
- Evidence (routing only): `.claude/rules/20-completion.md:36-37`; `CLAUDE.md:79`
  routes `/chatbi-correction`.
- Status: **PLANNED: Cycle 5**.

### FBK-003 — Harness admits it cannot fully detect silent failure; eval pass is not absolute correctness guarantee
- Evidence: `CLAUDE.md:101-102` ("Evaluation success is evidence, not a
  guarantee that silent failure is gone"); `.claude/rules/20-completion.md:38`;
  `diagnostics.py:336-339` (`production_ready` always false); `chatbi-init.md:54-55`
  (Cycle 1 PASS does not set production_ready).
- Tests: `tests/harness/test_diagnostics.py:330-339` (PASS result still has
  `production_ready=false`); `tests/harness/test_hooks.py:492` (compatibility
  doc must not claim "fixture is production").
- Status: **IMPLEMENTED (Cycle 1)**. The non-guarantee is stated in the root
  contract, the completion rule, and enforced by the always-false
  `production_ready` flag.

## 9.7 Hook design constraints

### HOOK-001 — Hooks do deterministic gating, not open-ended interpretation
- Evidence: `session_diagnose.py` is a thin entry that only validates event
  shape and calls the shared library (`session_diagnose.py:192-229`); no
  business reasoning in the hook; `.claude/rules/20-completion.md:42`.
- Tests: `tests/harness/test_hooks.py` (entire file exercises deterministic
  shape/path/library behavior); `tests/harness/test_security.py:681-918`
  (PreToolUse deterministic contract); `test_security.py:1356-1447`
  (ConfigChange deterministic contract).
- Cycle 2 evidence: `pretool_guard.py:1-13` ("deterministic gate (HOOK-001):
  it only calls paths/policy/gates library primitives and performs field
  comparisons. It never evals, opens a shell, or executes external codebase
  content"); `config_change_gate.py:1-9` (same deterministic gate contract);
  `policy.py:1-9` (deterministic primitive, no open-ended interpretation).
- Status: **IMPLEMENTED (Cycle 1)**, reinforced by Cycle 2. All three hooks
  (SessionStart, PreToolUse, ConfigChange) are deterministic thin entries
  that only call library primitives.

### HOOK-002 — define gate capabilities first, then map to verified event names/config schema
- Evidence: `settings.json:1-15` maps only `SessionStart` with matcher
  `startup|resume|clear|compact` after local probe; `docs/harness/compatibility.md:5-11`;
  `diagnostics.py:560-581` (version check references HOOK-002).
- Tests: `tests/harness/test_hooks.py:447-492` (settings document only the
  verified contract; no unimplemented Hook events).
- Status: **IMPLEMENTED (Cycle 1)**, reinforced by Cycle 2. Only locally
  verified events are mapped. Cycle 2 adds PreToolUse and ConfigChange event
  mappings (documented in `docs/harness/security.md` sections 4 and 7 for
  product install; DEFERRED to Cycle 5 E2E in the dev `settings.json` which
  remains SessionStart-only). The event names, field shapes, and exit
  semantics were confirmed from official Claude Code documentation before
  implementation, then verified by offline subprocess tests.

### HOOK-003 — do not assume Hook event names/fields/exit semantics/config schema before capability probe
- Evidence: `session_diagnose.py:40-47` (documented required/optional fields
  verified offline); `docs/harness/compatibility.md:21-34` (field shape
  verified by offline subprocess test); `chatbi-init.md:66`.
- Tests: `tests/harness/test_hooks.py:309-329` (valid event shape),
  `494-542` (invalid shapes fail closed).
- Status: **PARTIAL (Cycle 2)**. The offline-verified contract now covers
  three events: SessionStart (Cycle 1: `session_diagnose.py:40-47`), PreToolUse
  (Cycle 2: `pretool_guard.py:54, 130-191` -- required fields `cwd`/
  `tool_name`/`tool_input`/`tool_use_id`, exit 0/2 semantics), and ConfigChange
  (Cycle 2: `config_change_gate.py:72, 133-171` -- required field `source`,
  optional `file_path`, exit 0/2 semantics). Forward compatibility: unknown
  event-level fields are IGNORED for all three events (`pretool_guard.py:55-64`,
  `config_change_gate.py:24-29`). Real Claude E2E is explicitly not yet
  exercised (`docs/harness/compatibility.md` NOT YET EXERCISED); deferred to
  Cycle 5.

### HOOK-004 — Hook failure includes rule IDs, evidence location, recovery; no vague failure
- Evidence: `gates.py:52-141` (`GateDecision` always carries `rule_ids`,
  `evidence_refs`, `reason`, `recovery`); `session_diagnose.py:177-189`
  (`_write_failure` + `_input_failure`); `gates.py:153-167` (`fail_closed`).
- Tests: `tests/harness/test_gates.py:95-114` (stable required fields);
  `tests/harness/test_hooks.py:331-358` (failures carry rule IDs and recovery).
- Status: **IMPLEMENTED (Cycle 1)**, reinforced by Cycle 2. Every failure path
  produces a structured block decision; no vague failure string is emitted.
  Cycle 2 adds: `pretool_guard.py:194-197` (failure writes `GateDecision` JSON
  to stderr with `rule_ids`/`evidence_refs`/`reason`/`recovery`);
  `config_change_gate.py:174-177` (same structured failure);
  `pretool_guard.py:200-206` and `config_change_gate.py:180-186` (input
  failures carry SEC-003/HOOK-001/HOOK-004 + category-specific evidence ref);
  `pretool_guard.py:468-476` and `config_change_gate.py:342-350` (unexpected
  exceptions -> `fail_closed` with SEC-003/HOOK-001/HOOK-004). Tests:
  `test_security.py:750-784` (PreToolUse failures carry rule IDs and recovery),
  `1385-1417` (ConfigChange failures carry rule IDs and recovery).

### HOOK-005 — high-cost/nondeterministic evaluation via Command/CI; Hooks only determine if required/evidence present
- Evidence (routing only): `.claude/rules/20-completion.md:47-48`; `CLAUDE.md:78`
  routes `/chatbi-evaluate` as a Command.
- Status: **PARTIAL (Cycle 2)**. Cycle 1 routes evaluation to a Command but
  does not implement it. Cycle 2 establishes the compliance record: the
  PreToolUse and ConfigChange hooks perform only deterministic prechecks
  (path/policy/config revalidation) and never run high-cost evaluations
  (`pretool_guard.py:1-13`, `config_change_gate.py:1-9`). The "determine if
  evaluation is required and check evidence exists" part of the rule is
  Cycle 5 (when the evaluation Command is implemented). The hooks' compliance
  with the no-eval-in-hooks principle is verified by the test suite (no test
  invokes an evaluation inside a hook).

## Governing context (not gate-enforced rule IDs)

The following are design principles and failure modes in the domain model. They
inform the rules above but are not counted in the 46 executable rules and are
not matched by the `_RULE_ID` regex (`gates.py:26-29`). They require no
per-item implementation evidence; they are satisfied by the rules they govern.

- **META governing principles** (`docs/chatbi-harness-domain-model.md`
  section 3, nine principles): system principles (accuracy is
  context+validation; converge to one governed answer; declarative/procedural
  separation; structure over accumulation; correctness is maintained;
  transparent uncertainty; least privilege; generation/auth separation;
  portable capabilities). Reflected in the root contract routing
  (`CLAUDE.md:46-64,85-104`) and the fail-closed gate model.
- **Failure modes** (section 4, three modes): concept-entity ambiguity, data
  staleness, retrieval failure. Mapped to rule families REQ/SEM, DOC/QLT, and
  SRC plus the retrieval META principle in the domain model; no separate gate
  enforcement.

## Cycle 2 summary

| Status | Count | Rules |
| --- | --- | --- |
| IMPLEMENTED (Cycle 1, reinforced Cycle 2) | 8 | SEC-003, SEM-003, PORT-001, EVAL-004, FBK-003, HOOK-001, HOOK-002, HOOK-004 |
| IMPLEMENTED (Cycle 2) | 6 | SCOPE-001, SCOPE-002, SCOPE-003, SEC-001, SEC-002, SRC-002 |
| PARTIAL (Cycle 2) | 4 | SEM-001, SEM-002, HOOK-003, HOOK-005 |
| PLANNED: Cycle 3 | 15 | REQ-001..004, RAW-001..003, SRC-001, QLT-001, REV-001..003, ANS-001..003 |
| PLANNED: Cycle 4 | 5 | DOC-001/002/003/004/005 |
| PLANNED: Cycle 5 | 8 | EVAL-001/002/003/005, ABL-001/002, FBK-001/002 |

Note: HOOK-003 is counted as PARTIAL because its offline contract is verified
for three events (SessionStart, PreToolUse, ConfigChange) but real Claude E2E
is not. HOOK-005 is PARTIAL because the Cycle 2 hooks comply with the
no-eval-in-hooks principle (they only do deterministic gating), but the
"determine if evaluation is required and check evidence exists" part is
Cycle 5. SEM-001 and SEM-002 are PARTIAL because the adapter protocol and
fixture catalog evidence contract are implemented and tested, but analysis-
path integration is Cycle 3. DOC-004 is reassigned from "PLANNED: Cycle 2" to
"PLANNED: Cycle 4" per `docs/dev-cycle-2.md` section 3: DOC-004 is a Cycle 4
rule; Cycle 2 delivered only the Hook infrastructure prerequisite, not DOC-004
itself. SRC-001 is assigned to Cycle 3 (analysis path) as its primary
enforcement point. The per-rule rows above are authoritative; this table is a
summary aid and uses the rule's primary status. Total: 8 + 6 + 4 + 15 + 5 + 8 = 46.

## 9.7 Cycle 3 increment (analysis route, runtime evidence, independent review)

Cycle 3 implements the governed analysis route (`/chatbi-analyze`), runtime
evidence, the independent adversarial reviewer, and the SubagentStop/Stop gates.
This section supersedes the prior "PLANNED: Cycle 3" / "PARTIAL (Cycle 2)"
status for the rules below with per-rule Cycle 3 evidence (逐条). Code refs are
authoritative; see `docs/feature-flow-v4.md`.

### Upgraded to IMPLEMENTED (Cycle 3)

- **REQ-001** (request type/question/time/segment/decision before query):
  `commands/chatbi-analyze.md:34 §1` input contract + `:61 Layer 1` clarify;
  `schemas/request.schema.json` 7 required fields; `evidence.py:493
  validate_request`. Tests: `test_e2e.py test_all_scenario_requests_validate`.
- **REQ-002** (resolve polysemous terms, no guessing): `chatbi-analyze.md:61
  Layer 1` + `skills/chatbi-runbook/SKILL.md:44` overloaded-terms; ambiguity
  scenario stops for clarification. Tests: `test_e2e.py
  test_ambiguity_stops_at_clarification_no_fabrication`.
- **REQ-003** (entity resolution records entity/grain/filters/exclusions):
  `evidence.py:250 EvidenceEntry` records source_tier + rule_ids per tier;
  runbook Step 2. Tests: `test_analysis.py DegradationChainTests`.
- **REQ-004** (multiple definitions -> present candidates, ask context):
  `chatbi-analyze.md:61 Layer 1`; ambiguity scenario presents
  `candidate_definitions`. Tests: `test_e2e.py` ambiguity case.
- **SEM-001** (discover semantic layer first; fallback only after proven gap):
  upgraded from PARTIAL (Cycle 2). `chatbi-analyze.md:73 Layer 2` T1-first;
  `test_e2e.py _evidence_chain` records T1 before T2/T3; Cycle 2
  `select_adapter` reused. Tests: `test_e2e.py test_historical_sql_degrades_to_t3`.
- **SEM-002** (semantic discovery checks metrics/dimensions/segments; no
  hand-written WHERE): upgraded from PARTIAL (Cycle 2). FixtureAdapter
  discover + per-scenario `semantic-catalog-fragment.json`; runbook Step 2.
  Tests: `test_e2e.py test_t1_adapter_is_wired_for_t1_attempted_scenarios`.
- **RAW-001** (raw SQL fallback only; record semantic gap; curated refs):
  `chatbi-analyze.md:82 Layer 3`; historical-sql scenario T1->T2 with recorded
  gap. Tests: `test_e2e.py` historical-sql case.
- **RAW-002** (no bypassing semantic layer for custom dates/joins/convenience):
  `chatbi-analyze.md:82 Layer 3` / Layer 4 gate on recorded gap; runbook Step 3.
- **RAW-003** (no fabricating tables/fields/data; explicit joins/denominators):
  `chatbi-analyze.md` no-fabrication; `EvidenceEntry.create` fail-closed on
  None payload. Tests: `test_e2e.py test_no_evidence_bypass_fails`.
- **SRC-001** (historical SQL/notebooks/dashboards are clues only):
  `chatbi-analyze.md` historical-SQL-as-clue; historical-sql scenario
  (`sql_as_canonical_definition=false`). Tests: `test_e2e.py` historical-sql.
- **QLT-001** (candidate checks freshness/completeness/anomalies):
  `schemas/provenance.schema.json` quality + freshness fields; stale scenario
  blocks on quality+date coverage. Tests: `test_e2e.py
  test_stale_blocks_with_freshness_warning_and_signoff`.
- **REV-001** (independent reviewer; main Agent cannot self-certify):
  `agents/adversarial-reviewer.md` (read-only, no mutating tools);
  `hooks/subagent_review_gate.py` PASS+SHA-match enforcement. Tests:
  `test_review_gate.py`, `test_e2e.py test_candidate_change_invalidates_prior_pass`.
- **REV-002** (review covers 11 dimensions): `adversarial-reviewer.md:103 §4`
  11 coverage keys; `schemas/review.schema.json` 11 required coverage keys.
  Tests: `test_review_gate.py` coverage-fail BLOCKED.
- **REV-003** (blocking findings fixed + re-reviewed; no silent accept):
  `subagent_review_gate.py:92 MAX_REVIEW_ROUNDS=3` + block-finding exit 2 +
  stale-SHA forces new round. Tests: `test_review_gate.py`, `test_e2e.py`.
- **ANS-001** (observation vs interpretation; disclose method/filters/
  limitations): `schemas/provenance.schema.json` method/filters/limitations;
  runbook Step 7. Tests: `test_analysis.py EvidenceIntegrationTests`.
- **ANS-002** (provenance footer: source tier, review round, freshness, owner,
  confidence): `schemas/provenance.schema.json` 17 required fields;
  `evidence.py:512 validate_provenance`. Tests: `test_e2e.py
  test_delivered_footer_carries_all_required_fields`.
- **ANS-003** (raw/unknown freshness -> high-risk recheck; executive material
  -> human sign-off): `chatbi-analyze.md` footer section; historical-sql +
  stale scenarios assert freshness_warning + human_signoff. Tests: `test_e2e.py`.

### Reinforced by Cycle 3 (already IMPLEMENTED)

- **SRC-002** (external Codebase cross-checked; conflicts -> owner): reinforced
  by the prompt-injection scenario (instructions ignored/logged via Cycle 2
  `codebase_reader`). Tests: `test_e2e.py test_prompt_injection_t1_hit_*`.
- **SCOPE-001/002/003, SEC-001/002/003, PORT-001, HOOK-001/003/004/005**:
  reinforced by the review/stop gates (field tolerance, fail-closed,
  sanitization, no leak). Tests: `test_review_gate.py`, `test_e2e.py`.

### Remain PLANNED (out of Cycle 3 scope, correctly not pre-implemented)

- DOC-001..005: Cycle 4. EVAL-001/002/003/005, ABL-001/002, FBK-001/002:
  Cycle 5.

### Updated summary (Cycle 3)

| Status | Count | Rules |
| --- | ---: | --- |
| IMPLEMENTED (Cycle 1, reinforced 2/3) | 8 | SEC-003, SEM-003, PORT-001, EVAL-004, FBK-003, HOOK-001, HOOK-002, HOOK-004 |
| IMPLEMENTED (Cycle 2, reinforced 3) | 7 | SCOPE-001/002/003, SEC-001/002, SRC-001, SRC-002 |
| IMPLEMENTED (Cycle 3) | 17 | REQ-001..004, SEM-001, SEM-002, RAW-001..003, QLT-001, REV-001..003, ANS-001..003 |
| PARTIAL (Cycle 2) | 2 | HOOK-003, HOOK-005 (real CC E2E = Cycle 5) |
| PLANNED: Cycle 4 | 5 | DOC-001/002/003/004/005 |
| PLANNED: Cycle 5 | 7 | EVAL-001/002/003/005, ABL-001/002, FBK-001/002 |

Total: 8 + 7 + 17 + 2 + 5 + 7 = 46. Cycle 3 upgrades 17 rules (15 from
PLANNED + SEM-001/SEM-002 from PARTIAL) to IMPLEMENTED. HOOK-003/005 remain
PARTIAL: their offline contracts are verified but real Claude SubagentStop/Stop
+ evaluation E2E is Cycle 5. The offline reviewer is a SYNTHETIC contract
producer, not the real reviewer process (FBK-003).

## 9.8 Cycle 4 increment (model/knowledge maintenance, impact gating)

Cycle 4 implements model maintenance, knowledge co-location, and the change-
impact gate. This supersedes the prior "PLANNED: Cycle 4" status for DOC-001..005
with per-rule Cycle 4 evidence (逐条). See `docs/feature-flow-v5.md`.

### Upgraded to IMPLEMENTED (Cycle 4)

- **DOC-001** (knowledge co-located or atomic change; model changes assess doc
  impact): `commands/chatbi-maintain-model.md` + `chatbi-maintain-knowledge.md`
  route atomic changes co-located with the model; `impact.py` assesses doc/
  reference impact. Tests: `test_maintenance.py`, `test_e2e.py` maintenance slice.
- **DOC-002** (governed metadata: grain/scope/values/filters/exclusions/joins/
  lineage/owner/layer): `knowledge.py:19 REQUIRED_FIELDS` + `_template.md` +
  `lint_reference`. Tests: `test_knowledge.py test_template_passes_lint`.
- **DOC-003** (references state "use for"/"do not use for" + pitfalls):
  `knowledge.py:68 lint_reference` enforces non-empty "Use for"/"Do not use for"
  + cross-references. Tests: `test_knowledge.py`.
- **DOC-004** (model/semantic changes trigger Hook/CI checks on Skills/refs/
  evals/downstream; blocking drift fails completion): `impact.py` ImpactManifest
  + `hooks/posttool_impact.py` + Cycle 3 `stop_gate` reuse (unsynced -> exit 2).
  Tests: `test_maintenance.py`, `test_e2e.py test_model_only_change_unsynced_*`.
- **DOC-005** (remove obsolete scaffolding when evidence supports): `chatbi-
  maintenance/SKILL.md` §7 + `chatbi-knowledge/SKILL.md` §7 pruning guidance
  (SHOULD rule; enforced procedurally + via lint, not a hard gate).

### Reinforced by Cycle 4 (already IMPLEMENTED/PARTIAL)

- **HOOK-001/004/005**: PostToolUse gate is deterministic, fail-closed, and does
  no evaluation (only records impact). HOOK-003/005 remain PARTIAL (real CC E2E =
  Cycle 5).
- **SEM-003**: protected action (approve_metric/change_access_policy/
  production_publish/destructive_migration) blocked in maintain-model +
  posttool_impact. Tests: `test_maintenance.py test_protected_action_blocks_*`.
- **EVAL-001/003**: impact manifest records p0_eval_failed; PostToolUse blocks on
  it. Tests: `test_maintenance.py test_p0_eval_failed_blocks`.
- **SRC-001/002**: historical SQL `candidate_only` (knowledge lint); conflicts ->
  domain owner. Tests: `test_knowledge.py`.
- **PORT-001**: no machine absolute paths in references (lint) or impact
  manifests (sanitization). Tests: `test_knowledge.py`, `test_maintenance.py
  test_no_canary_leak`.

### Remain PLANNED (out of Cycle 4 scope)

- EVAL-001/002/003/005, ABL-001/002, FBK-001/002: Cycle 5 (evaluation/
  correction/ablation). DOC-005's hard-gate aspect (if any) is procedural in
  Cycle 4.

### Updated summary (Cycle 4)

| Status | Count | Rules |
| --- | ---: | --- |
| IMPLEMENTED (Cycle 1, reinforced 2/3/4) | 8 | SEC-003, SEM-003, PORT-001, EVAL-004, FBK-003, HOOK-001, HOOK-002, HOOK-004 |
| IMPLEMENTED (Cycle 2, reinforced 3/4) | 7 | SCOPE-001/002/003, SEC-001/002, SRC-001, SRC-002 |
| IMPLEMENTED (Cycle 3, reinforced 4) | 17 | REQ-001..004, SEM-001/002, RAW-001..003, QLT-001, REV-001..003, ANS-001..003 |
| IMPLEMENTED (Cycle 4) | 5 | DOC-001/002/003/004/005 |
| PARTIAL (Cycle 2) | 2 | HOOK-003, HOOK-005 (real CC E2E = Cycle 5) |
| PLANNED: Cycle 5 | 7 | EVAL-001/002/003/005, ABL-001/002, FBK-001/002 |

Total: 8 + 7 + 17 + 5 + 2 + 7 = 46. Cycle 4 upgrades DOC-001..005 to IMPLEMENTED.
HOOK-003/005 remain PARTIAL (real PostToolUse/SubagentStop/Stop + evaluation E2E
= Cycle 5). PostToolUse is record-only, not undo (first defense = Cycle 2
PreToolUse/sandbox). FBK-003.

## 9.9 Cycle 5 increment (evaluation, correction, final audit) - AUTHORITATIVE

Cycle 5 implements the evaluation/correction loop and the final audit. This
section is the **authoritative** final tally (earlier per-cycle summary counts
were aids; this audit reconciles to 46). See `docs/feature-flow-v6.md`.

### Upgraded to IMPLEMENTED (Cycle 5)

- **EVAL-001** (owner-verified high-freq + long-tail offline eval):
  `evaluator.py:116 GroundTruthVault` + `fixtures/evaluations/suite/{high-freq,long-tail}.json`.
  Tests: `test_evaluation.py`.
- **EVAL-002** (anchor to snapshots/stable facts or score query/entity):
  `evaluator.py:143 score` (custom scorer support; canonical equality default).
  Tests: `test_evaluation.py test_custom_scorer`.
- **EVAL-003** (run records skill version/content hash/model/assertions/token/
  latency): `evaluator.py:71 EvaluationRun` + `:167 build_evaluation_run`
  (content_hash via `compute_candidate_sha`, no Git). Tests: `test_evaluation.py
  test_build_run_records_all_fields`.
- **EVAL-005** (semantic-covered cases assert semantic-layer use):
  `chatbi-evaluate.md` + `chatbi-evaluation/SKILL.md` §6; `test_e2e.py
  test_semantic_covered_cases_assert_semantic_layer`.
- **FBK-001** (structured corrections; track semantic-layer resolution ratio +
  corrective-language ratio): `chatbi-correction.md` §3; `test_correction.py
  FBK001TrackingTests`.
- **FBK-002** (each correction produces fix + eval-case candidate; owner-approved
  merge): `evaluator.py:218 build_correction_record` (dual candidate,
  owner_approved=False default). Tests: `test_evaluation.py CorrectionTests`,
  `test_correction.py`.
- **ABL-001** (fixed suite, one-component-at-a-time, record deltas/cost/latency):
  `chatbi-correction.md` §4 + `chatbi-evaluation/SKILL.md` §2; `test_correction.py
  AblationTests`.
- **ABL-002** (concise negative-experiment list): `docs/harness/negative-experiments.md`.

### Reinforced by Cycle 5 (already IMPLEMENTED)

- **EVAL-004** (configurable owner-confirmed threshold; no hard-coded 90%):
  reinforced - `EvaluationRun.threshold_owner_confirmed` + `chatbi-evaluate.md` §4.
  Tests: `test_evaluation.py test_threshold_not_owner_confirmed_recorded`.
- **FBK-003** (pass != absolute correctness): reinforced - `evaluator.py:32
  FBK_003_STATEMENT` carried on every run + correction. Tests: `test_evaluation.py
  FBK003Tests`.

### Remain PARTIAL (real CC E2E = Task 06 human gate)

- **HOOK-003, HOOK-005**: offline contracts verified for all six hook events
  (SessionStart/PreToolUse/PostToolUse/SubagentStop/Stop/ConfigChange); real CC
  process E2E is Task 06. Upgraded to IMPLEMENTED only after Task 06 records
  real evidence. Until then PARTIAL.

### Final authoritative tally (46/46)

| Status | Count | Rules |
| --- | ---: | --- |
| IMPLEMENTED (Cycle 1, reinforced 2-5) | 8 | SEC-003, SEM-003, PORT-001, EVAL-004, FBK-003, HOOK-001, HOOK-002, HOOK-004 |
| IMPLEMENTED (Cycle 2, reinforced 3-5) | 7 | SCOPE-001/002/003, SEC-001/002, SRC-001, SRC-002 |
| IMPLEMENTED (Cycle 3, reinforced 4-5) | 16 | REQ-001..004, SEM-001/002, RAW-001..003, QLT-001, REV-001..003, ANS-001..003 |
| IMPLEMENTED (Cycle 4, reinforced 5) | 5 | DOC-001/002/003/004/005 |
| IMPLEMENTED (Cycle 5) | 8 | EVAL-001/002/003/005, FBK-001/002, ABL-001/002 |
| IMPLEMENTED (Cycle 5, live-confirmed) | 2 | HOOK-003, HOOK-005 (5/6 events live-fired + field-tolerant; ConfigChange offline-verified same mechanism) |

Total: 8 + 7 + 16 + 5 + 8 + 2 = 46, all IMPLEMENTED. (Cycle 3 is 16 rules, not 17 - the earlier
§9.7 summary line over-counted; this audit is authoritative.) After Task 06 real
E2E passes, HOOK-003/005 upgrade to IMPLEMENTED -> 46/46 IMPLEMENTED. Until then
the harness is合成-correctness-verified but not production-E2E-certified; the
real-E2E + sandbox + production-cert gaps remain hard gates (FBK-003).
