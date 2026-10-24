#!/usr/bin/env python
"""
karpathy_nudge.py -- advance ONE project thread by ONE round.

Replaces the kanban-card worker (Task 6 of the 2026-09-23 rebuild plan). A
round is now a turn in a real, long-lived Hermes Desktop session:
  hermes -p <profile> -z "<round prompt>" --resume <session_id>
so the work is visible in the app, resumable, and carries the full harness.
NO-AGENT when there is nothing to do: exits before spending a single token if
the loop is paused or another nudge is in flight.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import threads                     # noqa: E402
import round_prompt                # noqa: E402
import loopctl                     # noqa: E402
import angle_pick                  # noqa: E402
import improver as I               # noqa: E402
import discord_notify as dn        # noqa: E402

HERMES_HOME = Path(r"C:\Users\gene\AppData\Local\hermes")
HERMES_PY = HERMES_HOME / "hermes-agent" / "venv" / "Scripts" / "python.exe"
LOG = ROOT / "logs" / "nudge.log"
LOCK = ROOT / "state" / "nudge.lock"
LOCK_STALE_S = 6 * 3600


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))


def drive(session_id: str, prompt: str, timeout: int = 5400):
    """Run one turn in the thread. Returns (rc, stdout tail)."""
    cmd = [str(HERMES_PY), "-m", "hermes_cli.main",
           "-p", threads.PROFILE, "-z", prompt, "--resume", session_id]
    try:
        r = subprocess.run(cmd, cwd=str(HERMES_HOME / "hermes-agent"),
                           capture_output=True, text=True, timeout=timeout,
                           errors="replace", creationflags=0x08000000)
        return r.returncode, ((r.stdout or "") + (r.stderr or ""))[-4000:]
    except subprocess.TimeoutExpired:
        return 124, "timeout after %ds" % timeout


def main(argv=None) -> int:
    lc = loopctl.load()
    if not lc.get("running"):
        log("paused (%s) -- nothing nudged" % (lc.get("paused_reason") or "not started"))
        return 0

    # One round at a time: refuse to start a second while one is in flight.
    if LOCK.exists():
        try:
            age = time.time() - LOCK.stat().st_mtime
            pid = int(LOCK.read_text(encoding="utf-8").strip() or 0)
        except Exception:
            age, pid = 0, 0
        if age < LOCK_STALE_S and pid and _pid_alive(pid):
            log("another nudge is in flight (pid %s, %ds) -- exiting" % (pid, int(age)))
            return 0
        LOCK.unlink(missing_ok=True)
    LOCK.write_text(str(os.getpid()), encoding="utf-8")

    try:
        return _round()
    finally:
        LOCK.unlink(missing_ok=True)


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV"],
                             capture_output=True, text=True, timeout=60,
                             errors="replace", creationflags=0x08000000).stdout or ""
        return "python" in out.lower()
    except Exception:
        return True  # assume alive on failure: never double-fire


def _round() -> int:
    m = I.load_manifest()
    projs = I.enabled(m)
    if not projs:
        log("no enabled projects")
        return 1

    reg = threads.load()
    # Least-recently-nudged project wins; never-nudged sorts first.
    projs = sorted(projs, key=lambda p: (reg.get(p["name"], {}).get("last_nudge") or 0))
    proj = projs[0]
    name = proj["name"]

    sid = threads.resolve(name)
    if not sid:
        log("could not resolve/create a thread for %s" % name)
        return 1

    entry = reg.setdefault(name, {})
    round_no = int(entry.get("rounds") or 0) + 1
    facts = I.repo_facts(proj)
    angle = angle_pick.pick(name, facts=facts)
    prompt = round_prompt.build(name, angle, proj.get("gate") or "", round_no, entry)

    dn.progress(name, "round %d: angle %s -- nudge starting" %
                (round_no, (angle or {}).get("id")), round_no=round_no)
    rc, tail = drive(sid, prompt)

    entry["rounds"] = round_no
    entry["last_nudge"] = time.time()
    entry["last_rc"] = rc
    entry["last_angle"] = (angle or {}).get("id")
    entry["session_id"] = sid
    entry["title"] = threads.title_for(name)
    reg[name] = entry
    threads.save(reg)

    # compaction check (Task 9): every 8 rounds, compact the thread
    if round_no % 8 == 0:
        try:
            import compact_thread
            crc = compact_thread.compact(sid)
            entry["last_compacted_round"] = round_no
            entry["compact_count"] = int(entry.get("compact_count") or 0) + 1
            reg[name] = entry
            threads.save(reg)
            log("%s round %d compacted rc=%s" % (name, round_no, crc))
        except Exception as e:
            log("%s round %d compaction FAILED: %s" % (name, round_no, e))

    log("%s round %d rc=%s" % (name, round_no, rc))
    dn.progress(name, "round %d done (rc=%s)" % (round_no, rc), round_no=round_no)
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
