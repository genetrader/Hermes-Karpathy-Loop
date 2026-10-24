# MASTER PLAN — fix and finish the Karpathy/surfaces loop
**2026-09-30 · owner: Boss · evidence base: plans/2026-09-30_CONSOLIDATED-FINDINGS.md**

Every fix below follows the session discipline: **small piece → defect drill
(plant the bug, watch the test FAIL, restore, watch it pass) → verify against
the real system → paste the actual output.** No claim without a shown result.
The loop stays PAUSED until F5 passes; the enabled cron (karpathy-nudge →
watchdog) is safe while paused because the watchdog only respawns an ARMED loop.

---

## Phase F0 — stop the bleeding (no behavior change beyond refusal) — ~1h

| # | Fix | File | Drill test |
|---|---|---|---|
| F0-1 | **Refuse to launch when pre-round state is unsafe**: `pre_round_head is None` OR `pre_round_dirty is not False` → log + record `entry["round_refused"]="unsafe start"` + return rc=1 BEFORE Popen. (T1-1, Saul blocker 2) | karpathy_runner.py:386-398 | behavioral: dirty tracked file + untracked file + None-sha → assert Popen never called, files byte-identical |
| F0-2 | **Make threads.save atomic** (tmp + os.replace) and **fail loud** in load() on corrupt JSON (rename the corrupt file aside, return {} but log + emit `state/threads.json.corrupt-<ts>`). | threads.py:62-71 | unit: torn file → corrupt copy exists, load returns {}, save produces parseable file |
| F0-3 | **Make loopctl.save + _sync_rotation atomic** (same pattern). | loopctl.py:64-97 | unit: torn loop.json → defaults logged + corrupt copy preserved |
| F0-4 | **Wrap dn.progress/stuck/ask call sites** in the runner + improver with try/except (Discord is notification, never round control flow). Keep SystemExit inside token() but catch it at call sites. | karpathy_runner.py:477,824,891; improver dn calls | unit: token() raising → round bookkeeping still completes |
| F0-5 | **Fix is_running() PID check**: match image name AND pid in CSV (copy the exact logic from _acquire_single_flight:126-129). | karpathy_runner.py:143-157 | drill: unrelated live python PID in pid file → is_running() False |

## Phase F1 — make acceptance real (the loop works again) — ~3h

| # | Fix | File | Notes |
|---|---|---|---|
| F1-1 | **Wire evidence correctly end-to-end**: keep the `_ev` local from the round_evidence block in a variable that survives to the acceptance block; read gate from `_ev["gate"]` at :666 instead of `entry["evidence"]`. Delete the dead `evidence`/`last_evidence` reads. (T0-1) | karpathy_runner.py:625-635, 666-668 | the one-line structural fix: `_ev` is already computed at :627 — just hoist its scope |
| F1-2 | **Fix the off-by-one**: move `entry["angle_history"] = hist[:20]` to BEFORE the round_evidence block so attach() writes onto the NEW row. (T0-3) | karpathy_runner.py:613-635 | replay test: 2-row history → new row carries gate/commit |
| F1-3 | **proposal_state joins acceptance**: `proposal_state = True` only after propose() returned with proposal_ok=True; False on proposal_ok=False AND on any exception in the propose block. `_round_ok = rc==0 and _gate_ok and _head_ok is True and proposal_state is True`. (T1-3, Saul blocker 3) | karpathy_runner.py:549-579, 716 | drill: remove the term → test fails; also proposal raising AttributeError → round rejected, commit rolled back |
| F1-4 | **Skip the gate when rc!=0** (T3-1): a failed round doesn't need a 900s gate run before rollback. | karpathy_runner.py:628 | behavioral |
| F1-5 | **Reorder the round tail to Saul's order**: acceptance → campaign commit → containment → persist COMPLETE entry (incl. quarantine) → publish only after persist. Set `state_persisted` only after the FINAL save. (T1-2, Saul blocker 1) | karpathy_runner.py:732-826 | behavioral: force ROLLBACK UNVERIFIED → reload registry from disk → quarantined+reason present; selector excludes repo |
| F1-6 | **Checkpoint authorship**: runner does `git rev-parse refs/tags/<tag>` first; if tag exists at a DIFFERENT sha → publish FAILED loudly (never push mismatched). Remove the "Tag the accepted round" line from round_prompt so only the runner tags. Add `publication_state` (pending/published/failed) + `publication_detail`. (T2-1) | karpathy_runner.py:297-310; round_prompt.py:39 | drill: pre-existing tag at wrong sha → publication_state=failed, no push |
| F1-7 | **Fix deferred checkbox** in round_prompt (use terminal-verdict check for `[x]` and counts). (T3-3) | round_prompt.py:152-163 | unit: deferred renders `[ ]` |
| F1-8 | **Remove or replace /compact**: drop compact_thread.compact() call + bookkeeping (auto-compression already handles context), OR invoke the real compression API. Choose drop — simplest, honest. (T2-2) | karpathy_runner.py:638-646; compact_thread.py | grep: no `-z /compact` remains |

## Phase F2 — behavioral orchestration tests through the REAL run_round — ~4h

Build `tests/test_round_orchestration.py`: real temp git repo, injectable child
(monkeypatch the Popen call to a script that commits/files/returns a chosen rc),
controlled gate, real registry file on disk in a temp dir, reload-then-assert.
Saul's required scenarios:

1. rejected child commit removed; 2. rejected untracked residue removed;
3. accepted second round's published range contains none of round 1's work;
4. rollback failure → quarantine SURVIVES registry reload; 5. reloaded selector
excludes the quarantined repo; 6. dirty start never launches, files preserved;
7. proposal_ok=False rejects + rolls back; 8. publish happens only after final
persistence; 9. evidence lands on the NEW row; 10. gate skipped on rc!=0.

AST tests stay as smoke tests (relabel file docstring honestly).

## Phase F3 — isolation re-architecture (Saul's core recommendation) — ~1-2 days

Per-round disposable worktree; trusted parent is the only integrator/publisher.

1. `git worktree add <state>/worktrees/<repo>/rNN <anchor-sha> --detach` at round start.
2. Child runs with cwd=worktree; env has NO push credential (empty credential helper already in place) — add `remote.origin.pushurl` invalidation in the worktree + document egress boundary as defense-in-depth.
3. On reject: delete the worktree + branch (containment = deletion, no reset --hard on canonical, ignored-file blindness moot).
4. On accept: parent verifies ancestry, then merges/fast-forwards canonical branch to the round commit; run gate again in canonical if cheap; publish.
5. Crash recovery: at startup, list abandoned worktrees older than N hours → quarantine-with-reason (never auto-delete without the audit note).
6. Watchdog: no change needed (it respawns the runner, not rounds).
7. Model seats: read from loop.json seats, validate against configured providers at startup, fail fast with a named error; delete KL_REVIEWER_MODEL dead constants; fix round_prompt hardcoded seats. (T2-3)
8. `loopctl bump` wired into the runner's accepted-round path so rounds_done is real and max_rounds/autopause works. (T2-4)
9. Scope `pause --now` kill to session ids in threads.json (not any --resume python). (T2-8)
10. Keep `_kill_inflight_round`'s wmic usage but fall back to PowerShell Get-CimInstance when wmic is absent. (T3-8's wmic note)

## Phase F4 — surfaced new defects, interleaved by severity

**Lane 2 has landed (2026-09-30). Fold-in assignment:**

Into **F1** (fix during the acceptance pass):
- T2-9 scope.parse reason-destroying ternary (`reason = reason or rest`)
- T2-10 opening-round baseline must not consume the additions cap
- T2-12 `_slug` collision (id includes round number)
- T3-12 round_prompt verdict rendering (`_is_terminal`, no raw dict repr)
- T3-14 marker-template wording (emit exactly ONE of the three words); guard
  terminal→terminal overwrite in parse (sticky in both directions)

Into **F2** (orchestration/behavioral test file):
- Lane-2's 5 required tests (parse reason preservation, blocked-campaign
  lifecycle, opening baseline, id uniqueness, fallback booking + rendering)

Into **F3** (scheduler-adjacent structure):
- T2-11 blocked-campaign recovery semantics (attempt counting on evidence-backed
  progress OR explicit unblock; runner steers outstanding campaign surfaces —
  this is also the Phase B3/B4 design input)
- T2-13 surface_pick fallback records its choice
- T2-14 reconcile rename docstring honesty (or alias map — decide in F3)
- T3-13 delete the `next_campaign` shim after migrating tests to propose+commit

Into **F4 cleanup**:
- T3-11 surface_discover: wire it (Phase B discovery) or delete it — Gene decides;
  if kept, implement the parent/child dedupe and a confidence threshold
- T3-5 already listed; add: `campaigns_closed` truncation should log when it drops

Also scheduled here:
- Rotation/monitor cleanup: monitor.py reads rotation.json (retired engine) — switch to threads.json LRU or hide the panel rows (T3-9); prune stale state files (checkpoints.json retired-path rows, asked.json, answer.json qtest) with a note (T3-10).
- maintained() stops stamping last_activity_at=now on every tick (T3-7).
- Question pipeline: either wire answer.json/assumed answers into round_prompt (the "continue with best-reasoned default" promise) or remove the promise; Gene decides which. (T2-6)
- scope.parse: log when a second CROSS_CUTTING line is dropped (T3-4); commit(accepted=False) records a rejection only when the proposal carried a claim (T3-5).

## Phase F5 — verification gate — ~2h

1. Full suite (target: 123 existing + ~25 new = ~148 passing).
2. Re-run every defect drill from F0/F1 (table in the run log).
3. Dispatch Saul review #3 of the repaired tree (prose description + live code citations, NO unified diff).
4. Saul's 7-point minimum gate checklist, each point with its proving test name.
5. Only then: propose Phase B start to Gene.

## Phase B — surfaces scheduler (BLOCKED until F5)

B1 state machine + bounded attempts · B2 burst+LRU rotation · B3 opening gate
(LOCAL/CROSS_CUTTING/UNCERTAIN) · B4 closing gate (narrow falsification,
repo-cited) · B5 wire into runner + full suite + Saul review. Build only after
F5; the scheduler's correctness assumptions (durable quarantine, real
acceptance, safe containment) do not exist until F1-F3 land.

---

## Sequencing rationale
- F0 is hours, removes data-loss + registry-wipe risk today.
- F1 makes the loop FUNCTION (acceptance currently rejects everything).
- F2 is the proof layer Saul demanded; without it F3's re-architecture has no net.
- F3 is the structural fix that makes containment safe by construction.
- B stays blocked: building a scheduler on a loop that rejects+destroys every
  round would be the D1 mistake again at larger scale.
