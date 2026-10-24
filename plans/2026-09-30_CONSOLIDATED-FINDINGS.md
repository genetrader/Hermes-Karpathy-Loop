# CONSOLIDATED FINDINGS — full codebase re-review, 2026-09-30

Sources merged here:
- **Saul's Phase A review** (2026-09-30, reran our 123-test suite himself)
- **Lane 1: round engine** (deleg_66743781 task-0, 102 API calls, 37 min)
- **Lane 3: support/config** (deleg_66743781 task-2, 25 API calls, 36 min)
- **My own verifications** — every severity-blocker claim below was re-proven by
  direct execution before being accepted; reviewer reports are self-reports.
- **Prior ledgers**: D1–D5 (plans/2026-09-29_SURFACES-REPAIR-STATE.md),
  REVIEW-TRIAGE-2026-09-28.md P1/P2 items 4–13 (still open)
- **Lane 2: scope/surfaces** — first dispatch stalled; re-dispatched
  (deleg_f1803e79). Its findings will be appended when it reports.

Verification status: every item marked **[V]** was reproduced by me with a live
command this session. Items marked **[R]** were reproduced by the lane reviewer
with quoted output. **[H]** = hypothesis, cited by file:line, not yet executed.

---

## TIER 0 — the loop is currently broken end-to-end for NEW rounds

**T0-1. Acceptance reads evidence from a key NOTHING writes. [V]**
`karpathy_runner.py:666-668` reads `entry["evidence"]` / `entry["last_evidence"]`.
Grep confirms no code assigns either key; live `threads.json` has neither key on
any of the 4 repos. So `_gate_state` is always `None`, `_round_ok` is always
`False` — for every future round, regardless of quality.

**T0-2. …and then the rollback DELETES the round's good commit. [V]**
Because `_round_ok=False`, containment runs `reset --hard <anchor>`, destroying
the child's committed work even on rc==0 + gate-pass + HEAD-advanced. T0-1+T0-2
together: **every future successful round is rejected and destroyed**.

**T0-3. The evidence that DOES get collected lands on the WRONG row (off-by-one). [V]**
`hist.insert(0, new_row)` builds a local list (line 613-620) but
`entry["angle_history"]` is only reassigned at line 635 — AFTER
`round_evidence.attach(entry, _ev)` at 629. attach() reads the OLD list and
writes gate/commit/files onto the PREVIOUS round's row. My replay: gate landed
on row1 (old), row0 (new) got nothing.

**T0-4. This code has never run a live round.**
`karpathy_runner.py` mtime 2026-09-29 23:05 is after the last real round
(runner.log 09-28 13:26). The 123 tests pass because they test the pieces, not
the wiring — the exact "tests pass, live loop dead" pattern of the original D1.

## TIER 1 — severe, independent of T0

**T1-1. Dirty-start rollback destroys pre-existing work. [V — read verbatim]**
`karpathy_runner.py:386-398` only LOGS a dirty/unknown tree, then launches the
child anyway (Popen :490). Rejection path `reset --hard`+`clean -ffd` (:234)
erases pre-existing tracked/staged/untracked work. Also the no-HEAD branch
(:389-394) says "must not run" then runs. (Saul blocker, confirmed.)

**T1-2. Quarantine/rollback fields never persisted. [V — read verbatim]**
`threads.save(reg)` at :776; `entry["quarantined"]`/`quarantine_reason`/
`last_rollback`/`last_checkpoint`/`state_persisted` set at :810-821 — after the
save. Selector reloads from disk (:861), so "QUARANTINED" never sticks.
(Saul blocker, confirmed.)

**T1-3. `proposal_ok` is a dead flag; proposal exceptions are swallowed. [V]**
`_round_ok` (:716) is `rc==0 and _gate_ok and _head_ok is True` — proposal state
absent. Runner catches ALL proposal exceptions (:573-579) leaving
`campaign_prop=None`, but acceptance can still pass. (Saul blocker, confirmed.)

**T1-4. `threads.save()` non-atomic + load() swallows to `{}` + two-writer race. [R][V]**
threads.py:62-71 bare `write_text`; runner (:774-776) and watchdog
`maintained()` (every 15 min, separate process) both do load→mutate→save of the
WHOLE registry with no lock — verified lost-update shape by reviewer repro
(watchdog tip/chain fields reverted). A torn file → load() returns `{}` → next
save wipes all history, `resolve()` mints fresh sessions. Old triage B5, still
open, now load-bearing.

**T1-5. `is_running()` accepts ANY python.exe holding the PID. [R]**
karpathy_runner.py:143-157: `"python" in out.lower()` for `PID eq N`. PID
recycling → watchdog says "alive" forever, or `loopctl start` refuses to spawn.
The code fixed this exact trap in `_acquire_single_flight` (:126-129) but not
here. Watchdog respawn + wedge detection both depend on this.

**T1-6. `loopctl.save()`/`_sync_rotation()` non-atomic; corrupt read → DEFAULTS. [R]**
loopctl.py:64-73, 87-97. A torn loop.json + any loopctl command rewrites the
file as full defaults: projects list lost, `max_rounds` cleared,
`running:false`. Same crash class as T1-4, worse blast radius.

## TIER 2 — major

**T2-1. Checkpoint tag mismatch treated as success (rc=128). [R]**
checkpoint() :297-310 records rc strings, never raises; the round PROMPT also
tells the child to create the same tag (round_prompt.py:39 — verified), so 88 of
112 logged checkpoints are rc=128 "already exists". Live proof:
`kp/tombs-and-tyrants/r34` points at a 12:52:49 commit but round 34 rc=0 logged
at 09:29:08 — the published tag is NOT round 34's state. Stray child tags exist
(kp/zip-code-ranker/r997-998).

**T2-2. `/compact` via `-z` never compacts — false bookkeeping. [R]**
compact_thread.py:31-34 sends `/compact` as chat text; the command is only
dispatched by the interactive REPL's process_command, which oneshot bypasses.
state.db proof: session 20260928_064028_c5773f rowid 212198 has literal
`'/compact'` user row followed by an assistant NARRATIVE, no compaction handoff.
Every 8 rounds burns a model turn and logs "compacted" falsely.

**T2-3. BUILDER_MODEL default 404s — provider gone. [R]**
`deepseek-v41-flash-3x-spark/deepseek-v4.1-flash` not in profile providers;
probe → HTTP 404 at the default endpoint. Regressed since 09-28 (provider
existed then). Reviewer seats in round_prompt.py:52-53 hardcode models that
don't route either; `KL_REVIEWER_MODEL`/`KL_REVIEWER_ALT_MODEL` (runner :50-53)
defined and never used; loop.json seats never read (env-only). UI seat-swapping
changes nothing.

**T2-4. `rounds_done` never increments — max_rounds cap is dead. [V]**
`loopctl bump` (loopctl.py:491) has ZERO callers (grep). loop.json says
`rounds_done: 2`; actual rounds are 35/37/37/13. `should-run --autopause` can
never trip. Also explains checkpoints.json's colliding `round: 3` entries.

**T2-5. Discord outage aborts an otherwise-finished round. [R]**
`dn.progress()` → `send()` → `_req()` re-raises HTTPError; `token()` raises
SystemExit. Call sites at :477 and :824 are NOT in try blocks; the exception
escapes to main()'s broad handler, logged as "round CRASHED" — skipping
bookkeeping/checkpoint for a round whose child already finished fine.

**T2-6. Question→assumption→answer pipeline severed. [V — grep]**
`dn.remind()` writes assumptions to state/answer.json + questions/assumed/;
watch_answers writes real answers to answer.json — but NO reader of any of
these exists in the live round path. The prompt says "continue with the
best-reasoned default" but the human's actual answer never reaches the next
round. state/answer.json holds a stale qtest.

**T2-7. `improver.py run` is RETIRED but cron jobs still reference it. [V]**
improver.py:655-663 returns rc=2 "retired". karpathy_cron.cmd_rotator maps ANY
nonzero rc to "idle" rc=0 (karpathy_cron.py:75-86) — re-enabling karpathy-rotator
would tick forever doing nothing, silently. Dead wiring: run_rotator.sh,
run_watcher.sh (log targets don't exist), karpathy_nudge.py (orphaned duplicate
driver with DIFFERENT semantics — no scope/gate/campaign; timeout 5400 vs 4200;
`_pid_alive` returns True on tasklist failure → 6h self-block).

**T2-8. `pause --now` kill blast radius. [R/V]**
`_kill_inflight_round` (loopctl.py:283-315) matches ANY python with
`--resume`+`hermes_cli.main` — includes the user's own interactive sessions.
(Old triage B6, still open.) Also `wmic` dependency (lane-3 L3): removed in
newer Win11 → silent no-op hard-stop.

## TIER 3 — real but smaller

- **T3-1 [R]** Gate runs even on rc!=0 rounds (up to 900s wasted before rollback).
- **T3-2 [R]** Heartbeat stale for the whole 50-60 min round + during the 900s
  all-quarantined sleep → wedge detector can't distinguish healthy-long vs hung.
- **T3-3 [R]** Round prompt renders `deferred` as `[x]` accounted-for
  (round_prompt.py:152-163, truthiness vs terminal check).
- **T3-4 [R]** `scope.parse` keeps only the FIRST CROSS_CUTTING line.
- **T3-5 [R]** `scope.commit(accepted=False)` appends a rejection record even for
  rounds with no claim — rejection log pollution.
- **T3-6 [R]** Timeout reaping can leave a zombie grandchild (HYPOTHESIS).
- **T3-7 [V]** `maintained()` stamps `last_activity_at=now` every 15-min tick —
  sidebar recency ordering permanently falsified.
- **T3-8 [R]** AST wiring tests weaker than claimed — `if False and not
  _round_ok and ...` mutation stays green (I ran it: 9 passed). Saul predicted
  this exactly. Demote to smoke tests; behavioral tests required.
- **T3-9 [R]** `monitor.py` renders rotation "next up"/"turns taken" from
  rotation.json (retired engine) — fiction vs the live LRU rotation.
- **T3-10 [R]** Stale state files: checkpoints.json (all round:3 from retired
  path), asked.json, answer.json (stale qtest), monitor.html (Sep 23).

## SOUND (do not re-review)
Tri-state gate semantics; full-SHA + ancestor check; _rollback_to verify-after-
reset; checkpoint env hardening; pre-truncation scope parse; D1 shadowing gone;
_run_gate tri-state; threads.resolve/chain_tip resume semantics; single-flight
acquire (O_EXCL) incl. watchdog double-spawn guard; drain-mode pause ordering;
atomic heartbeat write; see_it.probe parsing; cron inventory (all scripts
exist); manifest/state agreement across loop.json/improve.yaml/threads.json;
py_compile clean on all lane files.

## OPEN (pending lane 2)
scope.py/surface_pick.py/surface_discover.py/round_prompt.py marker sections +
test-gap analysis → will be appended as T2/T3 items when deleg_f1803e79 reports.

## LANE 2 APPENDED (2026-09-30, deleg_f1803e79) — all 5 majors reproduced by me

**T2-9. Unrecognized-verdict branch destroys the parsed reason. [V]**
scope.py:122-123 `reason = (rest if not reason else rest).strip()` — the ternary
always yields `rest`, so a non-terminal verdict's audit reason becomes the raw
text INCLUDING the verdict word. Repro:
`parse('SURFACE-DONE: server :: mostly-fine -- actual reason here')` → reason
`'mostly-fine -- actual reason here'`. Fix: `reason = reason or rest`.

**T2-10. MAX_ADDITIONS caps the OPENING round's baseline, contradicting the
design comment at :258-260. [V]**
Repro: `propose(parse('CROSS_CUTTING: a,b,c :: x'), declared=['a','b','c'])` →
surfaces `['a','b']`, capped=True. The third DECLARED surface silently drops;
the checklist no longer represents the child's stated blast radius. Fix: skip
the cap while `camp["id"] is None` (opening admission), cap only later widening.

**T2-11. Blocked campaigns brick with no recovery except luck. [V]**
Driven exactly: 3 prose verdicts on `a` burn attempts (prose counts as a real
failure, :276) → attempt cap → then terminal on the OTHER surface (`b`) leaves
the campaign `blocked` with `a` outstanding. The runner never steers rounds at a
blocked campaign's outstanding surfaces, so the campaign is dead weight unless
the child happens to emit `done`/`not-applicable` for every remaining surface.
Fix options: don't count attempts when the round produced evidence-backed
progress; or unblock when an outstanding surface went terminal; and have the
runner prefer outstanding campaign surfaces when picking (Phase B concern).

**T2-12. `_slug` collision — sequential campaigns share one id. [V]**
Id is `project-x<count>`: two 2-surface campaigns both get `p-x2`. Repro'd.
Fix: include the round number in the id.

**T2-13. surface_pick fallback not recorded → same surface worked twice. [V]**
Simulated `save_state` failure → fallback returns `alpha` without booking;
healthy next pick → `alpha` AGAIN, visits shows 1. A round of work is invisible
to the rotation. Fix: best-effort `record()` inside the fallback branch.

**T2-14. A rename orphans the cursor and restarts rotation from head,
contradicting the module docstring's promise. [R]**
Cheapest honest fix is the docstring (rename = new surface by slug identity).

**T3-11. surface_discover.py is dead code**: never imported, output reaches
nothing; and it has two internal bugs (unimplemented dedupe; emits
low-confidence junk that propose() would enforce). Phase B: wire it or delete it.

**T3-12. round_prompt renders raw verdict dicts + overstates completion**
(`[x] server -- {'verdict': 'done', ...}`; "accounted" uses truthiness not
`_is_terminal`). Adjacent to known T3-3, same root.

**T3-13. `next_campaign` shim bypasses the declared vocabulary** and commits
accepted=True unconditionally; most tests still drive the shim, testing a weaker
function than ships. Migrate tests to propose+commit, delete the shim.

**T3-14. Marker-template line invites a literal-copy verdict**
(`done | deferred | not-applicable` copied verbatim → unrecognized → burns an
attempt; 3 of those brick per T2-11). Also: two terminal markers for the same
slug — last wins, so a late `not-applicable` silently downgrades a `done`.

**Lane-2 sound (do not re-review):** propose() purity; sticky-verdict guard;
rounds_used None-math; opening-round vocabulary binding; parse() never raises on
11 hostile inputs; slug regex rejects `::`; CROSS_CUTTING single-surface ignored;
surface_pick round-robin fairness, corrupt-state rebuild, atomic writes,
write-back-before-choosing; pick() called exactly once per round; round_prompt
legacy byte-identity.

**Lane-2's 5 required missing tests:** parse() preserves reason for
unrecognized verdicts; blocked-campaign lifecycle end-to-end; opening baseline
vs addition budget; sequential campaign id uniqueness; surface_pick fallback
booking (+ rich-dict rendering).

## Known Saul minimum gate (unchanged, Phase B blocked until demonstrated)
1 dirty/unknown start cannot reach Popen; 2 proposal failure participates in
_round_ok; 3 failed rollback survives reload as quarantine; 4 reloaded selector
excludes that repo; 5 rejected work contained through real run_round(); 6 child
cannot publish directly; 7 crash/restart leaves canonical checkout safe.
Architecture direction: isolated per-round worktrees; trusted parent only
integrator/publisher.
