# PHASE C — repo cleanup + first real round, 2026-10-01 (11:00-11:15)

## Cleanup: all four repos cleaned, committed, verified
| Repo | Work committed | Gate before commit | Commit | Push | Post-clean status |
|---|---|---|---|---|---|
| tombs-and-tyrants | 86 modified files (+3.6k/-1.2k) + 35 new test files + .github + 3 staged deletions; untracked live-server runtime (_canon_run/ -> .gitignore, git rm --cached) | repo gate: 185 passed (12s) | 8c85201 | pushed to origin fix/code-review-2026-09-28 | dirty=0 |
| secondbrain | attention DTO fixes (ApiModels.kt Int-id + SerialName renames + tripped list) + AttentionContractRegressionTest.kt + .gitignore .worktrees/ | server gate: 921 passed (111s, ran from server/ with nested venv) | 531314e | (no remote configured — local-only) | dirty=0 |
| zip-code-ranker | 49 files (admin docs/bats, audit scripts, _*.js probes, watch-ralph.ps1, README) | npm test: 2 passed, rc=0 | d4b7c93 | pushed to origin main | dirty=0 (GB-scale data/runtime trees gitignored: data/ 310GB, audit/staged-sources 1.1GB, server/, website/, output/, tmp/, screenshots/) |
| ai-consultancy-website | site build: 3 files +1171 lines + 18 new page/content/component trees; FIXED the 8 lint errors the gate fails on (5x unescaped entities, 2x setState-in-effect via render-time-adjustment + event-time-reset patterns) | npm run lint: 0 errors (6 warnings) + npm run build: green (full site prerenders, 47s) | 19a6448 | (no remote — local-only) | dirty=0 |

Rules honored: NOTHING deleted or reverted — every byte of agent work was
committed; runtime/data artifacts were gitignored, never removed. T&T's
live game server (uvicorn :8093) was left running; its state file is now
ignored so it cannot re-dirty the tree.

## First REAL round — LIVE (armed loop picked it up automatically)
Within ~1 minute of the last repo going clean:
- 11:11:30 tombs-and-tyrants round r36: worktree created
  (state/worktrees/tombs-and-tyrants/r36), .venv junction provisioned,
  angle too-many-steps.2 consumed (cycle 1, 16/168 used).
- Child session live: hermes_cli.main -p default --model
  custom:glm53-flash-4x-spark-tp4:GLM-5.3-Flash-FP8 (the implementer seat).
- Registry: pre_round_head=8c85201, pre_round_dirty=False, canonical HEAD
  == anchor while the child works (isolation contract verified live).
- t+2min: child has 2 files modified INSIDE the worktree; canonical clean.
Round duration expectation: 50-60 min; acceptance/gate/merge/tag evidence
will land in logs/runner.log (published kp/tombs-and-tyrants/r36 tag or a
containment entry). The loop then continues autonomously to secondbrain
(next-oldest), etc.

## Remaining watch items
- The other three repos get their first real rounds in turn as LRU advances.
- secondbrain will be the multi-surface exercise (server/android/
  chrome-extension surfaces, rotation hold, SCOPE gate).
- If a round rejects, containment = worktree deletion (canonical untouched).
## ADDENDUM 2026-10-01 ~13:10 — round-timer + per-repo averages (Gene's ask)
- Runner now stamps `last_round_seconds` + `round_seconds_hist` (cap 30) at
  every round end (clock = angle-pick start; refusals excluded; timeouts
  counted). Goes live after the boundary restart (state/boundary_restart.py).
- monitor.html queue table: new **Round** column (in-flight = `rN · running
  Xm` ticking with the 30s auto-reload; else `rN · last Xm`) + **Avg/round**
  column (`25m (n13)`). KPI tile shows the current round + repo + elapsed.
- loopctl status: per-repo table (rounds / avg (n) / last / IN FLIGHT).
- Heartbeat pid liveness guard: a dead runner's heartbeat can't tick a
  phantom timer (ctypes OpenProcess check, loopctl + monitor).
- Backfilled real durations parsed from runner.log (09-27 → 10-01, 13/13/
  12/13 completed rounds per repo): T&T ~25.6m avg, zip ~20.8m, sb ~14.3m,
  ai-c ~8.1m. NOTE: the backfill write raced the live runner's registry
  saves (its in-memory copy clobbered it once) — the boundary restart
  script re-seeds AFTER the runner exits.
- 8 new tests (tests/test_round_timing.py) + drill (hist-append plant →
  3 failed → restore → green). Full suite: **243 passed / 0 failed**.

## ADDENDUM 2026-10-01 ~13:10 — Phase C's first PUBLISHED round + live defects
- **zip-code-ranker r38 ACCEPTED + PUBLISHED**: HEAD 4b61d34 (lying-comment
  doc fix), tag kp/zip-code-ranker/r38 pushed to origin + main pushed.
  The complete accept→tag→push cycle is proven live. (First attempt hit the
  authorship guard: an OLD-era tag occupied r38 at a468c69; deleted +
  re-tagged at the correct sha.)
- **secondbrain r39 rejected** (gate failed): contained, worktree+branch
  deleted, canonical untouched. Rotation continued (965s = 16m).
- **T&T r37 rejected** (gate failed): contained cleanly. 1661s = 27m.
- **ai-consultancy r14 QUARANTINED then manually contained**: the child's
  `npm install` REPLACED the node_modules junction with a real 423MB
  install; junction rmdir failed → containment aborted → quarantine.
  Manually cleaned (real-dir rmtree + worktree removal), quarantine
  cleared, repo back in rotation.
- **FIXED F2**: junction creation now `dst.parent.mkdir(parents=True)` —
  gitignored parents (server/, .worktrees/*) don't exist in fresh
  worktrees; mklink refused without them (was noisy + dep dirs missing).
- **FIXED F3**: `_cleanup_junctions` now detects a REAL dir where the
  junction was (npm/uv replaces junctions) and rmtree's it as worktree-
  local data — containment no longer aborts; `_is_reparse_point` added
  (ctypes GetFileAttributesW). Verified by scratch drill (canonical
  canary intact in both cases).
- **STALE-TAG HAZARD FIXED via namespace rename** — see
  plans/2026-10-01_TAG-INCIDENT-AND-RECOVERY.md for the incident where my
  first rename pass DELETED tags (102 restored from remotes as
  kp/<repo>/old/rN; 8 old-era local-only lightweight tags unrecoverable,
  documented). New-namespace rNN tags are now collision-free.
## ADDENDUM 2026-10-01 ~16:00 — thread-model display + live timer (Gene's report)

Gene opened the T&T loop thread and saw DeepSeek; the elapsed timer stayed
a dash. Findings + fixes:

1. **The WORK was already on the configured seat.** Every round child is
   spawned `hermes -p default --model <implementer seat> ... --resume <sid>`
   — proven live: the in-flight r38 child's command line carries
   `--model custom:glm53-flash-4x-spark-tp4:GLM-5.3-Flash-FP8`. The setting
   already exists and is NOT hard-coded:
   `python loopctl.py config --implementer <seat> [--reviewer <seat>]`
   (loop.json; validated alive at runner startup; same-model guard).
2. **The DISPLAY lied.** sessions.model in Hermes' state.db keeps the
   CREATION-era value; 3 of the 4 loop threads were created in the
   DeepSeek era (09-27/28) → the sidebar showed deepseek-v4.1-flash
   (a dead provider) while the work ran GLM. Fixed:
   a) one-time stamp of all 4 loop threads' sessions.model ->
      GLM-5.3-Flash-FP8;
   b) threads.maintained() (watchdog tick, */15) now re-stamps every
      loop thread TIP's model from loop.json's implementer seat —
      config-driven, covers compaction children, never hard-coded
      (_seat_model_name).
   c) DORMANT BUG FOUND + FIXED by the stamp work: the T3-7 changed-branch
      SQL call was split by a comma into TWO string literals
      (execute(sql, stray_string, params) -> TypeError) — every
      changed=True heal pass silently aborted at the first project.
      Drill: plant -> 1 failed, restore -> pass.
3. **Timer fixed.** The page is a static file: the 30s meta-refresh
      re-fetched stale bytes, so a server-rendered timer could never
      move. Now: (a) the page embeds the in-flight round's start epoch
      and a JS ticker updates the elapsed value EVERY SECOND client-side;
      (b) new no-agent cron karpathy-monitor-refresh (146081438ad9,
      every 2m, silent) regenerates monitor.html so queue states,
      averages and last-round durations stay fresh.
   The dash itself was correct behavior at that moment: the timer counts
   from the round's work-order issue (angle pick) — before that there is
   no round to time.
4. **ai-c quarantine cleared (again)**: r15's npm replaced the junction
   with a real 423MB node_modules (old runner code aborted containment).
   Cleaned manually (real-dir rmtree + branch prune + force-rmtree),
   quarantine flag cleared. NOTE: registry write raced the in-flight
   round's save — re-verify ai-c is unquarantined after T&T r38 ends.

Tests: +4 (tests/test_thread_model_display.py) -> **247 passed / 0**.