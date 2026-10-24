# PHASE C ARM + LIVE OBSERVATION — 2026-10-01

## Armed
- `loopctl start` rc=0 at 10:39: 'runner spawned', running=true, 4 projects.
- Runner process: hermes-venv python (trampoline 47164) -> uv py3.11 (56616),
  single-flight lock held by 56616 (state/runner.pid).
- 'runner started (pid 56616) round_timeout=4200s' at 10:42:40. NOTE: first
  spawn took ~3m to reach main() (cold interpreter start; imports themselves
  measured 0.02s each). Watch whether the 3m repeats on future spawns.

## Live behavior observed (first ~12 minutes)
- Every enabled repo has a DIRTY working tree (T&T 121 files incl. parallel-
  agent work; secondbrain 4; zip-code-ranker 64; ai-consultancy 20).
- F0-1 correctly refuses every round ('working tree is DIRTY at round start
  -- refusing so rollback can never destroy pre-existing work').
- HIGH-1 fix verified LIVE: each refusal persists (round_refused, last_rc=1,
  last_nudge stamp) — registry confirms; rotation cycles FAIRLY:
  22 refusals per repo each in ~9 min, one refusal every ~5.2s overall,
  no repo monopolized, no hot-loop on a single repo.
- Heartbeat advances per round-start (r36 T&T -> r14 ai-consultancy...).
- Discord: token absent -> refusal notifications are silent (no spam).
- Single-flight: the karpathy-nudge cron did NOT double-spawn (lock held).

## What is needed for a REAL round
Gene must pick a repo to clean (commit/stash its parallel-agent work):
candidates with dirt: tombs-and-tyrants (121 files — likely his agents),
secondbrain (4: ApiModels.kt, SecondBrainApi.kt modified + untracked
AttentionContractRegressionTest.kt + .worktrees/), zip-code-ranker (64),
ai-consultancy-website (20). The loop will keep refuse-rotating (safe) until
then. Surface types ready for the real rounds: secondbrain = multi-surface
(server/android/chrome-extension — exercises campaigns + rotation hold),
ai-consultancy-website = website, zip-code-ranker = data/scripts,
T&T = game.

## Harness fix during watch
- test_round_orchestration's fake surface_pick.pick lacked the new prefer
  kwarg (harness noise 'surface pick failed ... prefer'; tests still passed
  15/15). Fixed: lambda accepts prefer=None; rerun 15 passed.

## Runner state at report time
- pid 56616 alive, refusing-rotating cleanly, registry persisting, no
  quarantines, no campaigns, seats live (GLM bottle + qwen d754 probes 200).