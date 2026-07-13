#!/usr/bin/env python3
"""Interlude control script — invoked by Claude Code hooks.

Decides when to open/close the learn-and-play window:

    on-prompt   (UserPromptSubmit)  -> Claude starts; open now only in prompt mode
    on-pretool  (PreToolUse)         -> Claude does something; open (tool mode, default)
    on-tool     (PostToolUse)        -> Claude still working; keep/re-open window
    on-stop     (Stop)               -> Claude finished; close window
    on-need     (Notification)       -> Claude needs you; close window

Whether a prompt or a tool triggers the popup is the `openOn` setting
(default "tool" — a pure text answer that uses no tools never opens the window).

The window is a native macOS WKWebView popup (webview.js, run via osascript)
with an *accessory* activation policy: it shows on screen but adds NO Dock icon
and no menu bar. Zero dependencies — no Chrome, no PWA install step.

Internal subcommands: _watch <gen>, _open, _close.
Admin: on | off | status | version | stop-server.

Kill switch: set INTERLUDE_DISABLED=1, or run `interlude off`.
Every hook entry point returns fast (heavy work is spawned detached).
"""
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

# Fallback only; the canonical version lives in the VERSION file next to this
# script (so it ships under app/ and updates with the rest of the app).
__version__ = "1.3.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = os.path.join(BASE_DIR, ".run")
STATUS_FILE = os.path.join(RUN_DIR, "status")
PORT_FILE = os.path.join(RUN_DIR, "port")
WINDOW_PID = os.path.join(RUN_DIR, "window.pid")
SERVER_PID = os.path.join(RUN_DIR, "server.pid")
SERVER_LOG = os.path.join(RUN_DIR, "server.log")
WATCH_PID = os.path.join(RUN_DIR, "watch.pid")
DISABLED = os.path.join(RUN_DIR, "disabled")
KEEP_FILE = os.path.join(RUN_DIR, "keep")
FRONT_FILE = os.path.join(RUN_DIR, "front")
OPEN_LOCK = os.path.join(RUN_DIR, "open.lock")
UPDATE_FILE = os.path.join(RUN_DIR, "update.json")
UPDATE_LOCK = os.path.join(RUN_DIR, "update.lock")
NO_UPDATE = os.path.join(RUN_DIR, "no-update")

SERVER_PY = os.path.join(BASE_DIR, "server.py")
WEBVIEW_JS = os.path.join(BASE_DIR, "webview.js")
VERSION_FILE = os.path.join(BASE_DIR, "VERSION")
SETTINGS_JSON = os.path.join(BASE_DIR, "settings.json")  # user-editable app settings
DEFAULT_PORT = int(os.environ.get("INTERLUDE_PORT", "47615"))
OPEN_DELAY = float(os.environ.get("INTERLUDE_DELAY", "3"))
WINDOW_W = int(os.environ.get("INTERLUDE_WIDTH", "1240"))
WINDOW_H = int(os.environ.get("INTERLUDE_HEIGHT", "840"))

# User-editable settings (via the in-app Settings view -> server -> settings.json).
# Env vars seed the defaults so existing installs keep their behavior; the file
# wins when present. Keep this schema in sync with server.py's SETTINGS_DEFAULTS.
#   openOn    -- "tool" (open only when Claude uses a tool), "prompt", or "both"
#   toolScope -- "all" tools, or "work" (file edits + commands only)
SETTINGS_DEFAULTS = {
    "openOn": os.environ.get("INTERLUDE_OPEN_ON", "tool"),
    "toolScope": os.environ.get("INTERLUDE_TOOL_SCOPE", "all"),
    "openDelay": OPEN_DELAY,
    "width": WINDOW_W,
    "height": WINDOW_H,
    "defaultView": "learn",
    "sound": False,
}
# Tools that count as Claude "doing something" when toolScope == "work".
WORK_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash", "Task"}

# --- auto-update settings (same source install.sh fetches from) ---
UPDATE_REPO = os.environ.get("INTERLUDE_REPO", "hamedvali/interlude")
UPDATE_REF = os.environ.get("INTERLUDE_REF", "main")
# how often to check, in seconds (default 6h); checks are throttled per this
UPDATE_INTERVAL = float(os.environ.get("INTERLUDE_UPDATE_INTERVAL", str(6 * 3600)))
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR", os.path.join(os.path.expanduser("~"), ".claude"))
SETTINGS_FILE = os.path.join(CLAUDE_DIR, "settings.json")


# ---------- small helpers ----------
def ensure_run():
    os.makedirs(RUN_DIR, exist_ok=True)


def is_disabled():
    return os.environ.get("INTERLUDE_DISABLED") == "1" or os.path.exists(DISABLED)


def load_settings():
    """Read user settings, falling back to defaults for any missing key."""
    data = dict(SETTINGS_DEFAULTS)
    try:
        with open(SETTINGS_JSON) as f:
            user = json.load(f)
        if isinstance(user, dict):
            for k in SETTINGS_DEFAULTS:
                if k in user:
                    data[k] = user[k]
    except Exception:
        pass
    return data


def hook_input():
    """Parse the JSON Claude Code passes on stdin (e.g. tool_name for PreToolUse).

    Returns {} if there's nothing to read or it isn't valid JSON. Reading is safe
    because Claude closes the pipe after writing, so read() never blocks forever.
    """
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        return json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return {}


def read_status():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except Exception:
        return {"busy": False, "gen": 0}


def write_status(busy, gen):
    ensure_run()
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"busy": busy, "gen": gen}, f)
    os.replace(tmp, STATUS_FILE)


def clear_keep():
    try:
        os.remove(KEEP_FILE)
    except OSError:
        pass


def keep_requested():
    """True if the app asked (via Esc / "Keep open") to keep THIS idle window.

    The web app posts the current generation to /api/keep; we honor it only
    while that generation is still the current idle one, so a stale keep from a
    previous turn can't wedge a window open forever.
    """
    try:
        with open(KEEP_FILE) as f:
            keep_gen = int(f.read().strip())
    except Exception:
        return False
    return keep_gen == int(read_status().get("gen", -1))


def read_port():
    try:
        with open(PORT_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return DEFAULT_PORT


def pid_from(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return None


def alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def spawn_detached(args):
    """Launch a fully detached background process and return its Popen."""
    return subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        cwd=BASE_DIR,
    )


def ping(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=0.5) as r:
            return r.status == 200
    except Exception:
        return False


# ---------- server lifecycle ----------
def ensure_server():
    port = read_port()
    if ping(port):
        return port
    ensure_run()
    with open(SERVER_LOG, "a") as log:
        p = subprocess.Popen(
            [sys.executable, SERVER_PY],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            cwd=BASE_DIR,
        )
    with open(SERVER_PID, "w") as f:
        f.write(str(p.pid))
    for _ in range(40):  # wait up to ~4s
        port = read_port()
        if ping(port):
            return port
        time.sleep(0.1)
    return read_port()


# ---------- window lifecycle ----------
def request_front():
    """Bump the front counter so a live window raises itself to the front.

    webview.js polls FRONT_FILE from its run loop; when the value changes it
    re-activates and orders the window front. This is how we surface a window
    that already exists (buried behind other apps, on another Space, or left
    over from a previous Claude session) instead of silently doing nothing.
    """
    ensure_run()
    try:
        n = 0
        try:
            with open(FRONT_FILE) as f:
                n = int(f.read().strip() or "0")
        except Exception:
            n = 0
        tmp = FRONT_FILE + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(n + 1))
        os.replace(tmp, FRONT_FILE)
    except OSError:
        pass


def do_open():
    """Open the Interlude popup: a native WKWebView window with no Dock icon.

    Rendered by webview.js via osascript (JavaScript for Automation). We track
    the osascript PID so do_close() can kill it cleanly.

    Serialized with an flock so two hooks racing (e.g. UserPromptSubmit and
    PostToolUse both firing a watcher) can never spawn two windows — the second
    caller finds the first's window and just re-fronts it. If a window is
    already alive we raise it to the front rather than no-op, so every prompt
    reliably brings Interlude forward.
    """
    ensure_run()
    with open(OPEN_LOCK, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
        except OSError:
            pass
        if alive(pid_from(WINDOW_PID)):
            request_front()  # already showing — raise it to the front
            return
        port = ensure_server()
        if not port:
            return
        url = f"http://127.0.0.1:{port}/"
        osa = shutil.which("osascript")
        if not osa or not os.path.exists(WEBVIEW_JS):
            # Last-ditch fallback: default browser (can't auto-close a plain tab,
            # and this one does show a Dock icon). Should never happen on macOS.
            subprocess.Popen(["open", url], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        s = load_settings()
        try:
            w, h = int(s.get("width", WINDOW_W)), int(s.get("height", WINDOW_H))
        except (TypeError, ValueError):
            w, h = WINDOW_W, WINDOW_H
        p = spawn_detached([osa, "-l", "JavaScript", WEBVIEW_JS,
                            url, str(w), str(h), FRONT_FILE])
        with open(WINDOW_PID, "w") as f:
            f.write(str(p.pid))


def do_close():
    """Let the window run its ~3s close countdown and self-close, then force it.

    The web app shows a "closing in 3, 2, 1" modal and calls window.close();
    webview.js turns that into an exit. We wait a bit past the countdown as a
    backstop and then kill by PID. Aborts if Claude becomes busy again mid-close,
    or if the user pressed Esc / "Keep open" (keep_requested), so we never kill
    a window that should stay up.
    """
    pid = pid_from(WINDOW_PID)
    if pid:
        for _ in range(48):  # ~4.8s: covers the 3s in-app countdown + margin
            if not alive(pid):
                break
            if read_status().get("busy"):
                return  # became busy again — keep the window
            if keep_requested():
                return  # user chose to keep it open
            time.sleep(0.1)
        if read_status().get("busy") or keep_requested():
            return
        if alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except Exception:
                try:
                    os.kill(pid, signal.SIGTERM)
                except Exception:
                    pass
            time.sleep(0.4)
            if alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
    try:
        os.remove(WINDOW_PID)
    except OSError:
        pass


def do_watch(gen):
    """Wait the open delay, then open only if this generation is still busy."""
    ensure_run()
    with open(WATCH_PID, "w") as f:
        f.write(str(os.getpid()))
    try:
        delay = float(load_settings().get("openDelay", OPEN_DELAY))
    except (TypeError, ValueError):
        delay = OPEN_DELAY
    time.sleep(max(0.0, delay))
    st = read_status()
    if st.get("gen") == gen and st.get("busy"):
        do_open()
    try:
        os.remove(WATCH_PID)
    except OSError:
        pass


# ---------- auto-update ----------
def local_version():
    try:
        with open(VERSION_FILE) as f:
            v = f.read().strip()
            if v:
                return v
    except Exception:
        pass
    return __version__


def parse_ver(s):
    parts = re.findall(r"\d+", s or "")
    return tuple(int(p) for p in parts[:4]) if parts else (0,)


def ver_gt(a, b):
    """True if version string a is numerically greater than b."""
    pa, pb = list(parse_ver(a)), list(parse_ver(b))
    n = max(len(pa), len(pb))
    pa += [0] * (n - len(pa))
    pb += [0] * (n - len(pb))
    return pa > pb


def read_update():
    try:
        with open(UPDATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"phase": "idle"}


def write_update(phase, **fields):
    ensure_run()
    data = read_update()
    data["phase"] = phase
    data.update(fields)
    tmp = UPDATE_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, UPDATE_FILE)
    except OSError:
        pass
    return data


def update_disabled():
    return os.environ.get("INTERLUDE_NO_UPDATE") == "1" or os.path.exists(NO_UPDATE)


def register_hooks():
    """Idempotently register our 4 hooks in Claude Code's settings.json.

    Mirrors install.sh's hook block so the app can re-register its own (possibly
    new) hook wiring after an update. Returns True if settings.json actually
    changed — the one case that needs a Claude Code restart to take effect.
    """
    python = sys.executable
    script = os.path.join(BASE_DIR, "interlude.py")
    try:
        with open(SETTINGS_FILE) as f:
            before = f.read()
    except FileNotFoundError:
        before = ""
    except Exception:
        before = ""
    try:
        data = json.loads(before) if before.strip() else {}
        if not isinstance(data, dict):
            data = {}
        before_norm = json.dumps(data, indent=2) + "\n" if before.strip() else ""
    except Exception:
        data = {}
        before_norm = None  # unparseable -> treat as changed

    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    data["hooks"] = hooks
    markers = ("interlude.py", "companion.py", BASE_DIR)

    def is_ours(entry):
        for h in entry.get("hooks", []):
            if any(m and m in h.get("command", "") for m in markers):
                return True
        return False

    def strip_ours(event):
        groups = hooks.get(event)
        return [g for g in groups if not is_ours(g)] if isinstance(groups, list) else []

    def cmd(sub):
        return {"type": "command", "command": f'"{python}" "{script}" {sub}'}

    plan = {
        "UserPromptSubmit": ("on-prompt", None),
        "PreToolUse": ("on-pretool", "*"),
        "PostToolUse": ("on-tool", "*"),
        "Stop": ("on-stop", None),
        "Notification": ("on-need", None),
    }
    for event, (sub, matcher) in plan.items():
        kept = strip_ours(event)
        group = {"hooks": [cmd(sub)]}
        if matcher is not None:
            group["matcher"] = matcher
        kept.append(group)
        hooks[event] = kept

    after = json.dumps(data, indent=2) + "\n"
    changed = (before_norm != after)
    if changed:
        try:
            os.makedirs(CLAUDE_DIR, exist_ok=True)
            if before:
                try:
                    shutil.copyfile(SETTINGS_FILE, SETTINGS_FILE + ".bak." + str(int(time.time())))
                except OSError:
                    pass
            tmp = SETTINGS_FILE + ".tmp"
            with open(tmp, "w") as f:
                f.write(after)
            os.replace(tmp, SETTINGS_FILE)
        except OSError:
            pass
    return changed


def fetch_remote_version():
    url = f"https://raw.githubusercontent.com/{UPDATE_REPO}/{UPDATE_REF}/app/VERSION"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "interlude-updater"})
        with urllib.request.urlopen(req, timeout=8) as r:
            if getattr(r, "status", 200) != 200:
                return None
            return r.read().decode("utf-8").strip()
    except Exception:
        return None


def _safe_extract(tar, dest):
    dest_real = os.path.realpath(dest)
    for m in tar.getmembers():
        target = os.path.realpath(os.path.join(dest, m.name))
        if target != dest_real and not target.startswith(dest_real + os.sep):
            raise RuntimeError("unsafe path in archive: " + m.name)
    tar.extractall(dest)


def _find_app_dir(root):
    for base, dirs, files in os.walk(root):
        if os.path.basename(base) == "app" and "interlude.py" in files:
            return base
    return None


def download_and_stage():
    """Download + extract the repo tarball; return a verified app/ dir or None."""
    url = f"https://github.com/{UPDATE_REPO}/archive/{UPDATE_REF}.tar.gz"
    tmp = tempfile.mkdtemp(prefix="interlude-upd-")
    tgz = os.path.join(tmp, "src.tar.gz")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "interlude-updater"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tgz, "wb") as f:
            shutil.copyfileobj(r, f)
        with tarfile.open(tgz, "r:gz") as tar:
            _safe_extract(tar, tmp)
        app_src = _find_app_dir(tmp)
        if not app_src:
            return None
        for required in ("interlude.py", "server.py", "app.html"):
            if not os.path.isfile(os.path.join(app_src, required)):
                return None  # doesn't look like Interlude — refuse to apply
        return app_src
    except Exception:
        return None


def apply_staged(app_src):
    """Copy staged app/* over the install, per-file atomically. Never touches
    files/dirs that hold local progress (state.json, .run/) — so a stray copy
    in the download can't clobber the user's saves."""
    keep = {"state.json", ".run"}
    try:
        for base, dirs, files in os.walk(app_src):
            rel = os.path.relpath(base, app_src)
            # don't descend into (or recreate) preserved local-only dirs
            dirs[:] = [d for d in dirs if d not in keep]
            dest_dir = BASE_DIR if rel == "." else os.path.join(BASE_DIR, rel)
            os.makedirs(dest_dir, exist_ok=True)
            for fn in files:
                if rel == "." and fn in keep:
                    continue
                dst = os.path.join(dest_dir, fn)
                tmp = dst + ".upd.tmp"
                shutil.copy2(os.path.join(base, fn), tmp)
                os.replace(tmp, dst)
        try:
            os.chmod(os.path.join(BASE_DIR, "interlude.py"), 0o755)
        except OSError:
            pass
        return True
    except Exception:
        return False


def run_apply_hooks():
    """Run the freshly-installed interlude.py to re-register hooks; returns
    True if the settings.json wiring changed (Claude restart needed)."""
    script = os.path.join(BASE_DIR, "interlude.py")
    try:
        out = subprocess.run([sys.executable, script, "_apply-hooks"],
                             capture_output=True, text=True, timeout=15)
        return out.stdout.strip() == "changed"
    except Exception:
        return False


def restart_server():
    """Relaunch the server so new server.py/app.html take effect (same port)."""
    port = read_port()
    if not ping(port):
        return  # not running; the next window open will launch the new server
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/quit", data=b"{}", timeout=2)
    except Exception:
        pass
    sp = pid_from(SERVER_PID)
    if alive(sp):
        try:
            os.kill(sp, signal.SIGTERM)
        except Exception:
            pass
    for _ in range(20):  # wait up to ~2s for it to exit
        if not alive(sp):
            break
        time.sleep(0.1)
    ensure_server()


def _update_pipeline(force):
    cur = local_version()
    now = int(time.time() * 1000)
    remote = fetch_remote_version()
    if remote is None:
        # couldn't reach the update server — keep any pending phase, flag it
        write_update(read_update().get("phase", "idle"), reachable=False, checkedAt=now)
        return
    if not force and not ver_gt(remote, cur):
        write_update("idle", version=remote, prev=cur, reachable=True, checkedAt=now)
        return
    write_update("downloading", version=remote, prev=cur, reachable=True, checkedAt=now)
    staged = download_and_stage()
    if not staged:
        write_update("error", version=remote, prev=cur, note="download failed", checkedAt=now)
        return
    write_update("downloaded", version=remote, prev=cur, checkedAt=now)
    write_update("applying", version=remote, prev=cur, checkedAt=now)
    if not apply_staged(staged):
        write_update("error", version=remote, prev=cur, note="apply failed", checkedAt=now)
        return
    hooks_changed = run_apply_hooks()
    restart_server()
    new_ver = local_version()
    write_update("restart_needed" if hooks_changed else "updated",
                 version=new_ver, prev=cur, checkedAt=now)


def do_update_run(force=False):
    ensure_run()
    try:
        lock = open(UPDATE_LOCK, "w")
    except OSError:
        return
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return  # another updater is already running
    try:
        _update_pipeline(force)
    except Exception:
        pass
    finally:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        except OSError:
            pass
        lock.close()


def maybe_check_update():
    """Called from on_prompt: throttled, opt-out-aware, spawns a detached check."""
    try:
        if update_disabled():
            return
        now = int(time.time() * 1000)
        up = read_update()
        if now - int(up.get("checkedAt", 0)) < UPDATE_INTERVAL * 1000:
            return
        write_update(up.get("phase", "idle"), checkedAt=now)  # claim this check window
        spawn_detached([sys.executable, __file__, "_update-run"])
    except Exception:
        pass


# ---------- hook entry points ----------
def _arm_watch(gen):
    """Spawn a delayed opener for this generation, unless one's already covering it."""
    if alive(pid_from(WINDOW_PID)):
        return  # window already up
    if alive(pid_from(WATCH_PID)):
        return  # a watcher is already pending
    spawn_detached([sys.executable, __file__, "_watch", str(gen)])


def on_prompt():
    if is_disabled():
        return
    st = read_status()
    gen = int(st.get("gen", 0)) + 1
    clear_keep()
    write_status(True, gen)
    # Only pop on the prompt itself when the user opted into prompt-based opening.
    # In "tool" mode we wait until Claude actually does something (see on_pretool),
    # so a pure text explanation never opens the window.
    if load_settings().get("openOn") in ("prompt", "both"):
        _arm_watch(gen)
    maybe_check_update()  # throttled, opt-out-aware, detached — never blocks the hook


def on_pretool():
    """Claude is about to use a tool (read/write/edit/run). This is the "doing
    something" trigger for tool-based opening."""
    if is_disabled():
        return
    s = load_settings()
    if s.get("openOn") == "prompt":
        return  # prompt-only mode: tool activity shouldn't open the window
    if s.get("toolScope") == "work":
        tool = hook_input().get("tool_name") or ""
        if tool and tool not in WORK_TOOLS:
            return  # a read-only tool (Read/Grep/Glob/…) — not "real work"
    st = read_status()
    gen = int(st.get("gen", 0))
    write_status(True, gen)  # keep same generation
    _arm_watch(gen)


def on_tool():
    if is_disabled():
        return
    st = read_status()
    gen = int(st.get("gen", 0))
    write_status(True, gen)  # keep same generation
    if alive(pid_from(WINDOW_PID)):
        return  # window already up
    if load_settings().get("openOn") == "prompt":
        return  # prompt-only mode: don't reopen off tool activity
    _arm_watch(gen)


def on_done():
    # Shared by on-stop and on-need: invalidate watchers, mark idle, close.
    if is_disabled():
        return
    st = read_status()
    gen = int(st.get("gen", 0)) + 1
    clear_keep()
    write_status(False, gen)
    spawn_detached([sys.executable, __file__, "_close"])


# ---------- admin ----------
def admin_off():
    ensure_run()
    open(DISABLED, "w").close()
    clear_keep()
    write_status(False, read_status().get("gen", 0) + 1)
    do_close()
    print("Interlude disabled. Re-enable with: interlude on")


def admin_on():
    try:
        os.remove(DISABLED)
    except OSError:
        pass
    print("Interlude enabled.")


def admin_status():
    port = read_port()
    print(json.dumps({
        "version": local_version(),
        "disabled": is_disabled(),
        "auto_update": not update_disabled(),
        "settings": load_settings(),
        "update": read_update(),
        "status": read_status(),
        "port": port,
        "server_up": ping(port),
        "window_open": alive(pid_from(WINDOW_PID)),
        "renderer": bool(shutil.which("osascript")) and os.path.exists(WEBVIEW_JS),
    }, indent=2))


def admin_version():
    print(f"interlude {local_version()}")


def admin_update():
    """`interlude update [off|on|--force]` — manage / force auto-update."""
    args = sys.argv[2:]
    if args and args[0] == "off":
        ensure_run()
        open(NO_UPDATE, "w").close()
        print("Auto-update disabled. Re-enable with: interlude update on")
        return
    if args and args[0] == "on":
        try:
            os.remove(NO_UPDATE)
        except OSError:
            pass
        print("Auto-update enabled.")
        return
    force = "--force" in args
    before = local_version()
    print(f"Checking {UPDATE_REPO}@{UPDATE_REF} (current v{before})…")
    do_update_run(force=force)
    up = read_update()
    phase = up.get("phase", "idle")
    after = local_version()
    if after != before:
        # code was actually applied during THIS run
        print(f"Updated to v{after}." +
              (" Restart Claude Code to finish." if phase == "restart_needed" else ""))
    elif phase == "error":
        print(f"Update failed: {up.get('note', 'unknown error')}")
    elif phase == "restart_needed":
        # a previously-applied update is still waiting to be acknowledged
        print(f"Update to v{up.get('version')} already applied — restart Claude Code to finish.")
    elif up.get("reachable") is False:
        print("Couldn't reach the update server; will retry later.")
    else:
        print(f"Already up to date (v{after}).")


def apply_hooks_cmd():
    print("changed" if register_hooks() else "unchanged")


def update_run_cmd():
    do_update_run(False)


def admin_stop_server():
    port = read_port()
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/quit", data=b"{}", timeout=1)
    except Exception:
        pass
    sp = pid_from(SERVER_PID)
    if alive(sp):
        try:
            os.kill(sp, signal.SIGTERM)
        except Exception:
            pass
    do_close()
    print("Interlude server stopped.")


COMMANDS = {
    "on-prompt": on_prompt,
    "on-pretool": on_pretool,
    "on-tool": on_tool,
    "on-stop": on_done,
    "on-need": on_done,
    "_open": do_open,
    "_close": do_close,
    "on": admin_on,
    "off": admin_off,
    "status": admin_status,
    "version": admin_version,
    "stop-server": admin_stop_server,
    "update": admin_update,
    "_apply-hooks": apply_hooks_cmd,
    "_update-run": update_run_cmd,
}


def main():
    if len(sys.argv) < 2:
        print("usage: interlude <command>")
        print("commands:", ", ".join(COMMANDS) + ", _watch <gen>")
        return
    cmd = sys.argv[1]
    if cmd in ("-v", "--version"):
        admin_version()
        return
    if cmd == "_watch":
        gen = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        do_watch(gen)
        return
    fn = COMMANDS.get(cmd)
    if not fn:
        print(f"unknown command: {cmd}")
        return
    try:
        fn()
    except Exception:
        # Hooks must never crash Claude Code; swallow and exit 0.
        pass


if __name__ == "__main__":
    main()
