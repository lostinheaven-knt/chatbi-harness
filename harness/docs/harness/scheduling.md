# Scheduling the governance crontab

The harness ships a PORTABLE crontab template at
`.claude/schedules/chatbi-governance.crontab`. It is a schedule specification, NOT a
runnable crontab as-is: the harness ships no scheduler and does not assume a resident
Agent (FR-2 non-goal). You adapt it to your real scheduling infrastructure.

## What it schedules

A unified set of periodic governance activities (not just the FR-2 drift chain):

| Activity | Command | Cadence | Evidence |
|---|---|---|---|
| Drift audit | /chatbi-audit-drift --scope all | daily | drift_report.json -> .chatbi/runs/<sid>/ |
| Reference refresh | /chatbi-maintain-knowledge | weekly | no write_state today (chat footer); follow-up |
| Model maintenance | /chatbi-maintain-model | weekly | no write_state today (chat footer); follow-up |
| Evaluation regression | /chatbi-evaluate | weekly | no write_state today (chat footer); follow-up |
| Correction review queue | /chatbi-correction | on-change | no write_state today (chat footer); follow-up |
| Readiness diagnostic | /chatbi-init | on-demand | diagnostic; no runs/ artifact |

## Two environment variables you MUST set

- `CHATBI_WORKSPACE`: absolute path to the Warehouse Workspace root.
- `CHATBI_INVOKE`: how your infra triggers a governance slash command. The harness
  does NOT guarantee a headless invocation path. `claude -p "/chatbi-xxx"` is a
  candidate; verify it loads the governance hooks and SKILLs in your environment.

If either is unset, the `${VAR:?}` expansion fails loud and the entry does not run
silently (fail-closed, HOOK-004).

## Adapting to your infra

- **cron (Vixie):** add `CHATBI_WORKSPACE=...` and `CHATBI_INVOKE=...` env lines at the
  top of your crontab, then the shipped command lines. Ensure cron's PATH can find
  your invoke binary.
- **Airflow:** translate each entry to a DAG task; set the two vars as Airflow
  variables / env; replace `${CHATBI_INVOKE:?}` with your operator's slash-command
  trigger.
- **k8s CronJob:** set the two vars in the container env; replace the cd+invoke with
  your container's entrypoint that triggers the slash command.

## Portability (PORT-001)

The shipped template contains NO machine paths. It is guarded by
`chatbi_harness.schedules.validate_crontab_portability` (unit tests) and the build
canary sweep. Do NOT edit a machine path into the shipped file; set `CHATBI_WORKSPACE`
in your infra. This mirrors the shared/local config split (see configuration.md):
machine specifics live in your environment, not in shipped artifacts.

## Human-protection

Every activity only DRAFTS candidates; all merges still require domain-owner approval
(SEM-003/FBK-002). The harness connects to no external communication system by default.

## Follow-ups (out of crontab-config-only scope)

- Persist maintain-knowledge/model, evaluate, correction results to
  `.chatbi/runs/<sid>/` (only audit-drift persists today).
- Verify a headless invocation path (`claude -p`) loads governance hooks/SKILLs.
- Optional: `/chatbi-init` could validate `CHATBI_WORKSPACE` is set + absolute.
