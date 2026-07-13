#!/usr/bin/env python3
"""Interlude server: serves the learn/play app and stores progress.

Zero dependencies (Python standard library only). Started lazily by
interlude.py, then left running between prompts. Shares a "busy" flag with
the hook scripts through the .run/status file so the web app knows when
Claude is working vs. ready.
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUN_DIR = os.path.join(BASE_DIR, ".run")
STATUS_FILE = os.path.join(RUN_DIR, "status")
PORT_FILE = os.path.join(RUN_DIR, "port")
KEEP_FILE = os.path.join(RUN_DIR, "keep")
UPDATE_FILE = os.path.join(RUN_DIR, "update.json")
APP_FILE = os.path.join(BASE_DIR, "app.html")
VERSION_FILE = os.path.join(BASE_DIR, "VERSION")
DECK_FILE = os.path.join(BASE_DIR, "words.json")
STATE_FILE = os.path.join(BASE_DIR, "state.json")
GAMES_DIR = os.path.join(BASE_DIR, "games")

DEFAULT_PORT = int(os.environ.get("INTERLUDE_PORT", "47615"))

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
    "games": {},        # gameId -> {"played": 0, "best": 0, "wins": 0}
    "arcade": {},       # gameId -> {"store": "<json>", "best": N, "updatedAt": ms}
    "streak": 0,
    "lastDay": "",
    "reviews": 0,
    "history": [],       # list of {"day": "YYYY-MM-DD", "reviews": N}
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


def read_status():
    try:
        with open(STATUS_FILE) as f:
            status = json.load(f)
    except Exception:
        status = {"busy": False, "gen": 0}
    # Fold in the running version + auto-update lifecycle so the app's existing
    # 800ms poll drives the update toast with no extra request.
    status["version"] = read_version()
    try:
        with open(UPDATE_FILE) as f:
            status["update"] = json.load(f)
    except Exception:
        status["update"] = {"phase": "idle"}
    return status


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def write_state(state):
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
        elif path == "/api/deck":
            self._send(200, read_json(DECK_FILE, {"words": []}))
        elif path == "/api/state":
            state = read_json(STATE_FILE, None)
            if state is None:
                state = json.loads(json.dumps(DEFAULT_STATE))
            self._send(200, state)
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
