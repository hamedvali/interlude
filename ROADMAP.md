# 🗺️ Interlude Roadmap

> Grounded in research (2026-07-13) into what developers actually ask for during
> the "dead time" while an AI coding agent works. Sources are cited inline. This
> is a living document — reorder freely as evidence and usage change.

## 🔎 What the research found

Signal, ranked by how strongly the evidence supported it. (A handful of source
claims were flagged as *over-generalized* during fact-checking — noted where relevant.)

1. **"Tell me when it's done or needs me."** The single most repeated ask across
   blogs, purpose-built tools, and official docs. Developers context-switch away,
   then forget the agent finished ("came back to find Claude finished 10 min ago").
   The recurring nuance: **distinct signals for distinct states** — done vs.
   needs-input vs. permission vs. error — not one undifferentiated ping. Alert
   fatigue is a real counter-force: fire on genuine completion (`Stop`) and on
   `Notification` states, not on every short turn.
   — [wmedia.es](https://wmedia.es/en/tips/claude-code-notify-when-done),
   [alexop.dev](https://alexop.dev/posts/claude-code-notification-hooks/),
   [d12frosted](https://www.d12frosted.io/posts/2026-01-05-claude-code-notifications),
   [AgentBell](https://agentbell.dev/blog/claude-code-notification-when-done),
   [Claude Code hooks docs](https://code.claude.com/docs/en/hooks-guide)

2. **Context-switching is the real enemy; staying in flow is the goal.** The
   notification is just a means. Recovery penalty after an interruption is
   ~23 min (UC Irvine).
   — [dev-tester.com](https://dev-tester.com/the-real-cost-of-a-slow-ci-build/)

3. **The wait itself is unpredictable, anxiety-inducing, boredom-filling** — and
   this is exactly Interlude's thesis. The ideal wait activity: **(a) loads no new
   context into working memory, (b) provides genuine micro-recovery, (c) is
   droppable instantly without loss.**
   — [super-productivity](https://super-productivity.com/blog/what-to-do-while-waiting-for-claude-code/),
   [Medium/riuzzang](https://medium.com/riuzzang/from-compiling-to-agent-thinking-the-excuse-that-took-20-years-to-recompile-0e83b66ed869)

4. **Parallel agents & orchestration overhead** — an emerging trend. Ceiling is
   cognitive, not technical (track ~3 reliably, drop past ~5; sweet spot 3–8).
   Pain becomes a **visibility problem**: "which agent needs me *right now*?"
   People want a unified view, not many terminals.
   — [Pragmatic Engineer](https://blog.pragmaticengineer.com/new-trend-programming-by-kicking-off-parallel-ai-agents/),
   [agentsroom.dev](https://agentsroom.dev/parallel-coding),
   [Conductor (HN)](https://news.ycombinator.com/item?id=44594584)

5. **Queue work while it runs** — moderate demand to queue the next prompt to
   send when the agent frees up.

## ✅ What Interlude already nails

Auto-appear on busy + instant auto-close = "droppable, no watching." That is a
direct answer to theme #3. The clearest *gaps* are in themes #1 and #4.

## 🎯 Prioritized features

### Tier 1 — strongly evidence-backed, on-brand
1. ✅ **State-aware attention routing** *(shipped v1.5.0)* — distinct sound +
   colored banner for done / needs-input / permission / error. Rides the #1 signal.
   **(Spec below.)**
2. **Focus mode ("don't interrupt when I'm watching")** — quiet/suppress when the
   user is actively at the terminal. **(Spec below.)** *(Prevalence of this exact
   ask was flagged as thin; treat as a strong design principle, not proven majority.)*
   *Partially delivered v1.6.0:* **Snooze** — a top-right control (and `interlude
   snooze 1h|3h|8h`) mutes the popup + sounds for a set time, then auto-resumes. That's
   this spec's headline manual toggle; the terminal-frontmost / idle auto-gating is still open.
3. **Wait-aware micro-recovery** — pick the activity by how long the wait is;
   add a genuinely zero-context "Reset" mode (breathe / look away / stretch).
   **(Spec below.)**

### Tier 2 — emerging trend, bigger bet, slightly off current model
4. **Multi-session status strip** — show which of several Claude sessions
   finished / needs input. High upside, stretches the single-window model.
5. **Elapsed-time / current-tool readout** — no ETA exists, but "running 4m ·
   editing server.py" reduces "is it stuck or thinking?" anxiety.

### Tier 3 — speculative / lower evidence
6. **Queue-next-prompt** box (type now, send when free).
7. More games / leaderboards — engagement, weakest evidence, risks violating the
   "instantly droppable / no new context" rule.

---

## 📐 Detailed specs

See the three Tier-1 specs below (state-aware attention routing, focus mode,
wait-aware micro-recovery). These are the recommended next build.

---

### Spec 1 — 🎛️ State-aware attention routing

**Problem it solves:** #1 signal. Today Interlude has one "voice": the window
opens, then closes. Developers who look away miss *why* Claude stopped — is it
done, blocked on permission, waiting for input, or errored? They want the state
to be legible from across the room, with a *different* cue per state.

**The idea:** Interlude already sees every state via its four hooks. Map each to
a distinct **sound + one-line banner + accent color + close behavior**. One knob:
`interlude sound on|off` (default off, opt-in — respects alert fatigue).

**The four states (all already wired to hooks):**

| State | Hook | Sound (afplay system chime) | Banner | Accent | Window does |
|-------|------|------------------------------|--------|--------|-------------|
| ✅ Done | `Stop` | soft two-note "ding-dong" | "Claude's done — closing in 3…2…1" | violet | countdown → close (today's behavior) |
| ✋ Needs input | `Notification` idle_prompt | single mid "boop" | "Claude's waiting for you" | amber | close immediately (today's behavior) |
| 🔐 Permission | `Notification` permission_prompt | urgent double "tap-tap" | "Claude needs permission" | red | close immediately, but flash first |
| ⚠️ Error/stuck | `Stop` w/ nonzero / no tool for N min | low "buzz" | "Something went wrong" | red | stay open, show last line |

**Example — walkthrough of a real session:**

```
you: "refactor the auth module and run the tests"
  → 3s pass, Claude still busy → window opens on the Learn card (as today)
  → Claude hits a Bash permission prompt
      🔊 tap-tap   banner turns RED: "Claude needs permission"
      window flashes once, then closes so you can approve
  → you approve; Claude keeps working → window re-opens, you play 2048
  → tests fail, Claude stops
      🔊 low buzz   banner RED: "Tests failed — see terminal"
      window STAYS OPEN with the last 2 lines of output pinned at top
```

vs. the happy path:

```
  → Claude finishes cleanly
      🔊 ding-dong   banner VIOLET: "Claude's done — closing in 3…2…1"
      window counts down and closes (today's behavior, now with a sound)
```

**How it looks:** a thin banner strip across the top of the existing popup
(above Learn/Play/Progress), colored by state, with the icon + line. It rides the
existing 800ms `/api/status` poll — `status.attention = {state, line, since}` —
so no new request. Sounds via `afplay /System/Library/Sounds/*.aiff` (zero-dep,
already have `subprocess`). The error/stuck case needs a new signal in
`interlude.py`: on `Stop`, peek at whether the last tool errored, and a
"no PostToolUse for N seconds while busy" watchdog for hangs.

**Why now:** highest-demand theme, and you're the *only* tool that already owns
the window — everyone else can only pop an OS notification. Turning your window
into the status display is your moat.

---

### Spec 2 — 🕶️ Focus mode ("don't interrupt when I'm watching")

**Problem it solves:** the loud counter-signal in the research — notifications
are *unwanted* when you're actively pairing with the agent and watching the
terminal. A window that pops over your work then is worse than nothing.

**The idea:** suppress or soften the popup when you're clearly still engaged, and
only surface it once you've actually looked away. Three ways to know you looked
away, cheapest first:

1. **Manual toggle** — `interlude focus on` (or a ⌘-key while the window is up):
   "shh, I'm watching." Interlude arms but doesn't open until the *next* prompt.
2. **Terminal-frontmost check** — before opening, ask via `osascript` whether the
   frontmost app is the terminal Claude runs in. If yes, **delay** the open (don't
   suppress forever) until focus leaves it. Zero new deps.
3. **Idle-based auto-open** — combine with macOS idle time (`ioreg`/`HIDIdleTime`):
   only open if no keyboard/mouse for ~`INTERLUDE_DELAY` seconds → a strong
   "walked away" signal.

**Example — the two paths:**

```
CASE A — you're heads-down reading Claude's diff in the terminal
  you: "explain this change"  → Claude busy 8s
  Interlude checks: frontmost app == iTerm, you typed 2s ago
  → does NOT pop. Waits.
  → you ⌘-tab to Slack to ask a coworker
  → now frontmost != terminal AND you're idle in it
  → NOW Interlude opens on a flashcard. No interruption while you were reading.

CASE B — you fire a long task and lean back
  you: "migrate the whole test suite to vitest"  → Claude busy
  you haven't touched keyboard for 3s, terminal not frontmost
  → Interlude opens right away (today's behavior)
```

**How it looks:** invisible when it works — the win is the popup *not* stealing
focus at the wrong moment. Add `interlude status` fields: `focus: on|off`,
`lastGate: "terminal-frontmost"` so it's debuggable. New env:
`INTERLUDE_FOCUS_GATE=off|frontmost|idle|both` (default `frontmost`).

**Caveat from research:** the *prevalence* of this exact preference was flagged as
thin during fact-checking — so ship it **on a gentle default**, not aggressive
suppression, and make the manual toggle the headline.

---

### Spec 3 — ⏱️ Wait-aware micro-recovery

**Problem it solves:** theme #3, the sharpest finding. The ideal wait activity
(a) loads **no new context** into working memory, (b) gives **genuine recovery**,
(c) is **droppable instantly**. A vocab flashcard quietly *violates* (a) — it asks
your brain to encode something new. Great for a 5-min wait; wrong for a 20-second
one, where it just adds a second context switch on top of the agent's.

**The idea:** pick the activity by **how long the wait has already lasted**, and
add a true zero-context "Reset" mode for short waits.

| Wait so far | Default surface | Rationale |
|-------------|-----------------|-----------|
| 0–20s | **Reset** (breathe / look 20ft away / roll shoulders) | no encoding, pure recovery, drop instantly |
| 20s–2m | **1 flashcard** or a single game round | light encoding, still droppable |
| 2m+ | **full Learn/Play** (today's experience) | genuine downtime, worth investing |

**"Reset" mode — what it looks like:**

```
┌────────────────────────────────────────────┐
│                                              │
│              ●                               │   a single dot that
│           breathe in…                        │   expands (4s) / holds (4s)
│                                              │   / contracts (4s) — box
│         ⟳ Claude is working                  │   breathing. Or:
│                                              │
│   "Look at something 20 feet away for 20s"   │   ← 20-20-20 eye rule
│                                              │
└────────────────────────────────────────────┘
```

No score, no streak, nothing to lose by closing — it just dissolves when Claude's
done. That's the whole point: recovery, not a task.

**Example — same wait, different surfaces:**

```
you: "fix this typo"                → Claude busy 6s
   → Reset dot appears, one breath cycle, Claude done, it fades. You never
     had to think about a vocab word for a 6-second job.

you: "add pagination to the users API"   → Claude busy 90s
   → 1 flashcard: "ephemeral — lasting a very short time." You answer, done.

you: "rewrite the whole billing service"  → Claude busy 6m
   → full Play view; you get a real 2048 run in, resume-safe as today.
```

**How it looks / builds:** the app already knows `busy` and can track `busySince`
from the status file. Add `status.waitedMs`; the front-end picks the surface from
the table above. "Reset" is a tiny self-contained view (pure CSS animation, no new
assets, no server state) — arguably the most on-thesis feature in the whole app
and the cheapest to build. Env to tune the thresholds:
`INTERLUDE_RESET_MAX=20`, `INTERLUDE_LIGHT_MAX=120`.

