# Settings Panel Plan — configure the loop from the Karpathy Loop page

Goal (the human's words): a SETTINGS CARD inside the Karpathy Loop page itself —
not Hermes' own settings — so every user can configure the loop from the UI,
and nothing remains hardcoded to the author's machine.

## Phase 1(a) — Audit: every remaining machine-specific / hardcoded thing

Found by grepping `Path(r"C:` / `C:\` / `os.environ` / hosts / ports across
`*.py`, `scripts/`, `widget/`, `*.html`, plus reading the call sites.

| # | Location | Problem | Disposition |
|---|---|---|---|
| A1 | `widget/plugin.js` L31–32 | `ROOT = "C:\\CODING\\project-improver"`, `PY = "C:\\Users\\you\\AppData\\...python.exe"` — the author's machine baked into the public widget. Every `runPy` call uses them. | **FIX**: lazy `KL_META` resolved from the status server (`GET /settings.json` → `runtime.root/python`), cached in `localStorage`, overridable in the card. No fallback author path; honest error when the server is unreachable. |
| A2 | `widget/plugin.js` L500 + `scripts/status_server.py` L92 | Status-server port `8765` hardcoded both sides. | **FIX**: new key `widget.status_port` (env `KL_STATUS_PORT`, default 8765); server binds it; widget base URL stays 127.0.0.1:port with a card-editable localStorage override. |
| A3 | `stamp_kl_titles.py` L20 | `DB = r"os.environ.get("LOCALAPPDATA"…` — a **SyntaxError**: the file cannot compile. Broken personal-path hack nobody noticed (nothing imports it). | **FIX**: derive from the settings layer (`threads.STATE_DB`, i.e. `hermes.home/state.db`). |
| A4 | `discover.py` L30–45 | Sweep roots `~/Documents, ~/coding, ~/Coding, ~/CODING` (OS-specific casing) + `KL_SWEEP_ROOTS` env only. | **FIX**: new key `projects.sweep_roots` (os.pathsep/comma list); env still wins. |
| A5 | `loopctl.py` DEFAULTS | `angles_per_visit: 2`, `sweep_minutes: 360`, `max_rounds: 0`, `max_hours: 0` hardcoded in code (loop.json wins at runtime, but a fresh deployment cannot seed them). | **FIX**: seed from new keys `runtime.angles_per_visit`, `runtime.sweep_minutes`, `runtime.max_rounds`, `runtime.max_hours`; loop.json still wins. Card edits loop.json through the server. |
| A6 | `checkpoint.py` L260/304/338 | git author identity `-c user.email=loop@local -c user.name=Karpathy Loop`. Generic (no PII) but not configurable. | **FIX**: keys `git.author_name` / `git.author_email`, defaults keep today's values. |
| A7 | `monitor.html` L45, L117 (sample data) | `D:/ZCODE/PROJECT A`, `C:\CODING\project-improver\…`, `Zip Crime Data V6`, `PROJECT B/C` — real-looking paths/names from the author's machine in a public repo. | **FIX**: genericize to `C:/code/project-a` style. |
| A8 | `METHOD.md` L151–152 | `D:/ZCODE/X` path in a war story. | **FIX**: genericize. |
| A9 | `tests/prove_repos_panel.py` L26, `tests/prove_wedge_render.py` L9 | Point at `ROOT/"review"/plugin.js` — a directory that no longer exists (file moved to `widget/`). Proofs silently dead (not pytest-collected). | **FIX**: repoint to `widget/plugin.js`. |
| A10 | `checkpoint.py` L639, `settings.py` L561 | summary model fallback `"gpt-4o-mini"` when endpoint configured but model blank. | Keep as documented generic default (no personal topology); note in README. |
| A11 | `discord_notify.py` L50 `API = https://discord.com/api/v10` | Real Discord API URL — correct to hardcode. | Keep. |
| A12 | `settings.py` `_hermes_default_home`, python default | Portable (`Path.home()`/`LOCALAPPDATA`) — already machine-neutral. | Keep. |
| A13 | `karpathy_runner.py` L65 `KL_RUNNER_LOG` | Already env-overridable. | Keep. |
| A14 | `improve.yaml` example paths | `C:/code/my-webapp` — generic. | Keep. |

No PII elsewhere: repo greps clean for usernames/emails/tokens beyond A7/A8.

## Phase 1(b) — Settings card UI (in the Karpathy Loop page)

Toggle: a **Settings** button in the header control row → `settingsModal`
(same modal shell style as the angle library). One card, five grouped sections
built from the **schema the server returns** (never a duplicated form spec in JS):

- **GitHub** — enabled toggle with the local-only mode front and center:
  *"No GitHub — local checkpoints only"*: when enabled=off, tags stay local,
  nothing is ever pushed, and **checkpoints/rollback remain fully available**
  (they are git tags; rollback never needs GitHub). Owner, token (paste →
  written to `settings.local.yaml` ONLY, displayed masked `•••• set via
  settings.local.yaml`), token_env, gh_cli path, require_repo, push_branches.
- **Models** — implementer / reviewer seats (form hint `provider:model`;
  same-seat error surfaced), builder fallback, brief model/profile, and the
  round-summary LLM (`summary_url`/`summary_model` + optional fallback pair).
- **Messaging** — backend picker `discord | none`, enabled toggle, channel id,
  ping user id, ping-on-stuck, include-summaries, bot token (masked secret),
  env_file.
- **Projects & Rotation** — manifest path, index_rows, sweep roots (add/remove
  rows), plus the live rotation values (angles/visit, sweep minutes, max
  rounds, max hours) which round-trip through the loop's `loop.json`.
- **Runtime** — round timeout, worktree abandon secs, gate timeout, git author
  name/email, status-server port (+ widget-local server base override).

Behaviour: values load from `GET /settings.json`; edits stage in a local
draft; **Save** sends only dirty fields; the server's validation issues
(reused verbatim from `settings.py`) render inline — errors red, warnings
amber — and the card stays open until the payload is clean. Secrets render
masked; typing replaces; "Clear" unsets. Source badge per field
(env / local / file / default) so a user sees why an input is inert.
Honest error states: server unreachable → named fix, never a silent blank.

## Phase 1(c) — Backend additions (scripts/status_server.py)

All writes land in **settings.local.yaml only** (the gitignored machine file);
the tracked `settings.yaml` is never written by the API.

- `GET /settings.json` → `{ schema: [{key,type,default,env,secret}], values:
  redacted_view(), sources, issues: [{level,key,message,fix}], runtime:
  {root, python} }`. Secret VALUES never appear — only `<set via …>` / null.
- `PUT /settings.json` `{values:{key:raw}}` → coerce+validate per key via the
  schema, write non-secrets to `settings.local.yaml`, return fresh redacted
  values + issues; 400 with the issue list on any validation error.
- `POST /settings/secret` `{key,value}` → key must be in `SECRET_LEAVES`;
  writes `settings.local.yaml` ONLY; response confirms `{key, set:true,
  source:"local"}` and **never echoes the value**.
- `POST /settings/secret/unset` `{key}` → remove from `settings.local.yaml`.
- `POST /rotation.json` `{angles_per_visit|sweep_minutes|max_rounds|max_hours|
  implementer|reviewer}` → validate pair-differs, write loop.json via
  loopctl's own save (same code path as `loopctl config`).
- Hardening for write endpoints (localhost server is still reachable from a
  browser via DNS rebinding): 127.0.0.1 bind (already), Host header must be
  localhost/127.*, Origin (when present) must be null/file/localhost; 256 KB
  body cap; OPTIONS preflight.
- Writes call `settings.reset_cache()` + bust the status cache so the card and
  dashboard agree immediately.

## Phase 2 — Build order

1. `settings.py`: new SCHEMA keys (A2,A4,A5,A6) + defaults + validation +
   accessors + example-text lines.
2. `scripts/status_server.py`: the four endpoints + hardening + meta.
3. `widget/plugin.js`: lazy ROOT/PY meta, Settings modal + sections, save /
   validate / secret flows, honest error states; drop A1 constants.
4. Scrub A3 (stamp_kl_titles), A7 (monitor.html), A8 (METHOD.md), A9 (prove_*).
5. Tests: `tests/test_settings_api.py` (endpoint contract, secret never
   echoed, local-only writes, validation round-trip, Origin guard),
   plugin import-check under node with react/SDK stubs, settings round-trip
   for the new keys. Keep the 295-test suite green; `node --check` +
   `node import()` clean; README "Configuring from the UI" section; push.

## Definition of done

- Fresh clone on any machine: card opens (given the status server), everything
  editable incl. GitHub token paste → local-only file, validation shown live,
  local-checkpoint mode copy emphasizes rollback still works.
- `grep -rn "project-improver\|ZCODE" *.py widget scripts tests *.html *.md` →
  clean.
- Full pytest green; plugin imports under node; pushed to master and verified.
