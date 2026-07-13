# Interlude

**Turn Claude's thinking time into learning time.** Interlude is a tiny,
no-typing companion for [Claude Code](https://claude.com/claude-code): while
Claude works, a little window pops up so you can learn English words and play
quick word games. When Claude finishes — or needs your input — the window
counts down and closes itself, so it's out of the way exactly when you need to
read or reply.

No accounts, no telemetry, no dependencies — it renders with the WebKit and
`osascript` that already ship with macOS. The window has **no Dock icon**.

```bash
curl -fsSL https://raw.githubusercontent.com/hamedvali/interlude/main/install.sh | bash
```

Then **restart Claude Code**. That's it — next time Claude runs for more than a
few seconds, Interlude appears.

> macOS only. Requires `python3` and `osascript` — both ship with macOS. The
> window is a native WKWebView popup with no Dock icon; no browser or PWA
> install is needed.

---

## How it works

Interlude installs four **global** [Claude Code hooks](https://docs.claude.com/en/docs/claude-code/hooks)
into `~/.claude/settings.json`. They call a small local control script that
manages a zero-dependency Python web server and a native macOS popup window:

| Hook event        | When it fires             | What Interlude does                          |
|-------------------|---------------------------|----------------------------------------------|
| `UserPromptSubmit`| You send a prompt         | Arms a timer; opens the window if Claude is still busy after ~3s |
| `PostToolUse`     | Claude runs a tool        | Keeps the window up while work continues     |
| `Stop`            | Claude finishes replying  | Shows a "closing in 3…2…1" modal, then closes |
| `Notification`    | Claude needs your input   | Closes the window so you can respond         |

The window is a native **WKWebView** popup rendered by `osascript` with an
*accessory* activation policy — so it shows on screen but adds **no Dock icon
and no menu bar**. It auto-closes by tracking its own process. Everything runs
on `127.0.0.1` — nothing leaves your machine.

## Controls

```bash
interlude off          # pause Interlude (stops opening the window)
interlude on           # resume
interlude status       # show version, server, and window state as JSON
interlude stop-server  # stop the background web server
interlude version      # print the version
```

If `~/.local/bin` isn't on your `PATH`, run it directly:
`python3 ~/.interlude/interlude.py <command>`.

## Customize

- **Words** — edit `~/.interlude/words.json` (a list of `{word, meaning, …}`).
- **Open delay** — set `INTERLUDE_DELAY` (seconds) before the window appears
  (default `3`). Export it in your shell profile.
- **Window size** — set `INTERLUDE_WIDTH` / `INTERLUDE_HEIGHT` (default `1240`×`840`).
- **Port** — set `INTERLUDE_PORT` (default `47615`) if it clashes with something.

Changes to hooks or env take effect after you restart Claude Code.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/hamedvali/interlude/main/uninstall.sh | bash
```

This removes the hooks (with a settings backup), the `interlude` CLI, and
`~/.interlude`. Add `--keep-data` to preserve your learning progress.

## Development

Clone the repo and install from your local checkout — no download, no GitHub
needed:

```bash
git clone https://github.com/hamedvali/interlude.git
cd interlude
bash install.sh --local
```

The app itself is `app/interlude.py` (hook controller), `app/webview.js` (the
native WKWebView popup, run via `osascript`), `app/server.py` (stdlib web
server), and `app/app.html` (the UI). `INTERLUDE_HOME` overrides the install
location — handy for isolated testing.

## License

[MIT](LICENSE)
