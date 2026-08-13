#!/bin/sh
# Install the ChatBI harness product into a Warehouse Workspace root.
#
# Usage: ./install.sh <target-workspace-root> [--target all|agno] [--force]
#
# Copies harness assets from this product directory into <target>.
#
# --target all  (default) full install: CC + agno assets (unchanged behavior).
# --target agno trimmed install: agno-only assets. Skips the CC execution
#               surface (agno does not consume it):
#                 .claude/commands|hooks|lib|settings.json|schedules,
#                 runtimes/claude_code, CLAUDE.md, CONTEXT.md, e2e-state.py
#               Keeps the agno-consumed assets:
#                 .claude/chatbi-harness.json + examples,
#                 .claude/agents|fixtures|rules|schemas|skills,
#                 packages/, runtimes/agno, workflows/, prompts/,
#                 conformance/, docs/, README.md, launch_agno.py
#
# In --target all mode the installer also verifies a Python 3.10+ executable
# outside the workspace boundary (PORT-001) and prints the exact
# `export CHATBI_PYTHON=...` to set. Fail-closed: if no 3.10+ python is found
# outside the workspace, it prints a clear warning - SessionStart will block
# until CHATBI_PYTHON is set to a confirmed 3.10+ executable.
#
# This installer is a convenience wrapper. The launcher
# (.claude/hooks/python_binding_launcher.py) re-validates CHATBI_PYTHON at every
# SessionStart; setting it here does not bypass that gate. Machine-local bindings
# still belong in .claude/chatbi-harness.local.json (path_bindings / cli_adapters),
# never in shared settings. In --target agno mode no CHATBI_PYTHON binding is
# required (no CC runtime); machine paths belong in deployment.json /
# .claude/chatbi-harness.local.json (PORT-001, deployment boundary).
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
TARGET=""
TARGET_MODE="all"
FORCE=0

usage() {
  cat >&2 <<EOF
Usage: $0 <target-workspace-root> [--target all|agno] [--force]

  <target-workspace-root>  directory that becomes the Warehouse Workspace root
                           (created if absent; must be empty unless --force)
  --target all|agno        all  = full install: CC + agno assets (default)
                           agno = trimmed install: agno-only assets (skips the
                                  CC execution surface: .claude/commands|hooks|
                                  lib|settings.json|schedules, runtimes/claude_code,
                                  CLAUDE.md, CONTEXT.md, e2e-state.py)
  --force                  proceed even if the target is non-empty
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1 ;;
    --target)
      shift
      [ $# -ge 1 ] || { echo "missing value for --target (all|agno)" >&2; usage; exit 2; }
      case "$1" in
        all|agno) TARGET_MODE="$1" ;;
        *) echo "unknown --target: $1 (expected all|agno)" >&2; usage; exit 2 ;;
      esac
      ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage; exit 2 ;;
    *) [ -z "$TARGET" ] && TARGET="$1" || { echo "extra argument: $1" >&2; exit 2; } ;;
  esac
  shift
done

[ -n "$TARGET" ] || { usage; exit 2; }

# ---------------------------------------------------------------------------
# 1. Pre-install self-check on the target
# ---------------------------------------------------------------------------
echo "==> Pre-install self-check: $TARGET (target=$TARGET_MODE)"

if [ -e "$TARGET" ]; then
  [ -d "$TARGET" ] || { echo "  FAIL: target exists and is not a directory: $TARGET" >&2; exit 1; }
  TARGET_ABS="$(cd "$TARGET" && pwd)"
else
  parent="$(dirname "$TARGET")"
  case "$parent" in
    /*) : ;;
    *) parent="$PWD/$parent" ;;
  esac
  [ -d "$parent" ] || { echo "  FAIL: parent does not exist: $parent" >&2; exit 1; }
  mkdir -p "$TARGET" || { echo "  FAIL: cannot create: $TARGET" >&2; exit 1; }
  TARGET_ABS="$(cd "$TARGET" && pwd)"
fi

if [ -e "$TARGET_ABS/.claude" ]; then
  echo "  FAIL: $TARGET_ABS/.claude already exists (another harness or CC config)." >&2
  echo "        Remove it or choose a different target." >&2
  exit 1
fi
# CLAUDE.md collision check applies to the full install only: --target agno
# does not install CLAUDE.md, so a pre-existing file is not a collision.
if [ "$TARGET_MODE" = "all" ] && [ -e "$TARGET_ABS/CLAUDE.md" ]; then
  echo "  FAIL: $TARGET_ABS/CLAUDE.md already exists (contract collision)." >&2
  echo "        Choose a different target or remove the existing file." >&2
  exit 1
fi

# dedicated-root check: abort if non-empty (ignoring .DS_Store) unless --force
entries=$(find "$TARGET_ABS" -mindepth 1 -maxdepth 1 ! -name '.DS_Store' 2>/dev/null)
if [ -n "$entries" ]; then
  if [ "$FORCE" -ne 1 ]; then
    echo "  FAIL: target is not empty (expected a dedicated root)." >&2
    echo "        Existing entries:" >&2
    echo "$entries" | sed 's/^/          /' >&2
    echo "        Re-run with --force to proceed anyway." >&2
    exit 1
  fi
  echo "  WARN: target non-empty (--force); proceeding." >&2
fi
echo "  ok: target is a clean dedicated root -> $TARGET_ABS"

# ---------------------------------------------------------------------------
# 2. Copy harness assets (this product dir -> target workspace root)
# ---------------------------------------------------------------------------
echo "==> Copying harness assets: $SRC -> $TARGET_ABS (target=$TARGET_MODE)"

if [ "$TARGET_MODE" = "all" ]; then
  # Full install: everything the product ships.
  cp -R "$SRC/.claude" "$TARGET_ABS/"
  cp -R "$SRC/docs"    "$TARGET_ABS/"
  # multi-runtime module 2: governance kernel + runtime adapters
  [ -d "$SRC/packages" ] && cp -R "$SRC/packages" "$TARGET_ABS/"
  [ -d "$SRC/runtimes" ] && cp -R "$SRC/runtimes" "$TARGET_ABS/"
  # multi-runtime module 3/4: IR workflows + prompt assets + conformance
  # snapshots (golden/expected frozen read-only baselines)
  [ -d "$SRC/workflows" ]    && cp -R "$SRC/workflows"    "$TARGET_ABS/"
  [ -d "$SRC/prompts" ]      && cp -R "$SRC/prompts"      "$TARGET_ABS/"
  [ -d "$SRC/conformance" ]  && cp -R "$SRC/conformance"  "$TARGET_ABS/"
  cp "$SRC/CLAUDE.md" "$SRC/CONTEXT.md" "$SRC/e2e-state.py" "$TARGET_ABS/"
  [ -f "$SRC/README.md" ] && cp "$SRC/README.md" "$TARGET_ABS/README.md"
  [ -f "$SRC/launch_agno.py" ] && cp "$SRC/launch_agno.py" "$TARGET_ABS/"
else
  # agno trimmed install: skip the CC execution surface (commands/hooks/lib/
  # settings.json/schedules, runtimes/claude_code, CLAUDE.md/CONTEXT.md/
  # e2e-state.py); keep the agno-consumed assets.
  mkdir -p "$TARGET_ABS/.claude"
  for d in agents fixtures rules schemas skills; do
    [ -d "$SRC/.claude/$d" ] && cp -R "$SRC/.claude/$d" "$TARGET_ABS/.claude/"
  done
  for f in chatbi-harness.json chatbi-harness.example.json \
           chatbi-harness.local.example.json; do
    [ -f "$SRC/.claude/$f" ] && cp "$SRC/.claude/$f" "$TARGET_ABS/.claude/"
  done
  cp -R "$SRC/docs" "$TARGET_ABS/"
  # multi-runtime module 2: governance kernel (packages/) + agno runtime
  # adapter (runtimes/agno + runtimes/__init__.py; claude_code runtime skipped)
  [ -d "$SRC/packages" ] && cp -R "$SRC/packages" "$TARGET_ABS/"
  mkdir -p "$TARGET_ABS/runtimes"
  for d in "$SRC"/runtimes/*; do
    [ -e "$d" ] || continue
    case "$(basename "$d")" in
      claude_code|__pycache__) continue ;;
    esac
    cp -R "$d" "$TARGET_ABS/runtimes/"
  done
  # multi-runtime module 3/4: IR workflows + prompt assets + conformance
  # snapshots (golden/expected frozen read-only baselines)
  [ -d "$SRC/workflows" ]    && cp -R "$SRC/workflows"    "$TARGET_ABS/"
  [ -d "$SRC/prompts" ]      && cp -R "$SRC/prompts"      "$TARGET_ABS/"
  [ -d "$SRC/conformance" ]  && cp -R "$SRC/conformance"  "$TARGET_ABS/"
  [ -f "$SRC/README.md" ] && cp "$SRC/README.md" "$TARGET_ABS/README.md"
  [ -f "$SRC/launch_agno.py" ] && cp "$SRC/launch_agno.py" "$TARGET_ABS/"
fi

# defensive: strip any pycache that rode along, and ensure the launcher is +x
find "$TARGET_ABS/.claude" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
chmod +x "$TARGET_ABS/launch_agno.py" 2>/dev/null || true
# hooks are only installed in --target all mode; the guard keeps the agno
# trimmed install silent about a dir that must not exist there.
if [ -d "$TARGET_ABS/.claude/hooks" ] && [ ! -x "$TARGET_ABS/.claude/hooks/session_diagnose" ]; then
  chmod +x "$TARGET_ABS/.claude/hooks/session_diagnose" 2>/dev/null || \
    echo "  WARN: could not chmod +x .claude/hooks/session_diagnose" >&2
fi

if [ "$TARGET_MODE" = "agno" ]; then
  echo "  ok: agno trimmed install: .claude/{agents,fixtures,rules,schemas,skills,"
  echo "      chatbi-harness.json,chatbi-harness.example.json,chatbi-harness.local.example.json}"
  echo "      packages/ runtimes/agno workflows/ prompts/ conformance/ docs/ README.md"
  echo "      launch_agno.py"
  echo "      (skipped CC surface: .claude/{commands,hooks,lib,schedules,settings.json},"
  echo "      runtimes/claude_code, CLAUDE.md, CONTEXT.md, e2e-state.py)"
else
  echo "  ok: copied .claude/ packages/ runtimes/ workflows/ prompts/ conformance/ docs/ CLAUDE.md CONTEXT.md e2e-state.py README.md launch_agno.py"
fi

# ---------------------------------------------------------------------------
# 3. Find a Python 3.10+ executable outside the workspace boundary
#    Mirrors python_binding_launcher.py: absolute, regular file, executable,
#    resolved path NOT inside the workspace root.
#    (--target agno: no CC runtime installed, so no CHATBI_PYTHON binding is
#    required; machine paths belong in deployment.json / the local config.)
# ---------------------------------------------------------------------------
if [ "$TARGET_MODE" = "agno" ]; then
  echo ""
  echo "==> agno target: CC runtime not installed - no CHATBI_PYTHON binding required."
  echo "    Machine-specific bindings (agno venv, mysql/dbt executables) belong in"
  echo "    deployment.json / .claude/chatbi-harness.local.json (PORT-001, deployment"
  echo "    boundary) - never in shared settings."
else
  echo "==> Verifying Python 3.10+ outside workspace boundary"

  cand_list=""
  [ -n "${CHATBI_PYTHON:-}" ] && cand_list="$cand_list $CHATBI_PYTHON"
  cand_list="$cand_list /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3"
  cand_list="$cand_list python3.14 python3.13 python3.12 python3.11 python3.10 python3"

  CHOSEN=""
  for c in $cand_list; do
    # resolve to an absolute path we can execute
    if [ -n "${c##/*}" ]; then
      p=$(command -v "$c" 2>/dev/null) || continue
    else
      p="$c"
      [ -x "$p" ] || continue
    fi
    [ -n "$p" ] || continue
    # one python call: resolve realpath, check regular+executable+outside, emit version
    out=$("$p" -c '
import os, sys, stat
target = os.path.realpath(sys.argv[1])
py = os.path.realpath(sys.argv[2])
try:
    st = os.stat(py, follow_symlinks=False)
except OSError:
    sys.exit(1)
if not stat.S_ISREG(st.st_mode) or not os.access(py, os.X_OK):
    sys.exit(1)
inside = False
try:
    inside = os.path.commonpath([py, target]) == target
except ValueError:
    inside = False
if inside:
    sys.exit(2)
maj, min = sys.version_info[:2]
print("%d.%d %s" % (maj, min, py))
' "$TARGET_ABS" "$p" 2>/dev/null) || continue
    [ -n "$out" ] || continue
    ver=${out%% *}
    path=${out#* }
    vmaj=${ver%%.*}
    vmin=${ver#*.}
    if [ "$vmaj" -gt 3 ] || { [ "$vmaj" -eq 3 ] && [ "$vmin" -ge 10 ]; }; then
      # Recommend the stable entry path ($p, e.g. /opt/homebrew/bin/python3) rather
      # than the resolved Cellar realpath ($path, which changes on brew upgrade).
      # The launcher resolves $p itself at SessionStart; we only validated $path.
      CHOSEN="$p"
      echo "  ok: $c -> $path (Python $ver); recommend CHATBI_PYTHON=$p"
      break
    fi
    echo "  skip: $c -> Python $ver (need 3.10+)"
  done

  # ---------------------------------------------------------------------------
  # 4. Report CHATBI_PYTHON + next steps
  # ---------------------------------------------------------------------------
  echo ""
  if [ -n "$CHOSEN" ]; then
    echo "==> Set CHATBI_PYTHON (required at every SessionStart)"
    echo "    export CHATBI_PYTHON=$CHOSEN"
    echo "    Claude Code must inherit this env: export it in the shell you start"
    echo "    'claude' from, or add the line to ~/.zshrc (macOS) / ~/.bashrc and"
    echo "    open a new shell. Never persist it in shared settings."
  else
    echo "==> WARNING: no Python 3.10+ executable found outside the workspace boundary."
    echo "    SessionStart will BLOCK until CHATBI_PYTHON is set to a confirmed 3.10+"
    echo "    executable that resolves OUTSIDE the workspace and any Business Codebase."
    echo "    Install one (e.g. 'brew install python@3.12' -> /opt/homebrew/bin/python3.12)"
    echo "    then: export CHATBI_PYTHON=/absolute/path/to/python3.12"
  fi

  echo ""
  echo "==> Next steps"
  echo "    cd $TARGET_ABS"
  echo "    # ensure CHATBI_PYTHON is exported in this shell, then:"
  echo "    claude"
  echo "    # in Claude Code, run: /chatbi-init"
  echo "    # Live PreToolUse/PostToolUse/SubagentStop/Stop/ConfigChange hooks are NOT"
  echo "    # registered by default (settings.json is SessionStart-only). To enable live"
  echo "    # gating see docs/harness/e2e-checklist.md - and only in a throwaway workspace"
  echo "    # (a blocking hook hot-reloads settings.json and can deadlock a dev session)."
fi

echo ""
if [ "$TARGET_MODE" = "agno" ]; then
  echo "==> Next steps (agno target)"
  echo "    cd $TARGET_ABS"
  echo "    # deployment-boundary machine paths (PORT-001) go in deployment.json"
  echo "    # (agno_main / cli_allowlist / warehouse_db) and"
  echo "    # .claude/chatbi-harness.local.json - see docs/agno-acceptance-manual.md"
  echo "    # for the operator runbook. The launcher ships with this install"
  echo "    # (launch_agno.py); launch the governed service with your agno venv"
  echo "    # python, e.g.:"
  echo "    #   CHATBI_AGNO_MAIN=<agno-main-root> <agno-venv>/bin/python launch_agno.py"
fi
echo ""
echo "==> Done. Assets installed to: $TARGET_ABS (target=$TARGET_MODE)"
