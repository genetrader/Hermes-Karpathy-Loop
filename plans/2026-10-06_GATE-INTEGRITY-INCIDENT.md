# KARPATHY LOOP — GATE-INTEGRITY INCIDENT + SYSTEMIC FIX (2026-10-06)

Written by Picard after the secondbrain audit. Evidence base: runner.log,
state/threads.json, live replication of runner._run_gate in real worktrees.

## The incident

secondbrain rejected rounds **r38 through r67 — thirty in a row — all with
`gate failed`**, while the children's own gate runs showed **921 passed,
EXIT=0**. Accepted work was being destroyed by containment on a false
verdict (r67's chrome-extension fix, commit a5faddc, orphaned; later
cherry-picked to audit-fixes as 379394e by Picard).

## Root causes (all REPRODUCED on 2026-10-06)

1. **Env seeding gap.** Fresh kl worktrees lack the gitignored
   `server/.env`; `test_graph_enrichment`'s job-api test then raises
   `LLMUnavailable` (openai_model empty) — the runner gate fails EVERY round
   regardless of the child's diff. Children learned the copy-the-.env
   workaround; the runner never had it.
   FIX (shipped): `_seed_env_files()` in karpathy_runner.py, called from
   `_provision_worktree_deps` — copies git-IGNORED env files only
   (check-ignore enforced), copy not junction (child must not write back
   into operator secrets).

2. **F-Z resolver trusted exists() on junctions.** With `cd server` resolving
   inside the worktree, F-Z rewrote `.venv/Scripts/python.exe` to the
   CANONICAL target — the wrong interpreter for the worktree gate. With the
   original venv broken (below) it hard-failed rc=106 "No pyvenv.cfg file".
   FIX (shipped): candidate must be `is_file()` to be rewritten; otherwise
   the token stays verbatim and cmd resolves it inside the workdir.

3. **Silent fall-through when `cd <sub>` is missing.** Old code ran the gate
   in the REPO ROOT — a gate measuring the wrong tree, indistinguishable
   from a real verdict. FIX (shipped): missing cd target returns a LOUD
   named failure.

4. **Environmental bomb.** `server/.venv` had been reduced to a broken
   Scripts-only dir (no pyvenv.cfg, uv trampolines) at 17:26 today; the
   holder was a ghost uvicorn (`app.main:app :8823`) spawned from it that
   locked the EXEs. Rebuilt the venv (`uv venv --python 3.12 .venv &&
   uv pip install -e ".[dev,connectors]"` — the `[connectors]` extra is
   REQUIRED by the gate: googleapiclient is imported by test_gmail_ingest;
   omitting it makes the gate fail at collection). Killed the ghost; the
   real server was already restored on :8823.

## Verification (measured, not claimed)

- Simulated round-69 flow: provision → junction + seeded `.env` →
  `_run_gate` → **`{"ok": true, "detail": "921 passed"}`**.
- Negative: `cd nosuchdir` gate → loud named failure, no mis-measure.
- Runner restarted at boundary with patched code; log confirms the new line:
  `tombs-and-tyrants: worktree seeded .env (from canonical, gitignored)`
  — the seeding path is LIVE on every repo.
- Worktree quarantine debris swept (zip-code-ranker r103 abandoned cleared).

## Cross-repo guard — what this means for OTHER repos

The env-seeding fix is repo-agnostic: ANY gated repo whose gate depends on a
gitignored `.env` was vulnerable to the same 100%-rejection failure mode,
silently, forever. After this fix, EVERY worktree provision now logs
`seeded <file>` lines — a repo that needs seeding and does not show it is
now visible in runner.log. Action for the rotation owner:

1. Watch the next secondbrain round through to a `kp/secondbrain/rNN` tag —
   the first published tag after r37 is the regression proof.
2. ai-consultancy (Next.js) and zip-code-ranker (npm) gates do not read .env
   today, but if a gate ever depends on one, it now gets seeded for free.
3. Acceptance-honesty invariant to keep in reviews: a gate that COULD NOT
   RUN must never be recorded as a verdict. cd-target existence, interpreter
   resolution, and venv health must be pre-flight-checked, and their failure
   modes must be NAMED (distinguishable in logs), never collapsed into
   "gate failed".
4. If a repo's dep dirs or env files live OUTSIDE the gitignore rules, the
   runner deliberately refuses to provision them (commit-leakage safety) —
   fix the repo's .gitignore, do not weaken the safety check.

## Also fixed en route

- Ghost uvicorn (locked EXE holder) killed; server .venv rebuilt healthy.
- Stray loop child processes from the paused era killed; stale kl branches
  and abandoned worktrees pruned (r68 never ran; r103 51h abandoned).
- secondbrain + zip-code-ranker quarantine flags cleared after inspection.
