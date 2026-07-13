#!/usr/bin/env python3
"""Interlude control script — invoked by Claude Code hooks.

Decides when to open/close the learn-and-play window:

    on-prompt   (UserPromptSubmit)  -> Claude starts; open after ~3s if still busy
    on-tool     (PostToolUse)        -> Claude still working; keep/re-open window
    on-stop     (Stop)               -> Claude finished; close window
    on-need     (Notification)       -> Claude needs you; close window

The window is a native macOS WKWebView popup (webview.js, run via osascript)
with an *accessory* activation policy: it shows on screen but adds NO Dock icon
and no menu bar. Zero dependencies — no Chrome, no PWA install step.

Internal subcommands: _watch <gen>, _open, _close.
Admin: on | off | status | version | stop-server.

Kill switch: set INTERLUDE_DISABLED=1, or run `interlude off`.
Every hook entry point returns fast (heavy work is spawned detached).
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

__version__ = "1.1.0"

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

SERVER_PY = os.path.join(BASE_DIR, "server.py")
WEBVIEW_JS = os.path.join(BASE_DIR, "webview.js")
DEFAULT_PORT = int(os.environ.get("INTERLUDE_PORT", "47615"))
OPEN_DELAY = float(os.environ.get("INTERLUDE_DELAY", "3"))
WINDOW_W = int(os.environ.get("INTERLUDE_WIDTH", "1240"))
WINDOW_H = int(os.environ.get("INTERLUDE_HEIGHT", "840"))


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
    """Open the Interlude popup: a native WKWebView window with no Dock icon.

    Rendered by webview.js via osascript (JavaScript for Automation). We track
    the osascript PID so do_close() can kill it cleanly.
    """
    if alive(pid_from(WINDOW_PID)):
        return  # already showing
    port = ensure_server()
    if not port:
        return
    ensure_run()
    url = f"http://127.0.0.1:{port}/"
    osa = shutil.which("osascript")
    if not osa or not os.path.exists(WEBVIEW_JS):
        # Last-ditch fallback: default browser (can't auto-close a plain tab,
        # and this one does show a Dock icon). Should never happen on macOS.
        subprocess.Popen(["open", url], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    p = spawn_detached([osa, "-l", "JavaScript", WEBVIEW_JS,
                        url, str(WINDOW_W), str(WINDOW_H)])
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
    if alive(pid_from(WINDOW_PID)):
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


def admin_status():
    port = read_port()
    print(json.dumps({
        "version": __version__,
        "disabled": is_disabled(),
        "status": read_status(),
        "port": port,
        "server_up": ping(port),
        "window_open": alive(pid_from(WINDOW_PID)),
        "renderer": bool(shutil.which("osascript")) and os.path.exists(WEBVIEW_JS),
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
