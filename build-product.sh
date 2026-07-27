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
cp "$DEV/harness/.claude/settings.json" "$DEST/.claude/settings.json"
cp "$DEV/harness/.claude/chatbi-harness.json" \
   "$DEV/harness/.claude/chatbi-harness.example.json" \
   "$DEV/harness/.claude/chatbi-harness.local.example.json" "$DEST/.claude/"

# --- commands: the 6 chatbi commands (NOT orchestrate.md) ---
for c in chatbi-init chatbi-analyze chatbi-maintain-model \
         chatbi-maintain-knowledge chatbi-evaluate chatbi-correction; do
  cp "$DEV/harness/.claude/commands/$c.md" "$DEST/.claude/commands/"
done

# --- agents: ONLY adversarial-reviewer (NOT plan/coder/test-agent) ---
cp "$DEV/harness/.claude/agents/adversarial-reviewer.md" "$DEST/.claude/agents/"

# --- skills: all chatbi-* skills ---
rsync -a "$DEV/harness/.claude/skills/" "$DEST/.claude/skills/"

# --- docs: domain-model + harness/ only (NOT dev docs) ---
cp "$DEV/harness/docs/chatbi-harness-domain-model.md" "$DEST/docs/"
rsync -a "$DEV/harness/docs/harness/" "$DEST/docs/harness/"

# --- root files ---
cp "$DEV/harness/CLAUDE.md" "$DEV/harness/CONTEXT.md" "$DEV/harness/e2e-state.py" "$DEST/"
cp "$DEV/harness/product-README.md" "$DEST/README.md"
cp "$DEV/harness/install.sh" "$DEST/install.sh"
chmod +x "$DEST/install.sh"

# --- validate ---
echo "=== product built. Validating... ==="
( cd "$DEST" && PYTHONPATH=.claude/lib python3 -B -c \
    "import chatbi_harness.evidence, chatbi_harness.impact, chatbi_harness.evaluator, \
     chatbi_harness.knowledge, chatbi_harness.harness_state, chatbi_harness.policy, \
     chatbi_harness.adapters; print('import OK')" )
echo "--- canary sweep (no machine path / secret) ---"
rg -n '/Users/|/home/[a-z]|BEGIN .*PRIVATE KEY|sk-[A-Za-z0-9]{20}' "$DEST" 2>/dev/null \
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
