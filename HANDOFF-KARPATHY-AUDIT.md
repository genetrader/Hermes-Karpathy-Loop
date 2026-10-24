# Karpathy Loop — full-audit handoff (2026-10-15/16, Picard)

Durable findings from the 10-15/16 review cycle. Memory tool was full, so this
file is the canonical record. The skill `karpathy-loop` carries the fixed-item
details; this file carries status + open items.

## Context: the lane outage that started this (10-15)

- The d754 TensorFold lane (192.168.1.159:8899) is DEAD — container
  `vllm-fn-tp1` Exited 137 on Oct 9 and the supervisor holds relaunch because a
  manual stop-flag sits at
  `/mnt/data/qwen38/Qwen3.8-Flash-Next-Single-DGX-Spark/logs/stopping`.
- ALL other lanes down except **evo-x2 (192.168.1.49:8081)** — serves
  qwen3.8-flash-next, honors ANY model-name alias (GLM included), 131k context.
- Repointed: both seat slugs in Hermes config.yaml (python line-replace — the
  patch tool refuses Hermes config; backup at
  config.yaml.bak-pre-evo-x2-repoint-20261015), checkpoint.py
  PLAIN_ENDPOINTS (evo-x2 first, 8899 kept as fallback),
  settings.local.yaml summary_url/summary_url_2.
- Desktop app must be restarted to pick up provider changes.
- git push stalls on this box are self-inflicted probe-stacking: kill hung
  git-remote-https between attempts, verify via `gh api`, one clean push lands
  in ~90s. checkpoint() now uses Popen+120s+kill+single-retry (F-Q).

## Review agents (2 × GLM-5.3-Flash-EXL3, 10-16)

Both reports preserved: state.db message 295387 (runner-core agent) and
cache/delegation/live/deleg_6df3adb8/task-0.log (support-layer agent).
Suite verified 300/300 after every fix.

### FIXED (all verified live)

1. **F-B4 — `pause --now` could not kill the runner.** Matcher required argv[0]
   to end with `karpathy_runner.py`, but the live runner spawns as
   `python.exe …karpathy_runner.py` → matched FALSE on both processes, printed
   "0 runner proc" while the runner survived. Fix in loopctl.py: last
   whitespace token of CSV field 0 must BE the script path. Verified against
   the live table (finds 297328+296680) + 6-case matrix + 300/300.
2. **F-R2 — `checkpoint.py current` reported a dead loop as in-flight forever.**
   current_angle fallback had no pid/staleness check. Now: heartbeat pid must
   be alive (tasklist) and fresh; fallback trusts current_angle only < 2h old;
   not-provably-alive → `{"running": false}`. Verified BOTH directions (fake
   dead heartbeat → false; live → true).
3. **F-B5 — `_re` NameError at karpathy_runner.py:894.** Recovery sweep's
   branch deletion called `_re.match` with only `re` imported; bare except
   swallowed it → round branch never deleted → every post-quarantine round
   refused on `branch already exists`. This was a REAL contributor to the
   r84/r407 wedge. Fixed to `re.match` + None-guard.
4. **F-B6 — `git worktree remove --force` deletes THROUGH nested junctions.**
   Reproduced by agent 1: `server/.venv` junction (secondbrain shape) with a
   missing/corrupt .deps.json sidecar → canonical dep dir emptied.
   `_cleanup_junctions` now ALSO walks the worktree for reparse points
   (os.walk, no descent into junctions) and cleans every one found. Verified:
   nested junction with NO sidecar → removed from worktree, canonical
   sentinel intact; 300/300.

### OPEN (verified findings, not yet fixed — priority order)

1. **Startup-only sweep + existing-worktree refusal = per-process wedge**
   (karpathy_runner.py:2172 sweep vs :432-434 refusal). A worktree abandoned
   mid-run is refused every visit for the life of the process even past 6h.
   Candidate: re-check age in `_make_worktree` before refusing, or run the
   recovery sweep periodically inside main().
2. **Corrupt threads.json silently un-quarantines everything**
   (threads.py:80-102 — salvage returns {}). Quarantine flags vanish;
   quarantined repos become schedulable. Salvage print goes to detached
   stdout, never runner.log.
3. **Unguarded loads in main() → 15-min crash loop** — loopctl.load
   (:2179), I.load_manifest (:2184, raises on corrupt YAML), threads.load
   (:2200) all outside try; watchdog respawns into the same crash.
4. **Widget renders angle.prompt/prompt_lines that activity.py never ships**
   (plugin.js:2580-2588 vs activity.py:770-782) — prompt-display feature is
   dead. Same class as the cot.items/steps kill.
5. **activity.py `_live_sids` is wmic-only with swallow-to-empty**
   (activity.py:523-535) — on a wmic-less box everything renders idle and
   `best` ships the WRONG project's transcript. Needs loopctl-style
   PowerShell fallback.
6. **Gateless enabled project auto-rejected forever** — upsert_missing creates
   blocks with no gate and path:"" (improver.py:90-97); tri-state gate logic
   (:1813-1825) rejects them every round.
7. **Long pause + resume trips max_hours** — cmd_start keeps original
   started_at (loopctl.py:392); paused wall-clock counts toward the cap.
   Invalid started_at silently disables the cap (line 779).
8. **Loop.json read-modify-write races** — runner bump() vs operator
   config/start/pause, no lock, last writer wins.
9. Minor: count-based prompt-cycle reset; serial 45s endpoint timeouts in
   _plain_for can wedge a panel poll ~10min; improve.yaml still carries
   HIDDEN dead seats read by legacy `improver.py run`; `_sweep_worktree_processes`
   can kill an editor open on the worktree; heartbeat frozen during a round
   can false-positive "wedged" with the 3h ceiling; failed tag push never
   retried by later rounds (doc/behavior mismatch); fixed .json.tmp name in
   threads.save; F-Z2 comment claims junctions "stay verbatim" but resolve()
   follows them (benign — verified rc=0 both paths).

### VERIFIED SOUND (do not re-litigate)

- F-Q/F-P push-retry handle paths (_p/_p2/_p3, no NameError path, no double
  verdict), kill_tree + argv containment, single-flight lock layers,
  refusal persistence, accept-path ordering, quarantine-at-pick, chain-tip
  resolution, prompt layer (184/46 full-table walk), widget payload budget
  (3400B < 3800B) + ts normalization, see-probe parsing, pause drain
  semantics, heartbeat liveness + wedge banner.

## Loop state at handoff (10-15 ~22:00 ET)

- RUNNING on evo-x2; rounds landing: T&T r91/r92/r93 published + pushed
  (31cc73c, 3973d0c verified on GitHub via gh api), ZCR r415 published +
  pushed (01301c5), aic r131 "no new commit" (clean skip), ZCR r416 rc=2
  (child died; containment ran). No wedges, no refusals outstanding.
- KL_ROUND_TIMEOUT=10800 at machine level (HKLM), watchdog reads it.
- Runner pid 296680.

## If you pick this up

- Run `python loopctl.py status` first; check `logs/runner.log` for
  `rc=` lines with the parser in the skill (never substring-count).
- The OPEN list above is ordered by real-world risk. #1-#3 are the ones that
  can wedge or crash-loop the fleet loop; #4-#5 are panel-lies class.
- Do NOT touch live model seats without checking lane health first
  (probe /v1/models on 49:8081 and 159:8899).
