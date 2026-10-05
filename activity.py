#!/usr/bin/env python3
"""
activity.py -- the live "what is it doing / thinking" feed for the Karpathy Loop.

Two jobs:
  1. Which project is mid-round, which card is in flight, how long it has run.
  2. The CHAIN OF THOUGHT of the in-flight worker: its thinking blocks and the
     tool calls it is making, parsed out of the kanban worker transcript.

The dispatcher writes a human-readable transcript to
  <hermes home>/kanban/boards/<board>/logs/<task_id>.log

    Query: work kanban task t_42fa5ba4
      ┊ 💻 preparing terminal…
      ┊ 💻 $   git log --oneline -5 + 1 command  7.1s
      ╭─ ☤ Hermes ─────────────────────────────╮
      Baseline: 180 passed, 3 skipped, gate green.
      ╰────────────────────────────────────────╯

We parse that into structured steps so the widget renders it natively rather
than dumping raw text.

Usage:
    python activity.py            # JSON on stdout (the widget consumes this)
    python activity.py --text     # human readable, for the terminal
    python activity.py --steps N  # cap CoT steps (default 40)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# The desktop bridge returns only the last ~4000 chars of stdout.
# The payload must stay under this or the widget's JSON.parse fails.
BRIDGE_BUDGET = 3800
STATE = ROOT / "state" / "rotation.json"
HERMES_HOME = Path(
    os.environ.get("HERMES_HOME")
    or (Path.home() / "AppData" / "Local" / "hermes")
)
BOARDS = HERMES_HOME / "kanban" / "boards"

sys.path.insert(0, str(ROOT))

# ── transcript parsing ──────────────────────────────────────────────────────

# The dispatcher writes ANSI colour escapes. Strip them BEFORE any parsing:
# otherwise the ╭─/╰─ box borders are "\x1b[...m╰─..." and never match, so
# thinking blocks leaked their borders and swallowed following tool lines.
RE_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

RE_ACTION = re.compile(r"^\s*┊\s*(?P<icon>\S+)\s+(?P<detail>.*?)\s*$")
RE_TIME = re.compile(r"\s+(?P<t>\d+(?:\.\d+)?s|\d+m\s?\d+s)\s*$")

ICON_KIND = {
    "💻": "shell", "🖥️": "shell",
    "🔧": "edit", "🐍": "code", "🧪": "test", "🌿": "git", "🧹": "review",
    "📖": "read", "✍️": "write",
    "📋": "task", "⚡": "task",
    "🔎": "search", "🌐": "web", "🧠": "think",
}


# ── reasoning feed (the worker's own narration) ─────────────────────────────
#
# The kanban worker log is a TOOL-CALL transcript: it records `preparing
# terminal…` / `$ cmd` lines and little else. The model's actual reasoning is
# NOT written there -- which is why the CoT panel looked like a wall of shell
# commands and no thinking.
#
# The reasoning lives in the worker profile's session DB instead:
#   <hermes home>/profiles/improver/state.db  ->  messages
# as the assistant's own narrative messages (`content`), plus `reasoning_content`
# when the serving model populates a separate reasoning channel.
#
# NOTE (measured 2026-09-23): the deepseek-v4.1-flash endpoint returns
# `reasoning_content` EMPTY and puts its reasoning inline in `content`, so
# `content` is the only reliable source. Read both, prefer whichever is non-empty.
SESSIONS_DB = HERMES_HOME / "state.db"   # KL threads run in the DEFAULT profile
                                         # -- its DB is hermes home's state.db
                                         # (profiles/improver/state.db is the OLD
                                         # card-era profile; threads aren't there)

# Terse/procedural assistant lines that are not "thinking" worth showing.
_RE_NOISE = re.compile(
    r"^(now (let me|i'll|i will)\b|let me\b|next,? i|i'?ll now\b)", re.I)


def card_session(board: str, task_id: str) -> str | None:
    """
    The session that OWNS this card, from the board's own kanban.db.

    Authoritative when present: `tasks.session_id`. It can still be NULL while a
    card is early in its run, so callers must treat a miss as "unknown", not
    "no session".
    """
    db = BOARDS / board / "kanban.db"
    if not db.exists():
        return None
    try:
        import sqlite3
        c = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
        row = c.execute("select session_id from tasks where id = ?",
                        (str(task_id),)).fetchone()
        c.close()
        return (row[0] or None) if row else None
    except Exception:
        return None


def read_reasoning(task_id: str | None, max_items: int = 40,
                   board: str | None = None) -> list[dict]:
    """
    The worker's narration for a card, oldest -> newest.

    Resolution order:
      1. `tasks.session_id` from the board DB -- authoritative, but NULL early
         in a run.
      2. Fall back to the session where this task id appears EARLIEST. Searching
         for "any session mentioning the task" is wrong: one worker session can
         touch several cards (a rotator pass that hands work on), so the newest
         match can belong to a DIFFERENT card and show the wrong thoughts.
    """
    if not SESSIONS_DB.exists():
        return []
    try:
        import sqlite3
        c = sqlite3.connect("file:%s?mode=ro" % SESSIONS_DB.as_posix(), uri=True)
        c.row_factory = sqlite3.Row
    except Exception:
        return []

    # 1. authoritative link from the board DB
    sid = card_session(board, task_id) if board else None

    # 2. fallback: the FIRST session in which this task id appears
    if not sid:
        try:
            row = c.execute(
                "select session_id, min(rowid) from messages "
                "where content like ? group by session_id order by min(rowid) asc "
                "limit 1", ("%" + str(task_id) + "%",)).fetchone()
            sid = row["session_id"] if row else None
        except Exception:
            sid = None

    if not sid:
        c.close()
        return []

    try:
        rows = c.execute(
            "select role, content, reasoning_content, reasoning, timestamp "
            "from messages where session_id = ? "
            "and role in ('assistant','user') order by rowid asc", (sid,)
        ).fetchall()
    except Exception:
        c.close()
        return []
    c.close()

    out: list[dict] = []
    for r in rows:
        txt = (r["reasoning"] or "").strip() or (r["reasoning_content"] or "").strip()
        kind = "think"
        if not txt:
            txt = (r["content"] or "").strip()
        if not txt:
            continue
        # Skip pure tool-call envelopes and one-liners that add nothing.
        if len(txt) < 25:
            continue
        if r["role"] == "user":
            kind = "prompt"
        elif _RE_NOISE.match(txt) and len(txt) < 120:
            continue
        out.append({"kind": kind, "icon": "💭" if kind == "think" else "▶",
                    "text": txt[:400], "ts": r["timestamp"]})
    return out[-12:]


def thread_cot(session_id: str | None, max_steps: int = 40) -> dict:
    """
    CoT for the NEW architecture: read the LIVE project's Hermes thread straight
    from the session DB (assistant reasoning + narration), oldest -> newest.

    The old path (worker_log + card_session) is kanban-card-based and dead: with
    no cards, `live_task` is None and cot.steps stayed empty -- the widget showed
    "no chain of thought" while rounds were actually executing (Gene, 21:xx).
    """
    out: list[dict] = []
    if not session_id or not SESSIONS_DB.exists():
        return {"steps": out, "steps_total": 0}
    try:
        import sqlite3
        c = sqlite3.connect("file:%s?mode=ro" % SESSIONS_DB.as_posix(), uri=True)
    except Exception:
        return {"steps": out, "steps_total": 0}
    try:
        total = c.execute(
            "select count(*) from messages where session_id = ?",
            (session_id,)).fetchone()[0]
        rows = c.execute(
            "select role, content, reasoning_content, reasoning, timestamp "
            "from messages where session_id = ? "
            "and role in ('assistant','user') order by rowid desc limit 400",
            (session_id,)).fetchall()
    except Exception:
        c.close()
        return {"steps": out, "steps_total": 0}
    c.close()

    for r in reversed(rows):          # oldest -> newest
        txt = (r[3] or "").strip() or (r[2] or "").strip()
        kind = "think"
        if not txt:
            txt = (r[1] or "").strip()
        if not txt:
            continue
        if r[0] == "user":
            kind = "prompt"
        elif len(txt) < 25 or (_RE_NOISE.match(txt) and len(txt) < 120):
            continue
        out.append({"kind": kind, "icon": "💭" if kind == "think" else "▶",
                    "text": txt[:2000], "ts": r[4]})
    return {"steps": out[-max_steps:], "steps_total": total}


def worker_log(board: str, task_id: str) -> Path | None:
    """The dispatcher transcript for one task, if it exists."""
    if not task_id:
        return None
    p = BOARDS / board / "logs" / f"{task_id}.log"
    return p if p.exists() else None


def parse_log(path: Path, max_steps: int = 40) -> dict:
    """Split a worker transcript into thinking/action steps, oldest -> newest.

    TAIL-READ ONLY. Worker logs are append-only and reach several MB (one round
    measured 317 tool calls of verbose text). Reading the whole file and
    re-regexing it on every widget tick was the memory pressure behind the
    desktop crashes: each tick allocated the full file as a string plus its
    split lines, while the screenshot machinery was simultaneously allocating a
    framebuffer. Only the last TAIL_BYTES can contribute to the last `max_steps`
    steps, so we never read more than that.
    """
    TAIL_BYTES = 384 * 1024
    try:
        size = path.stat().st_size
    except OSError as e:
        return {"steps": [], "error": f"cannot stat log: {e}"}
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                raw = fh.read()
                # Drop the leading partial line, it is not a real record.
                nl = raw.find("\n")
                if nl >= 0:
                    raw = raw[nl + 1:]
            else:
                raw = fh.read()
    except OSError as e:
        return {"steps": [], "error": f"cannot read log: {e}"}

    raw = RE_ANSI.sub("", raw)
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    steps: list[dict] = []
    think: list[str] = []
    in_think = False

    def flush_think() -> None:
        """Emit the buffered thinking block, if any."""
        nonlocal in_think, think
        keep = [x.strip() for x in think if x.strip()]
        keep = [x for x in keep if not x.startswith("╰") and not x.startswith("╭")]
        keep = [re.sub(r"╰─+╯?$", "", x).strip() for x in keep]
        txt = " ".join(k for k in keep if k).strip()
        txt = re.sub(r"\s*╰─+╯?\s*$", "", txt).strip()
        txt = re.sub(r"^╭─+╮?\s*", "", txt).strip()
        if txt:
            steps.append({"kind": "think", "icon": "🧠", "text": txt})
        in_think = False
        think = []

    for ln in lines:
        s = ln.rstrip()
        stripped = s.lstrip()

        # ── thinking block ───────────────────────────────────────────────
        if "☤ Hermes" in s:
            flush_think()
            in_think = True
            think = []
            continue

        if in_think:
            # A new tool line ENDS the block. Without this the flag stayed set
            # whenever a block lacked its ╰ closer, swallowing every later step
            # (the "log has 243 steps but the panel showed 6" bug).
            if stripped.startswith("╰"):
                flush_think()
                continue
            if stripped.startswith("╭"):
                continue
            if "┊" in s:
                flush_think()
                # fall through so this same line is parsed as a tool call
            elif s.strip():
                think.append(s)
                continue
            else:
                continue

        # ── tool call ────────────────────────────────────────────────────
        if "┊" not in s:
            continue
        m = RE_ACTION.match(s)
        if not m:
            continue
        icon = m.group("icon")
        detail = (m.group("detail") or "").strip()
        # The log pads each action with its own verb ("read      file.py").
        # The icon conveys that already, so drop it.
        detail = re.sub(r"^(read|write|patch|exec|diff|task|shell)\s{2,}", "",
                        detail).strip()
        if not detail or detail.startswith("preparing"):
            continue
        tm = RE_TIME.search(detail)
        dur = tm.group("t") if tm else ""
        if tm:
            detail = detail[: tm.start()].strip()
        detail = re.sub(r"\s*\+\s*\d+\s+commands?\s*$", "", detail).strip()
        detail = detail.replace("\\|", "|")
        detail = re.sub(r"^\$\s+", "", detail).strip()
        detail = re.sub(r"[A-Za-z]:\\\\?[^\\s]*?\\.worktrees\\\\?[^\\s\\\\]+\\\\?",
                        "", detail).strip()
        note = ""
        nb = detail.find(" [")
        if nb != -1:
            note = detail[nb + 2:].rstrip("]")[:110]
            detail = detail[:nb].strip()
        steps.append({
            "kind": ICON_KIND.get(icon, "action"),
            "icon": icon,
            "text": detail,
            "duration": dur,
            "note": note,
        })

    total = len(steps)
    if max_steps and len(steps) > max_steps:
        steps = steps[-max_steps:]

    return {"steps": steps, "steps_total": total, "log_lines": len(lines)}


# ── project / round state ───────────────────────────────────────────────────


def _surface_pos(cur) -> str:
    """`"2/5"` for the surface a round is on right now, else "".

    Kept as a short string rather than two ints so the widget renders it without
    arithmetic -- and so an absent/partial surface costs zero bytes.
    Never raises: a malformed registry entry must not take down a refresh.
    """
    try:
        idx = (cur or {}).get("index")
        total = (cur or {}).get("total")
        if isinstance(idx, int) and isinstance(total, int) and total:
            return "%d/%d" % (idx + 1, total)
    except Exception:
        pass
    return ""


def _campaign_outstanding(camp) -> int:
    """How many surfaces of a running campaign still lack a TERMINAL verdict.

    F-D (2026-10-02): this used to count "has ANY verdict key" as closed, so a
    campaign whose surfaces were all `deferred`/`done-uncited` rendered
    campaign_open: 0 -- the panel said finished while scope said everything
    was outstanding. Delegate to scope's own definition so the widget and the
    scheduler can never disagree. 0 when there is no campaign.
    """
    try:
        import scope
        return len(scope.outstanding_surfaces(camp))
    except Exception:
        return 0


def _see_for(name: str) -> dict | None:
    """The widget-facing `see` descriptor for a project, or None.

    Reads improve.yaml's optional `see:` block. Shape stays tiny on purpose: the
    poll payload shares a ~3800 B bridge budget with the feed and the rows.
    NOTE: `start` is a STRING for display/copy only -- nothing here (or in the
    widget) ever executes it.
    """
    try:
        import improver as _I
        for proj in _I.enabled(_I.load_manifest()):
            if proj.get("name") != name:
                continue
            see = proj.get("see") or None
            if not see:
                return None
            return {
                "kind": see.get("kind") or "unknown",
                "label": see.get("kind") or "open",
                "url": see.get("url") or "",
                "path": see.get("path") or "",
                "note": (see.get("note") or "")[:160],
                "start_cmd": (see.get("start") or "")[:300],
            }
    except Exception:
        return None
    return None


def _first_line(text) -> str:
    """First non-empty line of a round's captured output, trimmed for the UI.

    The widget shows this as the one-line reason a repo's last round failed.
    Returns "" for missing/blank input so the row renders nothing rather than
    "None".
    """
    if not text:
        return ""
    for ln in str(text).splitlines():
        ln = ln.strip()
        # skip the runner's own bookkeeping line so we surface the CHILD's words
        if ln and not ln.startswith("rc="):
            return ln[:180]
    return ""


def _load_rotation() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _tasks(board: str) -> list[dict]:
    """
    Open tasks on a board -- read the board's kanban.db DIRECTLY.

    This used to shell `hermes kanban list --json` per project: a full Hermes CLI
    boot (~6-8s) per board, x3 boards, made activity.py take ~29s -- past the
    widget's 30s bridge timeout, which is why every panel request timed out
    (Gene, 2026-09-23). The boards are local sqlite; reading them takes ~0ms.
    Card fields are legacy (thread era keeps boards empty) but still feed the
    STALLED row logic.
    """
    import sqlite3
    db = BOARDS / board / "kanban.db"
    if not db.exists():
        return []
    try:
        c = sqlite3.connect("file:%s?mode=ro" % db.as_posix(), uri=True)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "select id, title, status, started_at from tasks "
            "where status not in ('done','archived')").fetchall()
        c.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


OPEN_STATES = ("running", "ready", "todo", "blocked", "review")


def activities(max_steps: int = 40) -> dict:
    rot = _load_rotation()

    # Start from the ENABLED project list, not rotation history. A project that
    # is ticked but has never been given a round (because the rotator returns
    # early whenever any project has an open card) must still appear -- otherwise
    # the table silently hides exactly the projects the user is waiting on.
    order: list[str] = []
    try:
        import loopctl as L
        for n in (L.load().get("projects") or []):
            if n and n not in order:
                order.append(n)
    except Exception:
        pass
    for h in rot.get("history") or []:
        n = h.get("project")
        if n and n not in order:
            order.append(n)

    running_flag = None
    try:
        import loopctl as L
        running_flag = bool(L.load().get("running"))
    except Exception:
        pass

    # Per-project liveness, NEW architecture: kanban cards are gone; a project is
    # "running" when a hermes process is actively driving its registered thread
    # (`--resume <thread_id>` in a live process command line). Card-based
    # derivation always yields False now (no cards exist) -- that is the bug that
    # made Gene's widget show "all idle" while a round was mid-flight.
    _live_sids: set[str] = set()
    try:
        _out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=30, errors="replace", creationflags=0x08000000).stdout or ""
        for _blk in _out.split("CommandLine="):
            if "--resume" in _blk and "hermes_cli.main" in _blk:
                _sid = _blk.split("--resume")[1].split()[0].strip('"')
                if _sid:
                    _live_sids.add(_sid)
    except Exception:
        _live_sids = set()

    rows: list[dict] = []
    live_task = live_project = live_board = None

    for name in order:
        # Card fields kept for backward compatibility (boards are empty now, but
        # Historical rows and STALLED logic still read them).
        tasks = _tasks(name)
        running = [t for t in tasks
                   if str(t.get("status", "")).lower() == "running"]
        open_ = [t for t in tasks
                 if str(t.get("status", "")).lower() in OPEN_STATES]
        act = running[0] if running else (open_[0] if open_ else None)
        tid = act.get("id") if act else None
        status = (act or {}).get("status")

        # Thread-registry entry first (may fill in below).
        thread_id = thread_rounds = thread_title = last_compacted = None
        _ent: dict = {}
        try:
            import threads as _threads
            _ent = _threads.load().get(name) or {}
            thread_id = _ent.get("session_id")
            # Hand out the CHAIN TIP so "open this thread" lands on the session that
            # actually holds the newest messages (a compacted head is a dead end).
            if thread_id:
                try:
                    thread_id = _threads.chain_tip(thread_id)
                except Exception:
                    pass
            thread_rounds = _ent.get("rounds")
            thread_title = _ent.get("title") or _threads.title_for(name)
            last_compacted = _ent.get("last_compacted_round")
        except Exception:
            pass

        # LIVE liveness: this project's thread has an active hermes process.
        # A live round may be resuming any session in the chain (the runner resolves
        # the tip, but an older process can still hold the head). Any match counts.
        try:
            _chain_ids = _threads.chain_ids(_ent.get("session_id") or thread_id) \
                if _ent.get("session_id") else [thread_id]
        except Exception:
            _chain_ids = [thread_id]
        thread_running = any(s and s in _live_sids for s in _chain_ids)
        # F-U (2026-10-05, Gene: "loop shows running but no repo in
        # rotation"): between a round's child exiting and the next Popen there
        # is a multi-minute window (gate run, checkpoint, containment, worktree
        # teardown/setup) where NO live --resume python exists -- the widget
        # read that honest gap as "nothing in rotation" while the loop was
        # healthy. The runner heartbeat is the authority on WHICH project is
        # mid-round; use it as a fallback while it is fresh.
        try:
            _hb = json.loads((ROOT / "state" / "runner_heartbeat.json")
                             .read_text(encoding="utf-8"))
            if (_hb.get("state") == "round-started"
                    and _hb.get("project") == name
                    and (time.time() - float(_hb.get("ts") or 0)) < 4200):
                thread_running = True
        except Exception:
            pass
        # Round-in-flight elapsed time: measure from THIS round's actual start.
        #
        # BUG FIX (Gene, 2026-10-01: "no elapsed time / how long has it been on
        # the same repo"): the old proxy used `last_nudge` -- the PREVIOUS
        # round's END -- so a round 4 minutes old showed "2.1h" (start + idle
        # gap), and any poll where liveness detection missed showed NOTHING.
        # The new engine stamps `current_angle.started` (and
        # `current_surface.started`) at round start; that is the true t0.
        # Fallbacks keep the display honest in priority order:
        #   1. current_angle.started  (this round's start -- authoritative)
        #   2. current_surface.started (same instant, surface view)
        #   3. last_nudge              (legacy entries only; noted as approximate)
        elapsed = ""
        started = (act or {}).get("started_at")
        if thread_running:
            try:
                _ca = _ent.get("current_angle") or {}
                _cs = _ent.get("current_surface") or {}
                _t0 = None
                for cand in (_ca.get("started"), _cs.get("started")):
                    if isinstance(cand, (int, float)) and cand > 0:
                        _t0 = float(cand)
                        break
                if _t0 is None:
                    _t0 = float(_ent.get("last_nudge") or 0)  # legacy fallback
                if _t0:
                    secs = max(0.0, time.time() - _t0)
                    elapsed = (f"{int(secs // 60)}m {int(secs % 60)}s"
                               if secs < 3600 else f"{secs / 3600:.1f}h")
            except Exception:
                elapsed = ""
        if not elapsed and started:
            try:
                secs = max(0.0, time.time() - float(started))
                elapsed = (f"{int(secs // 60)}m {int(secs % 60)}s"
                           if secs < 3600 else f"{secs / 3600:.1f}h")
            except Exception:
                pass

        rounds_hist = len([h for h in (rot.get("history") or [])
                      if h.get("project") == name])

        # NEVER-ROTATED must read the THREAD registry, not rotation.json history.
        # rotation.json is a dead kanban-era file the thread runner never writes:
        # a repo with 10 completed thread rounds and 0 history rows showed
        # "waiting" forever (project-d, 2026-09-27). thread_rounds
        # is the authoritative count; a repo with no thread at all is genuinely
        # waiting for its first run.
        _effective_rounds = thread_rounds if thread_rounds is not None else rounds_hist
        never_rotated = (not _effective_rounds) and act is None

        rows.append({
            "name": name,
            # thread liveness is the ONLY loop-running signal: the loop is session-based
            # (karpathy_nudge), and a status=running kanban card on this board can belong to
            # a FOREIGN agent (2026-09-24: the T&T review session's card made the widget show
            # RUNNING while the loop was paused). Cards stay for title/status display only.
            "running": thread_running,
            "rounds": _effective_rounds,
            "elapsed": elapsed,
            "thread_id": thread_id,
            "thread_rounds": thread_rounds,
            "thread_title": thread_title,
            "active_status": status,
            # The widget's task cell reads `active_task` (and render_text below);
            # shipping only `active_status` rendered an empty/undefined cell.
            # BUG FIX (Gene, 2026-10-01, "something under the word card"):
            # active_task came ONLY from the retired kanban-card path, so it was
            # None for every row -- the Card column showed a bare em-dash even
            # while a round was live, and the CoT card's task line showed
            # nothing/null. The loop is thread-based now: the CURRENT ANGLE is
            # what the repo is working on. Use it; keep the card value when one
            # exists (legacy compatibility).
            "active_task": (status or (act or {}).get("id"))
                           or (((_ent or {}).get("current_angle") or {}).get("id")
                               or ((_ent or {}).get("current_surface") or {}).get("id")
                               if thread_running else None),
            # ^ angle id only while the round is LIVE: an idle repo's LAST angle
            # rendered under Card read as if it were still working on it.
            "never_rotated": never_rotated,
            # Surface the LAST ROUND's outcome. 87% of rounds exit non-zero and
            # until now nothing in the UI said so -- the row looked identical to a
            # healthy one. `last_rc` + a one-line reason let the panel flag a
            # failing repo instead of showing a confident "idle".
            "last_rc": (_ent or {}).get("last_rc"),
            "last_error": _first_line((_ent or {}).get("last_output")),
            # SEE-IT: the declared way to LOOK at this project. Only the small
            # descriptor ships here; probes run on demand (see_it.py) because a
            # socket/HTTP check per repo per poll would slow every refresh.
            "see": _see_for(name),
            "angle": (_ent or {}).get("current_angle", {}).get("id") or (_ent or {}).get("last_angle") or "",
            "angle_family": (_ent or {}).get("current_angle", {}).get("family") or "",
            "angle_round": (_ent or {}).get("current_angle", {}).get("round") or 0,
            # SURFACE SCOPE: which sub-category of this repo the current round is
            # working. A surface is a frontier, not a rotation slot, so it rides
            # alongside `angle` rather than replacing it. Budget matters here --
            # the bridge clips the whole payload at ~4,000 B and `see` already
            # spends it -- so only the slug and a short position string ship.
            "surface": (_ent or {}).get("current_surface", {}).get("id") or "",
            "surface_pos": _surface_pos((_ent or {}).get("current_surface")),
            # CAMPAIGN: the parallel-work checklist. `campaign_n`/`campaign_open`
            # are two ints deliberately -- the panel needs "2 of 3 done" and
            # nothing more; the full surface list would blow the budget.
            "campaign": (_ent or {}).get("campaign", {}).get("id") or "",
            "campaign_n": len((_ent or {}).get("campaign", {}).get("surfaces") or []),
            "campaign_open": _campaign_outstanding((_ent or {}).get("campaign")),
        })

        if thread_running and not live_task:
            live_task = tid
            live_project = name
            live_board = name

    # Canonical feed key is `steps`. `items` is a LEGACY alias the widget still
    # accepts (plugin.js: `cot.steps || cot.items`) so an older installed copy
    # keeps working; the producer itself only ever fills `steps`.
    cot: dict = {"steps": [], "steps_total": 0, "task": None,
                 "project": None, "running": False}

    # NEW architecture: the CoT source is the LIVE PROJECT'S THREAD, not a kanban
    # card log. Pick the best candidate: thread_running rows first (an actual
    # hermes process is driving that thread right now), else the project with the
    # richest recent thread. This replaces the dead worker_log/card_session path
    # that left the widget's CoT empty while rounds ran (Gene, 2026-09-23).
    if rows:
        cands = [r for r in rows if r.get("thread_id")]
        if cands:
            best = max(cands, key=lambda r: (1 if r.get("running") else 0,
                                             int(r.get("thread_rounds") or 0)))
            sid = best.get("thread_id")
            cot["task"] = sid
            cot["project"] = best.get("name")
            cot["live_thread"] = bool(best.get("running"))
            cot["running"] = bool(best.get("running"))
            try:
                import thread_feed
                # Read the WHOLE compression chain (head..tip) so the panel shows
                # every round, not just the newest session's slice.
                try:
                    _chain = _threads.chain_ids(sid)
                except Exception:
                    _chain = [sid]
                tf = thread_feed.feed(sid, limit=12, session_ids=_chain)
                # SHIP AS `steps` -- the widget reads cot.steps (plugin.js cotCard).
                # This feed was originally shipped as `items` while the widget kept
                # reading `steps`, so the live panel rendered an empty array no
                # matter how many transcript entries were collected. render_text()
                # reads either name.
                # DO NOT also ship `items`: both keys would alias the same list and
                # the payload would carry every step TWICE (measured: 2195 B x2),
                # halving how many steps survive the size guard.
                cot["steps"] = tf["items"]          # live transcript: prompt/think/tool/result
                cot["steps_total"] = tf["total"]
                # Age of the newest transcript line: lets the panel show a live
                # "thinking..." heartbeat during a long model call instead of
                # looking frozen (a round spends most of its wall-clock waiting on
                # the model, not writing DB rows). The widget reads `log_age_s`
                # for both the heartbeat and its 10-minute liveness window --
                # shipping only `head_age_s` left that window on its default.
                try:
                    import sqlite3 as _s
                    _c = _s.connect("file:%s?mode=ro" % SESSIONS_DB.as_posix(), uri=True)
                    _ts = _c.execute("select max(timestamp) from messages where session_id=?",
                                     (sid,)).fetchone()[0]
                    _c.close()
                    if _ts:
                        _age = max(0, int(time.time() - float(_ts)))
                        cot["head_age_s"] = _age
                        cot["log_age_s"] = _age
                except Exception:
                    pass
                cot["session_id"] = sid           # widget uses this to open the live thread
                cot["thread_id"] = sid
                # WHICH ANGLE this round is working, and the prompt it was given:
                # answers "what is it actually iterating on right now?" without
                # opening the thread. The full 1500-char prompt does NOT fit the
                # bridge: the size guard in main() pops every feed item trying to
                # make room and the live feed ships EMPTY (2026-09-27). The widget
                # renders neither `prompt` nor `prompt_lines`, so ship a short
                # hunt-summary instead: the angle id, the family, and the first
                # lines of applies_when/hunt -- a few hundred bytes that actually
                # survive the trip.
                try:
                    _entry = (_threads.load() or {}).get(best.get("name")) or {}
                    _cur = _entry.get("current_angle") or {}
                    _p = _cur.get("prompt") or ""
                    _lines = [ln.strip() for ln in _p.splitlines() if ln.strip()]
                    _pick = []
                    for _ln in _lines:
                        if len(" ".join(_pick + [_ln])) > 220:
                            break
                        _pick.append(_ln)
                    cot["angle"] = {
                        "id": _cur.get("id") or _entry.get("last_angle") or "",
                        "family": _cur.get("family") or "",
                        "lens": (_cur.get("lens") or "")[:160],
                        "round": _cur.get("round") or 0,
                        "started": _cur.get("started") or 0,
                        "summary": " ".join(_pick),
                    }
                    cot["angle_history"] = [
                        {"id": h.get("id"), "round": h.get("round"), "rc": h.get("rc")}
                        for h in (_entry.get("angle_history") or [])[:4]
                    ]
                except Exception as e:
                    cot["angle_error"] = "%s: %s" % (type(e).__name__, e)
                # Do NOT also ship `reasoning`: the widget merges steps+reasoning
                # into one feed, so duplicating doubled the payload past the
                # gateway's 4000-char stdout clip and cut the JSON mid-string
                # ("Unexpected token 'h'").
            except Exception as e:
                cot["reasoning_error"] = "%s: %s" % (type(e).__name__, e)

    return {
        "ts": time.time(),
        "running": running_flag,
        "projects": order,
        "rows": rows,
        "cot": cot,
    }


def selfcheck() -> int:
    """Validate the payload contract against what the widget actually reads.

    Guards the class of bug that blanked the live feed: activity.py shipping a
    key the widget does not read (or vice versa). Exits non-zero so it can gate.
    """
    d = activities()
    cot, rows = d.get("cot") or {}, d.get("rows") or []
    problems = []
    if "steps" not in cot:
        problems.append("cot.steps missing (widget reads cot.steps || cot.items)")
    if not isinstance(cot.get("steps"), list):
        problems.append("cot.steps is not a list")
    for k in ("steps_total", "log_age_s", "task", "project", "running"):
        if k not in cot:
            problems.append("cot.%s missing (widget reads it)" % k)
    if not rows:
        problems.append("rows empty")
    for r in rows:
        for k in ("name", "running", "rounds", "never_rotated", "active_task"):
            if k not in r:
                problems.append("row %r missing %s" % (r.get("name"), k))
    raw = compact_payload(d)
    if len(raw) > BRIDGE_BUDGET:
        problems.append("payload %d B exceeds bridge budget %d B" % (len(raw), BRIDGE_BUDGET))
    print("payload %d B | steps %d of %s | rows %d"
          % (len(raw), len(cot.get("steps") or []), cot.get("steps_total"), len(rows)))
    if problems:
        print("SELFCHECK FAILED:")
        for pr in problems:
            print("  -", pr)
        return 1
    print("SELFCHECK OK")
    return 0


def render_text(d: dict) -> str:
    out = [f"KARPATHY LOOP: {'RUNNING' if d.get('running') else 'PAUSED'}"]
    for r in d["rows"]:
        out.append(f"  {r['name']:22} {(r['active_status'] or 'idle'):9} "
                   f"{(r.get('active_task') or r.get('active_status') or '-'):14} "
                   f"{r['rounds']:>3} rounds  {r.get('elapsed', '')}")
    cot = d.get("cot") or {}
    # steps/items alias the same list; read either.
    steps = cot.get("steps") or cot.get("items") or []
    if steps:
        out.append("")
        out.append(f"CHAIN OF THOUGHT — {cot.get('project')} / {cot.get('task')} "
                   f"({cot.get('steps_total', 0)} steps)")
        for s in steps:
            if s["kind"] == "think":
                out.append(f"  🧠 {s['text']}")
            else:
                dur = f"  {s['duration']}" if s.get("duration") else ""
                out.append(f"  {s.get('icon', '·')} {s['kind']:6} {s['text']}{dur}")
    return "\n".join(out)


def compact_payload(d: dict) -> str:
    """Serialize the widget payload under BRIDGE_BUDGET.

    Single source of truth for the size guard: main() prints what this
    returns and selfcheck() measures the same bytes, so the two can never
    disagree (they did: selfcheck measured the RAW payload and reported
    4558 B while the shipped one was 3.2 KB).
    """
    # Compact + strip the step text to a sane length. The panel shows a
    # trimmed preview anyway, so shipping 238 full edit paths is waste that
    # risks the bridge clipping the payload mid-string (which surfaces as
    # "unexpected output" with a fragment of JSON on screen).
    cot = d.get("cot", {})
    # Feed lives under `steps` (the widget's key). Do NOT mirror it to
    # `items` -- the duplicate key serialized the whole list twice and
    # halved the number of steps that fit under the size guard.
    feed = cot.get("steps") or []
    for s in feed:
        t = str(s.get("text") or "")
        if len(t) > 220:
            s["text"] = t[:217] + "..."
    # Trim the angle summary too: it competes with the feed for the budget.
    ang = cot.get("angle") or {}
    if len(str(ang.get("summary") or "")) > 240:
        ang["summary"] = str(ang["summary"])[:237] + "..."
    if len(str(ang.get("lens") or "")) > 160:
        ang["lens"] = str(ang["lens"])[:157] + "..."
    # F-R2: shrink the rows' `see` blocks FIRST (the "See it" column needs
    # only url/kind; the note/url text is decoration the widget shows in a
    # tooltip). Four rows of full `see` cost ~1.3 KB -- freed BEFORE popping
    # steps, so the live transcript survives.
    for r in d.get("rows") or []:
        s = r.get("see")
        if isinstance(s, dict):
            r["see"] = {k: s.get(k) for k in ("url", "kind") if s.get(k)}
    # HARD SIZE GUARD. The desktop bridge returns only the LAST 4000 chars of
    # stdout; anything bigger gets its JSON head chopped, which the widget
    # reports as "activity did not return JSON (Unexpected token ...)" and the
    # whole panel goes blank. Drop the OLDEST steps until it fits, so the
    # payload can never be clipped. (Measured failure: the 1500-char prompt
    # block alone made the guard pop ALL 21 items -> the live feed shipped
    # empty every tick, 2026-09-27.)
    payload = json.dumps(d, separators=(",", ":"))
    while len(payload) > 3400 and len(feed) > 1:
        feed.pop(0)
        payload = json.dumps(d, separators=(",", ":"))
    if len(payload) > 3400:
        # Even one step does not fit: sacrifice the angle block entirely --
        # a live feed with no angle caption beats no live feed at all.
        cot.pop("angle", None)
        cot.pop("angle_history", None)
        payload = json.dumps(d, separators=(",", ":"))
    while len(payload) > 3400 and feed:
        feed.pop(0)
        payload = json.dumps(d, separators=(",", ":"))
    # FLOOR GUARD: the rows block alone can exceed the budget (rows carry the
    # round/angle/failure fields). Popping steps cannot fix that, so degrade
    # the rows themselves -- drop the optional keys first, then shorten the
    # free-text ones. The widget only needs name/running/rounds/never_rotated/
    # active_task; everything else is decoration. Never emit >3400 B: a
    # clipped payload makes the widget's JSON.parse throw and blanks the panel.
    # F-R (2026-10-05, Gene: "the elapsed time at the very top doesn't work"):
    # `elapsed` was in ROW_OPTIONAL and got dropped whenever the payload popped
    # over budget -- while every row still carried a ~326-byte `see` block.
    # Reorder the sacrifice: shrink `see` (the "See it" column only needs
    # url/kind), THEN drop the other optionals. `elapsed` is NEVER dropped:
    # it is 8 bytes and the one number the operator watches.
    if len(payload) > 3400:
        for r in d.get("rows") or []:
            s = r.get("see")
            if isinstance(s, dict):
                r["see"] = {k: s.get(k) for k in ("url", "kind") if s.get(k)}
        payload = json.dumps(d, separators=(",", ":"))
    ROW_OPTIONAL = ("angle_family", "angle_round", "thread_title",
                    "active_status", "last_error")
    if len(payload) > 3400:
        for r in d.get("rows") or []:
            for k in ROW_OPTIONAL:
                r.pop(k, None)
        payload = json.dumps(d, separators=(",", ":"))
    if len(payload) > 3400:
        for r in d.get("rows") or []:
            if isinstance(r.get("active_task"), str) and len(r["active_task"]) > 60:
                r["active_task"] = r["active_task"][:57] + "..."
            if isinstance(r.get("thread_title"), str) and len(r["thread_title"]) > 60:
                r["thread_title"] = r["thread_title"][:57] + "..."
        payload = json.dumps(d, separators=(",", ":"))
    if len(payload) > 3400:
        # Last resort: keep the feed and the row identities, drop the rest.
        d["rows"] = [{k: r.get(k) for k in
                      ("name", "running", "rounds", "never_rotated", "active_task")
                      if k in r} for r in (d.get("rows") or [])]
        cot.pop("angle_history", None)
        payload = json.dumps(d, separators=(",", ":"))
    return payload



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true", help="human readable")
    ap.add_argument("--steps", type=int, default=18, help="max CoT steps")
    ap.add_argument("--pretty", action="store_true",
                    help="indented JSON (default is compact: the desktop bridge "
                         "clips large stdout, and indent=1 on 40 steps is ~8 KB")
    ap.add_argument("--selfcheck", action="store_true",
                    help="validate the widget payload contract and exit")
    a = ap.parse_args()
    if a.selfcheck:
        return selfcheck()
    d = activities(max_steps=a.steps)
    if a.text:
        print(render_text(d))
    elif a.pretty:
        print(json.dumps(d, indent=1))
    else:
        print(compact_payload(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())