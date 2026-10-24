# Karpathy Loop — perpetual codebase improvement

**Author:** Boss · **Date:** 2026-09-22 · **Status:** PLAN, for Gene's sign-off

You asked for a thing that turns on, pulls a *different angle* each round,
rotates projects, checkpoints everything, and turns off cleanly. Here is what
already exists, what's missing, and what I'd build.

---

## 1. What already exists (verified, not assumed)

| Piece | State | Evidence |
|---|---|---|
| Rotation over projects | ✅ built | `improver.py` — `cmd_run`, `state/rotation.json` |
| Six-stage process per project | ✅ built | `method.py` — S1 scout → S6 verify |
| Goal-mode loop per card | ✅ exists in Hermes | kanban `--goal --goal-max-turns N` |
| Deterministic gate | ✅ built | 7-file pytest gate in `improve.yaml` |
| Discord progress + nag-to-answer | ✅ live | `#project-reviewer`, ping verified |
| Desktop plugin menu (Artifacts/RSS style) | ✅ exists | `desktop-plugins/hermes-rss/plugin.js` |
| Plugin → Python bridge | ✅ exists | `host.requestProfile(route,"shell.exec",{command})` |
| Checkpoint/rollback | ✅ native | git worktree `wt/<task_id>` per card, 257 commits |
| **Preset prompt library (30-40 angles)** | ❌ **missing** | nothing references presets |
| **Random prompt pull per round** | ❌ **missing** | `next_stage()` walks a fixed 6-stage order |
| **Turn on/off switch** | ⚠️ partial | cron + card exist; no single "stop" control |
| **Desktop UI page** | ❌ **missing** | no plugin for this yet |

**44 of 225 projects are git repos** — those are the ones that can take a
checkpoint. That is the rotation pool.

---

## 2. The core idea — *angles* are the new unit of work

Today the rotator walks fixed stages (scout → baseline → backlog → …). You
want something freer: a library of **independent angles**, each of which can
be applied to any codebase, pulled at random so the loop doesn't tunnel.

So the unit of one round becomes:

```
(pick project) × (pull a random angle) → one bounded improvement → gate → commit → next
```

**One round = one angle on one project.** Not one stage. The six-stage method
becomes a *setup* pass (run once per project to get a baseline + backlog), and
the angle library becomes the *steady state*.

### Why random, and why "twice"

You said "randomly pulling a prompt twice for the next project." I read that
as: pull 2 angles per project visit, then rotate on. That gives the loop a
rhythm — enough to get past the shallow first idea, not so much it overfits
one repo. I'd make this configurable (`angles_per_visit: 2`) rather than
hardcode it.

---

## 3. The angle library — 40 angles in 8 families

Each angle is a **lens**, not a task list. It must work on any repo. Every one
is written to be *falsifiable* — it tells the agent what evidence to produce,
so the gate can judge it.

### Family A — Correctness (technical)
1. **Find the lying test** — tests that pass but assert nothing real (no assert, assert True, mock-of-mock).
2. **Find the silently-swallowed exception** — bare `except:` / `except Exception: pass` hiding real failures.
3. **Find the off-by-one and the boundary** — loops, slices, ranges, pagination edges.
4. **Find the race** — shared mutable state, no lock, TOCTOU, unsynchronised read-modify-write.
5. **Find the resource leak** — unclosed file/socket/connection/handle on the error path.
6. **Find the None that shouldn't be there** — unchecked optional chaining, dict `.get()` without default.
7. **Find the unreachable branch** — dead `if`, contradictory conditions, code after `return`.
8. **Find the state that never round-trips** — save → load → save produces different data.

### Family B — Robustness
9. **Break it with garbage input** — empty string, huge number, unicode, negative, null.
10. **Kill the network mid-call** — what happens on timeout / partial response / offline.
11. **Fill the disk / exhaust the budget** — unbounded growth, no cap, no eviction.
12. **Restart it at the worst moment** — crash between write and commit.
13. **Feed it the file it just wrote** — idempotency, self-consistency.

### Family C — Simplification
14. **Delete something** — find code that nothing calls; prove it with a grep + test.
15. **Collapse duplication** — the same logic in 3 places, diverged.
16. **Inline the premature abstraction** — a one-caller helper that adds indirection only.
17. **Flatten the deep nesting** — guard-clause the pyramid.
18. **Remove the configuration nobody sets** — flags with one value, dead defaults.

### Family D — Performance
19. **Find the accidental O(n²)** — nested scan, `in` on a list inside a loop.
20. **Find the repeated work** — recomputed invariants, N+1 queries, re-parsed files.
21. **Find the unbounded memory** — lists that grow forever, caches with no TTL.
22. **Find the slow startup** — imports that block, work that could be lazy.

### Family E — Gameplay (Tombs & Tyrants-shaped projects)
23. **Play a session and log every refusal** — count the turns the engine rejected, with the exact text.
24. **Find the dead end** — a player action that produces no forward state change.
25. **Find the choice that doesn't matter** — options with identical outcomes.
26. **Find the punishment that isn't telegraphed** — damage/failure with no warning.
27. **Find the reward loop that's too easy or too grindy** — measure actual pacing.
28. **Find the inconsistency** — the world contradicts itself between scenes.
29. **Find the NPC that doesn't react** — an actor present but inert to the obvious input.

### Family F — Interface / UX
30. **Find the error the user can't act on** — "something went wrong", no cause, no next step.
31. **Find the action needing too many steps** — the 5-click path to a 1-click intent.
32. **Find the state the UI doesn't show** — work in flight, failure, staleness.
33. **Find the unreadable text** — contrast, size, truncation, jargon.
34. **Find the destructive action without a confirm or undo.**

### Family G — Comprehension (the thing that compounds)
35. **Find the function nobody can safely modify** — too long, too many branches, no tests.
36. **Find the comment that lies** — docstring disagrees with behaviour.
37. **Find the naming that misleads** — a `get_` that mutates, a `is_` that isn't boolean.
38. **Write the missing architecture note** — the diagram that would have saved you an hour.

### Family H — Meta / adversarial
39. **Argue the codebase should be deleted** — steelman the case for removal; if you can't, say why not.
40. **Apply the previous round's fix to the opposite case** — generalise what you just learned.

**Each angle is stored with:** `id`, `family`, `lens`, `what to look for`,
`evidence required` (what the agent must produce to claim it did the work),
and `when NOT to use it` (dead-code angle on a repo with no dead code wastes
a round).

---

## 4. Checkpoints and rollback — how you back out a tangent

Already native, and I'd surface it properly:

- Every round commits to the task's branch `wt/<task_id>` — **never to main**.
- Each accepted round gets a **tag**: `kp/<project>/<round>/<angle-id>`.
- The panel (and the new desktop page) lists rounds newest-first with:
  angle used · what changed · gate before/after · commit · **Revert** button.
- Revert = `git revert <sha>` on that branch (revert, not reset — keeps
  history so you can see what was tried).
- **Nothing merges to main without you.** That's the whole safety story: the
  loop can only ever produce branches and tags, so "turn it off and roll back"
  is always available.

**One honest limit:** tags live in `D:\ZCODE\TOMBS AND TYRANTS`, which has a
GitHub backup. I'd push tags to that remote per accepted round so rollback
survives a disk failure. Say yes and I'll wire it; it does mean the loop
writes to GitHub.

---

## 5. The switch — turn it on, turn it off

You want a real on/off, not three cron jobs to remember.

`state/loop.json` is the single source of truth:

```json
{
  "running": true,
  "paused_reason": null,
  "projects": ["tombs-and-tyrants", "secondbrain"],
  "angles_per_visit": 2,
  "max_rounds": 0,
  "max_hours": 0,
  "paused_at": null
}
```

- **Start** → `running: true`. The rotator's next tick creates a card.
- **Pause** → `running: false`. Finishes the round in flight, creates no new
  card. Nothing is killed mid-edit.
- **Stop** → pause + `reclaim` the running card. Its worktree is left intact
  so the round's work isn't lost.
- Guards: `max_rounds` / `max_hours` auto-pause (0 = forever). A "spend cap"
  so an unattended loop can't run for a week.

---

## 6. The desktop page — "Karpathy Loop" in the sidebar

Exactly the Artifacts / RSS pattern, verified from the installed RSS plugin:

```js
ctx.register({ id:"navigation", area:SIDEBAR_NAV_AREA,
               data:{ path:"/karpathy", label:"Karpathy Loop", codicon:"sync" }});
```

Data comes over `host.requestProfile(route, "shell.exec", {command})` — the
same bridge RSS uses to run curl. My page shells to
`python control_panel.py --json` and renders.

**The page shows:**
- **Big switch** — Running / Paused, with the pause reason.
- **Current round** — project · angle pulled · what it's doing right now.
- **Round history** — table: round, project, angle, gate Δ, commit, Revert.
- **Angle library** — all 40, grouped by family, showing per-angle usage count
  and success rate (so you can see which angles actually produce commits, and
  retire the duds).
- **Project picker** — the 44 git repos, with checkboxes to include in rotation.
- **Live log tail** — the worker's current tool calls.

The existing `panel.html` stays as the deep/offline view; the desktop page is
the in-app control.

---

## 7. What I'd build, in order

| # | Thing | Why first |
|---|---|---|
| 1 | `angles.yaml` — the 40 angles, 8 families | Everything else references it |
| 2 | `angle_pick.py` — weighted-random pull, avoids recent repeats, retires duds | The core new behaviour |
| 3 | `improver.py` — round = (project × angle); `angles_per_visit: 2` | Turns the rotator into the loop |
| 4 | `state/loop.json` + `loopctl.py start\|pause\|stop\|status` | The on/off switch |
| 5 | Checkpoint tags `kp/...` per accepted round + revert helper | Rollback safety |
| 6 | `control_panel.py --json` output mode | Feeds the desktop page |
| 7 | `desktop-plugins/karpathy-loop/plugin.js` | The in-app window |
| 8 | Wire the existing `improver` worker card to consume an angle instead of a stage | Closes the loop |

Steps 1–4 are the substance and are testable from the CLI today. Steps 5–7 are
surfacing. Step 8 connects it to the live worker.

---

## 8. The three questions I need answered

1. **Rotation pool** — all 44 git repos, or start with a hand-picked subset
   (say 5-8 you actually care about)? I'd start small; 44 projects × 40 angles
   is 1,760 rounds before you've seen everything once.
2. **Rounds per visit** — you said 1-2. Confirm 2, or 1 to move faster between
   projects?
3. **Push checkpoint tags to GitHub** per accepted round, or keep them local
   only (rollback then depends on this disk)?

## 9. What I am NOT doing

- Not rebuilding the loop engine — it exists (`--goal --goal-max-turns`).
- Not creating a second notification channel — Discord is wired and tested.
- Not letting the loop merge to main. Ever. That's your call, by hand.
- Not running angles against the 181 non-git projects — no checkpoint, no
  rollback, so no autonomous writes.