#!/usr/bin/env python3
"""Interlude server: serves the learn/play app and stores progress.

Zero dependencies (Python standard library only). Started lazily by
interlude.py, then left running between prompts. Shares a "busy" flag with
the hook scripts through the .run/status file so the web app knows when
Claude is working vs. ready.
"""
import json
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _data_dir():
    """Where progress, settings, and runtime files live. Kept apart from
    BASE_DIR (the code) so a plugin update can't delete a user's flashcard
    history. Must stay in sync with interlude.py's copy."""
    env = os.environ.get("INTERLUDE_DATA")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if os.path.exists(os.path.join(BASE_DIR, "state.json")):
        return BASE_DIR
    return os.path.expanduser("~/.interlude")


DATA_DIR = _data_dir()
RUN_DIR = os.path.join(DATA_DIR, ".run")
STATUS_FILE = os.path.join(RUN_DIR, "status")
PORT_FILE = os.path.join(RUN_DIR, "port")
KEEP_FILE = os.path.join(RUN_DIR, "keep")
UPDATE_FILE = os.path.join(RUN_DIR, "update.json")
DISABLED_FILE = os.path.join(RUN_DIR, "disabled")
NO_UPDATE_FILE = os.path.join(RUN_DIR, "no-update")
SNOOZE_FILE = os.path.join(RUN_DIR, "snooze")   # epoch-ms deadline while the app is muted
APP_FILE = os.path.join(BASE_DIR, "app.html")
VERSION_FILE = os.path.join(BASE_DIR, "VERSION")
SETTINGS_JSON = os.path.join(DATA_DIR, "settings.json")
DECK_FILE = os.path.join(BASE_DIR, "words.json")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
GAMES_DIR = os.path.join(BASE_DIR, "games")
BROWSER_JS = os.path.join(BASE_DIR, "browser.js")

DEFAULT_PORT = int(os.environ.get("INTERLUDE_PORT", "47615"))

# Social platforms the Social view can open. Each opens in its own native
# WKWebView window (browser.js) — a top-level load, since these sites forbid
# <iframe> embedding via X-Frame-Options / CSP frame-ancestors. The window
# shares the persistent cookie store, so a login sticks across sessions.
SOCIAL_SITES = {
    "instagram": ("Instagram", "https://www.instagram.com/"),
    "x": ("X", "https://x.com/"),
    "tiktok": ("TikTok", "https://www.tiktok.com/"),
}
SOCIAL_WIN = (1040, 880)  # width, height of a social window

# User-editable settings the app's Settings view reads/writes. Env vars seed the
# defaults so existing installs keep their behavior. Keep this in sync with
# interlude.py's SETTINGS_DEFAULTS (both read the same settings.json).
SETTINGS_DEFAULTS = {
    "openOn": os.environ.get("INTERLUDE_OPEN_ON", "tool"),
    "toolScope": os.environ.get("INTERLUDE_TOOL_SCOPE", "all"),
    "openDelay": float(os.environ.get("INTERLUDE_DELAY", "3")),
    "width": int(os.environ.get("INTERLUDE_WIDTH", "350")),
    "height": int(os.environ.get("INTERLUDE_HEIGHT", "800")),
    "sound": False,
}
_SETTINGS_CHOICES = {
    "openOn": {"tool", "prompt", "both"},
    "toolScope": {"all", "work"},
}

# Content types for the vendored games served out of app/games/.
MIME = {
    ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".webp": "image/webp", ".bmp": "image/bmp",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4",
    ".map": "application/json", ".txt": "text/plain; charset=utf-8",
    ".webmanifest": "application/manifest+json",
}

DEFAULT_STATE = {
    "words": {},        # word -> {"box": 1, "seen": 0, "correct": 0}
    "deck": [],          # user-managed word list, seeded from words.json on first run
    "deckSeeded": False, # True once seeded, so an emptied deck isn't re-seeded
    "games": {},        # gameId -> {"played": 0, "best": 0, "wins": 0}
    "arcade": {},       # gameId -> {"store": "<json>", "best": N, "updatedAt": ms}
    "streak": 0,
    "lastDay": "",
    "reviews": 0,
    "history": [],       # list of {"day": "YYYY-MM-DD", "reviews": N}
    "nav": {},           # last-visited location: {"view","learnTab","social","arcade"}
}


def read_version():
    try:
        with open(VERSION_FILE) as f:
            v = f.read().strip()
            if v:
                return v
    except Exception:
        pass
    return "0"


def read_settings():
    """Merge settings.json over the defaults, plus the marker-file toggles
    (enabled / autoUpdate) so the Settings view sees one unified object."""
    data = dict(SETTINGS_DEFAULTS)
    user = read_json(SETTINGS_JSON, {})
    if isinstance(user, dict):
        for k in SETTINGS_DEFAULTS:
            if k in user:
                data[k] = user[k]
    data["enabled"] = not os.path.exists(DISABLED_FILE)
    data["autoUpdate"] = not os.path.exists(NO_UPDATE_FILE)
    data["version"] = read_version()
    return data


def _coerce_settings(incoming):
    """Validate/clamp an incoming settings patch down to known, safe values."""
    out = {}
    for k in ("openOn", "toolScope"):
        if k in incoming and incoming[k] in _SETTINGS_CHOICES[k]:
            out[k] = incoming[k]
    if "openDelay" in incoming:
        try:
            out["openDelay"] = max(0.0, min(30.0, float(incoming["openDelay"])))
        except (TypeError, ValueError):
            pass
    for k in ("width", "height"):
        if k in incoming:
            try:
                out[k] = max(320, min(4000, int(incoming[k])))
            except (TypeError, ValueError):
                pass
    if "sound" in incoming:
        out["sound"] = bool(incoming["sound"])
    return out


def _set_marker(path, present):
    """Create the marker file when `present`, remove it otherwise."""
    if present:
        try:
            os.makedirs(RUN_DIR, exist_ok=True)
            open(path, "w").close()
        except OSError:
            pass
    else:
        try:
            os.remove(path)
        except OSError:
            pass


def write_settings(incoming):
    """Persist a settings patch: tunables to settings.json, enabled/autoUpdate
    to their marker files (so the CLI `interlude on/off`, `update off/on` stay
    consistent with the UI)."""
    patch = _coerce_settings(incoming)
    if patch:
        cur = read_json(SETTINGS_JSON, {})
        if not isinstance(cur, dict):
            cur = {}
        cur.update(patch)
        tmp = SETTINGS_JSON + ".tmp"
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(tmp, "w") as f:
                json.dump(cur, f, indent=2)
            os.replace(tmp, SETTINGS_JSON)
        except OSError:
            pass
    if "enabled" in incoming:
        _set_marker(DISABLED_FILE, not bool(incoming["enabled"]))
    if "autoUpdate" in incoming:
        _set_marker(NO_UPDATE_FILE, not bool(incoming["autoUpdate"]))
    return read_settings()


def read_snooze():
    """Active snooze as {"until": <epoch-ms>}, or None. Self-cleans a lapsed file."""
    try:
        with open(SNOOZE_FILE) as f:
            until = int(f.read().strip() or 0)
    except Exception:
        return None
    if until > int(time.time() * 1000):
        return {"until": until}
    try:
        os.remove(SNOOZE_FILE)   # expired — tidy up so the state reads cleanly
    except OSError:
        pass
    return None


def write_snooze(hours):
    """Set (hours in {1,3,8}) or clear (hours == 0) the snooze deadline. Returns the deadline ms (0 = cleared)."""
    if hours == 0:
        try:
            os.remove(SNOOZE_FILE)
        except OSError:
            pass
        return 0
    until = int(time.time() * 1000) + hours * 3600_000
    tmp = SNOOZE_FILE + ".tmp"
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(tmp, "w") as f:
            f.write(str(until))
        os.replace(tmp, SNOOZE_FILE)
    except OSError:
        return 0
    return until


def read_status():
    try:
        with open(STATUS_FILE) as f:
            status = json.load(f)
    except Exception:
        status = {"busy": False, "gen": 0}
    # The attention field (state-aware routing) may be absent on older status
    # files or while busy; keep it always present so the app can read it.
    status.setdefault("attention", None)
    # Fold in the running version + auto-update lifecycle so the app's existing
    # 800ms poll drives the update toast with no extra request.
    status["version"] = read_version()
    try:
        with open(UPDATE_FILE) as f:
            status["update"] = json.load(f)
    except Exception:
        status["update"] = {"phase": "idle"}
    # Snooze rides the same poll so the top-right control ticks + auto-clears live.
    status["snooze"] = read_snooze()
    return status


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def write_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def deep_merge(base, incoming):
    """Recursively merge incoming into base (incoming wins on leaves)."""
    for k, v in incoming.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _bump_front(path):
    """Increment a re-front counter; browser.js polls it to raise its window."""
    try:
        n = 0
        try:
            with open(path) as f:
                n = int(f.read().strip() or "0")
        except Exception:
            n = 0
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(n + 1))
        os.replace(tmp, path)
    except OSError:
        pass


def open_social(site):
    """Open (or re-front) a signed-in native window for one social platform.

    If a window for this platform is already alive, bump its front-file so it
    raises itself instead of spawning a duplicate. Falls back to the system
    browser if the native renderer (osascript + browser.js) isn't available.
    """
    if site not in SOCIAL_SITES:
        return {"ok": False, "error": "unknown site"}
    name, url = SOCIAL_SITES[site]
    os.makedirs(RUN_DIR, exist_ok=True)
    pid_file = os.path.join(RUN_DIR, "social-%s.pid" % site)
    front_file = os.path.join(RUN_DIR, "social-%s.front" % site)

    if _pid_alive(_read_pid(pid_file)):
        _bump_front(front_file)
        return {"ok": True, "fronted": True}

    osa = shutil.which("osascript")
    if osa and os.path.exists(BROWSER_JS):
        try:
            p = subprocess.Popen(
                [osa, "-l", "JavaScript", BROWSER_JS, url, name,
                 str(SOCIAL_WIN[0]), str(SOCIAL_WIN[1]), front_file],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True, cwd=BASE_DIR)
            with open(pid_file, "w") as f:
                f.write(str(p.pid))
            return {"ok": True, "opened": True}
        except OSError:
            pass

    # Last-ditch fallback: the default browser (still persists the login there).
    try:
        subprocess.Popen(["open", url], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"ok": True, "opened": True, "fallback": "browser"}
    except OSError:
        return {"ok": False, "error": "cannot open"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence request logging
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path):
        """Serve a vendored game file from app/games/, guarding traversal."""
        rel = unquote(path[len("/games/"):])
        base = os.path.realpath(GAMES_DIR)
        full = os.path.realpath(os.path.join(GAMES_DIR, rel))
        if full != base and not full.startswith(base + os.sep):
            self._send(403, {"error": "forbidden"})
            return
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            self._send(404, {"error": "not found"})
            return
        ctype = MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        try:
            with open(full, "rb") as f:
                self._send(200, f.read(), ctype)
        except OSError:
            self._send(404, {"error": "not found"})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/app.html", "/index.html"):
            try:
                with open(APP_FILE, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, {"error": "app.html missing"})
        elif path.startswith("/games/"):
            self._serve_static(path)
        elif path == "/api/ping":
            self._send(200, {"ok": True})
        elif path == "/api/status":
            self._send(200, read_status())
        elif path == "/api/settings":
            self._send(200, read_settings())
        elif path == "/api/deck":
            self._send(200, read_json(DECK_FILE, {"words": []}))
        elif path == "/api/state":
            state = read_json(STATE_FILE, None)
            if state is None:
                state = json.loads(json.dumps(DEFAULT_STATE))
            self._send(200, state)
        elif path == "/api/social/open":
            q = parse_qs(urlparse(self.path).query)
            site = (q.get("site") or [""])[0]
            self._send(200, open_social(site))
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        if path == "/api/state":
            try:
                incoming = json.loads(raw or b"{}")
            except Exception:
                self._send(400, {"error": "bad json"})
                return
            state = read_json(STATE_FILE, None)
            if state is None:
                state = json.loads(json.dumps(DEFAULT_STATE))
            deep_merge(state, incoming)
            write_state(state)
            self._send(200, state)
        elif path == "/api/settings":
            try:
                incoming = json.loads(raw or b"{}")
            except Exception:
                self._send(400, {"error": "bad json"})
                return
            if not isinstance(incoming, dict):
                self._send(400, {"error": "expected object"})
                return
            self._send(200, write_settings(incoming))
        elif path == "/api/keep":
            # The user pressed Esc / "Keep open" during the close countdown.
            # Record the generation being kept so interlude.py aborts its kill.
            try:
                gen = int(json.loads(raw or b"{}").get("gen", 0))
            except Exception:
                gen = 0
            try:
                with open(KEEP_FILE, "w") as f:
                    f.write(str(gen))
            except OSError:
                pass
            self._send(200, {"ok": True, "kept": gen})
        elif path == "/api/snooze":
            # Mute the app for 1/3/8h (hours in {1,3,8}) or clear it (hours == 0).
            try:
                hours = int(json.loads(raw or b"{}").get("hours", 0))
            except Exception:
                hours = 0
            if hours not in (0, 1, 3, 8):
                self._send(400, {"error": "hours must be 0, 1, 3, or 8"})
                return
            until = write_snooze(hours)
            self._send(200, {"ok": True, "until": until})
        elif path == "/api/update":
            # The app acknowledges/dismisses a finished update toast
            # (restart_needed / error / updated) — reset the lifecycle to idle.
            try:
                with open(UPDATE_FILE) as f:
                    data = json.load(f)
            except Exception:
                data = {}
            data["phase"] = "idle"
            tmp = UPDATE_FILE + ".tmp"
            try:
                with open(tmp, "w") as f:
                    json.dump(data, f)
                os.replace(tmp, UPDATE_FILE)
            except OSError:
                pass
            self._send(200, {"ok": True})
        elif path == "/api/quit":
            self._send(200, {"ok": True, "bye": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(404, {"error": "not found"})


def main():
    os.makedirs(RUN_DIR, exist_ok=True)
    port = DEFAULT_PORT
    httpd = None
    # Try the preferred port, then a few fallbacks if it's taken.
    for candidate in [DEFAULT_PORT] + list(range(DEFAULT_PORT + 1, DEFAULT_PORT + 20)):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit("Could not bind a port for the Interlude server")
    with open(PORT_FILE, "w") as f:
        f.write(str(port))
    print(f"Interlude server on http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
