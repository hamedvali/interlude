#!/usr/bin/env bash
#
# Interlude uninstaller — removes the hooks, the CLI, and the app files.
#
#   curl -fsSL https://raw.githubusercontent.com/hamedvali/interlude/main/uninstall.sh | bash
#
# Flags:
#   --keep-data   remove code + hooks but keep ~/.interlude/state.json (progress)
#
# Env overrides:
#   INTERLUDE_HOME   install location (default: ~/.interlude)
#
set -euo pipefail

INTERLUDE_HOME="${INTERLUDE_HOME:-$HOME/.interlude}"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"

KEEP_DATA=0
for arg in "$@"; do
  case "$arg" in
    --keep-data) KEEP_DATA=1 ;;
    *) ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
info() { printf '\033[1;35m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }

info "Uninstalling Interlude"

# ---------- best-effort: stop a running window/server ----------
if [ -f "$INTERLUDE_HOME/interlude.py" ] && command -v python3 >/dev/null 2>&1; then
  python3 "$INTERLUDE_HOME/interlude.py" stop-server >/dev/null 2>&1 || true
fi

# ---------- strip hooks from settings.json ----------
PYTHON="$(command -v python3 || true)"
if [ -n "$PYTHON" ] && [ -f "$SETTINGS" ]; then
  cp "$SETTINGS" "$SETTINGS.bak.$(date +%s)"
  say "Backed up settings.json"
  INTERLUDE_HOME="$INTERLUDE_HOME" SETTINGS="$SETTINGS" "$PYTHON" - <<'PYEOF'
import json, os, sys

settings_path = os.environ["SETTINGS"]
home = os.environ["INTERLUDE_HOME"]
markers = ("interlude.py", "companion.py", home)

try:
    with open(settings_path) as f:
        data = json.load(f)
except Exception:
    sys.exit(0)

hooks = data.get("hooks")
if not isinstance(hooks, dict):
    sys.exit(0)

def is_ours(entry):
    for h in entry.get("hooks", []):
        cmd = h.get("command", "")
        if any(m and m in cmd for m in markers):
            return True
    return False

for event in list(hooks.keys()):
    groups = hooks.get(event)
    if not isinstance(groups, list):
        continue
    kept = [g for g in groups if not is_ours(g)]
    if kept:
        hooks[event] = kept
    else:
        del hooks[event]

if not hooks:
    data.pop("hooks", None)

tmp = settings_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.replace(tmp, settings_path)
print("  Interlude hooks removed from settings.json")
PYEOF
else
  warn "No settings.json found (or python3 missing) — skipping hook removal."
fi

# ---------- remove the CLI ----------
for d in "$HOME/.local/bin" "/usr/local/bin"; do
  if [ -f "$d/interlude" ] && grep -q "interlude.py" "$d/interlude" 2>/dev/null; then
    rm -f "$d/interlude" && say "Removed CLI: $d/interlude"
  fi
done

# ---------- remove app files ----------
if [ -d "$INTERLUDE_HOME" ]; then
  if [ "$KEEP_DATA" -eq 1 ] && [ -f "$INTERLUDE_HOME/state.json" ]; then
    SAVED="$(mktemp "${TMPDIR:-/tmp}/interlude-state.XXXXXX.json")"
    cp "$INTERLUDE_HOME/state.json" "$SAVED"
    rm -rf "$INTERLUDE_HOME"
    mkdir -p "$INTERLUDE_HOME"
    mv "$SAVED" "$INTERLUDE_HOME/state.json"
    say "Kept your progress at $INTERLUDE_HOME/state.json"
  else
    rm -rf "$INTERLUDE_HOME"
    say "Removed $INTERLUDE_HOME"
  fi
fi

printf '\n'
info "Interlude uninstalled. Restart Claude Code to unload the hooks."
say "If you installed the dock-icon app, remove it in your browser at chrome://apps."
