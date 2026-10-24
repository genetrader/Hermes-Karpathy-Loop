# Karpathy Loop — Repos Panel Rebuild: BUILD LOG

Running log so a resumed session knows exactly where this stands.
Plan: `plans/2026-09-28_repos-panel.md`

---

## State at handoff (2026-09-28, ~13:52 local)

| Item | State |
|---|---|
| Loop | **PAUSED** 17:49 UTC, "paused by user" — stays paused for the build |
| Baseline | `review/plugin.baseline.js` frozen, MD5 `547b9062f393ef890f58d34e26cd2703`, parses OK |
| Installed widget | == baseline (restored, verified identical) |
| Broken copy kept | `desktop-plugins/karpathy-loop/plugin.js.broken-seeit-20260928` |
| Conventions doc | `WIDGET-CONVENTIONS.md` (verified from source, not guessed) |
| Reviewers | deleg_91c3c9f6 — 2 agents running (widget review + backend/security review) |

### Already DONE in the backend — do NOT rebuild
- `repo_survey.py` — deterministic repo facts (asset dirs skipped)
- `repo_brief.py` — 4/4 one-time briefs in `state/repos/*.json`
- `round_evidence.py` — wired into `karpathy_runner.py` after each round
- `see_it.py` — read-only probes; verified zero execution paths
- `see:` blocks in `improve.yaml` for all 4 repos
- `_see_for()` in `activity.py` — ships the small `see` descriptor per row
- `loopctl.py` — heartbeat PID liveness (`[alive]` / `[process GONE - stale]`)

### STILL TO BUILD (the widget only)
- **P1** `see` column header in the projects table
- **P2** `see` cell per row (clickable URL, or honest "—")
- **P3** `reposPanel` component definition
- **P4** render `reposPanel` below the projects table
- **P5** on-demand brief fetch (`useRepoExtras`)

Each piece: edit → `node --check` → verify no `children:` outside props → diff vs
baseline shows only intended lines.

---

## THE BUG THAT CAUSED THIS REBUILD (do not repeat)

```js
jsx("div", { style: S.grid }, children: [ ... ])          // ✗ SYNTAX ERROR
jsxs("div", { style: S.grid, children: [ ... ] })         // ✓ correct
```

`children` belongs INSIDE the props object. `jsxs` for arrays, `jsx` for one child.
Third positional arg is the **key**. Five call sites had this error; the whole
plugin failed to load with "missing ) after argument list".

Root cause of the original failure: one bulk insert of ~130 lines into a
1,496-line file whose conventions I had NOT verified first. Hence: verify, then
small pieces, each checked.

---

## Verified widget conventions (full detail in WIDGET-CONVENTIONS.md)

- Imports: `jsx, jsxs` from `react/jsx-runtime`; `Button, host, useValue` + 3
  `*_AREA` consts from `@hermes/plugin-sdk`.
- Local components: `Panel` (815), `Chip` (827), `KarpathyLoop` (1002). No other.
- `Panel` props: `title`, `note?`, `right?`, `children`.
- `Chip` props: `tone` (`live`|`wait`|`err`|undefined), `children`.
- Style tokens `S` (546), `T` (532); CSS vars `--foreground`,
  `--muted-foreground`, `--card`, `--border`, `--accent`.
- Bridge: `host.requestProfile(route, "shell.exec", { command })`; read `r.value`
  (fall back to `r.output`). Bridge clips ≈4,000 B; producer guards 3400 B.
- Poll contract — rows: `name, running, rounds, active_task, never_rotated,
  elapsed, last_rc, last_error, see`; cot: `steps` (accepts `items`),
  `steps_total`, `log_age_s`, `angle`, `running`, `task`.

---

## Open question for Gene (non-blocking)

Split SecondBrain's `see:` into separate Android / Chrome-extension rows so a
server-only round cannot read as Android progress? The brief lists three surfaces
(`server`, `android`, `chrome-extension`).

---

## Next action when resuming

1. Read the two reviewers' findings; triage before writing code.
2. Build P1 → P5 one at a time, checking after each.
3. Install, verify `diff -q` identical.
4. Independent verifier agent re-checks the finished file (different agent from
   the builder) and QUOTES real command output — a self-report is not sufficient.
5. Tell Gene to ⌘K → "Reload desktop plugins".