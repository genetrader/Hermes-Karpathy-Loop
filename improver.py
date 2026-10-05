#!/usr/bin/env python
"""
project-improver / improver.py — the ROTATOR

Walks the enabled projects in improve.yaml on a schedule. For each project whose
turn is up, it creates ONE goal-mode kanban card (a full OS process that survives
gateway restarts), then waits for that card to finish before advancing.

It does NOT implement the loop. Hermes ships the loop
(`hermes kanban create --goal --goal-max-turns N`). This file only decides
WHICH project gets a card and WHEN.

Usage:
  python improver.py status      show rotation state
  python improver.py run         one rotation pass (call from cron)
  python improver.py plan        show what run WOULD do, create nothing
  python improver.py init        create boards for every enabled project
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import discord_notify as dn
import angle_pick as ap
import loopctl

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state" / "rotation.json"
MANIFEST = ROOT / "improve.yaml"
# Where kanban boards live. Each board dir holds board.json (slug, name,
# default_workdir) next to its kanban.db. A card created on a board with no
# default_workdir can never start -- see board_workdir().
BOARD_HOME = Path(r"C:\Users\gene\AppData\Local\hermes\kanban\boards")
HERMES_PY = Path(r"C:\Users\gene\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe")
HERMES_MAIN = Path(r"C:\Users\gene\AppData\Local\hermes\hermes-agent\hermes_cli\main.py")


# ---------------------------------------------------------------- manifest

def load_manifest() -> dict:
    import yaml
    if not MANIFEST.exists():
        raise SystemExit(f"no manifest at {MANIFEST} — run pick.py then edit improve.yaml")
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}


def enabled(m: dict) -> list[dict]:
    return [p for p in (m.get("projects") or []) if p.get("enabled")]


def wanted_names(lc: dict) -> list[str]:
    return [str(x).strip() for x in (lc.get("projects") or []) if str(x).strip()]


def check_selection(m: dict, lc: dict) -> list[str]:
    """Names selected in loop.json that are NOT in the manifest.

    The rotator reads improve.yaml, so a name that only exists in loop.json
    is silently un-runnable -- which is exactly how 'I picked 8 repos and it
    worked on 1' happened. Return them so the caller can shout instead of
    rotating past them.
    """
    have = {p.get("name") for p in (m.get("projects") or []) if p.get("name")}
    return [n for n in wanted_names(lc) if n not in have]


def upsert_missing(m: dict, lc: dict) -> list[str]:
    """Create a minimal manifest entry for every loop.json name the manifest lacks.

    The picker policy is: the picker writes improve.yaml and enables/disables --
    objectives are written later in the UI. But a picker save can leave a name in
    loop.json with no manifest block (the manifest half of the save failed), and
    the rotator -- which reads ONLY improve.yaml -- then never gives that repo a
    turn (the 2026-09-26 project-d bug). Rather than shout and skip,
    upsert the missing names so the selection always rotates. Existing blocks and
    hand-written objectives are never touched.

    Returns the names it created (empty on the happy path)."""
    orphans = check_selection(m, lc)
    if not orphans:
        return []
    import yaml
    m.setdefault("projects", [])
    for n in orphans:
        m["projects"].append({
            "name": n,
            "path": "",
            "board": n,
            "loops": 1,
            "objective": f"TODO: objective not set for {n} — add it before starting.",
            "enabled": True,
        })
    try:
        doc = load_manifest()
        doc["projects"] = m["projects"]
        text = MANIFEST.read_text(encoding="utf-8")
        head = text.split("projects:", 1)[0]
        body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                              default_flow_style=False, width=100)
        MANIFEST.write_text(head + "projects:\n\n" + body.split("projects:", 1)[-1].lstrip("\n"),
                            encoding="utf-8")
    except Exception as e:
        print(f"WARN: could not write manifest for {orphans}: {e!r}")
    return orphans


# ---------------------------------------------------------------- hermes cli

def hermes(*args, timeout: int = 180) -> tuple[int, str]:
    """
    Run the hermes CLI. cwd must be a plain Windows dir: MSYS rewrites
    /c/... style paths when it launches a native .exe, which corrupts the
    script argument. Passing a native path + explicit cwd avoids that.
    """
    cmd = [str(HERMES_PY), str(HERMES_MAIN), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           cwd=str(HERMES_MAIN.parent.parent))
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def board_exists(slug: str) -> bool:
    """
    Filesystem check, not a subprocess.

    This used to shell out to `hermes kanban boards list` -- a full interpreter
    start (up to a 180s timeout) per board per rotation pass, just to answer
    "does this directory exist". With 8 projects that is 8+ needless process
    spawns per pass, any of which could hang the whole rotation.

    A board exists iff BOARD_HOME/<slug>/board.json is there -- the same test
    board_workdir() already relies on.
    """
    return (BOARD_HOME / slug / "board.json").exists()


def create_board(slug: str, workdir: str) -> tuple[int, str]:
    # Check the filesystem first so we never parse CLI prose to decide
    # idempotency. The old `"exist" in out.lower()` match could swallow a real
    # failure whose message merely contained the word.
    if board_exists(slug):
        rc, out = hermes("kanban", "boards", "set-default-workdir", slug, workdir)
        return rc, out
    rc, out = hermes("kanban", "boards", "create", slug, "--name", slug, "--json")
    if rc != 0 and not board_exists(slug):
        return rc, out
    rc2, out2 = hermes("kanban", "boards", "set-default-workdir", slug, workdir)
    return rc2, out2


def board_workdir(slug: str):
    """
    Read a board's default_workdir from its board.json.

    Returns None when the board does not exist or has no workdir set. A card on
    such a board can never start (the worker has nowhere to open a worktree), so
    callers use this to self-heal before creating work.
    """
    import json as _json
    p = BOARD_HOME / slug / "board.json"
    try:
        return _json.loads(p.read_text(encoding="utf-8")).get("default_workdir")
    except Exception:
        return None


def _first_json(out: str):
    """Extract the FIRST complete JSON value from CLI output.

    The CLI may print warning lines before the payload AND a second JSON
    document after it ("Extra data: line N column 1"). json.loads() on the
    whole remainder therefore fails and the caller silently sees zero rows —
    which made `card_running()` always False and would let the rotator stack
    duplicate cards for a project that already had one in flight.
    Use raw_decode so only the first complete value is consumed.
    """
    dec = json.JSONDecoder()
    for i, ch in enumerate(out):
        if ch in "[{":
            try:
                value, _ = dec.raw_decode(out[i:])
                return value
            except Exception:
                continue
    return None


def list_tasks(board: str) -> list[dict]:
    """Return open (non-done, non-archived) tasks on a board."""
    rc, out = hermes("kanban", "--board", board, "list", "--json")
    if rc != 0 or not out.strip():
        return []
    data = _first_json(out)
    if data is None:
        return []
    if isinstance(data, dict):
        data = data.get("tasks") or data.get("rows") or []
    return [t for t in data if isinstance(t, dict)
            and str(t.get("status", "")).lower() not in {"done", "archived"}]


def card_running(board: str) -> bool:
    """
    True only if a turn is genuinely IN FLIGHT on this board.

    ONLY `running` counts. Everything else is absence of work, not work:

    * `blocked` -- no live worker: waiting on a human answer, or killed mid-run
      (the 2026-09-22 card that hit its turn cap exited rc=1 and was marked
      `superseded`, yet still counted as open). Treating it as in-flight froze
      the rotation -- T&T held the turn ~5.4h while the other two never ran.
    * `ready` / `todo` -- not started. If the dispatcher is wedged, or the
      board has no workdir, the card sits there FOREVER and a queued card
      would jam the rotation permanently.
    * `review` -- waiting on a human, exactly like `blocked`.

    A `running` card also goes stale: a worker killed mid-run can leave
    status=`running` in the DB with no process behind it. In that case the
    card's age is checked against STALE_LOCK_S and the turn is released.
    """
    for t in list_tasks(board):
        st = str(t.get("status", "")).lower()
        if st != "running":
            continue
        started = t.get("started_at") or t.get("updated_at") or 0
        try:
            age = time.time() - float(started)
            if started and age > STALE_LOCK_S:
                # A running card older than the staleness window has no worker
                # behind it. Record it so the caller can report the wedge, but
                # do NOT hold the turn.
                continue
        except (TypeError, ValueError):
            pass
        return True
    return False


def zombie_card(board: str) -> dict | None:
    """A card stuck in `running` past STALE_LOCK_S -- a worker that died without
    releasing the row. Returned so the caller can archive it and say so."""
    for t in list_tasks(board):
        if str(t.get("status", "")).lower() != "running":
            continue
        started = t.get("started_at") or t.get("updated_at") or 0
        try:
            if started and (time.time() - float(started)) > STALE_LOCK_S:
                return t
        except (TypeError, ValueError):
            pass
    return None


def stuck_card(board: str) -> dict | None:
    """
    The open `blocked` card on this board, if any -- i.e. a card that will never
    finish on its own and is holding the rotation hostage.

    The rotator cannot clear it automatically (a needs_input block may be waiting
    on Gene), but it must not let it stall everyone else either. Callers use this
    to report the stall and move on.
    """
    for t in list_tasks(board):
        if str(t.get("status", "")).lower() == "blocked":
            return t
    return None


# ------------------------------------------------------------------ budgeting

# Measured cost of ONE real round, from the worker logs:
#   t_8e7a4676 (1 round, rc=0) -> 317 tool calls, 1h07m
#   t_42fa5ba4 (3 rounds, rc=1) -> 513 tool calls, killed at a 250 cap
# A round is ~300+ turns of investigate/verify/commit. Budget for the rounds we
# ask for PLUS wrap-up headroom, and never below the floor -- the old code set
# this from `loops`, which produced a 24-turn budget that could not cover even
# one round.
TURNS_PER_ROUND = 320
WRAPUP_TURNS = 120          # final gate run, commit, tag, checkpoint, report
MIN_GOAL_TURNS = 800        # one full round + wrap-up must always fit

# A rotator lock older than the longest plausible card runtime is treated as
# stale (dead process that never cleaned up). --max-runtime is 12h; allow margin.
STALE_LOCK_S = 13 * 3600


def goal_turns(loops: int) -> int:
    """
    Turn budget for a card doing `loops` rounds of real work.

    One round measured at ~317 tool calls, so per-round cost dominates. The
    floor (MIN_GOAL_TURNS) only guards the degenerate 1-round case: with the
    measured constants, `2 * 320 + 120 = 760` falls BELOW the 800 floor, which
    would silently hand a 2-round card less than one round's worth of headroom.
    So the floor is applied to the WRAP-UP allowance instead of the total --
    every round gets its full measured budget, plus a floor-sized tail.
    """
    try:
        n = max(1, int(loops))
    except (TypeError, ValueError):
        n = 1
    return n * TURNS_PER_ROUND + max(WRAPUP_TURNS, MIN_GOAL_TURNS - TURNS_PER_ROUND)


def create_card(proj: dict, loops: int, stage: dict | None = None,
                angle: dict | None = None) -> tuple[int, str]:
    """
    Create the goal-mode card that IS the iteration loop.

    Two modes:
      * `angle` given -> ROUND MODE. The body is ONE ANGLE (a lens) to apply to
        this repo, pulled at random by angle_pick.py. This is the steady state of
        the Karpathy Loop: every round is a fresh angle, not the next step of a
        fixed script.
      * `stage` given -> METHOD MODE. The body is the STAGE prompt from method.py,
        so the worker follows the six-stage process. Used once per project to
        establish its baseline and backlog.
    """
    board = proj["board"]

    # Self-heal a missing/misconfigured board BEFORE creating a card.
    #
    # The rotator used to create a card on a board it had never initialised, and
    # the worker then died with "has workspace_kind=worktree but no
    # workspace_path, and board has no default_workdir set" -- a card that could
    # never run, on a board that looked fine in `boards list`. Ensure the board
    # exists and carries the project's path every time.
    if not board_exists(board) or board_workdir(board) in (None, ""):
        rc_b, out_b = create_board(board, proj["path"].replace("\\", "/"))
        print(f"board '{board}' initialised (workdir {proj['path']}) rc={rc_b}")
        if rc_b != 0:
            return rc_b, out_b

    if angle:
        title = f"{proj['name']}: angle {angle['angle']} ({angle['family']}) - {loops} loops"
        lookfor = "\n".join(f"  - {x}" for x in angle.get("look_for", []))
        body = f"""Objective:
{proj.get('objective', '').strip()}

Acceptance criteria (the judge only sees this):
{proj.get('acceptance', '').strip()}

Boundaries:
{proj.get('boundary', 'This repo only.')}

Gate: {proj.get('gate') or '(none — no test command for this project)'}

=== THIS ROUND'S ANGLE: {angle['angle']} ({angle['family']}) ===
LENS: {angle.get('lens', '')}

LOOK FOR:
{lookfor}

EVIDENCE YOU MUST PRODUCE (this is how the round is judged):
{angle.get('evidence_required', '')}

Apply ONLY this lens this round. Do not drift into unrelated cleanup - another
round will carry another angle. If, after real investigation, this angle
genuinely does not apply to this repo, say so with the evidence that convinced
you and stop; a clean "not applicable, here is why" is a valid round outcome
and is better than inventing work.

Loop contract:
- Iterate up to {loops} rounds. Each round: change code -> run the gate -> if it
  fails, repair and retry; if it passes, commit a checkpoint.
- ONE finding per loop. Do not batch multiple findings into one unverified commit.
- Commit EVERY passing round as its own commit on this task's branch.
- Tag each accepted round:  git tag kp/{proj['name']}/<round>/{angle['angle']}

WRAP-UP (mandatory, and it comes BEFORE the budget runs out):
- Watch your turn budget. When you have used roughly 70% of it, STOP starting new
  work and wrap up: run the gate one final time, commit whatever PASSES, tag it
  with the next round number, and write your summary.
- A round that ends complete is worth far more than one that ends at the ceiling.
  The previous card hit its cap mid-round, exited rc=1, and left the rotation
  frozen -- do not repeat that. Always leave the branch consistent and the gate
  green before you finish, even if that means reporting fewer rounds than asked.
- If you hit a decision only Gene can make, STOP and post ONE question
  (one question at a time, never two) via: python C:\\CODING\\project-improver\\discord_notify.py ask "{proj['name']}" "<question>"
  then call kanban_block with kind=needs_input. Resume when unblocked.
- Report each round with:
  python C:\\CODING\\project-improver\\discord_notify.py progress "{proj['name']}" "round N/{loops}: angle {angle['angle']} - <what changed>, gate <pass/fail>, commit <hash>"
- Record the angle outcome when done:
  python C:\\CODING\\project-improver\\angle_pick.py mark --angle {angle['angle']} --project {proj['name']} --family {angle['family']} --result accepted
- Do NOT merge to main. Do NOT push to a shared branch. Checkpoint branches only.
"""
    else:
        code, key, what, art, instruction = stage if stage else ("S4", "iterate", "", "", "")
        title = f"{proj['name']}: {code} {key} ({loops} loops)"
        body = f"""Objective:
{proj.get('objective', '').strip()}

Acceptance criteria (the judge only sees this):
{proj.get('acceptance', '').strip()}

Boundaries:
{proj.get('boundary', 'This repo only.')}

Gate: {proj.get('gate') or '(none — no test command for this project)'}

THIS STAGE: {code} {key} — {what}
Artifact to write: state/plan/{proj['name']}/{code}-{key}.md
{instruction}

Loop contract:
- Iterate up to {loops} rounds. Each round: change code -> run the gate -> if it
  fails, repair and retry; if it passes, commit a checkpoint.
- ONE backlog item per loop. Do not batch multiple items into one unverified commit.
- Commit EVERY passing round as its own commit on this task's branch.
- If you hit a decision only Gene can make, STOP and post ONE question
  (one question at a time, never two) via: python C:\\CODING\\project-improver\\discord_notify.py ask "{proj['name']}" "<question>"
  then call kanban_block with kind=needs_input. Resume when unblocked.
- Report each round with:
  python C:\\CODING\\project-improver\\discord_notify.py progress "{proj['name']}" "round N/{loops}: <what changed>, gate <pass/fail>, commit <hash>"
- When the stage is finished, record it:
  python C:\\CODING\\project-improver\\method.py advance "{proj['name']}" {key} ok
- Do NOT merge to main. Do NOT push to a shared branch. Checkpoint branches only.
"""
    args = [
            "kanban", "--board", board, "create", title,
            "--body", body,
            "--workspace", "worktree",
            # Turn budget is NOT the loop count. Measured on 2026-09-22: one round of
            # real work costs ~317 tool calls, so a 24-turn budget was never enough
            # and a --max-runtime cap killed the card at 250/250 (rc=1, superseded).
            # Size the budget from the WORK, with margin for wrap-up, and never let
            # it go below the floor.
            "--goal", "--goal-max-turns", str(goal_turns(loops)),
            "--max-runtime", "12h",
            "--skill", "kanban-worker",
            "--assignee", proj.get("assignee") or "improver",
            "--json",
        ]
    if proj.get("gate"):
        # completion_contract accepts only: local-only | OWNER/REPO | PR URL.
        # The gate command belongs in the BODY (the worker reads it there).
        args += ["--completion-contract", "local-only"]
    if proj.get("implementer"):
        prov, _, mdl = str(proj["implementer"]).partition(":")
        if mdl:
            args += ["--provider", prov, "--model", mdl]
    return hermes(*args, timeout=240)


# ---------------------------------------------------------------- rotation

def repo_facts(proj: dict) -> dict:
    """
    Cheap repo facts used by angle_pick's avoid_when gates, so an angle that
    cannot apply (e.g. gameplay angles on a website) never burns a round.
    Deliberately shallow: a few directory reads, no language server.
    """
    facts: dict = {}
    root = Path(proj.get("path") or "")
    try:
        if not root.is_dir():
            return facts
        langs = {"py": 0, "js": 0, "ts": 0, "php": 0, "rb": 0, "cs": 0, "java": 0}
        files = tests = 0
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            n = f.name.lower()
            if any(s in f.parts for s in (".git", "node_modules", ".venv", "venv", "__pycache__")):
                continue
            files += 1
            if "test" in n or "spec" in n:
                tests += 1
            for ext in langs:
                if n.endswith("." + ext):
                    langs[ext] += 1
        facts["file_count"] = files
        facts["test_files"] = tests
        top = max(langs, key=lambda k: langs[k])
        facts["language"] = {"py": "python", "js": "javascript", "ts": "typescript",
                             "php": "php", "rb": "ruby", "cs": "csharp",
                             "java": "java"}.get(top, "unknown")
        facts["has_tests"] = tests > 0
        facts["has_dependency_manifest"] = any(
            (root / n).exists() for n in
            ("requirements.txt", "pyproject.toml", "package.json", "composer.json",
             "Gemfile", "go.mod", "Cargo.toml"))
        facts["has_architecture_doc"] = any(
            (root / n).exists() for n in ("ARCHITECTURE.md", "docs/ARCHITECTURE.md"))
        # heuristics for the remaining gates
        name = (proj.get("name") or "").lower()
        facts["is_game"] = "game" in name or "tyrants" in name or "minecraft" in name
        facts["has_ui"] = any(
            (root / n).exists() for n in ("index.html", "static", "templates", "ui", "frontend"))
        facts["is_service"] = (root / "server.py").exists() or (root / "app.py").exists()
        facts["is_library"] = (root / "setup.py").exists() and not facts["is_service"]
    except Exception:
        pass
    return facts


def surfaces_for(proj: dict) -> list[str]:
    """
    Canonical surface slugs for a project, from the manifest (Gene, 2026-09-28:
    the manifest is authoritative -- anything the rotation consumes must live in
    improve.yaml or it breaks "the runner reads improve.yaml only").

    Accepts BOTH declared shapes so S2's richer form can land without a flag day:
      * legacy flat string  :  surfaces: server, android, chrome-extension
                              (improve.yaml:109 today -- pure documentation
                              until S1, which is exactly why it drifted)
      * object list         :  surfaces: [{name: server, kind: server, where: ...}]
                              (the shape repo_brief.py already emits)

    Returns [] when the project declares no surfaces. Callers MUST treat empty
    as single-surface/no-op so every current project behaves exactly as before.
    Never raises: a malformed entry is skipped, not fatal -- a manifest typo
    must not take down a round (same convention as repo_facts above).
    """
    out: list[str] = []
    try:
        raw = (proj or {}).get("see", {}).get("surfaces")
    except AttributeError:
        # `see` declared as a non-dict (scalar/None) -- same as absent
        return []
    if not raw:
        return []
    if isinstance(raw, str):
        items = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        items = []
        for e in raw:
            if isinstance(e, dict):
                # object entry: take `name`, falling back to `id`/`slug` so a
                # renamed key degrades to "no surfaces" instead of a crash
                items.append(e.get("name") or e.get("id") or e.get("slug") or "")
            else:
                items.append(str(e))
    else:
        return []
    for it in items:
        slug = str(it).strip().lower().replace(" ", "-")
        if slug and slug not in out:
            out.append(slug)          # de-dupe, order preserved
    return out


def surface_see_for(proj: dict) -> dict:
    """
    Per-surface viewability, from the manifest's `see.surface_see` map.

    Gene, 2026-09-28: "each surface can be looked at." The surfaces of one repo
    are NOT equally viewable -- project-b's web server answers on :8823, its
    web app is a page INSIDE that server, its Chrome extension is not
    URL-addressable at all, and its two Android apps are device installs. One
    project-level `see:` block cannot say that, so each surface may carry its own.

    Shape (every key optional)::

        see:
          url: http://127.0.0.1:8823/health      # project-level default
          surface_see:
            server:
              url: http://127.0.0.1:8823/
              note: the web app is a page served by the server itself
            android-assistant:
              where: android/assistant/
              start: cd ... && ./gradlew :assistant:installDebug
            chrome-extension:
              where: chrome-extension/
              probe: none        # not URL-addressable -> honest NA, not "down"

    Returns {} when nothing is declared, so callers fall back to the
    project-level block and every single-surface repo behaves as it does today.
    Never raises.
    """
    try:
        raw = (proj or {}).get("see", {}).get("surface_see")
    except AttributeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for k, v in raw.items():
        slug = str(k).strip().lower().replace(" ", "-")
        if not slug:
            continue
        if isinstance(v, dict):
            # keep only the keys the prompt/widget actually render, and clip so a
            # pasted megabyte of YAML cannot bloat a round prompt
            out[slug] = {kk: str(v[kk])[:400] for kk in
                         ("url", "where", "note", "start", "probe", "kind", "label")
                         if v.get(kk)}
        else:
            out[slug] = {"where": str(v)[:400]}
    return out


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"order": [], "idx": 0, "history": []}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def cmd_init():
    m = load_manifest()
    for p in enabled(m):
        exists = board_exists(p["board"])
        rc, out = create_board(p["board"], p["path"].replace("\\", "/"))
        dn.progress(p["name"], f"board `{p['board']}` {'already existed' if exists else 'created'} "
                               f"(default workdir {p['path']})")
        print(f"{p['name']:28} board={p['board']:24} {'exists' if exists else 'CREATED'} rc={rc}")
    print("boards done")


def cmd_plan():
    m = load_manifest()
    s = load_state()
    projs = enabled(m)
    if not projs:
        print("no enabled projects — edit improve.yaml")
        return
    for i, p in enumerate(projs):
        running = card_running(p["board"])
        print(f"[{i}] {p['name']:28} board={p['board']:22} loops={p.get('loops', 5):3} "
              f"gate={'yes' if p.get('gate') else 'NO':3} running={running}")
    print(f"\nstate: idx={s.get('idx', 0)} history={len(s.get('history', []))}")
    nxt = projs[s.get("idx", 0) % len(projs)]
    print(f"next up: {nxt['name']}")


def next_stage(proj_name: str):
    """
    Which stage is this project on? Drives the rotation off the process rather
    than iterating blindly. Falls back to S4 if method.py is unavailable.
    """
    try:
        import method
    except Exception:
        return ("S4", "iterate", "Run the loops.", "", "Work the backlog top-down.")
    plan = method.load_plan().get(proj_name) or {}
    done = plan.get("done") or {}
    for stage in method.STAGES:
        if stage[1] not in done:
            return stage
    # all six stages recorded -> new cycle, back to scout with fresh eyes
    return method.STAGES[0]


def cmd_run():
    # RETIRED 2026-09-23 (Task 11 of the embedded-threads rebuild): kanban-card
    # workers are replaced by the continuous runner (karpathy_runner.py) with
    # per-round disposable worktrees. T2-7 (2026-09-30): the pointer below
    # used to name karpathy_nudge.py, which is itself retired now -- a stale
    # reader would chase a moved file.
    print("retired: kanban-card workers are gone; the continuous runner (karpathy_runner.py) owns rounds")
    return 2


def _cmd_run_retired():
    m = load_manifest()
    s = load_state()
    projs = enabled(m)
    if not projs:
        print("no enabled projects")
        return

    # Serialize the whole run behind an exclusive lock.
    #
    # FAILURE 2026-09-23: `run` was invoked twice (a timed-out call whose child
    # process kept going, then a second launch). Both passes read the board as
    # free, both created a card for project-c, and TWO workers ended up
    # editing one repo on two branches. The old guard -- "is a card open?" --
    # is check-then-act and has no lock between the check and the create, so it
    # cannot prevent this. Serialize the ENTIRE run instead: the lock is held
    # before any board is read and released only after the card is created.
    lock_path = ROOT / "state" / "rotator.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        # A stale lock (dead pid, or older than the max card runtime) must not
        # wedge the rotation forever -- that is how the board froze before.
        try:
            age = time.time() - lock_path.stat().st_mtime
            holder = (lock_path.read_text() or "").strip()
            alive = False
            if holder.isdigit():
                try:
                    os.kill(int(holder), 0)
                    alive = True
                except OSError:
                    alive = False
            if alive and age < STALE_LOCK_S:
                print("another rotation run is in flight (pid %s, %.0fs) -- exiting"
                      % (holder or "?", age))
                return
            print("clearing stale rotator lock (pid %s, %.0fs old)"
                  % (holder or "?", age))
            lock_path.unlink()
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as e:
            print("could not acquire rotator lock: %s" % e)
            return
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return _run_locked(m, s, projs)
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def _run_locked(m, s, projs):

    # Shout if the selection names projects the manifest doesn't define. Without
    # this the rotator silently works on a subset and the user believes all
    # their picks are running (the 2026-09-22 'picked 5, only 1 ever runs' bug).
    try:
        import loopctl as _lc
        _orphans = check_selection(m, _lc.load())
        if _orphans:
            msg = ("selected but NOT in improve.yaml (will never rotate): "
                   + ", ".join(_orphans)
                   + " -- run: python manifest_sync.py sync --projects <list>")
            print("WARNING: " + msg)
            try:
                import discord_notify as _dn
                _dn.progress("loop", "⚠️ " + msg)
            except Exception:
                pass
    except Exception:
        pass

    # Is anything already in flight? Don't stack cards.
    #
    # Two independent checks, because the board query failed once in a way
    # that produced a duplicate worker on the same repo (2026-09-22 14:30):
    # card_running() returned False for a genuinely running card, a second
    # card was created, and two agents edited the same worktree. A defensive
    # lock file costs nothing and cannot silently succeed the way a bad parse
    # did.
    #
    # A STUCK (blocked) card must NOT stall the whole fleet. Skip that project
    # and give the turn to the next one; report the stall so it is visible
    # rather than silent. This is the fix for the starvation that left
    # project-c and project-b on 0 rounds for hours.
    # A BLOCKED card means a HUMAN is needed. Skipping it silently and looping
    # forever is the bug Gene reported: Discord got the same "skipping stuck
    # board(s)" line over and over and NOTHING ever asked him the actual
    # question. `discord_notify.ask()` exists and pages him until answered --
    # use it. Ask ONCE per stuck card (tracked in state/asked.json), then keep
    # rotating so the other projects still make progress while he decides.
    skipped_stuck: list[str] = []
    question_sent: list[str] = []
    busy = [p["name"] for p in projs if card_running(p["board"])]

    asked = {}
    try:
        asked = json.loads((ROOT / "state" / "asked.json").read_text(encoding="utf-8"))
    except Exception:
        asked = {}

    for p in projs:
        stuck = stuck_card(p["board"])
        if not stuck:
            continue
        skipped_stuck.append(p["name"])
        cid = str(stuck.get("id"))
        print("'%s' has a BLOCKED card %s (%s) — needs a human; skipping its turn"
              % (p["name"], cid, stuck.get("status")))

        # Ask ONCE per card. Re-asking the same card every tick is what turned
        # this into Discord spam.
        if asked.get(cid):
            continue
        try:
            import discord_notify as _dn
            reason = str(stuck.get("last_failure_error")
                         or stuck.get("result")
                         or stuck.get("error") or "").strip()
            q = ("Project **%s** is parked and cannot continue without you.\n"
                 "Card `%s` is `%s`." % (p["name"], cid, stuck.get("status")))
            if reason:
                q += "\n\nWhy it stopped:\n> " + reason.replace("\n", "\n> ")[:600]
            q += ("\n\nReply in this channel and I'll resume it on the next pass. "
                  "Until then the other projects keep rotating.")
            qid = "stuck-%s" % cid
            _dn.ask(p["name"], q, qid=qid)
            question_sent.append(qid)
            asked[cid] = {"qid": qid, "at": time.time(), "project": p["name"]}
        except Exception as e:
            print("  (could not ask on Discord: %s)" % e)

    if asked:
        try:
            (ROOT / "state" / "asked.json").write_text(
                json.dumps(asked, indent=2), encoding="utf-8")
        except Exception:
            pass

    if question_sent:
        print("asked Gene about: " + ", ".join(question_sent))

    # A project with a LIVE card is busy, not stuck. The rotator used to `return`
    # on the FIRST busy project, which meant one running round froze the whole
    # fleet: project-c held the turn and project-b/T&T could not start
    # even though their boards were idle. Skip busy projects and start the next
    # runnable one instead — the whole point of a rotator is that it keeps
    # handing out turns while other work is in flight.
    if busy:
        print("busy (live card, skipped): " + ", ".join(busy))

    # NB: STATE is the rotation.json FILE (used as a file by load_state/save_state);
    # the lock lives beside it and is taken by the caller (cmd_run) before any
    # board is read. Do not re-check it here -- a second check on a lock we just
    # acquired ourselves always reports "held" and makes the rotator a no-op.

    try:
        idx = s.get("idx", 0) % len(projs)
        p = projs[idx]

        # Never start a turn on a board that can't take one -- walk the ring to the
        # next project that can actually run. Excluded:
        #   - skipped_stuck: has a BLOCKED card (cannot finish on its own)
        #   - busy:          has a LIVE card (already working; don't stack)
        # Without this, one blocked OR one busy project froze the whole fleet and
        # the other projects never ran once.
        blocked_names = set(skipped_stuck) | set(busy)
        if blocked_names:
            picked = None
            for off in range(len(projs)):
                cand = projs[(idx + off) % len(projs)]
                if cand["name"] not in blocked_names:
                    picked = (cand, (idx + off) % len(projs))
                    break
            if picked is None:
                print("no runnable project right now (all busy or blocked) "
                      "- nothing to start")
                return
            p, idx = picked

        # --- the 2-round rule -------------------------------------------
        # Gene's standing rule: at most TWO rounds on a project before rotating
        # on. `loops` in improve.yaml is a turn ceiling, NOT the rotation rule --
        # conflating them is what let T&T take three rounds inside one card.
        #
        # Rotate on CONSECUTIVE rounds for this project, not on total history.
        # Total-history modulo was WRONG (found by simulating the real history
        # 2026-09-23): it counts every turn the project ever had, so a project
        # with 3 prior turns re-rotated on its own turn 4 -- and because the ring
        # index and the counter stepped in lockstep, the "rotation" resolved back
        # to the SAME project. The rule was a no-op.
        ROUNDS_BEFORE_ROTATE = 2
        hist = s.get("history") or []
        run = 0
        for h in reversed(hist):
            if h.get("project") == p["name"]:
                run += 1
            else:
                break
        if run >= ROUNDS_BEFORE_ROTATE:
            nxt = None
            for off in range(1, len(projs)):
                cand = projs[(idx + off) % len(projs)]
                if cand["name"] not in skipped_stuck:
                    nxt = (cand, (idx + off) % len(projs))
                    break
            if nxt is not None and nxt[0]["name"] != p["name"]:
                print("%s has had %d consecutive round(s) (%d-round rule) - "
                      "rotating to %s"
                      % (p["name"], run, ROUNDS_BEFORE_ROTATE, nxt[0]["name"]))
                p, idx = nxt

        # One round per card: the card IS the round. Asking for many loops made
        # the worker keep iterating inside a single card, which is what blew the
        # budget and left the branch mid-flight. The rotator -- not the budget --
        # is what produces multi-round behaviour.
        loops = 1

        # --- the on/off switch -------------------------------------------
        lc = loopctl.load()
        if not lc.get("running"):
            print(f"loop is paused ({lc.get('paused_reason') or 'not started'}) "
                  "— nothing created")
            return
        if lc.get("max_rounds") and lc.get("rounds_done", 0) >= lc["max_rounds"]:
            print(f"max_rounds reached ({lc['rounds_done']}/{lc['max_rounds']}) "
                  "— nothing created")
            return

        # --- ANGLE MODE: a fresh lens every round ------------------------
        # Round 1 for a project still walks the six-stage method (to get a
        # baseline + backlog). After that, every round pulls a random angle,
        # which is what stops the loop tunnelling down one fixed path.
        st = method_state(p["name"]) if "method_state" in dir() else None
        use_angle = True
        try:
            import method as _m
            md = _m.load_plan(p["name"]) if hasattr(_m, "load_plan") else {}
            stages = (md or {}).get("stages") or {}
            done = [k for k, v in stages.items() if isinstance(v, dict) and v.get("status") == "done"]
            use_angle = len(done) >= 2      # scout + baseline recorded -> free-run on angles
        except Exception:
            use_angle = True                # can't tell -> prefer the loop behaviour

        angle = None
        stage = None
        if use_angle:
            facts = repo_facts(p)
            angle = ap.pick(p["name"], facts)
            dn.progress(p["name"],
                        f"round {lc.get('rounds_done', 0) + 1} — angle "
                        f"`{angle['angle']}` ({angle['family']}): {angle['lens']}")
            print(f"angle: {angle['angle']} ({angle['family']})")
        else:
            stage = next_stage(p["name"])
            dn.progress(p["name"], f"starting {stage[0]} {stage[1]} — a {loops}-loop turn. "
                                   f"objective: {(p.get('objective') or '').strip()[:140]}")

        if not board_exists(p["board"]):
            print(f"board '{p['board']}' missing — creating")
            create_board(p["board"], p["path"].replace("\\", "/"))

        # --- CHECKPOINT ENGINE (wired) ---
        # Before an agent touches a byte: guarantee a SAFE push target and lay
        # down a START checkpoint. Rollback target = kp/<project>/r<NN>-start.
        # Locked policy (Gene 2026-09-22):
        #   public upstream -> fork to the user's account, never push upstream
        #   no remote       -> create a PRIVATE repo and push
        #   private own     -> use it
        # A round that cannot be rolled back must not start, so an unsafe repo
        # ABORTS the turn (fail closed) rather than iterating unbacked-up.
        try:
            import checkpoint as _ckpt
            _ck_repo = Path(p["path"])
            _ck_round = int(lc.get("rounds_done", 0)) + 1
            _ck_angle = (angle or {}).get("angle") if angle else "session"

            _rem = _ckpt.ensure_remote(_ck_repo, p["name"], dry=False)
            if not _rem.get("safe"):
                dn.stuck(p["name"],
                         f"checkpoint REFUSED: {_rem.get('action')} — {_rem.get('detail')}")
                print(f"checkpoint refused for {p['name']}: {_rem}")
                return
            if _rem.get("action") in ("forked", "created-private"):
                dn.progress(p["name"], f"checkpoint safety: {_rem['detail']}")

            _ck = _ckpt.cmd_prepare_local(_ck_repo, p["name"], _ck_round,
                                          _ck_angle, push=bool(lc.get("pushed_to_github", True)))
            if not _ck.get("ok"):
                dn.stuck(p["name"], f"checkpoint prepare failed: {_ck.get('detail')}")
                print(f"checkpoint prepare failed: {_ck}")
                return
            print(f"checkpoint start: {_ck.get('start_tag', {}).get('tag')}")
            dn.progress(p["name"],
                        f"checkpoint `{_ck.get('start_tag', {}).get('tag')}` laid down "
                        f"before round {_ck_round} (rollback: "
                        f"`git checkout {_ck.get('start_tag', {}).get('tag')}`)")
        except Exception as _e:
            dn.stuck(p["name"], f"checkpoint engine error: {_e!r}")
            print(f"checkpoint engine error: {_e!r}")
            return

        rc, out = create_card(p, loops, stage, angle=angle)
        if rc != 0:
            msg = out.strip()[-500:]
            dn.stuck(p["name"], f"could not create the loop card (rc={rc}). {msg}")
            print("CREATE FAILED", rc, msg)
            return

        task_id = None
        d = _first_json(out)
        if isinstance(d, dict):
            # `kanban create --json` nests the row under "task"; older builds
            # returned the row at the top level. Accept both.
            row = d.get("task") if isinstance(d.get("task"), dict) else d
            task_id = row.get("id") or row.get("task_id")

        s.setdefault("history", []).append({
            "project": p["name"], "task_id": task_id, "loops": loops,
            "at": time.time(),
        })
        s["idx"] = (idx + 1) % len(projs)
        save_state(s)

        # A card was created for this project, so a turn IS in flight: hold
        # the lock until the card finishes. The next tick sees the open card
        # and returns early anyway; the lock is the belt to that braces.
        dn.progress(p["name"], f"loop card created: `{task_id}` ({loops} loops). "
                               f"next turn → {projs[s['idx']]['name']}")
        print(f"created {task_id} for {p['name']} ({loops} loops); next={projs[s['idx']]['name']}")
    except Exception as exc:
        print(f"rotator error: {exc!r}")
        raise
    finally:
        # Release the lock. It exists to serialize the CREATE step (read board ->
        # create card), NOT to gate on a live card: that is card_running()'s job,
        # and holding the lock for a live card made the rotator a no-op for the
        # whole duration of every round. Released unconditionally here; the
        # caller owns acquisition.
        pass


def cmd_status():
    m = load_manifest() if MANIFEST.exists() else {"projects": []}
    s = load_state()
    projs = enabled(m)
    print(f"manifest      : {MANIFEST}")
    print(f"enabled       : {len(projs)} project(s)")
    for p in projs:
        print(f"  - {p['name']:26} board={p['board']:22} loops={p.get('loops',5)} "
              f"open_card={card_running(p['board'])}")
    print(f"rotation idx  : {s.get('idx', 0)}")
    print(f"turns taken   : {len(s.get('history', []))}")
    for h in s.get("history", [])[-5:]:
        ts = time.strftime("%m-%d %H:%M", time.localtime(h["at"]))
        print(f"  {ts}  {h['project']:26} task={h.get('task_id')} loops={h['loops']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"run": cmd_run, "plan": cmd_plan, "init": cmd_init, "status": cmd_status}.get(
        cmd, lambda: print(__doc__))()