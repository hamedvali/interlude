# Interlude

**Turn Claude's thinking time into learning time.** Interlude is a tiny,
no-typing companion for [Claude Code](https://claude.com/claude-code): while
Claude works, a little window pops up so you can learn English words and play
quick word games. When Claude finishes — or needs your input — the window
counts down and closes itself, so it's out of the way exactly when you need to
read or reply.

No accounts, no telemetry, no dependencies beyond Python 3 and a browser.

```bash
curl -fsSL https://raw.githubusercontent.com/hamedvali/interlude/main/install.sh | bash
```

Then **restart Claude Code**. That's it — next time Claude runs for more than a
few seconds, Interlude appears.

> macOS only for now. Requires `python3` (ships with macOS) and, for the best
> experience, Google Chrome (Edge / Brave / Chromium also work). Without a
> Chromium browser it falls back to your default browser in a normal tab.

---

## How it works

Interlude installs four **global** [Claude Code hooks](https://docs.claude.com/en/docs/claude-code/hooks)
into `~/.claude/settings.json`. They call a small local control script that
manages a zero-dependency Python web server and a dedicated browser window:

| Hook event        | When it fires             | What Interlude does                          |
|-------------------|---------------------------|----------------------------------------------|
| `UserPromptSubmit`| You send a prompt         | Arms a timer; opens the window if Claude is still busy after ~3s |
| `PostToolUse`     | Claude runs a tool        | Keeps the window up while work continues     |
| `Stop`            | Claude finishes replying  | Shows a "closing in 3…2…1" modal, then closes |
| `Notification`    | Claude needs your input   | Closes the window so you can respond         |

The window lives in an isolated browser profile under `~/.interlude/.run/`, so
it never touches your normal browsing, and it auto-closes by tracking its own
process. Everything runs on `127.0.0.1` — nothing leaves your machine.

## Controls

```bash
interlude off          # pause Interlude (stops opening the window)
interlude on           # resume
interlude status       # show version, server, and window state as JSON
interlude install      # register the window as an installed app (own dock icon)
interlude stop-server  # stop the background web server
interlude version      # print the version
```

If `~/.local/bin` isn't on your `PATH`, run it directly:
`python3 ~/.interlude/interlude.py <command>`.

### Give it a dock icon

The **first time** the window opens, a small **"Install Interlude"** toast slides
in at the top. Click **Install** once and Interlude becomes a standalone app with
its own dock icon — every window after that opens as the clean installed app, and
the toast never returns. That's the whole setup; no command needed.

(The first-run window is a normal browser window because Chrome only offers the
one-click install there. After you install, it switches to the clean app frame.)

Prefer to do it by hand, or the toast didn't appear? Run `interlude install` and
click **Install** in the window that opens (address-bar icon, or ⋮ menu →
*Cast, save, and share* → *Install page as app…*).

## Customize

- **Words** — edit `~/.interlude/words.json` (a list of `{word, meaning, …}`).
- **Open delay** — set `INTERLUDE_DELAY` (seconds) before the window appears
  (default `3`). Export it in your shell profile.
- **Port** — set `INTERLUDE_PORT` (default `47615`) if it clashes with something.

Changes to hooks or env take effect after you restart Claude Code.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/hamedvali/interlude/main/uninstall.sh | bash
```

This removes the hooks (with a settings backup), the `interlude` CLI, and
`~/.interlude`. Add `--keep-data` to preserve your learning progress. If you
installed the dock-icon app, remove it from your browser at `chrome://apps`.

## Development

Clone the repo and install from your local checkout — no download, no GitHub
needed:

```bash
git clone https://github.com/hamedvali/interlude.git
cd interlude
bash install.sh --local
```

The app itself is `app/interlude.py` (hook controller), `app/server.py`
(stdlib web server), and `app/app.html` (the UI). `INTERLUDE_HOME` overrides
the install location — handy for isolated testing.

## License

[MIT](LICENSE)
