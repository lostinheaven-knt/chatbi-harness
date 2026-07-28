# ChatBI Harness installation: initialization and SessionStart diagnosis

This page documents the explicit `/chatbi-init` capability and offline `SessionStart` contract
delivered in Cycle 1 Tickets 04 and 05. It does not describe later analysis, maintenance, or
production rollout.

## Pre-install self-check (target workspace)

The harness install directory becomes the Warehouse Workspace root: SCOPE-001
confines agent reads, writes, and commands to that root, so it must be a clean,
dedicated boundary - not a subdirectory of an existing project. Before copying,
confirm:

- **Dedicated, empty root.** The target is an empty directory (ignoring
  `.DS_Store`). `./install.sh <target>` aborts on a non-empty target unless
  passed `--force`. Installing inside an existing project blurs the SCOPE-001
  write boundary.
- **No `.claude/` collision.** The target must not already contain `.claude/`
  (another harness or Claude Code config). `install.sh` aborts on collision.
- **No `CLAUDE.md` collision.** The target must not already contain `CLAUDE.md`
  (contract collision). `install.sh` aborts on collision.
- **Python 3.10+ outside the boundary.** A confirmed absolute Python 3.10+
  executable must resolve OUTSIDE the target workspace (PORT-001). The harness
  uses `@dataclass(slots=True)` (3.10+); Apple's `/usr/bin/python3` is 3.9 on
  macOS and is too old - it is only the fixed bootstrap, never the runtime.
  Homebrew `/opt/homebrew/bin/python3` or any 3.10+ `python3.*` works. The
  launcher (`python_binding_launcher.py`) re-validates at every SessionStart and
  fails closed if the binding is missing, relative, non-executable, or resolves
  inside the workspace or a Business Codebase root.
- **`CHATBI_PYTHON` is an env var, never a shared setting.** It is read from the
  environment at SessionStart; do not persist it in `.claude/settings.json` or
  any shared/git-tracked file. Claude Code must inherit it - export it in the
  shell that starts `claude`, or in `~/.zshrc` / `~/.bashrc`.

`./install.sh <target>` performs the copy and the 3.10+/boundary check
automatically and prints the exact `export CHATBI_PYTHON=...` line. It does not
modify your shell rc; that edit is yours to make.

## Install and run

1. Place the shared Harness files under the intended Warehouse Workspace.
   Automated: run `./install.sh <workspace-root>` from this product directory -
   it copies the assets, verifies a Python 3.10+ executable outside the
   workspace, and prints the `CHATBI_PYTHON` export. Manual alternative: copy
   `.claude/`, `docs/`, `CLAUDE.md`, `CONTEXT.md`, and `e2e-state.py` into the
   workspace root. Either way, satisfy the pre-install self-check above first.
2. Review `.claude/chatbi-harness.json` without inserting machine paths, credentials, or invented
   organization facts.
3. If external roots or local CLIs are needed, copy the local example only after the user confirms
   each binding. Keep the resulting local file out of shared artifacts.
4. Obtain explicit confirmation of the exact absolute Python executable used by the Hook. It must
   exist, be executable, and remain outside the Workspace and every configured Business Codebase.
   Store it only in the non-sensitive local environment, for example
   `CHATBI_PYTHON=/confirmed/absolute/path/to/python`; never put it in shared settings.
5. From the Workspace root, invoke `/chatbi-init` and inspect its single diagnostic JSON object.
6. Resolve every `BLOCKED` or `WARN` check using its recovery action. Cycle 1 never claims
   production readiness, including when the offline diagnostic reports `PASS`.

## Bootstrap a from-zero Warehouse (`/chatbi-bootstrap`, MySQL-only v1)

After `/chatbi-init` reports a clean installation, `/chatbi-bootstrap` scaffolds a
from-zero local Warehouse so the agent can then build ODS/DWD/DWS via
`/chatbi-maintain-model`. It is **MySQL-only v1** and **INFRA SETUP only** - it
does not create governed models, approve metrics, publish, or run destructive
migrations (SEM-003).

What it does:

- Writes `.claude/chatbi-harness.local.json` with `cli_adapters.mysql` (argv +
  `credential_env_names`) and an optional `path_binding` for a declared Business
  Codebase `path_ref`. The password is an env var NAME (e.g. `MYSQL_PWD`) or
  empty for local no-password root; never a value (SEC-003).
- Appends `cli:mysql` to shared `adapters.query` (idempotent, if absent). This is
  the one shared-config write.
- Creates the `dw` database via `CREATE DATABASE IF NOT EXISTS dw`
  (non-destructive; warns if `dw` already has tables).
- Introspects the source `public` schema via `INFORMATION_SCHEMA` and writes
  `.chatbi/bootstrap/source_inventory.json` (the hand-off to
  `/chatbi-maintain-model`).
- Scaffolds `dbt_project.yml` + `models/{ods,dwd,dws,dim}/` (empty) + a stub
  `docs/org/data-warehouse-blueprint.md`.

Before running it, confirm the exact absolute `mysql` executable path (e.g.
`/usr/local/mysql/bin/mysql` or an Homebrew `mysql-client` path). This mirrors
the `CHATBI_PYTHON` / `claude_executable` confirmation pattern: the confirmed
realpath is the single-element `cli_allowlist` passed to `resolve_executable`.
Do NOT execute `mysql` from unconfirmed inherited `PATH` - an empty allowlist
means the CLI adapter chain STOPs fail-closed (SEC-001/PORT-001/HOOK-004).

Evidence status: the deterministic lib surface (`build_mysql_adapter_spec`,
`merge_local_config`, `SourceInventory`) is **VERIFIED OFFLINE** by
`tests/harness/test_bootstrap.py`. Live MySQL execution (`CREATE DATABASE`,
`INFORMATION_SCHEMA` introspection) is **NOT YET EXERCISED** in CI; the runbook
at `.claude/skills/chatbi-bootstrap/SKILL.md` covers the live path for manual
E2E. StarRocks / other MySQL-protocol engines are unverified for v1.

## Evidence status

- **VERIFIED OFFLINE:** Python tests exercise the real domain contract, shared/local config loader,
  realpath identity checks, portable revisions, normalized capability snapshots, and deterministic
  `PASS | WARN | BLOCKED` JSON without Git, login, Warehouse credentials, or network access.
- **NOT YET EXERCISED:** a real logged-in Claude process loading project settings and triggering
  SessionStart, managed sandbox enforcement, and real adapter connections require later real E2E.
- **PRODUCTION BLOCKER:** missing Claude login, verified sandbox, configured adapter capabilities,
  domain owner, PII policy/disclosure mode, or owner-approved release threshold. The Harness never
  fills these with sample values.

The Claude version and doctor probes use bounded subprocess calls and retain only normalized status
fields. Raw stdout/stderr, environment values, credentials, absolute executable paths, and local
roots are not persisted in the diagnostic. Claude is discovered only from the strict system
allowlist or an absolute executable path the user explicitly confirmed; arbitrary inherited
`PATH` directories are not executed.

The SessionStart setting invokes only `.claude/hooks/session_diagnose`. This relative launcher has
no PATH fallback: it fails with exit 2 before the confirmed Python runtime starts when
`CHATBI_PYTHON` is missing, relative, non-executable, or resolves inside the Workspace or a locally
configured Business root.
The launcher never falls back to a bare `python3` command. On the verified Darwin baseline it uses
only the fixed OS `/usr/bin/python3` bootstrap to parse the bounded local JSON and realpath every
root before it executes the confirmed runtime. Absence of that trusted bootstrap also fails closed;
other operating systems remain unverified until an equivalent fixed bootstrap passes this matrix.

If Git metadata is unavailable, initialization keeps `content_sha256` revision evidence. This is a
supported portability fallback, not proof that later safety and review gates have run.
