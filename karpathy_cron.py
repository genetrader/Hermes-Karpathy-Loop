#!/usr/bin/env python3
"""
karpathy_cron.py -- cron entrypoint for the Karpathy Loop.

Runs on a schedule and does the quiet work: discovery + rotator tick.
Deliberately NO-AGENT: it shells straight to the Python tools and prints a
short line, so it never burns model tokens just to check whether a card
should be created.

  python karpathy_cron.py discover   # thread + codebase discovery, rebuild UI
  python karpathy_cron.py rotator    # is the loop running? create a round if so
  python karpathy_cron.py status
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"C:\CODING\project-improver")
PY = Path(r"<LOCALAPPDATA>\..\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe")


def run(script: str, *args, timeout=600):
    cmd = [str(PY), str(ROOT / script), *args]
    try:
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 1, "", f"timeout after {timeout}s"
    except Exception as e:
        return 1, "", str(e)


def log(msg: str) -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    with (ROOT / "logs" / "cron.log").open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}\n")


def cmd_discover(argv):
    rc, out, err = run("discover.py", "run")
    if rc != 0:
        log(f"discover FAILED rc={rc} {err[:300]}")
        print(f"discovery failed: {err[:200]}")
        return 1
    first = [l for l in out.splitlines() if "new threads" in l or "new codebases" in l]
    summary = "; ".join(x.strip() for x in first)

    # The selector rebuild costs several seconds (225 projects x git facts) and blew the
    # desktop bridge's hard 30s shell.exec ceiling -- "discovery timed out after 30 seconds",
    # 2026-09-24. Two changes make that unreachable:
    #   * parallel git facts inside build_selector_data.py (~15s -> ~7s)
    #   * skip the rebuild entirely when discovery found NOTHING new (the common case: the
    #     page's data is already current, so there is nothing to re-render).
    force = "--force-ui" in argv or "--force" in argv
    new_finds = not re.search(r"new threads\s*:\s*0\b", out) or \
                not re.search(r"new codebases\s*:\s*0\b", out)
    if new_finds or force:
        run("build_selector_data.py")
        run("build_selector.py")
        summary += " (selector rebuilt)"
    else:
        summary += " (nothing new -- selector rebuild skipped)"

    log(f"discover ok — {summary}")
    print(f"[karpathy] {summary}")
    return 0


def cmd_rotator(argv):
    # T2-7 (2026-09-30): this used to spawn `improver.py run`, whose
    # kanban-card path is RETIRED (exits rc=2) -- and the mapping
    # `return 0 if rc == 0 else 0` turned that failure into a silent green
    # tick. The continuous runner owns rounds now; this command only
    # REPORTS: is the loop armed, and is a runner actually driving it? (The
    # active watchdog cron karpathy-nudge owns respawning; nothing spawns
    # cards here anymore.)
    rc, out, err = run("loopctl.py", "should-run")
    if rc != 0:
        # paused / capped -- say so once, quietly
        print(f"[karpathy] idle: {out.strip()}")
        return 0
    # armed: report runner liveness (read-only; the watchdog owns respawns).
    rrc, rout, rerr = run("karpathy_runner.py", "--check-alive", timeout=60)
    state = (rout or rerr).strip() or "no output"
    log(f"rotator tick: loop armed; runner check rc={rrc} {state[:160]}")
    print(f"[karpathy] loop ARMED -- runner check rc={rrc}: {state[:200]}")
    return 0


def cmd_status(argv):
    rc, out, _ = run("loopctl.py", "status")
    print(out)
    rc, out, _ = run("discover.py", "report")
    print(out)
    return 0


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "status"
    sys.exit({"discover": cmd_discover, "rotator": cmd_rotator,
              "status": cmd_status}.get(what, cmd_status)(sys.argv[1:]))