#!/bin/sh
# Build the clean installable ChatBI harness product from this dev workspace.
#
# Usage:
#   ./build-product.sh                # builds ../chatbi
#   ./build-product.sh /tmp/chatbi    # custom destination
#
# The product is a strict subset of this dev repo: the harness code, hooks,
# commands, skills, schemas, fixtures, rules, config, docs/harness, the domain
# model, and the root contract files. It EXCLUDES the dev-workflow framework
# (orchestrate command, plan/coder/test agents), tests, scratch, and dev docs
# (technical-design, requirements, dev-cycles, orchestrator-state, feature-flow,
# test-*, optimization-*). Re-run anytime; it rebuilds from this source.
set -e

DEV="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-../chatbi}"

echo "Building product: $DEV -> $DEST"
rm -rf "$DEST"
mkdir -p "$DEST/.claude/agents" "$DEST/.claude/commands" "$DEST/.claude/skills" \
         "$DEST/docs/harness"

# --- harness code + hooks + schemas + fixtures + rules + config ---
rsync -a --exclude='__pycache__' "$DEV/harness/.claude/lib/"        "$DEST/.claude/lib/"
rsync -a --exclude='__pycache__' "$DEV/harness/.claude/hooks/"      "$DEST/.claude/hooks/"
rsync -a                         "$DEV/harness/.claude/schemas/"    "$DEST/.claude/schemas/"
rsync -a                         "$DEV/harness/.claude/fixtures/"   "$DEST/.claude/fixtures/"
rsync -a                         "$DEV/harness/.claude/rules/"      "$DEST/.claude/rules/"
rsync -a                         "$DEV/harness/.claude/schedules/"  "$DEST/.claude/schedules/"
cp "$DEV/harness/.claude/settings.json" "$DEST/.claude/settings.json"

# --- governance kernel + runtimes (multi-runtime module 2) ---
rsync -a --exclude='__pycache__' "$DEV/harness/packages/"  "$DEST/packages/"
rsync -a --exclude='__pycache__' "$DEV/harness/runtimes/"  "$DEST/runtimes/"
cp "$DEV/harness/.claude/chatbi-harness.json" \
   "$DEV/harness/.claude/chatbi-harness.example.json" \
   "$DEV/harness/.claude/chatbi-harness.local.example.json" "$DEST/.claude/"

# --- multi-runtime module 3/4: IR workflows + prompt assets + conformance
#     snapshots (golden/expected are READ-ONLY frozen baselines) ---
rsync -a "$DEV/harness/workflows/"  "$DEST/workflows/"
rsync -a "$DEV/harness/prompts/"    "$DEST/prompts/"
rsync -a "$DEV/harness/conformance/expected/" "$DEST/conformance/expected/"
rsync -a "$DEV/harness/conformance/golden/"   "$DEST/conformance/golden/"

# --- commands: the 9 chatbi commands (NOT orchestrate.md) ---
for c in chatbi-init chatbi-analyze chatbi-maintain-model \
         chatbi-maintain-knowledge chatbi-evaluate chatbi-correction \
         chatbi-bootstrap chatbi-build-from-requirement chatbi-audit-drift; do
  cp "$DEV/harness/.claude/commands/$c.md" "$DEST/.claude/commands/"
done

# --- agents: ONLY adversarial-reviewer (NOT plan/coder/test-agent) ---
cp "$DEV/harness/.claude/agents/adversarial-reviewer.md" "$DEST/.claude/agents/"

# --- skills: all chatbi-* skills ---
rsync -a "$DEV/harness/.claude/skills/" "$DEST/.claude/skills/"

# --- docs: domain-model + harness/ + agno acceptance manual (NOT dev docs) ---
cp "$DEV/harness/docs/chatbi-harness-domain-model.md" "$DEST/docs/"
# agno-target acceptance manual (f6f0d67: harness/docs/agno-acceptance-manual.md
# ships with the product; the earlier build missed it — one-line gap fix)
cp "$DEV/harness/docs/agno-acceptance-manual.md" "$DEST/docs/"
rsync -a "$DEV/harness/docs/harness/" "$DEST/docs/harness/"

# --- root files ---
cp "$DEV/harness/CLAUDE.md" "$DEV/harness/CONTEXT.md" "$DEV/harness/e2e-state.py" "$DEST/"
cp "$DEV/harness/product-README.md" "$DEST/README.md"
cp "$DEV/harness/install.sh" "$DEST/install.sh"
chmod +x "$DEST/install.sh"

# --- validate ---
echo "=== product built. Validating... ==="
# Legacy import surface (via the shim) + kernel package + IR/contract
# packages + runtime probe/adapter/reconcile (multi-runtime modules 2-4).
# NOTE: PYTHONPATH uses the product root, NOT the runtimes/ dir — a
# runtimes/ entry would shadow the installed `agno` package with
# runtimes/agno (module-5 sys.path-hygiene rule).
( cd "$DEST" && PYTHONPATH=.claude/lib:packages:. python3 -B -c \
    "import chatbi_harness.evidence, chatbi_harness.impact, chatbi_harness.evaluator, \
     chatbi_harness.knowledge, chatbi_harness.harness_state, chatbi_harness.policy, \
     chatbi_harness.adapters, chatbi_harness.bootstrap, chatbi_harness.build_plan, \
     chatbi_harness.drift, chatbi_harness.schedules, \
     chatbi_governance.resources, chatbi_governance.evidence, \
     chatbi_governance.adapters.fixture, \
     chatbi_harness_ir, chatbi_runtime_contract, \
     runtimes.claude_code.probe, runtimes.claude_code.adapter, \
     runtimes.claude_code.build_manifest, runtimes.claude_code.reconcile, \
     runtimes.agno.probe, runtimes.agno.evidence_index, runtimes.agno.events, \
     runtimes.agno.approvals, runtimes.agno.reviewer, runtimes.agno.config, \
     runtimes.agno.governed_tools, runtimes.agno.prompt_loader, \
     runtimes.agno.packager, runtimes.agno.observability, runtimes.agno.auth; \
     print('import OK')" )
echo "--- canary sweep (no machine path / secret) ---"
# grep -rnIE: POSIX grep (always present under /bin/sh). rg is not a binary in
# some envs (zsh function) and its absence was masked by 2>/dev/null + || true,
# making this sweep a silent no-op. /Users/[a-z] matches real user paths but not
# doc placeholders like /Users/... (mirrors the /home/[a-z] narrowing).
# /private/(tmp|var|etc) closes the macOS real-path prefix blind spot (OBS-C):
# /tmp and /var are symlinks into /private on macOS, so a machine path spelled
# /private/tmp/... must be caught. The /private/ + "followed by" wording in
# schedules.py's docstring is a rule description, not a path — the narrowed
# alternation does not match it.
grep -rnIE '/Users/[a-z]|/home/[a-z]|/private/(tmp|var|etc)|BEGIN .*PRIVATE KEY|sk-[A-Za-z0-9]{20}' "$DEST" \
    | grep -vE 'fixtures/config/embedded-secret|fixtures/codebases|malicious|prohibition|example' \
    | head -5 || true
echo "--- dev-only files must be absent ---"
for p in tests .scratch AGENTS.md \
         .claude/commands/orchestrate.md \
         .claude/agents/plan-agent.md .claude/agents/coder-agent.md .claude/agents/test-agent.md \
         docs/technical-design.md docs/requirements.md docs/orchestrator-state.md \
         docs/dev-cycles.md docs/dev-cycle-1.md; do
  [ -e "$DEST/$p" ] && echo "  LEAK $p" || true
done
echo "=== done: $DEST ==="
