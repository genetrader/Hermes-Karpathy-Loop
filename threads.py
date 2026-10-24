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
# The leading yellow square is a deliberate VISUAL marker (Gene, 2026-09-26):
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

HERMES_HOME = Path(os.environ.get("HERMES_HOME")
                   or (Path.home() / "AppData" / "Local" / "hermes"))
STATE_DB = HERMES_HOME / "state.db"
HERMES_PY = HERMES_HOME / "hermes-agent" / "venv" / "Scripts" / "python.exe"
PROFILE = "default"


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


class RegistryCorrupt(RuntimeError):
    """The registry exists but cannot be parsed (W4: fail closed, never {})."""


LOCK = STATE.with_suffix(".lock")
LOCK_STALE_SEC = 900          # 15 min: longer than any single round


def _pid_alive(pid: int) -> bool:
    """Is this pid a live process on THIS host? (cheap, no psutil)"""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259        # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def acquire_lock(timeout: float = 20.0, poll: float = 0.15) -> bool:
    """Single-writer lock (W4). Returns True if held, False on timeout.

    A lock FILE carrying pid+timestamp rather than an OS lock: a killed runner
    must not wedge the loop forever. A lock whose holder is dead, or that is
    older than LOCK_STALE_SEC, is broken with a warning.
    """
    deadline = time.time() + timeout
    while True:
        try:
            fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({"pid": os.getpid(),
                                     "at": time.time()}).encode())
            os.close(fd)
            return True
        except FileExistsError:
            holder, age = 0, 0.0
            try:
                info = json.loads(LOCK.read_text(encoding="utf-8"))
                holder = int(info.get("pid") or 0)
                age = time.time() - float(info.get("at") or 0)
            except Exception:
                age = LOCK_STALE_SEC + 1     # unreadable == treat as stale
            if holder and _pid_alive(holder) and age < LOCK_STALE_SEC:
                if time.time() >= deadline:
                    return False
                time.sleep(poll)
                continue
            print("threads: BREAKING STALE LOCK (pid=%s, age=%.0fs)"
                  % (holder or "?", age))
            try:
                LOCK.unlink()
            except Exception:
                pass
        except Exception as e:
            print("threads: lock acquisition error: %s" % e)
            return False


def release_lock() -> None:
    try:
        info = json.loads(LOCK.read_text(encoding="utf-8"))
        if int(info.get("pid") or 0) != os.getpid():
            return           # not ours -- never release another process's lock
    except Exception:
        pass
    try:
        LOCK.unlink()
    except Exception:
        pass


class _locked:
    """Context manager: hold the registry lock, release it even on raise."""

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout
        self.held = False

    def __enter__(self):
        self.held = acquire_lock(self.timeout)
        if not self.held:
            raise TimeoutError("registry lock busy for %.0fs" % self.timeout)
        return self

    def __exit__(self, *exc):
        if self.held:
            release_lock()
        return False


def load(strict: bool = False) -> dict:
    """Load the registry. A CORRUPT file is NEVER silently treated as empty.

    W4: quarantines live in this registry. The old behavior -- move the damaged
    file aside and return {} -- meant one torn write un-quarantined every repo
    and erased 893 rounds of history while the loop carried on happily. That is
    the opposite of failing closed.

    strict=False (default): raise RegistryCorrupt so the caller can pause and
                            surface, preserving the moved-aside evidence.
    strict=True:            return {} -- ONLY for read-only inspection tools
                            (status panels) that must not crash on bad state.
    """
    try:
        return json.loads(STATE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        salvage = None
        try:
            if STATE.exists():
                salvage = STATE.parent / ("threads.json.corrupt-%s"
                                          % time.strftime("%Y%m%d-%H%M%S"))
                STATE.replace(salvage)
        except Exception:
            pass
        if strict:
            print("threads: REGISTRY CORRUPT (%s) -- moved to %s; strict "
                  "reader continuing empty." % (e, salvage and salvage.name))
            return {}
        raise RegistryCorrupt(
            "threads.json is corrupt (%s). Evidence preserved at %s. "
            "Refusing to continue with an EMPTY registry -- that would "
            "silently un-quarantine every repo and forget all round "
            "history. Inspect the salvaged file, then restore or rebuild."
            % (e, salvage.name if salvage else "<unavailable>"))


def save(data: dict, take_lock: bool = True) -> None:
    """Atomic registry write: temp file + os.replace, so a reader (or a crash
    mid-write) can never observe a torn file. (threads.json holds every
    project's rounds, campaigns, and quarantine state -- losing it silently
    resets the whole loop.)

    W4: also takes the single-writer lock unless the caller already holds it
    (take_lock=False), because runner/loopctl/plugin all read-modify-write
    this file and the later save silently discarded the earlier one's round.
    """
    STATE.parent.mkdir(parents=True, exist_ok=True)
    if take_lock:
        with _locked():
            _write_atomic(data)
    else:
        _write_atomic(data)


def _write_atomic(data: dict) -> None:
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, STATE)


def update(mutator) -> dict:
    """Read-modify-write the registry under ONE lock (W4).

    THE fix for the lost-update race: `reg = load(); reg[x] = y; save(reg)`
    lets two writers interleave so the later save discards the earlier's
    round. `update(lambda reg: reg.__setitem__(x, y))` holds the lock across
    the whole cycle. Returns the registry as written.
    """
    with _locked():
        reg = load()
        mutator(reg)
        save(reg, take_lock=False)
        return reg


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
    # The thread's STORED route is first-call-wins and sticky across resumes:
    # a thread created without a model keeps the desktop default forever and
    # every round silently runs it (the 2026-10-11 zip-code-ranker loop: the
    # command line said GLM, the session said red-snow). Pin the session to
    # the loop's builder seat so resume can never revert it.
    cmd = [str(HERMES_PY), "-m", "hermes_cli.main", "-p", PROFILE]
    # FULL `custom:slug:model` seat string -- NOT the display name. A bare
    # model name mis-parses (auto-detect finds nothing, free-cloud fallback),
    # which is exactly how the September INIT threads ended up on
    # upstage/solar-pro4:free.
    _seat = ""
    try:
        _seat = (json.loads((ROOT / "state" / "loop.json").read_text(
            encoding="utf-8")).get("implementer") or "").strip()
    except Exception:
        pass
    if _seat:
        cmd += ["--model", _seat]
    cmd += ["-z", prompt]
    try:
        subprocess.run(
            cmd,
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
