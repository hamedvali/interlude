#!/usr/bin/env python3
"""Interlude control script — invoked by Claude Code hooks.

Decides when to open/close the learn-and-play window:

    on-prompt   (UserPromptSubmit)  -> Claude starts; open after ~3s if still busy
    on-tool     (PostToolUse)        -> Claude still working; keep/re-open window
    on-stop     (Stop)               -> Claude finished; close window
    on-need     (Notification)       -> Claude needs you; close window

Internal subcommands: _watch <gen>, _open, _close.
Admin: on | off | install | status | version | stop-server.

Kill switch: set INTERLUDE_DISABLED=1, or run `interlude off`.
Every hook entry point returns fast (heavy work is spawned detached).
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request

__version__ = "1.0.0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = os.path.join(BASE_DIR, ".run")
STATUS_FILE = os.path.join(RUN_DIR, "status")
PORT_FILE = os.path.join(RUN_DIR, "port")
CHROME_PID = os.path.join(RUN_DIR, "chrome.pid")
SERVER_PID = os.path.join(RUN_DIR, "server.pid")
SERVER_LOG = os.path.join(RUN_DIR, "server.log")
WATCH_PID = os.path.join(RUN_DIR, "watch.pid")
DISABLED = os.path.join(RUN_DIR, "disabled")
KEEP_FILE = os.path.join(RUN_DIR, "keep")
CHROME_PROFILE = os.path.join(RUN_DIR, "chrome-profile")
APP_BASELINE = os.path.join(RUN_DIR, "app-baseline.json")
APP_ID_FILE = os.path.join(RUN_DIR, "app-id")

SERVER_PY = os.path.join(BASE_DIR, "server.py")
DEFAULT_PORT = int(os.environ.get("INTERLUDE_PORT", "47615"))
OPEN_DELAY = float(os.environ.get("INTERLUDE_DELAY", "3"))

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]


# ---------- small helpers ----------
def ensure_run():
    os.makedirs(RUN_DIR, exist_ok=True)


def is_disabled():
    return os.environ.get("INTERLUDE_DISABLED") == "1" or os.path.exists(DISABLED)


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


def find_chrome():
    for path in CHROME_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


_APP_ID_RE = re.compile(r"^[a-p]{32}$")


def _manifest_app_ids():
    """Set of Chrome web-app ids (32 chars a-p) present in the throwaway profile.

    Chrome stores each installed/preinstalled app under
    "<profile>/.../Web Applications/Manifest Resources/<app-id>/". A fresh
    profile is auto-seeded with Google's default apps (Docs, Gmail, ...), so a
    bare scan can't tell those from ours — see installed_app_id().
    """
    ids = set()
    bases = [
        os.path.join(CHROME_PROFILE, "Default", "Web Applications", "Manifest Resources"),
        os.path.join(CHROME_PROFILE, "Web Applications", "Manifest Resources"),
    ]
    for base in bases:
        try:
            for name in os.listdir(base):
                if _APP_ID_RE.match(name) and os.path.isdir(os.path.join(base, name)):
                    ids.add(name)
        except OSError:
            continue
    return ids


def installed_app_id():
    """Return the id of the *Interlude* PWA in our throwaway profile, or None.

    Chrome's app id is an opaque hash we can't reliably recompute, and the
    profile ships with Google's default apps, so we identify Interlude by
    exclusion: `interlude install` records the pre-existing app ids to
    app-baseline.json, and Interlude is whatever id shows up afterward. The
    discovered id is pinned to .run/app-id so later default-app churn can't
    confuse it. Without a baseline (never installed) we return None and the
    caller falls back to --app.
    """
    pinned = None
    try:
        with open(APP_ID_FILE) as f:
            pinned = f.read().strip()
    except OSError:
        pass
    present = _manifest_app_ids()
    if pinned and pinned in present:
        return pinned
    try:
        with open(APP_BASELINE) as f:
            baseline = set(json.load(f))
    except Exception:
        return None
    new = sorted(present - baseline)
    if not new:
        return None
    app_id = new[0]
    try:
        with open(APP_ID_FILE, "w") as f:
            f.write(app_id)
    except OSError:
        pass
    return app_id


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
def do_open():
    if alive(pid_from(CHROME_PID)):
        return  # already showing
    port = ensure_server()
    if not port:
        return
    chrome = find_chrome()
    url = f"http://127.0.0.1:{port}/"
    ensure_run()
    if not chrome:
        # Fallback: open in the default browser (cannot auto-close a plain tab).
        subprocess.Popen(["open", url], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    app_id = installed_app_id()
    launch = f"--app-id={app_id}" if app_id else f"--app={url}"
    args = [
        chrome,
        launch,
        f"--user-data-dir={CHROME_PROFILE}",
        "--window-size=1240,840",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-infobars",
    ]
    p = spawn_detached(args)
    with open(CHROME_PID, "w") as f:
        f.write(str(p.pid))


def do_close():
    """Let the window run its ~3s close countdown and self-close, then force it.

    The web app shows a "closing in 3, 2, 1" modal and closes itself; we wait a
    bit past that as a backstop. Aborts if Claude becomes busy again mid-close,
    or if the user pressed Esc / "Keep open" (keep_requested), so we never kill
    a window that should stay up.
    """
    pid = pid_from(CHROME_PID)
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
        os.remove(CHROME_PID)
    except OSError:
        pass


def do_watch(gen):
    """Wait OPEN_DELAY, then open only if this generation is still busy."""
    ensure_run()
    with open(WATCH_PID, "w") as f:
        f.write(str(os.getpid()))
    time.sleep(OPEN_DELAY)
    st = read_status()
    if st.get("gen") == gen and st.get("busy"):
        do_open()
    try:
        os.remove(WATCH_PID)
    except OSError:
        pass


# ---------- hook entry points ----------
def on_prompt():
    if is_disabled():
        return
    st = read_status()
    gen = int(st.get("gen", 0)) + 1
    clear_keep()
    write_status(True, gen)
    spawn_detached([sys.executable, __file__, "_watch", str(gen)])


def on_tool():
    if is_disabled():
        return
    st = read_status()
    gen = int(st.get("gen", 0))
    write_status(True, gen)  # keep same generation
    if alive(pid_from(CHROME_PID)):
        return  # window already up
    if alive(pid_from(WATCH_PID)):
        return  # a watcher is already pending
    spawn_detached([sys.executable, __file__, "_watch", str(gen)])


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


def admin_install():
    """Open a normal Chrome window (in our throwaway profile) so you can click
    "Install Interlude" once. After that, do_open() launches the installed app
    (own dock icon; clicking it reopens Interlude, not an empty window)."""
    port = ensure_server()
    if not port:
        print("Could not start the Interlude server.")
        return
    chrome = find_chrome()
    if not chrome:
        print("Chrome (or Edge/Brave/Chromium) not found; cannot install as an app.")
        return
    if installed_app_id():
        print("Interlude is already installed. Nothing to do.")
        return
    ensure_run()
    # Record the apps already present (Chrome's defaults) so we can later
    # identify Interlude as the one that appears after you click Install.
    with open(APP_BASELINE, "w") as f:
        json.dump(sorted(_manifest_app_ids()), f)
    try:
        os.remove(APP_ID_FILE)
    except OSError:
        pass
    url = f"http://127.0.0.1:{port}/"
    spawn_detached([
        chrome,
        "--new-window", url,
        f"--user-data-dir={CHROME_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
    ])
    print("A Chrome window opened at Interlude.")
    print('Install it: click the install icon in the address bar (or ⋮ menu →')
    print('  "Cast, save, and share" → "Install page as app…"), then confirm "Install".')
    print("Close that window afterward. From then on, Interlude launches as its own")
    print("app with its own dock icon whenever Claude is working.")


def admin_status():
    port = read_port()
    print(json.dumps({
        "version": __version__,
        "disabled": is_disabled(),
        "status": read_status(),
        "port": port,
        "server_up": ping(port),
        "window_open": alive(pid_from(CHROME_PID)),
        "chrome": find_chrome(),
        "installed_app": installed_app_id(),
    }, indent=2))


def admin_version():
    print(f"interlude {__version__}")


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
    "on-tool": on_tool,
    "on-stop": on_done,
    "on-need": on_done,
    "_open": do_open,
    "_close": do_close,
    "on": admin_on,
    "off": admin_off,
    "install": admin_install,
    "status": admin_status,
    "version": admin_version,
    "stop-server": admin_stop_server,
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
