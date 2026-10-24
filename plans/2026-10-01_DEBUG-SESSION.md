# DEBUG SESSION — two-agent review + fixes, 2026-10-01
Reviewers: 2 independent GLM-5.3-Flash-FP8 subagents (Gene-directed model),
deleg_6e742cb0: A = scheduler core (scope/surface_pick/round_prompt),
B = runner/wiring (karpathy_runner/loopctl/threads/monitor).
Both independently re-ran the full suite: A=228 passed, B=228 passed
(baseline confirmed before any fixes). Zero modifications by the agents.

## Findings triage (17 total: 6 A + 11 B)

### Fixed pre-arm (11)
| # | Sev | Finding | Evidence | Fix | Verified by |
|---|---|---|---|---|---|
| B-1 | HIGH | Refusal-path bookkeeping never persisted (hot-loop fix dead code; dirty repo re-picked every tick) | REPRODUCED end-to-end | _persist_entry() called on both refusal paths | end-to-end repro: on-disk registry now carries round_refused/last_nudge/last_rc |
| B-2 | HIGH | Dep provisioning junctions only top-level dep dirs — secondbrain's `cd server && .venv/...` gate could never pass in a worktree; every secondbrain round rejects forever | REPRODUCED (runner's own functions) | _discover_dep_dirs(): asks git for the repo's OWN ignored dirs at any depth (bounded 12, depth<=4), fallback rglob | live drill on a replica server/.venv layout: junction made, gate python reachable, cleanup removes junction only |
| B-3 | HIGH | Crash-sweep required publication_state=='skipped', which cannot exist on disk in the crash window it targets (dead code) | HYPOTHESIS (line-logic confirmed) | condition accepts publication_state ABSENT or 'skipped' | synthetic crash-window entry -> sweep quarantined it (live probe) |
| A-1 | MED | Sticky parse dropped a same-message cited re-emission -> B4 downgraded a done that DID cite (costs an extra round) | REPRODUCED | later terminal line MERGES its reason (verdict word stays sticky) | regression test |
| A-2 | MED | lstrip('./') mangled dot-prefixed paths (.github/...) -> real citations always rejected | REPRODUCED | literal './'/'../' prefix strip only | regression test: .github path passes, invented github path still rejected |
| A-3 | MED | surface picker ignores the campaign checklist -> held rounds could land on terminal surfaces, burning hold/ceiling budget | REPRODUCED | pick(prefer=outstanding) rotates among the campaign's outstanding surfaces; runner passes scope.outstanding_surfaces(campaign) for OPEN campaigns | regression test + legacy path unchanged |
| A-4 | MED | SCOPE regex failed OPEN on punctuation variants (colon note, no colon, hyphenated cross-cutting) | REPRODUCED | regex widened (separator optional, [=:] allowed, CROSS-CUTTING normalized) | regression test (5 shapes) |
| B-7 | LOW | _hold_state ValueError on non-numeric campaign_holds -> selection-sort crash + watchdog respawn loop | REPRODUCED | _safe_int() in all three paths + cap-release log | regression test |
| A-5 | LOW | legacy campaign without opened_round insta-blocked (round ceiling = whole history) | REPRODUCED | opened_round defaults to CURRENT round | regression test |
| A-6 | LOW | silent rounds on BLOCKED campaigns re-marked blocked_round forward (audit rot) | REPRODUCED | already-blocked campaigns are not re-marked | regression test |
| B-5 | MED | loopctl _liveness substring PID check | HYPOTHESIS | tasklist /FO CSV /NH + exact PID-field match | code-level (display path) |

### Also fixed same-day (pre-arm session, before the agents)
- test_unsafe_start_refused.py executed real run_round WITHOUT redirecting
  threads.STATE -> real registry carried test residue 't' + real prompt-cycle
  consumption + runner.log noise. Fixed (autouse STATE redirect), residue
  cleaned with backups. Registry md5-identical during test runs now.
- Refusal paths consume the scheduling slot (last_nudge stamp) — from
  yesterday's review, now actually persisted thanks to B-1.

### Deferred with reasons (6)
| # | Sev | Finding | Why deferred |
|---|---|---|---|
| B-4 | MED | threads.resolve can adopt an operator session whose first message merely quotes the init marker | All 4 enabled projects already have bound session_ids — resolve() will not run for them during Phase C; changing session-adoption right before arming is riskier than the residual exposure. Revisit after C. |
| B-8 | LOW | checkpoint() ignores pushed_to_github=false config | Local tag correct; push failure marks publication failed, no damage; wire in a follow-up |
| B-9 | LOW | cmd_start PID-unlink window can double-spawn (watchdog) | single-flight O_EXCL ends with exactly one runner; cosmetic |
| B-10 | LOW | junction-cleanup fallback can rmdir a real EMPTY dep dir when sidecar lost | Bounded (empty + sidecar lost); the load-bearing ordering was verified by the agent's live-junction drill |
| B-11 | LOW | monitor 'next up' sorts missing last_nudge as 0 | Panel-only, matches the runner's tie behavior |
| A/B info | INFO | cite gate fail-open when the index build fails (logged) | By design (dead gate must not block rounds); close attempt is still logged in the entry |

## Suite
**235 passed / 0 failed** (228 baseline + 7 new regression tests, incl. the
pollution fix). Full suite re-run twice after fixes: green both times.

## What the agents confirmed working (both reports)
- propose/commit split + ordering; rejected rounds cannot move campaign state.
- B3 gate behaviors (LOCAL contradiction, UNCERTAIN refuse, legacy open).
- B4 core (downgrade, dedicated fields, cannot close uncited, upgrade path,
  not-applicable exempt, fail-closed, lazy index).
- B2 hold (decisive shape), cap release, blocked≠hold, counter reset.
- Worktree isolation + containment + publish-last ordering + real-push egress
  drill + checkpoint authorship.
- Human-answer pipeline, atomic saves, salvage-not-reset corruption handling.