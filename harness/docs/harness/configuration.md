# ChatBI Harness configuration: explicit init fields

Cycle 1 separates portable shared policy from machine-local bindings.

## Shared configuration

`.claude/chatbi-harness.json` declares one Workspace alias, optional Business Codebase aliases,
adapter IDs, governance references, evaluation policy, and fail-closed runtime policy. Shared files
must not contain absolute machine paths, credential values, organization secrets, or placeholder
facts presented as real policy.

Production readiness requires real values supplied by accountable humans for:

- `governance.pii_policy_ref` and `governance.restricted_disclosure`;
- a default or metric-specific governed owner;
- `evaluation.release_threshold` and `evaluation.threshold_owner`;
- at least one configured adapter whose capability is actually available;
- `runtime.fail_if_sandbox_unavailable=true` plus a verified sandbox capability.

## Local configuration

The local layer may contain only `path_bindings` and `cli_adapters`. Obtain explicit confirmation
before persisting each alias-to-root binding or approved CLI declaration. Store credential
environment-variable names only, never their values. External Codebase roots remain read-only and
untrusted; a successful path diagnostic does not authorize execution from them.

## Diagnostic interpretation

- `PASS`: every current explicit-init check passed offline. Cycle 1 still reports
  `production_ready=false` until governed policy, sandbox, and adapters have closed-loop evidence.
- `WARN`: no hard check failed, but compatibility evidence is incomplete; `production_ready` is
  false until the recovery action is completed.
- `BLOCKED`: at least one mandatory contract, configuration, path, or capability check failed.

Path evidence is portable: alias, relative path, revision, and revision kind. A clean tracked file
may use `git_sha`; non-Git, dirty, directory, or otherwise untracked content uses `content_sha256`.
Configuration changes invalidate the prior effective configuration and diagnostic, so rerun
`/chatbi-init` after every confirmed local or shared change.
