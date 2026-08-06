#!/bin/sh
# Install the ChatBI harness product into a Warehouse Workspace root.
#
# Usage: ./install.sh <target-workspace-root> [--force]
#
# Copies harness assets from this product directory into <target>, verifies a
# Python 3.10+ executable outside the workspace boundary (PORT-001), and prints
# the exact `export CHATBI_PYTHON=...` to set. Fail-closed: if no 3.10+ python
# is found outside the workspace, it prints a clear warning - SessionStart will
# block until CHATBI_PYTHON is set to a confirmed 3.10+ executable.
#
# This installer is a convenience wrapper. The launcher
# (.claude/hooks/python_binding_launcher.py) re-validates CHATBI_PYTHON at every
# SessionStart; setting it here does not bypass that gate. Machine-local bindings
# still belong in .claude/chatbi-harness.local.json (path_bindings / cli_adapters),
# never in shared settings.
set -eu

SRC="$(cd "$(dirname "$0")" && pwd)"
TARGET=""
FORCE=0

usage() {
  cat >&2 <<EOF
Usage: $0 <target-workspace-root> [--force]

  <target-workspace-root>  directory that becomes the Warehouse Workspace root
                           (created if absent; must be empty unless --force)
  --force                  proceed even if the target is non-empty
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1 ;;
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
echo "==> Pre-install self-check: $TARGET"

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
if [ -e "$TARGET_ABS/CLAUDE.md" ]; then
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
echo "==> Copying harness assets: $SRC -> $TARGET_ABS"
cp -R "$SRC/.claude" "$TARGET_ABS/"
cp -R "$SRC/docs"    "$TARGET_ABS/"
# multi-runtime module 2: governance kernel + runtime adapters
[ -d "$SRC/packages" ] && cp -R "$SRC/packages" "$TARGET_ABS/"
[ -d "$SRC/runtimes" ] && cp -R "$SRC/runtimes" "$TARGET_ABS/"
cp "$SRC/CLAUDE.md" "$SRC/CONTEXT.md" "$SRC/e2e-state.py" "$TARGET_ABS/"
[ -f "$SRC/README.md" ] && cp "$SRC/README.md" "$TARGET_ABS/README.md"
# defensive: strip any pycache that rode along, and ensure the launcher is +x
find "$TARGET_ABS/.claude" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
if [ ! -x "$TARGET_ABS/.claude/hooks/session_diagnose" ]; then
  chmod +x "$TARGET_ABS/.claude/hooks/session_diagnose" 2>/dev/null || \
    echo "  WARN: could not chmod +x .claude/hooks/session_diagnose" >&2
fi
echo "  ok: copied .claude/ packages/ runtimes/ docs/ CLAUDE.md CONTEXT.md e2e-state.py README.md"

# ---------------------------------------------------------------------------
# 3. Find a Python 3.10+ executable outside the workspace boundary
#    Mirrors python_binding_launcher.py: absolute, regular file, executable,
#    resolved path NOT inside the workspace root.
# ---------------------------------------------------------------------------
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
echo ""
echo "==> Done. Assets installed to: $TARGET_ABS"
