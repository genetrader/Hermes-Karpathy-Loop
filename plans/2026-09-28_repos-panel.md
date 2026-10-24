# Plan: Repos panel + "See it" column (rebuild, staged)

**Created:** 2026-09-28
**Status:** IN PROGRESS — loop PAUSED at 17:49 UTC
**Why this plan exists:** the first attempt was one bulk insert into a 1,496-line
file. It shipped a hard syntax error (`children:` outside the props object) and
took the whole plugin down. Bulk-inserting derived code into a file whose
conventions I had not verified was the mistake. This plan verifies first, builds
in small checkable pieces, and has an independent agent check each piece.

---

## What we are building (and why)

Gene's ask (2026-09-28, verbatim): *"I want to see the work in progress... a
clickable URL to see the project or a clickable command to run the project... it
would have to tell me if the server is running."*

Two additions to the widget:

- **Repos panel** — per repo: what it IS (one-time brief), and a link to view it.
- **See it column** — in the existing projects table: a clickable URL, or an
  honest "—" when the repo declares no viewable surface.

Already built and verified in the BACKEND (do not rebuild):
`repo_survey.py`, `repo_brief.py`, `round_evidence.py`, `see_it.py`,
`see:` blocks in `improve.yaml`, `_see_for()` in `activity.py`.

---

## Hard constraints (locked by Gene)

1. **One-time brief per repo** — never regenerated per round.
2. **Command execution stays OUT of the widget.** The widget displays the start
   command as text; it never runs it. `see_it.py` has no execution path.
3. **Payload budget** — bridge clips ≈4,000 B; producer guards to 3400 B.
   Briefs are ~3 KB each, so they are fetched ON DEMAND, never in the poll.
4. Loop stays PAUSED for the duration of the build.

---

## Verified conventions (from WIDGET-CONVENTIONS.md — read it, don't guess)

- `children` goes INSIDE the props object.
- `jsxs(tag, {props, children: [...]})` for arrays; `jsx(tag, {props, children: x})`
  for one child. Third positional arg is the **key**, not children.
- Only three local components exist: `Panel` (815), `Chip` (827), `KarpathyLoop` (1002).
- `Button` is imported from `@hermes/plugin-sdk`.
- Style tokens: `S` (line 546), `T` (line 532), CSS vars `--foreground`,
  `--muted-foreground`, `--card`, `--border`, `--accent`.

---

## Method (six stages, each gated)

### S1 — Review (agents running)
Two independent reviewers, spawned 2026-09-28:
- **Agent A** reviews `review/plugin.js` for latent syntax/structural hazards.
- **Agent B** reviews the Python backend AND independently verifies the
  no-execution claim in `see_it.py` by grep.

Gate: findings read and triaged before any code is written.

### S2 — Freeze a known-good baseline
- The current `review/plugin.js` (MD5 `547b9062f393ef890f58d34e26cd2703`) is the
  baseline. Copy it to `review/plugin.baseline.js` before editing.
- Record MD5 so any regression is provable.

### S3 — Build in PIECES, checking after each
Each piece is a separate edit, each followed by the gate (`tests/gate.sh`):

- **P1** — add the `see` column header to the projects table. **DONE**
- **P2** — add the `see` cell to each row (link or honest "—"). **DONE**
- **P3** — define the `reposPanel` component (self-contained, no data fetch). **DONE**
- **P4** — render `reposPanel` in the page, below the projects table. **DONE**
- **P5** — add the on-demand brief fetch (`useRepoBriefs`). **DONE**

Gate per piece: `bash tests/gate.sh review/plugin.js` must print GATE PASS (rc=0)
AND `diff` against the baseline must show only the intended lines changed.

**CORRECTED 2026-09-28 — the original gate said `node --check`, which is the check
that FAILED THREE TIMES.** Measured on one file with the defect injected:

| Check | Good file | Broken file | Catches it? |
|---|---|---|---|
| `node --check f.js` | exit 0 | **exit 0, silent** | **NO** |
| `node --check f.mjs` (copy) | exit 0 | exit 1 + line | yes |
| `node --input-type=module --check < f.js` | exit 0 | exit 1 + line | **yes — used** |

As CommonJS the top-level imports are the first error and node reports-and-
continues past them, never reaching the real defect; the host loads the file as
an ES module. The gate now runs six checks: ESM parse, the loader's bare-specifier
scanner, a balanced-brace structural scan for `children`-outside-props, a raw-JSX
grep, a style-token declaration check, and the MD5.

### S4 — Install
Copy `review/plugin.js` → the installed path; verify `diff -q` identical.

### S5 — Independent verification (different agent from the builder)
A reviewer agent, given ONLY the new file and the conventions doc, must:
- run `node --check` and report rc;
- confirm every `jsx`/`jsxs` call has `children` inside its props object;
- confirm `see_it.py` still has zero execution paths;
- confirm the payload is still under budget via `activity.py --selfcheck`.
A clean self-report is NOT sufficient; this must be run and quoted.

### S6 — Report
Tell Gene: what changed, the verification output, and the exact next user action
(⌘K → "Reload desktop plugins").

---

## Rollback

`review/plugin.baseline.js` (MD5 recorded in S2) restores the exact known-good
state in one `cp`. The previously-broken copy is preserved at
`desktop-plugins/karpathy-loop/plugin.js.broken-seeit-20260928`.

---

## Open question for Gene (not blocking the build)

Should SecondBrain's `see:` be split into separate **Android** and
**Chrome-extension** rows, so a server-only round cannot read as Android
progress? The brief shows three surfaces (`server`, `android`,
`chrome-extension`), so this is a real ambiguity in the current single-row model.

---

# BUILD COMPLETE — 2026-09-28

**Installed MD5 `23daf28be641f64984895e43bd429cf7`** (baseline was `547b9062…`).
Both dev and installed copies gated identically. Loop left PAUSED.

## What shipped

| Piece | State |
|---|---|
| P1 "See it" column header | DONE |
| P2 "See it" cell (link, or an honest reason) | DONE |
| P3 `reposPanel` component | DONE |
| P4 render below the projects table | DONE |
| P5 `useRepoBriefs` on-demand fetch | DONE |

## Verification actually run

- `bash tests/gate.sh review/plugin.js` → **GATE PASS**, 6/6 checks, on BOTH the
  dev copy and the installed copy.
- `python tests/prove_repos_panel.py` → **19 passed, 0 failed**.
- `python tests/prove_wedge_render.py` → wedge path still correct (PAUSED).
- `py_compile` on all 8 Python modules → ok. Three orchestrator test files → rc=0.
- `activity.py --selfcheck` → `payload 3297 B | rows 4` / SELFCHECK OK.

## Defects found and fixed during the build (all were invisible to a parse)

1. **`T.idleBd` and `S.mutedNote` were referenced but never declared** (pre-existing).
   An undeclared style ref is not a parse or load error — it renders as literal
   `undefined`, which React DROPS. The idle status Chip had been rendering with
   **no border**, silently. Found by the new gate check `[5]`.
2. **The brief contract was wrong.** The panel read `brief.summary || brief.purpose`;
   the real brief files use `what_it_is` / `supposed_to_do`. All four repos would
   have shown "no description yet" while complete briefs sat on disk. Caught by
   `prove_repos_panel.py` checking the REAL files instead of an assumption.
3. **`see.url` flowed into `href` unvalidated** (reviewer finding, re-verified by
   me at both sites). A `javascript:` URL in `improve.yaml` would render a live
   link in the desktop renderer. Fixed with a `safeUrl()` http/https allowlist;
   a refused URL now says "not linkable" rather than silently vanishing.
4. **A brief-read failure was indistinguishable from "nothing written yet"**
   (reviewer finding). The old fallback told the operator to run `repo_brief.py`
   even when a brief existed and the read had failed — a false errand. Now says
   "could not read the description: …".
5. **The structure gate could not catch `children` as a 4th ARGUMENT** — the exact
   historical breaker. Measured: `jsx(t,{..},"k",children)` passes both the ESM
   parse and the old scan. `check_structure.js` now also **counts arguments** and
   fails anything over three. All four defect shapes now caught.

## Correction to the reviewer's report

The reviewer stated the four briefs use `what_it_is`/`supposed_to_do` and that the
widget "already reads the real key" — that was true only AFTER fix #2 above, which
I made from my own render proof before the reviewer's report arrived. The
reviewer's claim that the task premise was "stale" was itself partly stale.