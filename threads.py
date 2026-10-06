#!/usr/bin/env python
"""
threads.py -- the repo -> Hermes session registry for the Karpathy Loop.

One long-lived Hermes Desktop session per assigned repo, so the thread is
visible in the app, carries the FULL harness (skills/memory/tools of its
profile), and can be compacted instead of discarded.

Deliberately tiny: a JSON file mapping project -> session id + counters. The
sessions themselves live in Hermes' own state.db; we only remember which id
belongs to which repo.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state" / "threads.json"

# Every loop thread is titled with this prefix so they are easy to find in the
# desktop sidebar and in `sessions` queries.
#
# The leading yellow square is a deliberate VISUAL marker (the operator, 2026-09-26):
# the sidebar highlight he wanted is native desktop UI with no plugin hook, so
# the marker rides in the title instead -- scannable in the pinned list, zero
# app-rebuild risk, and it survives restarts because it is real persisted data.
# The title is the ONLY reliable carrier here: `title_for()` is applied at every
# write path (create / maintained / round finish), so an existing thread picks
# the marker up on its next healing pass rather than needing a manual retitle.
PREFIX = "\U0001F7E8 KL :: "
# What older threads carry, so title_for() can strip it and re-stamp cleanly
# instead of producing "\U0001F7E8 KL :: \U0001F7E8 KL :: <name>".
_LEGACY_PREFIXES = ("\U0001F7E8 KL :: ", "KL :: ")

import settings as _S   # noqa: E402  machine config: settings.py is the source

HERMES_HOME = _S.hermes_home()
STATE_DB = HERMES_HOME / "state.db"
HERMES_PY = _S.hermes_python()
PROFILE = _S.hermes_profile() or "default"


def _seat_model_name() -> str:
    """The loop's implementer seat, display-model part only.

    loop.json's `implementer` is the SETTING for what the rounds run on
    (loopctl config --implementer). The sidebar should show the same model
    the work actually uses -- not whatever the thread was created with
    (the 2026-10-01 DeepSeek-display confusion). No hard-coded model here:
    the seat is read fresh on every watchdog tick.
    """
    try:
        cfg = json.loads((ROOT / "state" / "loop.json").read_text(encoding="utf-8"))
        seat = (cfg.get("implementer") or "").strip()
        return seat.rsplit(":", 1)[-1] if seat else ""
    except Exception:
        return ""


def title_for(project: str) -> str:
    """Stable display title for a project's thread. Idempotent.

    Strips WHICHEVER marker the name already carries (current or legacy) before
    re-stamping, so calling this twice never accumulates prefixes -- the old
    code only knew its own PREFIX, which would have doubled the square the first
    time an already-marked title was healed.
    """
    name = str(project).strip()
    for p in _LEGACY_PREFIXES:
        if name.startswith(p):
            name = name[len(p):]
            break
    return PREFIX + name.strip()


def load() -> dict:
    """Load the registry. A CORRUPT file is never silently reset: the damaged
    file is moved aside with a timestamp so the data is recoverable, and the
    loss is loud in the log. (Old triage B5: load()'s bare `except: return {}`
    turned one torn write into a wiped registry on the next save.)"""
    try:
        return json.loads(STATE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        # Torn/corrupt: preserve the evidence, then start empty.
        try:
            if STATE.exists():
                salvage = STATE.parent / ("threads.json.corrupt-%s"
                                          % time.strftime("%Y%m%d-%H%M%S"))
                STATE.replace(salvage)
                print("threads: REGISTRY CORRUPT (%s) -- moved to %s; starting "
                      "empty. Round/campaign history was in the moved file."
                      % (e, salvage.name))
        except Exception:
            pass
        return {}


def save(data: dict) -> None:
    """Atomic registry write: temp file + os.replace, so a reader (or a crash
    mid-write) can never observe a torn file. (threads.json holds every
    project's rounds, campaigns, and quarantine state -- losing it silently
    resets the whole loop.)"""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, STATE)


def _live_session_ids() -> set:
    """Session ids that exist in the profile's state.db."""
    try:
        c = sqlite3.connect("file:%s?mode=ro" % STATE_DB.as_posix(), uri=True)
        ids = {str(r[0]) for r in c.execute("select id from sessions")}
        c.close()
        return ids
    except Exception:
        return set()


def _find_by_marker(marker: str):
    """
    Locate the session whose FIRST USER MESSAGE carried `marker`.

    Two traps made the naive version wrong (2026-09-24):
      1. A plain `content LIKE '%marker%'` also matches any session that merely
         READ or WROTE the string -- e.g. a chat where someone inspects
         threads.py, or a compaction summary of one. That returned unrelated
         sessions (and my own working session), so `resolve()` sometimes
         resumed a round onto the WRONG conversation.
      2. `order by rowid desc` then picks whichever mentioned it LAST, so the
         choice drifts as you work.

    The init prompt is the first USER row of its session, so match only
    role='user' rows and take the EARLIEST (that is the session's own init).
    """
    try:
        c = sqlite3.connect("file:%s?mode=ro" % STATE_DB.as_posix(), uri=True)
        rows = c.execute(
            "select session_id, rowid from messages "
            "where role = 'user' and content like ? order by rowid asc",
            ("%" + marker + "%",)).fetchall()
        c.close()
    except Exception:
        return None
    for sid, rowid in rows:
        try:
            c = sqlite3.connect("file:%s?mode=ro" % STATE_DB.as_posix(), uri=True)
            first = c.execute(
                "select rowid from messages where session_id = ? "
                "and role = 'user' order by rowid asc limit 1", (sid,)).fetchone()
            c.close()
        except Exception:
            continue
        # The init prompt IS the session's first user message.
        if first and int(first[0]) == int(rowid):
            return str(sid)
    return None


def _stamp_source(session_id: str, source: str = "cron") -> None:
    """
    Tag the session row so the desktop sidebar picks it up.

    The sidebar's built-in collapsible sections are driven by `sessions.source`
    (recents excludes 'oneshot', the "Cron jobs" section fetches source='cron').
    Threads created by the loop must land in a visible, collapsible category --
    source='cron' puts them in the Cron section next to the scheduler's own runs.
    Best-effort: a locked db must never break a round.
    """
    try:
        c = sqlite3.connect(str(STATE_DB), timeout=30)
        c.execute("PRAGMA busy_timeout=20000")
        c.execute("update sessions set source=? where id=?", (source, session_id))
        c.commit()
        c.close()
    except Exception:
        pass


def _create_session(project: str):
    """
    Create a real Hermes session for `project` and return its id.

    A bare `hermes -z` with no resume starts a fresh session; we then locate it
    by the unique init marker we planted in its first message. Uses the
    supported CLI rather than writing to state.db by hand.
    """
    marker = "KL::%s::INIT" % project
    prompt = ("Karpathy Loop thread for project '%s'. %s "
              "Reply with the single word: ready" % (project, marker))
    try:
        subprocess.run(
            [str(HERMES_PY), "-m", "hermes_cli.main", "-p", PROFILE, "-z", prompt],
            cwd=str(HERMES_HOME / "hermes-agent"),
            capture_output=True, text=True, timeout=900, errors="replace", creationflags=0x08000000)
    except Exception:
        return None
    return _find_by_marker(marker)


def chain_tip(session_id: str) -> str:
    """
    Follow a session's compression chain to its newest descendant.

    When Hermes compacts a long thread it creates a CHILD session and moves the live
    transcript there. `--resume <parent>` then silently redirects to the child
    (`session_db.resolve_resume_session_id`), so a round appears to "resume thread A"
    while every message lands in child B. The widget watched A and showed a dead
    transcript -- the "static blurbs, not real time" symptom (2026-09-24).

    Always hand out the TIP: the session that actually holds the newest messages.
    """
    cur = session_id
    seen: set[str] = set()
    while cur and cur not in seen:
        seen.add(cur)
        try:
            c = sqlite3.connect("file:%s?mode=ro" % STATE_DB.as_posix(), uri=True)
            row = c.execute(
                "select id from sessions where parent_session_id = ? "
                "order by started_at desc limit 1", (cur,)).fetchone()
            c.close()
        except Exception:
            return cur
        if not row or not row[0]:
            return cur
        cur = str(row[0])
    return cur


def chain_ids(session_id: str) -> list:
    """[head ... tip] -- every session in the compression chain, oldest first."""
    out = []
    cur = session_id
    seen: set[str] = set()
    while cur and cur not in seen:
        seen.add(cur)
        out.append(cur)
        try:
            c = sqlite3.connect("file:%s?mode=ro" % STATE_DB.as_posix(), uri=True)
            row = c.execute(
                "select id from sessions where parent_session_id = ? "
                "order by started_at asc limit 1", (cur,)).fetchone()
            c.close()
        except Exception:
            break
        if not row or not row[0]:
            break
        cur = str(row[0])
    return out


def resolve(project: str):
    """
    The session id for this project's thread, creating one if needed.

    A recorded id is validated against state.db -- a deleted or archived
    session must not be resumed blindly, or `--resume` fails every round and
    the project silently stops making progress.
    """
    reg = load()
    entry = reg.get(project) or {}
    sid = entry.get("session_id")
    if sid and sid in _live_session_ids():
        # Resume the CHAIN TIP, not the head: Hermes redirects a resume of an
        # already-compacted session to its descendant anyway, so returning the head
        # makes the registry disagree with where the messages actually land.
        return chain_tip(sid)

    # Adopt an orphaned init session (created but never registered) before
    # paying for a new one.
    sid = _find_by_marker("KL::%s::INIT" % project)
    if not sid:
        sid = _create_session(project)
    if sid:
        _stamp_source(sid)
        entry["session_id"] = sid
        entry.setdefault("rounds", 0)
        entry["title"] = title_for(project)
        reg[project] = entry
        save(reg)
    return sid


def maintained() -> dict:
    """
    Heal the sidebar view of every thread and return {project: tip}.

    A compacted thread grows children; without this the sidebar accumulates
    "(part N)" rows, the old heads look stale, and a tip can sit untitled because
    nothing re-stamped it. Cheap and idempotent -- the watchdog calls it each tick.
    """
    reg = load()
    out: dict = {}
    try:
        c = sqlite3.connect(str(STATE_DB), timeout=30)
        c.execute("PRAGMA busy_timeout=20000")
    except Exception:
        return out
    try:
        import time as _t
        now = _t.time()
        for project, entry in (reg or {}).items():
            head = entry.get("session_id") or ""
            if not head:
                continue
            tip = chain_tip(head)
            if not tip:
                continue
            chain = chain_ids(head)
            out[project] = tip
            # T3-7 (2026-09-30): the tick used to stamp last_activity_at=now
            # on EVERY pass, so a thread no round ever touched still looked
            # "active minutes ago" -- the activity view lied. Stamp only when
            # the chain actually moved or the title needs healing.
            changed = (entry.get("tip") != tip
                       or entry.get("chain") != chain
                       or entry.get("title") != title_for(project))
            entry["tip"] = tip
            entry["chain"] = chain
            entry.setdefault("title", title_for(project))
            # Only the tip stays visible: the pre-compaction parts are history and
            # belong behind the tip, not as separate sidebar rows.
            _seat_model = _seat_model_name()
            for sid in chain:
                if sid == tip:
                    if _seat_model:
                        row = c.execute("select model from sessions where id=?",
                                        (sid,)).fetchone()
                        if row and row[0] != _seat_model:
                            c.execute("update sessions set model=? where id=?",
                                      (_seat_model, sid))
                    if changed:
                        c.execute("update sessions set source='cron',"
                                  " title=?, display_name=?, hidden=0,"
                                  " last_activity_at=? where id=?",
                                  (title_for(project), title_for(project),
                                   now, sid))
                    else:
                        # keep the tip visible without bumping the activity
                        # clock (hidden may have been flipped elsewhere).
                        c.execute("update sessions set hidden=0 where id=?", (sid,))
                else:
                    c.execute("update sessions set hidden=1 where id=?", (sid,))
        c.commit()
        # F-G (2026-10-02): NEVER save the snapshot taken at function entry.
        # The runner (separate process, up to 70 min per round) may have saved
        # rounds/campaign/quarantine state while we held the stale `reg`; a
        # save of that snapshot reverts the registry to pre-round state
        # (reproduced: interleaved saves read rounds 5 after the runner wrote
        # 6). Re-load a FRESH copy and merge in ONLY the keys we computed.
        fresh = load()
        if isinstance(fresh, dict):
            for project, tip in out.items():
                ent = fresh.get(project)
                if isinstance(ent, dict):
                    ent["tip"] = tip
            save(fresh)
    except Exception as e:
        # F-G: a silent `except: pass` here hid failed saves entirely.
        print("threads.maintained: sidebar maintenance failed: %s" % e)
    finally:
        c.close()
    return out
