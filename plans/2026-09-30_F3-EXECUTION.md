# F3 EXECUTION LOG — worktree isolation, 2026-09-30
Baseline: 168 passed · Final: **173 passed / 0 failed** · Loop still PAUSED.

## What was built
1. **F3.1 Worktree per round** — `_make_worktree()` creates
   `git worktree add -b kl/<repo>/rNN <ROOT>/state/worktrees/<repo>/rNN <anchor>`;
   verifies worktree HEAD == anchor; writes an `rNN.started.json` sidecar
   (the recovery sweep's age source).
2. **F3.2 Child in the worktree** — Popen `cwd=worktree`, env from
   `_child_env()`: PYTHONPATH=<hermes-agent>, GIT_TERMINAL_PROMPT=0,
   GCM_INTERACTIVE=never, empty credential helper + core.askpass empty +
   **remote.origin.pushurl=DISABLED** via GIT_CONFIG_* entries (env-scoped, so
   the COMMON repo config — and the runner's own push — is untouched).
   Egress boundary documented in run_round's docstring. round_prompt renders a
   WORKING COPY block naming the worktree, the branch, and the off-limits
   canonical path.
3. **F3.3 Reject = deletion** — `_remove_worktree()`: junctions unlinked first
   (recorded in an `rNN.deps.json` sidecar), `git worktree remove --force`,
   `git branch -D kl/<repo>/rNN`. Removal failure → quarantine. No
   reset --hard on canonical in the round path; `_rollback_to` was DELETED
   (dead helper — keeping it would be drift).
4. **F3.4 Accept = parent integrates** — ff-merge `kl/<repo>/rNN` into
   canonical, re-run the gate IN CANONICAL (`post_merge_gate` in the entry);
   disagreement → `_reset_canonical()` restore + publication failed + worktree
   PRESERVED (branch keeps the work); agreement → `checkpoint()` in canonical
   (tag + push), then worktree + branch deleted regardless of push outcome.
   Gate + evidence pre-merge measure a proj copy with path=worktree.
5. **Dep junctions** — gates reference repo-local deps (tombs/secondbrain
   `.venv`, zip/consultancy `node_modules`), which a fresh worktree lacks;
   `_provision_worktree_deps()` junctions untracked dep dirs into the worktree
   so every gate command runs verbatim. Untracked-but-not-ignored dirs are
   skipped (a junction that shows in git status would let the child stage the
   venv). `_rmdir_link()` removes junctions WITHOUT following them into the
   canonical checkout — recursive deletion can never reach canonical through
   one. Crash-orphaned worktrees are quarantined, never auto-deleted.
6. **F3.5 Recovery** — `_recover_abandoned_worktrees()` at runner startup:
   sidecar older than KL_WT_ABANDON_SECS (default 6h) → repo QUARANTINED
   (reason: abandoned worktree + age), branch deleted only after the note;
   the abandoned tree itself is preserved.
7. **F3.7 Seats** — implementer/reviewer now live in loop.json
   (`custom:glm53-flash-4x-spark-tp4:GLM-5.3-Flash-FP8` /
   `custom:d754-mia-flashnext:qwen3.8-flash-next`; state/loop.json updated,
   backup loop.json.bak-pre-seats-20260930; loopctl DEFAULTS updated to the
   same live pair). `_validate_seats()` runs in main() BEFORE the single-flight
   lock: malformed seats (bare model name), unknown custom providers, and
   identical seats each refuse with a named error. Dead constants
   (KL_BUILDER_MODEL default deepseek-v41-3x, KL_REVIEWER_MODEL,
   KL_REVIEWER_ALT_MODEL) deleted; round_prompt seats are a parameter with
   live module fallbacks (d754 primary, evo-x2 alt — different machines).
8. **F3.8 bump wired** — `loopctl.bump()` (new function; cmd_bump delegates)
   called on ACCEPTED rounds only. rounds_done was never incremented before.
9. **F3.9 Kill scope** — `_kill_inflight_round` now kills ONLY hermes children
   resumed onto session ids owned by the loop (threads.json: session_id, tip,
   chain). `_pids_for_known_sessions()` + `_RESUME_RE` are pure and unit-
   testable. An unrelated `python --resume <sid>` can no longer die on pause --now.
10. **F3.10 wmic fallback** — `_process_lines_csv()` emits python.exe
    PID+CommandLine lines from wmic, falling back to PowerShell
    Get-CimInstance (CommandLine FIRST so the last CSV field stays the PID).
    Used by both kill sites.

## Tests
- tests/test_round_orchestration.py: REWRITTEN for worktree reality (14 tests).
  Child runs with the runner's real cwd (the worktree) + env; scenarios re-
  pointed: reject → worktree+branch deleted, canonical at anchor; accept →
  merged/tag==HEAD/cleanup; removal failure → quarantine survives reload +
  selector excludes; dirty start → no worktree; proposal failure → contained.
  NEW: rounds_done bumps on accept only (F3.8); abandoned-worktree recovery
  quarantines and preserves (F3.5); child-env push block proven by a REAL
  `git push` with the runner's env (fails: pushurl DISABLED); post-merge gate
  failure → no publish, canonical restored, work preserved on the branch.
- tests/test_containment_is_wired.py: re-pointed at the F3 seam
  (_remove_worktree/_reset_canonical); guard contract is now PER CALL SITE
  (reject path needs `not _round_ok`; accept-path cleanup needs positive
  `_round_ok`; every _reset_canonical under `_round_ok`) — the union-of-names
  version stayed green when the reject guard was neutered (found by drill).
- tests/test_rejected_work_is_contained.py: helper tests re-pointed at
  _reset_canonical; the decisive contamination scenario is now proven
  behaviorally through the real run_round (orchestration scenario 3) and the
  git-mechanics control stays here.
- tests/test_scope_callsite.py: publish-window test re-anchored to the reject
  branch (the accept branch now nests the integration if/else) + window
  widened 14000→18000 (measured: reject anchor +15,788).

## Drill (defect plant → RED, restore → GREEN)
- Plant: reject-path containment guard swapped to `if not rc:` (guard no
  longer tests _round_ok) → test_rollback_call_is_guarded_by_rejection FAILED.
- Restore → PASSED. (First drill attempt exposed the union-check weakness —
  fixed by the per-site contract above, then re-drilled.)

## Files touched
karpathy_runner.py (seats block, worktree/child-env/junction/recovery/validate
helpers, run_round rework, main() validation+recovery), round_prompt.py (seats
param, worktree boundary block, live seat constants), loopctl.py (bump(),
scoped kill, wmic fallback, live seat DEFAULTS), state/loop.json (seats; backup
kept), 4 test files. No live-Hermes files touched.
