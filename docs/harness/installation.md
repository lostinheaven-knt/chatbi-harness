# ChatBI Harness installation: initialization and SessionStart diagnosis

This page documents the explicit `/chatbi-init` capability and offline `SessionStart` contract
delivered in Cycle 1 Tickets 04 and 05. It does not describe later analysis, maintenance, or
production rollout.

## Install and run

1. Place the shared Harness files under the intended Warehouse Workspace.
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
