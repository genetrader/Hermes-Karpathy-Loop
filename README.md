# PROJECT IMPROVER — README

Continuous, unattended improvement of your projects, rotating on a schedule,
texting you on Discord, and checkpointing to git. Built on Hermes' own goal-mode
kanban loop — there is no custom loop runner to maintain.

---

## Status right now

| Piece | State |
|---|---|
| Discord bridge (`discord_notify.py`) | **LIVE** — posted a real progress line + question ping to Discord |
| Ping/mention to @genetrader | **LIVE** — verified the mention resolves to you |
| Nag loop (every 5 min, 20x) | **LIVE** — fired reminder #1 and is counting |
| Inventory picker (`pick.py`) | **LIVE** — 194 real projects on disk, 44 git repos |
| Rotation manifest (`improve.yaml`) | **WRITTEN** — tombs-and-tyrants enabled, secondbrain parked |
| Rotator (`improver.py`) | **LIVE** — verified against the real kanban CLI |
| Monitor (`monitor.py` → `monitor.html`) | **LIVE** — regenerates each rotation pass |
| Cron: nag (1m, no-agent, 0 tokens) | **LIVE** — job `15312f687d08` |
| Cron: rotator (30m) | **LIVE** — job `572f70d324c4` |
| Dedicated Discord channel | **NEEDS YOU** — bot lacks MANAGE_CHANNELS (403 verified) |

---

## The one thing you must do

Create the dedicated channel. The bot **cannot** — I verified it has
`MANAGE_CHANNELS: NO` on COMMAND CENTRAL and a create attempt returned
`403 Missing Permissions`.

1. In Discord → **COMMAND CENTRAL** → create a text channel named exactly
   **`project-improver`**
2. Make sure **Bastion - ClaudeCodeBot** can post there (it already has
   Send Messages / Embed Links / Read History on the guild, so the default
   new-channel permissions are fine).
3. Tell me, and I run:
   ```
   cd C:\CODING\project-improver
   python setup_channel.py
   hermes gateway restart
   ```

**Why this matters:** right now the bridge is posting to `#general`, where **8 of
your 15 cron jobs** also deliver (AI Builders Digest, Life Daily Review, GLM block
monitor, Bookmarked Podcast, Exposure Sentinel…). Your question ping got buried
under an AI digest within minutes. A dedicated channel fixes that and gives you a
separate notification sound per-channel.

---

## What you'll hear (the sound question)

Three deliberately different message classes:

| Class | Looks like | Pings you? | Sound |
|---|---|---|---|
| Progress | `[tombs-and-tyrants] round 3/12: gate green, commit a3f9c12` | No | Silent |
| Stuck | `**[STUCK]** [secondbrain] OAuth re-auth needs a browser session` | No | Silent |
| **Question** | `@genetrader **[QUESTION]** [selftest] …` | **YES** | **Discord ping** |

The question is the only thing that makes noise. That's your "tone I recognise."

### Setting the tone on your phone

Discord per-channel notification settings are what make this practical:

1. Tap the `#project-improver` channel → **Notification Settings**
2. Set **All Messages** (not just mentions) if you want to hear progress too;
   leave it on **Only @mentions** to hear *only questions* — I'd recommend this.
3. Pick a **Custom Notification Sound** distinct from your other channels —
   that is literally the "tone I'll recognise" you asked for.

### The repeat behaviour

- Reminder **every 5 minutes**, up to **20 times** (`ask_repeat_min` /
  `ask_max_repeat` in `improve.yaml`).
- Each reminder is a **fresh message containing the @mention** — Discord only
  re-notifies on a new ping, so an edit alone would be silent.
- After 20 unanswered reminders (100 minutes) it posts
  `[STUCK] no answer after 20 reminders — parking this question and rotating on`,
  moves the question to `questions/parked.json`, and **continues with the next
  project**. Your silence never deadlocks the fleet.
- Only ONE question is ever open at a time. A second question cannot be asked
  until the first is answered — that's the "one at a time" rule, enforced in code.

---

## Answering

Just **reply in the `#project-improver` channel**. The nag cron job polls the
channel every minute, sees a human message newer than the question, and:
- posts `✅ [project] got it — resuming` with your text quoted
- writes `state/answer.json`
- clears `questions/pending.json`
- (when a worker is actually blocked) calls `hermes kanban unblock <id>` so the
  worker resumes with your decision in context

---

## Daily use

```bash
cd C:\CODING\project-improver

python improver.py status     # what's enabled, rotation position, turn history
python improver.py plan       # what the next pass WOULD do — creates nothing
python improver.py run        # force one rotation pass now
python improver.py init       # create kanban boards for enabled projects
python monitor.py             # regenerate monitor.html
```

Open the monitor: **`C:\CODING\project-improver\monitor.html`**
(it auto-reloads every 30s; it's also the "special area" — I can embed it in a
Hermes Desktop pane plugin next if you want it inside the app rather than a tab)

---

## Controlling the loop

Edit **only** `improve.yaml`:

```yaml
projects:
  - name: tombs-and-tyrants
    path: D:/ZCODE/TOMBS AND TYRANTS
    board: tombs-and-tyrants
    loops: 12                 # iterations this turn
    objective: >              # what "better" means
    acceptance: >             # checkable criteria — the judge sees ONLY this
    gate: "python -m pytest tests/combat -q"   # exit code, not vibes
    boundary: "This repo only."
    implementer: glm53-flash-2x-spark:GLM-5.3-Flash-EXL3
    reviewer:    deepseek-v41-flash-3x-spark:deepseek-v4.1-flash
    enabled: true
```

- `enabled: false` parks a project without deleting it.
- Rotation walks the enabled list in order and wraps around.
- The rotator **refuses to stack cards**: if any project has an open card, it
  creates nothing that pass. No runaway parallelism.

---

## Why there's no loop runner in this repo

Hermes ships the loop: `hermes kanban create --goal --goal-max-turns N
--workspace worktree --skill kanban-worker`. That's a full OS process with its own
session, its own board row, retry on failure, and it survives a gateway restart.
`improver.py` only decides *which* project gets a card and *when*.

Design rules being followed (from the `karpathy-loop` skill):
- A goal-mode kanban card, **not** `delegate_task` (measured 12 done / 9 failed —
  all 9 died at the 7200s child timeout).
- A **deterministic gate** wherever the project has tests — a failed gate
  short-circuits the LLM judge and its output becomes the next prompt.
- The **reviewer is always a different model family** than the implementer.
- The **goal judge** is pinned to `deepseek-v4.1-flash`
  (`auxiliary.goal_judge`), so the judge is never the model under test.
- **No auto-merge to main.** Checkpoint branches only; you gate the merge.

---

## Files

| File | Role |
|---|---|
| `discord_notify.py` | All Discord I/O. `progress` / `stuck` / `ask` / `remind` / `answer` |
| `watch_answers.py` | Detects your reply; drives the reminder loop |
| `improver.py` | The rotator — picks the project, creates the goal-mode card |
| `pick.py` | Builds `pick.html` from ai-project-index (194 projects) |
| `monitor.py` → `monitor.html` | The monitor window |
| `setup_channel.py` | Run after you create the channel |
| `improve.yaml` | **The only file you edit** |
| `state/`, `questions/`, `logs/` | Runtime state |

---

## Fixes to your existing setup I'd suggest separately

1. **Cron noise in `#general`** — 8 jobs deliver there. Worth giving the noisy
   ones (AI Builders Digest, GLM block monitor) their own channels so your
   question pings never get buried again.
2. **Gateway restart pending** — the CLI warns a previous `hermes update` pulled
   code without restarting the gateway (`mixed sys.modules`). Worth a
   `hermes gateway restart` on its own merits.

Neither blocks the improver; both are hygiene.