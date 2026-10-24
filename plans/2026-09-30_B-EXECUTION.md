# B EXECUTION LOG -- surfaces scheduler (B1-B5), 2026-09-30
Plan: plans/2026-09-30_B-PLAN.md. Reviewer: Saul (gpt-5.6-sol, hermes -p saul,
chunked falsification prompts, no unified diffs).

## Pre-B leftovers closed (F5 addendum)
- monitor.html regenerated from real state (a test run had clobbered it with
  fake data at 15:35; monitor.OUT is now test-redirected so it cannot recur).
- F0/F1 drill re-runs completed (T2-9, T2-10, T2-12, T3-14 sticky, T3-14
  template, T3-12; F1-6 = permanent tests). Appended to F5-GATE.md.
  Honesty note: the first T3-14 template plant used comma separators and
  stayed green -- the drill must plant the ORIGINAL pipe-menu defect shape.

## B1 -- state machine + bounded attempts (already built; verified)
- scope.py constants: MAX_ADDITIONS=2, MAX_ATTEMPTS_PER_SURFACE=3,
  MAX_ROUNDS_PER_CAMPAIGN=12 (+ new MAX_CAMPAIGN_HOLDS, B2).
- Covered by existing tests (test_scope.py, test_scope_callsite.py,
  test_lane2_foldins.py, F4 Group-A tests) + F5 drills D1-D4.

## B2 -- campaign rotation hold (burst+LRU)  [karpathy_runner.py]
- pick_next_project(projs, reg) extracted; key = (0 if held else 1, last_nudge).
- _hold_state: held = open campaign AND holds < MAX_CAMPAIGN_HOLDS (15).
  BLOCKED campaigns do not hold. Sparse-entry safe.
- Main loop: hold bump on selection + loud cap-release log; post-commit reset
  to 0 when the campaign closes or blocks.
- Tests: 5 (decisive hold order, blocked-no-hold, cap release, sparse entry,
  wired behavioral). AST twin in test_containment_is_wired.py re-pointed to
  the pick_next_project seam (the old inline-sort assertion was the pre-B2
  shape).

## B3 -- opening gate  [scope.py + round_prompt.py + runner lifecycle]
- parse: SCOPE classification (LOCAL/CROSS_CUTTING/UNCERTAIN, first wins,
  case-insensitive), uncertain_note, scope_dropped. Legacy shape (no SCOPE
  line) = scope_class None, opens as before.
- propose gate (runs BEFORE campaign construction): LOCAL + cross line =
  contradiction (no open/widen, verdicts still fold, res["contradicted"]);
  UNCERTAIN = no open/widen (res["uncertain"]); absent/CROSS_CUTTING opens.
- Runner: entry["scope_question"]={round,note} on UNCERTAIN; cleared when a
  later round classifies LOCAL/CROSS_CUTTING; refreshed on repeated
  UNCERTAIN. Prompt renders the OPEN SCOPE QUESTION block + asks line 0
  (three separate lines -- T3-14 menu-copy lesson) when a surface is in play;
  legacy callers byte-unchanged.
- loopctl: READ-ONLY --show-campaign (design 6.2 item, was missing).

## B4 -- closing gate  [scope.py + runner]
- default_cite_check(root): bounded file index (os.walk, skip deps/build dirs,
  20k cap) + predicate over path tokens (slash paths + bare filenames,
  basename index). Fail-closed on empty index and on predicate exceptions.
- propose(cite_ok=...): an uncited `done` downgrades to done-uncited
  (non-terminal), attempt increments, reason carries the gate note,
  res["uncited"] lists the slug. Uncited claims can never be terminal, so the
  verdict-map-full close check cannot fire on them. Re-emission with a
  citation takes the normal upgrade path (attempts reset). not-applicable
  never gated. cite_ok=None = legacy.
- Runner binds cite_ok to the accepted round's worktree (claims describe that
  tree), with a build-failure fallback that disables the gate for the round
  and logs loudly.

## Tests
- New: tests/test_phase_b_scheduler.py -- 26 tests.
- Suite: **219 passed / 0 failed** (was 193; 168 pre-F3 baseline).
- Real bugs the new tests caught during wiring:
  1. cite predicate called with (slug, reason) vs its 1-arg contract ->
     every cited done was silently downgraded (TypeError swallowed by the
     fail-closed except). Fixed to single-arg; test + spy verified the fold.
  2. First hold-test draft was NON-DECISIVE: the held repo had the older
     nudge, so plain LRU already sorted it first -- D5's plant stayed GREEN.
     Made decisive (held repo has the NEWER nudge) before counting the drill.

## Defect drills (plant -> RED -> restore byte-verified -> GREEN)
| Drill | Planted | Result |
|---|---|---|
| D5 | selection key back to last_nudge-only | 1 failed -> 1 passed |
| D6a | LOCAL-contradiction gate disabled | 2 failed -> 2 passed |
| D6b | UNCERTAIN gate disabled | 1 failed -> 1 passed |
| D7 | cite downgrade disabled | 2 failed -> 2 passed |

Honesty note on D5: the FIRST plant stayed GREEN because the original hold
test used a non-decisive nudge ordering (the held repo had the OLDER nudge,
which plain LRU also sorts first). The test was made decisive (held repo has
the NEWER nudge; only the hold explains first place) BEFORE the drill was
counted. A decoration test caught by its own drill -- recorded, not hidden.

## Review #4 (Saul gpt-5.6-sol, chunked, no diffs)
Full triage: plans/2026-09-30_B-REVIEW.md (raw: _B-REVIEW-RAW.json).
VERDICTS: 4x FINDINGS. 8 findings reproduced and FIXED:
- a-2/b-3 fairness: refusal paths now stamp last_nudge (the 2026-09-28
  hot-loop item, fixed at last) -- a refusal consumes the scheduling slot.
- b-2 liveness: UNCERTAIN refreshes carry a seen counter; >=3 escalates loud.
- b-4 integrity: only an ACCEPTED round settles the scope question.
- c-1 false citations: slash tokens require EXACT relative-path resolution.
- c-4 cost: the repo index builds lazily on first predicate call.
- c-5 auditability: gate evidence in dedicated cite/gate_note fields;
  reason_full (bounded 600) preserves citations past the 200-char reason;
  the stored-verdict rebuild carries the fields through.
1 accepted-as-designed: c-2 laundering (claim-NARROWING gate, not evidence
verification -- documented in the docstring). 1 insisted-on test delivered:
--show-campaign proven byte-identical on a copied real registry.
Two prompts were lost when the background review runner died mid-flight; the
script was made resume-safe (loads prior results, skips done keys) and re-run.

## Final suite
**228 passed / 0 failed** (193 pre-B -> +35). Loop still PAUSED
(state/loop.json running=false, seats GLM bottle / qwen d754). The
nudge->watchdog cron stayed safe throughout (watchdog only respawns an ARMED
loop). monitor.html regenerated from real state after the test clobber.

## Phase C handoff (Gene's call)
The master plan's remaining phase is C: ARming the loop live (start the
runner, watch 2-3 real rounds, verify worktree isolation + acceptance +
campaign hold on REAL repos, then let it run). Everything up to and including
B is verified; the loop is one `loopctl start` away. Gene decides when.
