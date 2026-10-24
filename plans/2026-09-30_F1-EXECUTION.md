# F1 EXECUTION LOG — Karpathy loop repair, 2026-09-30
Owner: Boss (resumed the broken "build rotating project improvement agent" thread).
Baseline this pass: **141 passed / 2 failed** (both failures = stale windows in
tests/test_scope_callsite.py — the F1 fix commentary pushed the publish guard past
the old fixed-size windows; measured offsets: guard 10,380; round_accepted 6,219;
checkpoint log line 11,112 chars from the anchor).

## Where the broken thread left off
- F0 complete (unsafe-start refusal, atomic registries, Discord-outage isolation,
  PID-image check — their test files pass).
- F1-1/2/3/4/5/8 implemented with dated comments in karpathy_runner.py.
- F1-6, F1-7 and the F1 fold-ins (T2-9, T2-10, T2-12, T3-14, T3-12) were NOT
  implemented. This pass implemented them.

## Fixes landed this pass
| Item | Change | Evidence |
|---|---|---|
| stale windows | tests/test_scope_callsite.py 8000→14000 / 6000→12000 (measured margins) + bound re-check of the failure branch | suite green |
| F1-6 | runner checkpoint(): reads refs/tags/<k> first; tag at OTHER sha → `failed: ... refusing to move a tag the runner did not create`, no tag/push; tag at same sha → push-only re-publish (no rc=128 noise); return tokens `published:`/`failed ...`; publication_state/detail recorded at the publish site; post-publish save keeps last_checkpoint/publication_* durable (T1-2 pattern guard); round_prompt no longer tells the child to tag (RULES + WRAP-UP) | tests/test_checkpoint_authorship.py 4 cases, real temp git repo + bare origin |
| F1-7/T3-12 | round_prompt checklist: `[x]` and counts use terminal verdicts; verdicts render as clean words (no dict reprs) | tests/test_lane2_foldins.py |
| T2-9 | scope.py parse: reason = parsed reason (old ternary always yielded rest) | drill RED→GREEN |
| T2-10 | scope.py: opening baseline skips the additions cap; later widening still capped (budget = 2 additions TOTAL) | drill RED→GREEN |
| T2-12 | campaign id includes opening round: `repo-x2-r6` — sequential campaigns unique | drill RED→GREEN |
| T3-14a | parse(): terminal verdict is STICKY within one message (late not-applicable cannot downgrade done; non-terminal still upgrades) | drill RED→GREEN (2 tests) |
| T3-14b | marker template: `<verdict>` + "exactly ONE word … never copy the three words" | drill RED→GREEN |
| F1-8 note | `-z /compact` remains only in compact_thread.py (kept for reference, nothing imports it) and karpathy_nudge.py (orphaned duplicate driver, T2-7 — F3/F4 cleanup owns it) | grep |

## Defect drills (plant → test FAILS → restore → test passes)
| Drill | Planted | Result |
|---|---|---|
| T2-9 | ternary back in scope.py | 1 failed → 1 passed |
| T2-10 | unconditional cap check | 1 failed → 1 passed |
| T2-12 | count-only slug | 1 failed → 1 passed |
| T3-14 sticky | guard disabled (`if False and …`) | 2 failed → 2 passed |
| T3-14 template | literal menu line restored | 1 failed → 1 passed |
| T3-12 truthiness | `verdicts.get(s)` truthiness back | 1 failed/1 passed → 2 passed |
| F1-6 case 1 | foreign tag at other sha (real repo) | failed loudly, no push, tag not moved |
| F1-6 case 2 | tag at HEAD | push-only republish, no second tag |
| F1-6 case 3 | clean tag | tag rc=0 + push rc=0 → published |
| F1-6 case 4 | no remote | tag rc=0, push skipped, published |

## Suite state
**159 passed / 0 failed** (was 141/2). New: test_lane2_foldins.py (12),
test_checkpoint_authorship.py (4).

## F2 — behavioral orchestration through the REAL run_round (2026-09-30)
tests/test_round_orchestration.py — 9 tests, all Saul's scenarios:
real temp git repo + bare origin, REAL child process (the runner's actual
subprocess.Popen site, swapped only for hermes_cli.main children — a blanket
Popen patch would have swallowed every git helper via subprocess.run->Popen),
REAL gate commands, REAL registry file on disk in a temp dir (threads.STATE
redirected), REAL evidence/checkpoint/rollback. Injections: threads.resolve/
title_for, improver manifest fns, angle_pick (canned, no prompt_id so the real
prompt_cycles.json is untouched), surface_pick->None, discord no-op.

| # | Scenario | Verdict |
|---|---|---|
| 1 | rejected child commit removed | PASS (gate-fail rejection; tree back at anchor) |
| 2 | rejected untracked residue removed | PASS |
| 3 | round-2 published range excludes round-1 work | PASS (tag == r2 HEAD; diff anchor..tag has r2.py, not r1.py) |
| 4 | rollback failure -> quarantine survives reload | PASS (_head_full_sha broken after pre-round capture; quarantined True on disk) |
| 5 | reloaded selector excludes quarantined repo | PASS |
| 6 | dirty start never launches, files preserved | PASS (Popen spy: zero hermes children; human-work.txt intact) |
| 7 | proposal failure rejects + rolls back | PASS (propose seam fault injection) |
| 8 | publish only after final persistence | PASS (checkpoint spy read round_accepted=True from disk at publish time) |
| 9 | evidence lands on the NEW row | PASS (two rounds; gate/commit on hist[0]=r2; r1 row keeps its own commit) |
| 10 | gate skipped on rc!=0 | PASS (gate-call counter == 0; row gate not ok) |

**Suite after F2: 168 passed / 0 failed.**

## Remaining (per plans/2026-09-30_MASTER-PLAN.md)
- F2: behavioral orchestration tests through the REAL run_round (Saul's 10
  scenarios) — ~4h.
- F3: worktree isolation re-architecture — ~1-2 days.
- F4: cleanup lane (monitor.py rotation fiction, stale state files, T2-7 dead
  wiring incl. karpathy_nudge/compact_thread, question pipeline decision).
- F5: verification gate (full suite + drills + Saul review #3, NO unified diffs,
  reviewer endpoint must be the live GLM-5.3-Flash-FP8 bottle — the old EXL3
  .186:8888 seat was retired 09-30).
- Loop stays PAUSED (loop.json running=false, "paused by user") until F5 passes;
  the karpathy-nudge → watchdog cron is safe while paused.
