# PROJECT IMPROVER — Control Panel & Process

Two additions since the last handoff: **the panel** (your base platform) and
**the six-stage method** (the process that stops this being blind iteration).

---

## 1. The panel — `panel.html`

The whole inventory is the working surface, with the rotation on top.

```
C:\CODING\project-improver\panel.html     ← open this
python control_panel.py --open            ← rebuild + open
```

**Top strip — KPIs:** pinned in loop · total projects (225) · turns taken ·
waiting on you · % with a gate · answered.

**Left column:**
- **Rotation — pinned projects.** One card per project in `improve.yaml` with
  live badge (`NEXT UP` / `RUNNING` / `IDLE` / `PARKED`), objective, gate,
  implementer + reviewer models, inventory category/status, a stage progress
  bar, and **next stage**.
- **Planned order** — the queue with relative timing (`now`, `+30m`, `+60m`…).
- **Stage artifacts** — the real files each stage produced, with sizes.

**Right column:**
- **Rotation ledger** — what has been done. Merges stage completions (scout ✓,
  baseline ✓) with loop-card turns.

**Bottom — full inventory, 225 projects**, filterable by free text, category,
status, and a view selector: *only in rotation / only git repos / only with
tests / only existing on disk*. Each row shows the detected **gate command**,
size, and run command. Rows in the rotation are marked `● IN ROTATION`.

---

## 2. The method — `method.py`

Six stages, always in this order. **A project cannot skip a stage**, because each
stage consumes the previous stage's artifact.

| | Stage | What it does | Artifact |
|---|---|---|---|
| S1 | **scout** | Read-only reconnaissance. NO edits. Every claim needs a `file:line` anchor or must be marked HYPOTHESIS | `state/plan/<p>/S1-scout.md` |
| S2 | **baseline** | Run the gate for real. Record exact command, exit code, counts, wall time | `02-baseline.json` |
| S3 | **backlog** | Rank improvements by ROI. Separate FIXES / IMPROVEMENTS / REMOVALS | `03-backlog.md` |
| S4 | **iterate** | Run the loops — **ONE backlog item per loop**, gate per item | checkpoint commits |
| S5 | **review** | Adversarial review by a **different model family**, reading the diff only | `05-review.md` |
| S6 | **verify** | Re-run the *exact* baseline command; compare; close or reopen | `06-verify.json` |

```bash
python method.py stages                       # show the process
python method.py status                       # per-project stage table
python method.py prompt <project> <stage>     # emit the worker prompt for a stage
python method.py advance <project> <stage> ok # record a stage result
```

**The rotator follows the method.** `improver.py` calls `next_stage()` and puts
the *stage* into the card body — so a worker wakes up knowing it's doing S1
scout, not "improve this somehow." When all six stages are done, the cycle
restarts at S1 with fresh eyes.

---

## 3. Live state — project-a

**Objective (re-scoped 2026-09-22 to the engine-refusal problem):** make the
game engine accept natural-language player intent instead of refusing it before
the GM ever sees the turn.

**The reported failure, verbatim:**
```
Player: Henk, what work can you give me? I'm ready to handle your toughest jobs.
Engine: You look for toughest, but there is nothing of the sort here. ...
        a soldier learns early that seeing what is actually in front of him
        is worth more than seeing what he expects.
        [three meta-choices that advance nothing]
```

**Root cause — found, located, reproduced:** `phrases_in()` returned
`['toughest']`, so the existence checker demanded a physical object called a
"toughest" exist in The Blooming Tankard. `toughest` is the **superlative of
"tough"** — a degree of comparison. The GM never ran.

**Fixed — commit `00cfbde`** (structural class closure, not a word list):
- `_is_degree_of_comparison()` closes the class by shape; `_EST_ER_NOUNS`
  carries the exceptions (`chest`, `forest`, `water`, …)
- Second defect found while testing: the person/role exemption ran *after* the
  suffix test, so `barkeep` (already in `_PERSON_NOUNS`) was read as furniture

**Gate (real, executed) — `.venv/Scripts/python.exe -m pytest -q` + 7 files:**
```
exit_code : 0
wall      : 10.51 s
summary   : 183 passed, 2 warnings
```
| Check | Result |
|---|---|
| New regression file | **128 passed** |
| Full repo suite | **1994 passed, 6 failed** (all pre-existing `equipment_canon`) |
| Regressions | **0** |
| Anti-cheat guard | `read the signpost` still refused ✅ |

**Stage state:** `S1 scout ✓` (real artifact: `01-scout.md`, 8.8 KB) ·
`S2 baseline ✓` · next stage **S3 backlog** — card `t_8e7a4676` is **running**
on profile `improver`, branch `wt/t_8e7a4676`.

---

## 4. The worker — profile `improver`

The card used to be assigned to `analyst`, which has only the `hermes-cli`
toolset — no file editing, no shell. It would have stalled on spawn. Fixed:

- **Profile `improver`** — cloned, stripped of session/history, then given
  `toolsets: [file, terminal, code_execution, skills, todo, vision, web,
  memory, session_search]`
- **SOUL.md rewritten** — a repo worker: reproduce the baseline, one change at
  a time, re-run the gate, never fabricate a result, never weaken a test to
  make it pass, never merge to main
- **`kanban-worker` skill installed** into the profile's own skills dir
- Removed the stale `moa` toolset inherited from the clone
- Fixed `timezone: Eastern` → `America/New_York` in both configs (invalid IANA
  name silently fell back to server-local time)

Three real bugs found and fixed while wiring it:
- `--completion-contract` only accepts `local-only` / `OWNER/REPO` / PR URL —
  a prose sentence was rejected outright
- `kanban create --json` nests the row under `"task"`, so the rotator recorded
  the card id as `None`
- `discord_notify.py ask` — the CLI form shifted its arguments; the question
  text was landing in the `qid` field and `question` was empty

---

## 5. What changed in the code

| File | Change |
|---|---|
| `control_panel.py` | **NEW** — builds `panel.html` from real state |
| `method.py` | **NEW** — the six-stage process + prompts + stage ledger |
| `improver.py` | `next_stage()` drives rotation; `create_card()` takes the stage |
| `baseline_tt.py` | records the real S2 baseline (re-pointed at the new gate) |
| `diag_refusal.py`, `diag_class.py` | **NEW** — the repros that proved the bug and its class |

Two more real bugs found and fixed:
- `PLANDIR` was referenced in `control_panel.py` but defined only in `method.py`
  → `NameError`.
- **Path-join bug:** the manifest uses `D:/ZCODE/X` (forward slashes) while the
  inventory uses `D:\ZCODE\X` (backslashes), so category/status rendered as `?`.
  Fixed with a `norm_path()` canonicaliser used by every join.
- **Prompt/artifact collision:** `cmd_prompt()` wrote the *stage brief* to the
  same path as the *stage artifact*, so a worker's first write destroyed its own
  instructions. Prompts are now `prompt-S1-scout.md`; artifacts are `01-scout.md`.

---

## 6. Next

1. **S3 backlog is running now** (`t_8e7a4676`). Watch it in `#project-reviewer`.
2. **Pin more projects** — edit `improve.yaml`, flip `enabled: true`. The panel
   picks them up on the next build.
3. **Merges are yours.** Work lands on `wt/<task_id>` checkpoints only. Nothing
   merges to `master` without you.

## Files

| File | Role |
|---|---|
| `panel.html` | **The control panel** |
| `control_panel.py` | Builds it from index + manifest + plan + live boards |
| `method.py` | The six-stage process, prompts, stage ledger |
| `improver.py` | Rotator — decides which project, which stage, when |
| `discord_notify.py` | Discord I/O: progress / stuck / ask / remind / answer |
| `watch_answers.py` | Detects your reply, drives the nag loop |
| `improve.yaml` | **The only file you edit to control the loop** |
| `state/plan/<project>/` | Stage artifacts (the real evidence) |
| `state/plan.json` | Stage completion ledger |
| `diag_refusal.py` / `diag_class.py` | Repros for the fixed bug |