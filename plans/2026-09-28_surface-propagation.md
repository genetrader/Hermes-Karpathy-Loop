# Surface Propagation & Campaign Rotation — Design (WIP skeleton)

Date: 2026-09-28. Status: DRAFT — being refined against source.

## Problem
When round N improves surface A, nothing decides whether surfaces B/C/D need the same change,
and nothing holds the repo rotation while a campaign is open.

## Verified facts (from caller, to re-check in source)
- `scope.py`: `parse(text)` reads `SURFACE-DONE: <slug> :: <verdict> -- <reason>` and
  `CROSS_CUTTING: <slug>,<slug> :: <what>`; `next_campaign(entry, scope_dict, project)` advances
  campaigns. 27 tests pass in `tests/test_scope.py`.
- `karpathy_runner.py`: `_parse_scope()` parses FULL child output pre-truncation; campaign advance
  near `entry["title"] = threads.title_for(name)`.
- `ROUND_TIMEOUT = 4200` at `karpathy_runner.py:57`; on timeout harness still advances `last_nudge`
  (`:405`), wedge detector suppressed (`loopctl.py:133`, stale = 2x timeout).
- Rotation: `karpathy_runner.py:547` sorts by `(last_nudge or 0)` only.
- `entry["current_surface"]` published at `:304`, never read.
- `loopctl.py`: zero surface/campaign refs.
- `angles_per_visit` never in live round path; every round = one angle.

## Plan of sections (fill in)
1. Propagation decision: declared (CROSS_CUTTING) vs inferred (shared contracts) vs classifier.
2. Rotation hold: does an open campaign hold rotation? Cap? Escape hatch? Interaction with last_nudge.
3. Termination: caps, reopening rule, convergence.
4. Minimum viable version.
5. Failure modes & guards.
6. Decision tables + pseudocode with real line numbers.

## Hypotheses (to verify)
- Hold rotation = rotate key is `(campaign_open, last_nudge)` so open-campaign repos sort first
  among themselves by last_nudge, others unchanged. Cap: max N consecutive held rounds.
- Gate FAIL on closing round: campaign does NOT close (verdict missing), survives, retried next visit.
- Termination: per-campaign attempt cap (e.g. 4 rounds) + reopen only via new CROSS_CUTTING marker.
