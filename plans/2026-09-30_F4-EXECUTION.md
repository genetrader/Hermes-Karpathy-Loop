# F4 EXECUTION LOG — cleanup lane, 2026-09-30
Baseline: 173 (post-F3) · Final: **192 passed / 0 failed** · Loop still PAUSED.

## Group A — campaign/scope
- **T3-4** scope.parse counts extra CROSS_CUTTING lines → `cross_dropped` key
  (always present, even on the empty-input path — a real defect my own test
  caught: the early return dropped the key). Runner logs drops loudly.
- **T3-5** commit(accepted=False) records a rejection ONLY when the proposal
  carried a claim (campaign or closure); no-op proposals on failed rounds no
  longer pollute campaign_rejections.
- **campaigns_closed** truncation now prints when records are dropped by the
  [-20:] cap.
- **T2-11** blocked-campaign recovery: a terminal verdict on an outstanding
  surface is evidence-backed progress — it does NOT count as an attempt
  (attempts reset to 0) and a BLOCKED campaign with a terminal verdict this
  round UNBLOCKS (state → open, blocked_* fields cleared, `unblocked` flag in
  the proposal). The round ceiling still blocks regardless of progress.
- **T2-13** surface_pick's stateless fallback now BOOKS its choice
  (best-effort record() — previously the fallback re-returned the same first
  surface every round, starving the rest).
- **T2-14** surface_pick docstring states the honest limit: a RENAME is
  remove+add by slug identity; no alias map.
- **T3-13** `next_campaign` shim DELETED from scope.py; test_scope.py and
  test_scope_callsite.py migrated to propose+commit pairs (12 call sites;
  the migration initially dropped assignment targets — found by the suite and
  repaired); the runner must not call it is now asserted via AST (comments
  naming the historical bug are not calls).

## Group B — loop surfaces
- **T2-6 answer wiring (the decision: wire it)** — the runner consumes
  `state/answer.json` (written by watch_answers.py for real replies and by
  dn.remind for budget-expired assumptions) BEFORE building the prompt,
  renders a `HUMAN ANSWER` (or ASSUMED) block into THIS round's prompt via
  round_prompt.build(human_answer=...), and moves the file to
  `state/answers_consumed/<stamp>-<qid>.json` (audit trail; an answer is
  delivered exactly once, even across a crash; corrupt files are dropped,
  never replayed). The stale Sep-22 qtest entry was consumed during the state
  prune so it can never reach a project thread.
- **T3-7** threads.maintained() stamps `last_activity_at` only when the chain
  tip/chain/title actually changed; a no-op tick leaves the activity clock
  alone (the tip's hidden flag is still forced visible).
- **T3-9** monitor.py "next up" is now the live threads.json LRU (min
  last_nudge among enabled, non-quarantined projects); rotation.json's idx
  fiction is ignored (its history table still renders, labeled as the retired
  engine's record).
- **T3-10** state prune: checkpoints.json + asked.json moved to
  `state/wip-backups/retired-20260930/` with a README; answer.json's stale
  qtest consumed. angles_view.html kept (live UI writes it).
- **T2-7** dead cron wiring: karpathy_nudge.py + karpathy_nudge_cron.py +
  run_rotator.sh + run_watcher.sh moved to `retired/` (no live cron or
  scheduled task references them; the LIVE nudge cron runs
  hermes/scripts/karpathy_watchdog.py, which is untouched and compatible).
  karpathy_cron.cmd_rotator no longer spawns the retired `improver.py run`
  (rc=2 flattened to rc=0 every tick); it now reports loop-armed state and
  probes runner liveness via the new `karpathy_runner.py --check-alive`
  (read-only; exit 0 = runner alive). improver.py's retired-entry pointer no
  longer names the moved file.
- **T3-11** surface_discover.py → retired/ (decision 1).
- AST-mixed test file (test_scope_callsite) relabeled honestly as wiring
  smoke + behavioral split; test_ordering_is_enforced checked and left as-is
  (it is behavioral).

## Tests added
tests/test_f4_cleanup.py (8): cross_dropped counting, claim-gated rejection,
blocked→unblocked, terminal≠attempt, shim gone (AST), fallback booking,
rename honesty.
tests/test_f4_surfaces.py (10): answer consume-exactly-once/junk/assumed,
maintained no-op tick leaves activity, monitor LRU + quarantine skip,
rotator honesty (AST), --check-alive probe, orphans retired, stale state
pruned.
tests/test_round_orchestration.py (+1 = 15): a human answer reaches THIS
round's prompt and is consumed (end-to-end through the real run_round).

## Files touched
scope.py, surface_pick.py, threads.py, monitor.py, karpathy_runner.py
(answer intake + --check-alive), round_prompt.py (human_answer param),
karpathy_cron.py, improver.py (pointer), tests/ (2 new files, 3 updated),
retired/ (5 files + README), state/wip-backups/retired-20260930/ (+README).
