# Phase C pre-arm checks — 2026-10-01 09:30

## NEW DEFECT FOUND + FIXED (review-cross-check)
test_unsafe_start_refused.py executed the REAL run_round WITHOUT redirecting
threads.STATE; run_round persists entries via threads.load()+save() (runner
:1071-73), so the real registry carried a test residue entry 't' (rounds=1,
rc=1, cleanly rejected), the real prompt_cycles.json consumed a real angle
prompt for 't', and runner.log filled with test noise. FIXED:
- autouse fixture in test_unsafe_start_refused.py redirects threads.STATE
  (verified: 4 passed, registry md5 unchanged during the run).
- residue cleaned: 't' removed from threads.json + prompt_cycles.json
  (backups threads.json.bak-pre-tcleanup-20261001-093234,
  prompt_cycles.json.bak-pre-tcleanup-20261001-093245), empty worktrees/t
  dir removed.

## Seats (live, verified by real chat completions)
- GLM bottle 192.168.1.127:8093 — 200, finish=stop, 0 reasoning chars.
- qwen d754 192.168.1.159:8888 — 200, content 'OK', TensorFold drafting OK.

## Registry (LRU order after 't' cleanup)
tombs-and-tyrants(nudge oldest, 35 rounds) -> secondbrain(37) ->
zip-code-ranker(37) -> ai-consultancy-website(13). No campaigns open, no
quarantines, worktrees dir empty, loop.json running=false.

## BLOCKER for live rounds: every enabled repo has a dirty working tree
- tombs-and-tyrants: 121 dirty files (branch fix/code-review-2026-09-28;
  Gene's parallel agents work here)
- secondbrain: 4 (2 modified Kotlin + untracked test + .worktrees/)
- zip-code-ranker: 64
- ai-consultancy-website: 20
F0-1 will (correctly) refuse every round and the hot-loop fix will rotate on
each refusal. The loop would demonstrate refusal-rotation, not rounds.
Resolution needs Gene: which repo may be committed/stashed (NOT my call —
parallel agents own that work).

## Cron
karpathy-nudge */15 enabled (drives watchdog -> respawn ARMED loop only).
karpathy-rotator + karpathy-discovery disabled (retired engine / Phase B+).