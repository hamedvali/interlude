# Vendored games — credits & licenses

Interlude's **Arcade** games are third-party open-source projects vendored into
`app/games/<id>/`. Each game keeps its upstream `LICENSE` file verbatim. Only games
with a clear, permissive (MIT) license are included.

Interlude adds two small shared files to each game and nothing else touches the game's
own logic:

- `app/games/_theme.css` — Interlude light/dark design tokens.
- `app/games/_bridge.js` — save/resume: mirrors the game's `localStorage` to the
  Interlude server (`/api/state`) so play resumes mid-game after the window closes.

A per-game `interlude.css` (e.g. `app/games/2048/interlude.css`) only adjusts layout/
colours to fit the popup — it does not modify game behaviour.

| Game | Author | Source | License |
|------|--------|--------|---------|
| 2048 | Gabriele Cirulli | https://github.com/gabrielecirulli/2048 | MIT © 2014 Gabriele Cirulli |

## Adding more games

More classics are staged (commented out) in `ARCADE_GAMES` in `app/app.html`. To add one:

1. Vendor its source into `app/games/<id>/`, **keeping its `LICENSE`** (MIT only — verify
   before adding; if the license is GPL / CC-BY-SA / unclear, do not vendor it).
2. In the game's `index.html` `<head>`, before its own scripts, add:
   ```html
   <link href="/games/_theme.css" rel="stylesheet">
   <link href="interlude.css" rel="stylesheet">   <!-- optional per-game fit -->
   <script src="/games/_bridge.js"></script>
   ```
3. Uncomment (or add) the game's row in `ARCADE_GAMES` in `app/app.html`.
4. If the game keeps its state in `localStorage`, save/resume works automatically. If it
   keeps state only in memory, add a small adapter that calls `Interlude.save(snapshot)` /
   `Interlude.load()` (see `_bridge.js`) at the game's save/restore points.
5. Add a row to the table above.
