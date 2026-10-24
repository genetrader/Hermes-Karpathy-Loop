# SURFACES — implementation plan (design locked 2026-09-28)

See `2026-09-28_surfaces.md` for the rationale and the measured evidence. This file is
the BUILD ORDER: small pieces, gated after each, reviewer on the diff at the end.

## The contract in three lines

1. A **surface** is a sub-category of a repo that can be worked on independently.
2. Surfaces **round-robin** inside a project; the project still advances once per round.
3. A round may be declared **parallel** so ONE turn updates all surfaces, preventing drift.

## Insertion point (verified against the code, not assumed)

- Rotation: `karpathy_runner.py:475` sorts by `reg[...]["last_nudge"]` — **untouched**.
- The round's choices happen in `run_round()`:
  - `:269` `angle = angle_pick.pick(name, facts=facts)`
  - `:270` `prompt = round_prompt.build(name, angle, gate, round_no, entry)`
  - `:301` `entry["current_angle"] = {...}` ← the persistence pattern to copy
  - `:355` `entry["last_nudge"] = time.time()` ← what makes "one visit" true
- Prompt cycle is keyed by project alone: `angle_prompts.py:187` `rec = d.get(project)`.

## Pieces (each gated before the next starts)

| # | Piece | Files | Gate | State |
|---|---|---|---|---|
| S1 | `surfaces_for(proj)` + `surface_pick.pick()` + `surface_state` persistence | `improver.py`, new `surface_pick.py` | 13 unit tests | **DONE — 13/13 pass, verified by me** |
| S2 | Manifest `surfaces:` accepts BOTH flat string and object list | `improver.py` | both shapes parse identically | **DONE (folded into S1's `surfaces_for`)** |
| S3 | Thread the surface into the round prompt | `round_prompt.py`, `karpathy_runner.py:269-270` | golden: prompt has the surface block; no other key changes | **DONE — 6 probes asserted; legacy 4-arg call is byte-clean** |
| S4 | **CAMPAIGN mode** (N rounds sharing one checklist, NOT one giant round) | new `scope.py`, `karpathy_runner.py`, `round_prompt.py` | marker parsed from **`_out` before the `[-2000:]` slice**; verdict map must be full | **DONE — 27/27 in `tests/test_scope.py`** |
| S5 | `surface_see:` map; surface chip in the projects table | `improver.py:surface_see_for`, `activity.py`, `review/plugin.js` | gate.sh 6/6 + render proof | **DONE — 25/25 render proof, gate green on dev AND installed** |
| S6 | ~~Widget: surface rows under `cot`~~ | — | — | **RESOLVED DIFFERENTLY:** the chip rides the projects row (name cell), not `cot` and not `see`. No new column. The M1 budget hazard was per-row `see`; this adds ~40 B per row and the payload came DOWN to 2553 B. |
| S7 | Payload budget — **must test under LIVE-round pressure** | `activity.py compact_payload` | see M1 below | **PARTIAL** — measured 2553 B idle; still needs the live-round measurement (a round must be running, feed populated) |
| S8 | Manifest data: declare Gene's 5 surfaces for secondbrain | `improve.yaml` | union check prints the diff vs the brief | **GATED ON THE DISCOVERY DESIGN** (`deleg_31456dea`) — discovery proposes, a human confirms, and only then does the manifest change |
| **S9** | **Automatic surface DISCOVERY + classification** (nested apps, confidence, propose-and-confirm) | new `surface_discover.py` | prototype runs on real repos | **AGENT RUNNING** — `deleg_31456dea` task 0 |
| **S10** | **Cross-surface PROPAGATION design** (should B/C/D get the same change; termination; rotation) | new `plans/2026-09-28_surface-propagation.md` | design + decision tables | **AGENT RUNNING** — `deleg_31456dea` task 1 |

Gene asked for S9/S10 explicitly after S1–S5 landed, in his words: *"we're going to need
some logic build that defines and categorizes different surfaces in a repo… it's extremely
important for this loop because it will improve the work it does"* and *"if one surface is
changed for the better, there should be something… that's considering if other surfaces
should have those same changes."* The plumbing that
consumes a surface list exists; S9 is how the list stops being hand-written.

## Corrections forced by the adversarial review (all verified by me)

**H2 — the parallel-round design was unbuildable.** `ROUND_TIMEOUT = 4200` s and rounds already
take 50-60 min; a 5-surface round is 250-300 min. Worse, the timeout path still advances
`last_nudge`, so the loop rotates on with a half-edited tree. **Fixed by redefining parallel as
a CAMPAIGN across consecutive rounds.** See `2026-09-28_surfaces.md` §6.2.

**H1 — the marker had no prompt-side half.** The TEMPLATE never asks for `CROSS_CUTTING:`, and
the runner truncates output to 2000 B (`:340`) before anything could read it. Both halves are
now required: ask for it in the prompt, and **parse `_out` before the slice** (`:338`).
Precedent is unkind — the already-mandated `REVIEW()` line appeared in 0 of 3 recent rounds.

**M1 — the degradation ladder would eat the feed to save decoration.** Measured: a
`surface_see` map in every row's `see` pushes the payload 3,302 B → ~6,418 B; post-guard it
lands at **774 B with every `see` gone and the feed empty.** So surface data ships **only under
`cot`, for the current project only, ~150-200 B max** — never per-row. S7's gate must run
**with a live round in progress** (feed populated), not the idle 3.2 KB state.

**M2/M3 — scope had three homes and no precedence.** Now one: `entry["campaign"]`, runner-owned.
Manifest may *start* a campaign; `loopctl` gets a read-only `--show-campaign`;
`state/surfaces/` is the only surface-state home and `rotation.json` must never gain surface keys
(`_sync_rotation` at `loopctl.py:76-87` would silently drop them and reset cursors).

**M4 — a campaign round must stay ONE finding per surface,** and per-surface "done" is a CLAIM,
not verified fact: there is one project-level `gate:` command, so the runner cannot independently
verify each surface. Do not imply more rigor than exists.

**M5 — S6 must not touch row `see`.** `tests/prove_repos_panel.py:70` asserts the shipped `see`
keys are a fixed set; adding `surface_see` there breaks that gate.

**L1 — `angles_per_visit` is NOT live.** It appears 15 times but only in `loopctl` config plumbing
and `build_selector.py` (a UI). Every round is exactly one angle. The original design leaned on
this; it has been corrected.

**L2 — `karpathy_nudge.py` is a dormant second runner** missing half the round contract. Nothing
invokes it today. Wire S3/S4 into `karpathy_runner` ONLY, and quarantine nudge explicitly or a
future reactivation silently drops surfaces and markers.

## Anti-drift rules (Gene's requirement 3)

- A **parallel** round may not report success while any surface is unaccounted for; each
  surface needs `done` / `deferred+why` / `not-applicable+why`.
- A single-surface round that finds a cross-cutting change ends with
  `CROSS_CUTTING: <slug>,<slug> :: <what>` and the **next** round for that project becomes
  parallel, with the checklist seeded from those slugs.
- The trigger is a **declared marker**, never a heuristic — a guess misfires both ways and
  is unauditable.

## Definition of done

- [ ] All pieces gated; `gate.sh` 6/6 on dev AND installed widget copy
- [ ] `activity.py --selfcheck` under budget with surfaces present
- [ ] SecondBrain declares 5 surfaces incl. `android-assistant`
- [ ] **Two adversarial reviewers** attack the design and the build (Gene's explicit ask),
      and their high-severity findings are independently re-verified by me
- [ ] The loop is left in the state Gene wants (it is currently PAUSED)