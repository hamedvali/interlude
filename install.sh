#!/usr/bin/env bash
#
# Interlude installer — a no-typing learn/play window that opens while Claude
# Code works and closes when it's done.
#
#   curl -fsSL https://raw.githubusercontent.com/hamedvali/interlude/main/install.sh | bash
#
# What it does:
#   1. Copies the app into ~/.interlude   (override with INTERLUDE_HOME)
#   2. Registers 4 global Claude Code hooks in ~/.claude/settings.json
#   3. Installs an `interlude` CLI into ~/.local/bin
# Your progress (state.json) is preserved across re-installs/upgrades.
#
# Env overrides:
#   INTERLUDE_REPO   GitHub owner/repo to fetch from      (default: hamedvali/interlude)
#   INTERLUDE_REF    git ref/branch/tag                   (default: main)
#   INTERLUDE_HOME   install location                     (default: ~/.interlude)
#   INTERLUDE_SRC    local checkout to copy from          (implies --local)
#
# Flags:
#   --local          install from a local checkout instead of downloading
#
set -euo pipefail

REPO="${INTERLUDE_REPO:-hamedvali/interlude}"
REF="${INTERLUDE_REF:-main}"
INTERLUDE_HOME="${INTERLUDE_HOME:-$HOME/.interlude}"
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"

LOCAL_MODE=0
SRC="${INTERLUDE_SRC:-}"
[ -n "$SRC" ] && LOCAL_MODE=1
for arg in "$@"; do
  case "$arg" in
    --local) LOCAL_MODE=1 ;;
    *) ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
info() { printf '\033[1;35m▸\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

info "Installing Interlude"

# ---------- preflight ----------
PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || die "python3 is required but was not found on your PATH."

have_chrome=0
for app in \
  "/Applications/Google Chrome.app" \
  "/Applications/Microsoft Edge.app" \
  "/Applications/Brave Browser.app" \
  "/Applications/Chromium.app"; do
  [ -d "$app" ] && { have_chrome=1; break; }
done
if [ "$have_chrome" -eq 0 ]; then
  warn "No Chrome/Edge/Brave/Chromium found — Interlude will open in your default"
  warn "browser instead (that window can't auto-close). Chrome gives the best result."
fi

# ---------- fetch app files into a temp dir ----------
TMP="$(mktemp -d "${TMPDIR:-/tmp}/interlude.XXXXXX")"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

APP_SRC=""
if [ "$LOCAL_MODE" -eq 1 ]; then
  # Copy from a local checkout (this script's own dir, or INTERLUDE_SRC).
  if [ -z "$SRC" ]; then
    SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
    SRC="$SELF_DIR"
  fi
  [ -d "$SRC/app" ] || die "--local: no 'app/' directory under $SRC"
  APP_SRC="$SRC/app"
  info "Installing from local checkout: $SRC"
else
  command -v curl >/dev/null 2>&1 || die "curl is required to download Interlude."
  command -v tar  >/dev/null 2>&1 || die "tar is required to unpack Interlude."
  URL="https://github.com/$REPO/archive/$REF.tar.gz"
  info "Downloading $URL"
  curl -fsSL "$URL" -o "$TMP/src.tar.gz" \
    || die "Download failed. Check INTERLUDE_REPO/INTERLUDE_REF (currently $REPO@$REF)."
  tar -xzf "$TMP/src.tar.gz" -C "$TMP" || die "Failed to unpack the download."
  APP_SRC="$(find "$TMP" -maxdepth 2 -type d -name app | head -n1 || true)"
  [ -n "$APP_SRC" ] && [ -d "$APP_SRC" ] || die "Downloaded archive has no app/ directory."
fi

# ---------- copy code (preserving state.json and .run/) ----------
mkdir -p "$INTERLUDE_HOME"
# Copy contents of app/ into INTERLUDE_HOME. This overwrites code files but does
# NOT delete existing state.json / .run/ that aren't in the source.
cp -R "$APP_SRC/." "$INTERLUDE_HOME/"
chmod +x "$INTERLUDE_HOME/interlude.py" 2>/dev/null || true
info "App files installed to $INTERLUDE_HOME"

# ---------- register Claude Code hooks (idempotent) ----------
mkdir -p "$CLAUDE_DIR"
if [ -f "$SETTINGS" ]; then
  cp "$SETTINGS" "$SETTINGS.bak.$(date +%s)"
  say "Backed up existing settings.json"
fi

INTERLUDE_HOME="$INTERLUDE_HOME" PYTHON="$PYTHON" SETTINGS="$SETTINGS" \
"$PYTHON" - <<'PYEOF'
import json, os, sys

settings_path = os.environ["SETTINGS"]
home = os.environ["INTERLUDE_HOME"]
python = os.environ["PYTHON"]
script = os.path.join(home, "interlude.py")

try:
    with open(settings_path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        data = {}
except FileNotFoundError:
    data = {}
except Exception:
    print("  ! existing settings.json wasn't valid JSON; starting a fresh hooks block", file=sys.stderr)
    data = {}

hooks = data.get("hooks")
if not isinstance(hooks, dict):
    hooks = {}
data["hooks"] = hooks

# Markers that identify a hook as ours (current or older "companion" builds,
# or anything pointing into this install dir) so re-runs never duplicate.
markers = ("interlude.py", "companion.py", home)

def is_ours(entry):
    for h in entry.get("hooks", []):
        cmd = h.get("command", "")
        if any(m and m in cmd for m in markers):
            return True
    return False

def strip_ours(event):
    groups = hooks.get(event)
    if not isinstance(groups, list):
        return []
    kept = [g for g in groups if not is_ours(g)]
    return kept

def cmd(sub):
    return {"type": "command", "command": f'"{python}" "{script}" {sub}'}

# event -> (subcommand, matcher-or-None)
plan = {
    "UserPromptSubmit": ("on-prompt", None),
    "PostToolUse":      ("on-tool",   "*"),
    "Stop":             ("on-stop",   None),
    "Notification":     ("on-need",   None),
}

for event, (sub, matcher) in plan.items():
    kept = strip_ours(event)
    group = {"hooks": [cmd(sub)]}
    if matcher is not None:
        group["matcher"] = matcher
    kept.append(group)
    hooks[event] = kept

tmp = settings_path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.replace(tmp, settings_path)
print("  hooks registered: UserPromptSubmit, PostToolUse, Stop, Notification")
PYEOF
info "Claude Code hooks registered in $SETTINGS"

# ---------- install the CLI ----------
CLI_SRC="$INTERLUDE_HOME/interlude.py"
BIN_TARGET=""
for d in "$HOME/.local/bin" "/usr/local/bin"; do
  if mkdir -p "$d" 2>/dev/null && [ -w "$d" ]; then
    BIN_TARGET="$d/interlude"
    break
  fi
done

if [ -n "$BIN_TARGET" ]; then
  cat > "$BIN_TARGET" <<CLIEOF
#!/usr/bin/env bash
set -euo pipefail
HOME_DIR="\${INTERLUDE_HOME:-$INTERLUDE_HOME}"
exec "$PYTHON" "\$HOME_DIR/interlude.py" "\$@"
CLIEOF
  chmod +x "$BIN_TARGET"
  info "CLI installed: $BIN_TARGET"
  case ":$PATH:" in
    *":$(dirname "$BIN_TARGET"):"*) : ;;
    *) warn "$(dirname "$BIN_TARGET") is not on your PATH — add it, or run: $PYTHON $CLI_SRC <cmd>" ;;
  esac
else
  warn "Couldn't find a writable bin dir; run the CLI as: $PYTHON $CLI_SRC <cmd>"
fi

# ---------- done ----------
printf '\n'
info "Interlude installed."
say "1. Restart Claude Code (or open a new session) so the hooks load."
say "2. Optional — give it a dock icon: run  interlude install  and click \"Install\"."
say ""
say "Controls:  interlude off | on | status | install | stop-server"
say "Uninstall: curl -fsSL https://raw.githubusercontent.com/$REPO/$REF/uninstall.sh | bash"
