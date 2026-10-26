#!/usr/bin/env python
"""
karpathy_runner.py -- the continuous round runner (Start/Pause model).

Gene's model (2026-09-23, verbatim intent): "It starts when I start it and it
runs until it reaches a certain threshold... then saving a GitHub checkpoint...
ends the run with the updated checkpoint... goes to another project or does
another round... It's either running or it's not running."

Design:
  Start loop  ->  loopctl.running=true  +  spawn this runner (background)
  Pause loop  ->  loopctl.running=false  (the runner sees it and exits cleanly)
  cron        ->  watchdog ONLY: re-spawns the runner if running=true but the
                  runner process died (crash recovery), never a timed trigger.

The runner loops forever while running=true:
  pick least-recently-nudged project -> drive ONE round in its thread
  -> checkpoint tag + GitHub push -> rotate to the next project
  -> repeat immediately, no waiting between rounds.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import msvcrt
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import threads                     # noqa: E402
import round_prompt                # noqa: E402
import loopctl                     # noqa: E402
import angle_pick                  # noqa: E402
import improver as I               # noqa: E402
import scope                       # noqa: E402
import discord_notify as dn        # noqa: E402
import seat_preflight as pf        # noqa: E402
import checkpoint as checkpoint_mod  # noqa: E402

HERMES_HOME = Path(r"C:\Users\gene\AppData\Local\hermes")
HERMES_PY = HERMES_HOME / "hermes-agent" / "venv" / "Scripts" / "python.exe"

# --- model seats -----------------------------------------------------------------
# F3.7 / T2-3 (2026-09-30): seats are NOT module constants anymore. The implementer
# seat is read from state/loop.json every round (loopctl.load()["implementer"]); the
# reviewer seat is handed to the round prompt the same way (round_prompt.build(seats=)).
# The old constants hard-coded seats whose providers were HIDDEN on 2026-09-30
# (deepseek-v41-flash-3x-spark, glm53-flash-2x-spark) -- the loop kept launching
# children against a dead endpoint for hours because nothing validated the seat.
# main() now validates both seats at startup (_validate_seats) and refuses to run
# the loop on a dead or malformed one.
BUILDER_FALLBACK = os.environ.get(
    "KL_BUILDER_MODEL", "custom:glm53-flash-4x-spark-tp4:GLM-5.3-Flash-FP8")
REVIEWER_FALLBACK = os.environ.get(
    "KL_REVIEWER_MODEL", "custom:evo-x2:qwen3.8-flash-next")
# Test isolation (2026-10-01): tests that drive run_round() must NOT pollute the
# LIVE runner.log -- 367 "t round 1" junk lines from a test run leaked into the
# monitor's Recent activity feed. Any process can point the log elsewhere with
# KL_RUNNER_LOG; the live runner and the watchdog keep the default.
LOG = Path(os.environ.get("KL_RUNNER_LOG", str(ROOT / "logs" / "runner.log")))
# Per-round wall-clock ceiling. Rounds normally take 50-60 min; this is generous
# but bounded so a single hung child cannot stall the rotation for hours (it was
# 7200s with no kill).
ROUND_TIMEOUT = int(os.environ.get("KL_ROUND_TIMEOUT", "4200"))
# Written at the START and END of every round. `activity.py` and the widget read
# this to tell a HEALTHY loop from one whose status flag says running while
# nothing is actually happening (the 2026-09-28 wedge: flag said RUNNING, no
# round had landed in 104 minutes).
HEARTBEAT = ROOT / "state" / "runner_heartbeat.json"
PID_FILE = ROOT / "state" / "runner.pid"


def _persist_entry(name: str, entry: dict) -> None:
    """Persist ONE registry entry to disk, best-effort with a loud log.

    Review HIGH-1 (2026-10-01, repro): the refusal paths set round_refused /
    last_nudge / last_rc and returned WITHOUT any save, and main() never
    saves either -- so a persistently-refusing repo was re-picked every tick
    and the "refusal consumes the slot" fix was dead code. Called from every
    early-return path in run_round."""
    try:
        def _persist_refusal(reg):
            reg[name] = entry
        threads.update(_persist_refusal)
    except Exception as e:
        log("%s: refusal state NOT persisted: %s" % (name, e))


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))


def _sweep_worktree_processes(wt_path: str) -> int:
    """F-T (2026-10-05): kill any process whose command line references the
    round's worktree path. Rounds on next.js projects leak detached `next
    start` dev servers (measured: r57 held one, r58 leaked THREE); they lock
    the worktree so cleanup fails and the repo gets quarantined. Called at
    round END regardless of outcome. Returns how many were killed."""
    import csv as _csv, io as _io
    if not wt_path:
        return 0
    needle = str(wt_path).replace("/", "\\").lower()
    try:
        o = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object "
             "{$null -ne $_.CommandLine} | Select-Object ProcessId,CommandLine "
             "| ConvertTo-Csv -NoTypeInformation"],
            capture_output=True, text=True, timeout=60, errors="replace",
            creationflags=0x08000000)
        rows = list(_csv.DictReader(_io.StringIO(o.stdout or "")))
    except Exception:
        return 0
    killed = 0
    for r in rows:
        cl = (r.get("CommandLine") or "").replace("/", "\\").lower()
        if needle in cl and (r.get("ProcessId") or "").strip().isdigit():
            pid = int(r["ProcessId"])
            if pid == os.getpid():
                continue
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=30,
                               creationflags=0x08000000)
                killed += 1
            except Exception:
                pass
    return killed


def _kill_tree(pid) -> str:
    """Kill a round's child process tree; never raise. Returns what happened."""
    if not pid:
        return "no child pid recorded"
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, text=True, timeout=60,
                       errors="replace", creationflags=0x08000000)
        return "killed child pid %s" % pid
    except Exception as e:
        return "kill failed for pid %s: %r" % (pid, e)


def heartbeat(state: str, **extra) -> None:
    """Stamp runner liveness so a wedged loop is DETECTABLE, not silent.

    The 2026-09-28 wedge: loop.json said running=true for 104 minutes while no
    round was in flight and no round landed. Nothing anywhere recorded 'the
    runner is actually doing something', so every surface kept reporting healthy.
    This file is the missing signal. Never raises: liveness must not break the run.
    """
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": time.time(), "pid": os.getpid(), "state": state}
        payload.update(extra)
        tmp = HEARTBEAT.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(HEARTBEAT)          # atomic swap so readers never see a half file
    except Exception:
        pass


_LOCK_FILE = ROOT / "state" / "runner.locktxt"
_lock_handle = None            # module-global: process-lifetime OS byte lock


def _other_runner_pids() -> list:
    """PIDs of any OTHER karpathy_runner.py python processes on this box.

    The pid-file lock alone cannot see a live runner that never registered
    (2026-10-06 incident: an Oct-2 zombie runner predating the lock kept
    rotating repos while a fresh Oct-6 runner held runner.pid -- two rounds
    ran at once and collided on worktrees). The backstop scans real process
    command lines and refuses to start alongside any sibling.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
             "Where-Object { $_.CommandLine -like '*karpathy_runner.py*' } | "
             "Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=60,
            errors="replace", creationflags=0x08000000).stdout or ""
    except Exception:
        return []
    # Exclude our OWN ancestry: karpathy_runner.py appears in our own cmdline
    # AND in a venv->uv wrapper parent (venv python relaunches via the uv
    # interpreter, so the direct parent is the same script). Walk parents.
    mine = set()
    pid = os.getpid()
    for _ in range(4):            # self + up to 3 ancestors is plenty
        mine.add(pid)
        try:
            out2 = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').ParentProcessId" % pid],
                capture_output=True, text=True, timeout=30,
                errors="replace", creationflags=0x08000000).stdout or ""
            pid = int(out2.strip())
            if pid <= 0:
                break
        except Exception:
            break
    pids = []
    for line in out.split():
        try:
            q = int(line)
        except ValueError:
            continue
        if q not in mine:
            pids.append(q)
    return pids


def _acquire_single_flight() -> bool:
    """
    Exclusive single-runner guard, two layers (2026-10-06 double-runner fix):

    1. OS-HELD BYTE LOCK on state/runner.locktxt (msvcrt.locking). The lock
       lives in the FILE HANDLE, not the file contents -- Windows releases it
       automatically when the owning process dies, so there is no stale-file
       guessing and no window where a crashed runner blocks the next start.
       This replaces the old O_EXCL pid-file dance whose stale-reclaim logic
       handed the lock to a second runner while a pre-lock zombie was alive.
    2. PROCESS-SCAN BACKSTOP: refuse if any other karpathy_runner.py image is
       running, whatever it thinks about locks (catches zombies from before
       this fix, and any future code path that forgets to take the lock).

    runner.pid is still written for diagnostics/back-compat readers.
    """
    global _lock_handle
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Layer 2 first (cheap, decisive against zombies)
        others = _other_runner_pids()
        if others:
            log("START REFUSED -- another karpathy_runner.py is already alive "
                "(pid %s). One runner at a time." % ", ".join(map(str, sorted(others))))
            return False
        # Layer 1: OS-held byte lock
        _LOCK_FILE.touch(exist_ok=True)
        fd = os.open(str(_LOCK_FILE), os.O_RDWR)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(fd)
            log("START REFUSED -- runner lock is held by another process.")
            return False
        _lock_handle = fd
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).encode())
        # diagnostics pid file for older readers
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception as exc:
        log("lock acquisition failed: %r" % (exc,))
        return False


def is_running() -> bool:
    """Is a runner process alive? (pid file + process check)

    F0-5 (2026-09-30): this used to accept ANY process whose PID matched,
    because it only checked `"python" in out.lower()` for `PID eq N`. A recycled
    PID belonging to an unrelated python.exe then read as "runner alive" -- the
    watchdog would never respawn, and `loopctl start` would refuse to spawn.
    The exact trap `_acquire_single_flight()` already fixes at :126-129, applied
    here too: match the IMAGE NAME and the PID in the CSV row.
    """
    # 2026-10-06 double-runner fix: ANY live karpathy_runner.py counts. A
    # zombie started before the lock existed (or that lost its pid file)
    # would otherwise read as dead and the watchdog would respawn alongside
    # it -- the exact two-repos-at-once incident.
    if _other_runner_pids():
        return True
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip() or 0)
    except Exception:
        return False
    if not pid:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/FO", "CSV"],
                             capture_output=True, text=True, timeout=60,
                             errors="replace", creationflags=0x08000000).stdout or ""
        for line in out.splitlines():
            cols = [c.strip('"') for c in line.split('","')]
            if len(cols) < 2:
                continue
            # tasklist CSV column 1 is "PID Session" (e.g. "4242 Console"):
            # the PID is the first whitespace-delimited token.
            pid_field = cols[1].split()[0] if cols[1].split() else ""
            if pid_field == str(pid) and "python.exe" in cols[0].lower():  # F-K: exclude pythonw.exe
                return True
        return False
    except Exception:
        return False


def _head_full_sha(workdir: str) -> str | None:
    """FULL 40-char HEAD sha, or None.

    Deliberately `rev-parse HEAD` (full) rather than `%h` (short), because the
    two are compared for equality: a short sha would never equal a full one and
    the comparison would silently always report "changed". (Saul, 2026-09-29.)
    Never raises.
    """
    if not workdir:
        return None
    try:
        r = subprocess.run(["git", "-C", workdir, "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=60,
                           errors="replace", creationflags=0x08000000)
        return (r.stdout or "").strip() or None if r.returncode == 0 else None
    except Exception:
        return None


def _is_ancestor(workdir: str, older: str, newer: str) -> bool | None:
    """True if `older` is an ancestor of `newer` (i.e. history moved FORWARD).

    Returns None when git cannot answer (unknown), which callers must treat as
    "not proven", never as success. Rejects the amend/rebase/reset case where the
    sha changed but the round did not build on the recorded baseline.
    """
    if not (workdir and older and newer):
        return None
    try:
        r = subprocess.run(["git", "-C", workdir, "merge-base", "--is-ancestor",
                            older, newer],
                           capture_output=True, text=True, timeout=60,
                           errors="replace", creationflags=0x08000000)
        # exit 0 = is an ancestor; 1 = is not; anything else = could not tell
        if r.returncode == 0:
            return True
        if r.returncode == 1:
            return False
        return None
    except Exception:
        return None


def _is_dirty(workdir: str) -> bool | None:
    """Is the working tree dirty (modified/staged/untracked)?

    True = dirty, False = clean, None = could not tell. `None` must be treated as
    "cannot guarantee a rollback", never as clean.
    """
    if not workdir:
        return None
    try:
        r = subprocess.run(["git", "-C", workdir, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=120,
                           errors="replace", creationflags=0x08000000)
        if r.returncode != 0:
            return None
        return bool((r.stdout or "").strip())
    except Exception:
        return None




# ---------------------------------------------------------------------------
# F3 (2026-09-30): per-round worktree isolation. The child NEVER touches the
# canonical checkout: every round gets a disposable `git worktree` branched from
# the pre-round anchor. Containment on reject is DELETION of that worktree --
# `reset --hard` on canonical is no longer part of the round path at all (it
# survives only as the post-merge gate-disagreement restore and as a reject-path
# safety net that must be a no-op when the isolation held).
# ---------------------------------------------------------------------------
WT_ABANDON_SECS = int(os.environ.get("KL_WT_ABANDON_SECS", str(6 * 3600)))
_WT_DEP_DIRS = (".venv", "venv", "node_modules")
_CREATE_NO_WINDOW = 0x08000000


def _slugify(name: str) -> str:
    safe = str(name or "unknown").strip().lower().replace(" ", "-")
    return "".join(c for c in safe if c not in '\\/:*?"<>|') or "unknown"


def _wt_root(name: str) -> Path:
    return ROOT / "state" / "worktrees" / _slugify(name)


def _wt_path(name: str, round_no: int) -> Path:
    return _wt_root(name) / ("r%02d" % round_no)


def _branch_for(name: str, round_no: int) -> str:
    return "kl/%s/r%02d" % (_slugify(name), round_no)


def _child_env() -> dict:
    """Env for the round child: hermes importable + egress boundary.

    F3.2: the no-push boundary is set through GIT_CONFIG_* env entries, which
    apply to THIS process tree only. A per-repo `remote.origin.pushurl` rewrite
    in .git/config would live in the COMMON config shared with the canonical
    checkout and would also disable the runner's own checkpoint push.
    """
    env = dict(os.environ)
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "GIT_ASKPASS": "true",
        "SSH_ASKPASS": "true",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "remote.origin.pushurl",
        "GIT_CONFIG_VALUE_1": "DISABLED",
        "GIT_CONFIG_KEY_2": "core.askpass",
        "GIT_CONFIG_VALUE_2": "",
    })
    hp = str(HERMES_HOME / "hermes-agent")
    env["PYTHONPATH"] = (hp + os.pathsep + env["PYTHONPATH"]) \
        if env.get("PYTHONPATH") else hp
    return env


def _make_worktree(canonical: str, name: str, round_no: int,
                   anchor: str):
    """Create the round's disposable worktree + branch. Returns (path, note)."""
    wt = _wt_path(name, round_no)
    branch = _branch_for(name, round_no)
    if wt.exists():
        return None, ("worktree path already exists: %s (a stale round? "
                      "recovery quarantines abandoned ones)" % wt)
    try:
        r = subprocess.run(["git", "-C", canonical, "worktree", "add", "-b",
                            branch, str(wt), anchor],
                           capture_output=True, text=True, timeout=300,
                           errors="replace", creationflags=_CREATE_NO_WINDOW)
        if r.returncode != 0:
            return None, ("git worktree add failed rc=%s: %s"
                          % (r.returncode,
                             ((r.stderr or r.stdout or "").strip()[-300:])))
    except Exception as e:
        return None, "git worktree add could not run: %s" % e
    got = _head_full_sha(str(wt))
    if got != anchor:
        return None, ("worktree HEAD %r != anchor %r after add"
                      % (got, anchor))
    # sidecar: the recovery sweep uses THIS file's mtime as the round start --
    # a worktree dir's own mtime only moves when direct children change.
    try:
        wt.parent.mkdir(parents=True, exist_ok=True)
        (wt.parent / (wt.name + ".started.json")).write_text(
            json.dumps({"started": time.time(), "branch": branch,
                        "anchor": anchor}), encoding="utf-8")
    except Exception:
        pass
    return wt, "worktree ready at %s (branch %s from %s)" % (wt, branch, anchor[:8])


def _rmdir_link(p: Path) -> str:
    """Remove a directory junct

... [OUTPUT TRUNCATED - 82,826 chars omitted out of 132,752 total] ...

ust not have.
    _head_after = _head_full_sha(str(wt))                    # authoritative, from git
    _head_before = entry.get("pre_round_head")
    _ancestor = None
    if _head_before and _head_after:
        if _head_before == _head_after:
            _head_ok = False                    # no commit was produced
        else:
            _ancestor = _is_ancestor(str(wt), _head_before, _head_after)
            # require BOTH a real change AND descent from the baseline
            _head_ok = (_ancestor is True)
    else:
        _head_ok = None                          # unknown -- cannot authorise
    if _head_ok is None:
        log("%s round %d: HEAD delta UNKNOWN (before=%r after=%r) -- cannot "
            "verify a commit was produced, so the round cannot be accepted"
            % (name, round_no, _head_before, _head_after))
    elif _head_ok is False:
        log("%s round %d: HEAD did not advance (before=%s after=%s%s)"
            % (name, round_no, (_head_before or "?")[:8], (_head_after or "?")[:8],
               ", not a descendant" if _ancestor is False else ""))

    # UNKNOWN MUST REJECT. `is True` -- not `is not False`. This is the difference
    # between "we verified it advanced" and "we could not tell".
    #
    # F1-3: proposal_state joined acceptance. A malformed proposal or a propose()
    # exception must reject the round -- an infrastructure failure must never be
    # indistinguishable from a legitimate no-op (the exact swallow that hid D1).
    _round_ok = (rc == 0) and _gate_ok and (_head_ok is True) and (proposal_state is True)
    _why = []
    if rc != 0:
        _why.append("rc=%s" % rc)
    if _gate_state is None:
        _why.append("gate NOT MEASURED (unknown != passed)")
    elif not _gate_ok:
        _why.append("gate failed")
    if _head_ok is False:
        _why.append("no new commit")
    if _head_ok is None:
        _why.append("HEAD delta unknown")
    if proposal_state is not True:
        _why.append("proposal %s" % ("failed" if proposal_state is False
                                      else "NOT EVALUATED"))

    # A CHECKPOINT IS A CLAIM, AND IT NOW COMES LAST.
    #
    # Ordering matters here and was wrong until an external review caught it
    # (Saul, 2026-09-29): the tag was published BEFORE `scope.commit()` ran and
    # BEFORE the registry was persisted, so a failure in either left a published
    # tag describing a round whose scheduler state never moved. Correct order is:
    #
    #   1. decide acceptance        (rc + gate + HEAD, all runner-observed)
    #   2. commit campaign state    (the scheduler transition)
    #   3. CONTAIN rejected work    (rollback + quarantine decision)
    #   4. persist the COMPLETE final entry  (quarantine included -- F1-5,
    #      2026-09-30: containment used to run AFTER the save, so a failed
    #      rollback logged QUARANTINED while the disk still said runnable)
    #   5. ONLY THEN tag + push
    #
    # Any failure in 1-4 must prevent 5. `state_persisted` records that 4 happened.
    entry["round_accepted"] = bool(_round_ok)

    # ---- W3: RECORD THE ANGLE OUTCOME IN THE GLOBAL LEDGER ---------------
    # state/angle_history.json froze on day one (12 rows, all 2026-09-23)
    # while 893 rounds ran. Cause: the ONLY writer was an instruction in the
    # round prompt telling the CHILD to run `angle_pick.py mark`. Children did
    # not, so cooldown (8 rounds), family rotation and retirement (4 fails) all
    # read a day-one snapshot and stopped working.
    #
    # The runner is the only party that measures the gate and decides
    # acceptance, so it records the outcome itself. A child's self-report is
    # not evidence; this verdict is runner-observed.
    try:
        _aid_rec = (angle or {}).get("angle") or _aid
        _fam_rec = (angle or {}).get("family") or ""
        if _aid_rec:
            _st = angle_pick.record_round(
                name, _aid_rec, _fam_rec,
                "accepted" if _round_ok else "failed",
                round_no=round_no,
                note=(_why[0] if _why else ""),
            )
            entry["angle_stats"] = _st
            if _st.get("retired"):
                log("%s round %d: ANGLE RETIRED -- %s after %d consecutive "
                    "failures (used=%d)"
                    % (name, round_no, _aid_rec, _st.get("streak_fail"),
                       _st.get("used", 0)))
    except Exception as _ae:
        # Never let a ledger write break a round: the work is already done.
        log("%s round %d: angle ledger update failed: %s: %s"
            % (name, round_no, type(_ae).__name__, _ae))

    entry["acceptance_detail"] = _why
    state_persisted = False

    # F3.8 / T2-4: rounds_done was never incremented by the runner (the CLI
    # bump existed with ZERO callers), so max_rounds and every "rounds done"
    # surface read a stale counter. An ACCEPTED round is what counts.
    if _round_ok:
        try:
            _new_total = loopctl.bump()
            log("%s: rounds_done -> %s" % (name, _new_total))
        except Exception as e:
            log("%s round %d: loopctl bump failed: %s: %s"
                % (name, round_no, type(e).__name__, e))

    # ---- CAMPAIGN COMMIT (the mutation) -----------------------------------
    # After rc + gate + HEAD are known. A rejected round cannot open, advance, or
    # close a campaign -- `commit` records the rejected claim for audit and leaves
    # state alone. This ordering is the fix for Saul's blocker #1: the scheduler
    # transition consumes the runner's OBSERVATION, not the child's claim.
    campaign_commit_error = None
    if campaign_prop is not None:
        try:
            _c_before = scope.summary_line(entry.get("campaign"))
            _camp = scope.commit(entry, campaign_prop, accepted=bool(_round_ok))
            _c_after = scope.summary_line(_camp)
            if _c_after != _c_before:
                log("%s: campaign %s -> %s" % (name, _c_before, _c_after))
            if not _round_ok and (campaign_prop.get("campaign") or campaign_prop.get("closed")):
                log("%s: campaign claim DISCARDED -- the round was not accepted "
                    "(%s), so its %s marker cannot move campaign state"
                    % (name, ", ".join(_why),
                       "CROSS_CUTTING" if campaign_prop.get("campaign") else "completion"))
            if _camp and _camp.get("state") == "blocked":
                log("%s: campaign BLOCKED -- %s. Outstanding: %s"
                    % (name, _camp.get("blocked_because"),
                       ", ".join(scope.outstanding_surfaces(_camp))))
        except Exception as e:
            campaign_commit_error = "%s: %s" % (type(e).__name__, e)
            log("%s: campaign commit failed: %s" % (name, campaign_commit_error))

    # ---- B3/B2 lifecycle bookkeeping (2026-09-30) --------------------------
    # AFTER the commit block (which is the only place campaign state changes):
    #   * hold reset -- a CLOSED or BLOCKED campaign must not keep holding the
    #     rotation; its consecutive-hold counter goes back to zero.
    #   * scope-question lifecycle -- this round's classification settles or
    #     refreshes the open UNCERTAIN question (T2-11-style steering state).
    _camp_now = entry.get("campaign") if isinstance(entry.get("campaign"), dict) else None
    if _camp_now is None or (_camp_now.get("state") or "open") != "open":
        entry["campaign_holds"] = 0
    _cls_now = (round_scope or {}).get("scope_class")
    if _cls_now == "UNCERTAIN":
        _prev_q = entry.get("scope_question") if isinstance(entry.get("scope_question"), dict) else {}
        _seen = int(_prev_q.get("seen") or 0) + 1
        entry["scope_question"] = {"round": entry.get("rounds"),
                                   "note": (round_scope or {}).get("uncertain_note") or "",
                                   "seen": _seen}
        log("%s: scope UNCERTAIN (%d consecutive) -- question recorded for "
             "next round%s" % (name, _seen,
                               (": " + (round_scope or {}).get("uncertain_note", ""))
                               if (round_scope or {}).get("uncertain_note") else ""))
        if _seen >= 3:
            log("%s: ESCALATION -- %d consecutive UNCERTAIN classifications; "
                 "the loop will keep the scope question open until a round "
                 "settles it. Operator may want to answer the thread."
                 % (name, _seen))
    elif _cls_now in ("LOCAL", "CROSS_CUTTING") and entry.get("scope_question"):
        # Review finding (B round, 2026-09-30): only an ACCEPTED round may
        # settle the question -- a failed round's claims are discarded, so
        # its classification is not evidence either. UNCERTAIN refreshes
        # regardless (recording uncertainty is claim-independent).
        if _round_ok:
            entry.pop("scope_question", None)
            log("%s: scope question SETTLED (%s, round accepted)" % (name, _cls_now))
        else:
            log("%s: scope classification %s on a REJECTED round -- question "
                 "stays open" % (name, _cls_now))

    # ---- CONTAINMENT (Saul's blocker #6) ----------------------------------
    # A rejected round leaves a COMMIT and/or UNTRACKED files in the tree. If we
    # leave them, the next round inherits them and publishes them under ITS
    # checkpoint -- so the published range would contain work that never passed.
    # Roll the tree back to the sha WE captured at the start of this same round,
    # then VERIFY. An unverified rollback is not a rollback: if it fails, the repo
    # is marked QUARANTINED and must stop being scheduled until a human looks.
    #
    # F1-5: containment moved BEFORE the registry save so the quarantine flag and
    # the rollback result are part of the entry that reaches disk. (The old order
    # -- save at :849, contain at :884 -- meant the selector's reload never saw
    # the quarantine: the guarantee existed only in memory.)
    if not _round_ok:
        # F3.3: containment = DELETION of the worktree. The canonical checkout
        # was never touched, so there is nothing to roll back -- the check
        # below stays as a safety net that must be a no-op when the isolation
        # held (if it is ever NOT a no-op, the isolation broke and that is a
        # quarantine, not a quiet fix).
        _wt_removed = _remove_worktree(_workdir, name, round_no)
        entry["last_containment"] = _wt_removed
        log("%s round %d: containment -> %s" % (name, round_no, _wt_removed))
        if _wt_removed.startswith(("worktree remove failed",
                                   "worktree removal ABORTED",
                                   "worktree remove could not run")):
            entry["quarantined"] = True
            entry["quarantine_reason"] = _wt_removed
            log("%s: QUARANTINED -- %s. This repo must not be scheduled again "
                "until the worktree is inspected." % (name, _wt_removed))
        if _wt_removed.startswith("worktree removed"):
            entry["worktree"] = None
        if entry.get("pre_round_head"):
            if (_head_full_sha(_workdir) != entry["pre_round_head"]
                    or _is_dirty(_workdir) is not False):
                _rb = _reset_canonical(_workdir, entry["pre_round_head"])
                entry["last_rollback"] = _rb
                log("%s round %d: canonical moved during a REJECTED round -- "
                    "%s" % (name, round_no, _rb))
                if _rb.startswith(("RESET UNVERIFIED", "RESET FAILED")):
                    entry["quarantined"] = True
                    entry["quarantine_reason"] = _rb
                    log("%s: QUARANTINED -- %s. This repo must not be scheduled "
                        "again until the working tree is inspected." % (name, _rb))
            else:
                entry["last_rollback"] = "not needed (canonical untouched at the anchor)"

    # ---- PERSIST THE COMPLETE FINAL ENTRY ---------------------------------
    # F1-5: this save now includes containment results. A published tag must
    # never describe a scheduler state that was not written, and a quarantined
    # repo must actually be quarantined ON DISK for the selector's reload.
    try:
        def _persist_round(reg):
            reg[name] = entry
        threads.update(_persist_round)
        state_persisted = True
    except Exception as e:
        log("%s round %d: STATE PERSIST FAILED: %s: %s -- refusing to publish a "
            "checkpoint for a round whose state was not saved"
            % (name, round_no, type(e).__name__, e))

    # ---- PUBLISH (last) ---------------------------------------------------
    # F3.4: on accept the RUNNER integrates: ff-merge the round branch into
    # canonical, re-run the gate IN CANONICAL (a cheap second measurement of
    # the merged tree), and only then tag + push from canonical.
    entry["publication_state"] = "skipped"   # F1-6: pending/published/failed/skipped
    _wt_keep = False      # True = preserve the worktree + branch for inspection
    if _round_ok and state_persisted and campaign_commit_error is None:
        _branch = _branch_for(name, round_no)
        # ---- W6: HUMAN MERGE GATE --------------------------------------
        # The Sep 22 contract: nothing merges to main without Gene. The
        # shipped code had drifted into an unconditional ff-merge on accept.
        # Integration now requires BOTH a family-valid review (W2) and either
        # an explicit per-project auto_merge flag or a recorded human approval.
        # Default is STOP AT BRANCH + TAG.
        _elig = _merge_eligibility(proj, entry, round_no)
        entry["merge_eligibility"] = _elig
        if not _elig["eligible"]:
            # Preserve the work for a human: branch + worktree stay, tag the
            # round so it is findable, and touch canonical not at all.
            _wt_keep = True
            try:
                _tg = checkpoint_mod.tag_only(name, _workdir, round_no)
            except Exception as _te:
                _tg = "tag failed: %s" % _te
            entry["publication_state"] = "awaiting-merge"
            entry["merge_gate"] = _elig["reason"]
            log("%s round %d: MERGE GATE -- %s. Stopped at branch %s + tag "
                "(%s); canonical untouched, worktree preserved for human merge."
                % (name, round_no, _elig["reason"], _branch, _tg))
            ck = "STOPPED AT BRANCH+TAG (%s): %s" % (_branch, _elig["reason"])
        else:
            _do_integrate = True
            _merge_fail = None
            try:
                _mrg = subprocess.run(["git", "-C", _workdir, "merge", "--ff-only",
                                       _branch], capture_output=True, text=True,
                                      timeout=300, errors="replace",
                                      creationflags=0x08000000)
                if _mrg.returncode != 0:
                    _merge_fail = ("canonical ff-merge failed rc=%s: %s"
                                   % (_mrg.returncode,
                                      ((_mrg.stderr or _mrg.stdout or "").strip()[-300:])))
            except Exception as e:
                _merge_fail = "canonical ff-merge could not run: %s" % e
            if _merge_fail:
                # ff-only either moves the branch or fails atomically: canonical
                # is still at the anchor. The WORK is good -- preserve the
                # worktree + branch for a human, publish nothing.
                _wt_keep = True
                entry["merge_error"] = _merge_fail
                log("%s round %d: INTEGRATION FAILED -- %s. Work preserved in %s "
                    "(branch %s); publishing nothing." % (name, round_no, _merge_fail, wt, _branch))
                ck = "SKIPPED (not published: %s)" % _merge_fail
                entry["publication_state"] = "failed"
            else:
                # post-merge gate in CANONICAL -- the authoritative tree. The
                # worktree gate already passed; a disagreement is an environment
                # anomaly and must be LOUD, never silently published.
                _cgate = _run_gate(proj) if (proj.get("gate") or "").strip() else None
                entry["post_merge_gate"] = _cgate
                if _cgate is not None and _cgate.get("ok") is not True:
                    _rb = _reset_canonical(_workdir, entry["pre_round_head"])
                    entry["last_rollback"] = _rb
                    log("%s round %d: POST-MERGE GATE DISAGREEMENT -- canonical "
                        "gate: %s. %s" % (name, round_no, (_cgate or {}).get("detail"), _rb))
                    if _rb.startswith(("RESET UNVERIFIED", "RESET FAILED")):
                        entry["quarantined"] = True
                        entry["quarantine_reason"] = _rb
                        log("%s: QUARANTINED -- %s" % (name, _rb))
                    _wt_keep = True   # the work exists but is unpublishable: keep it
                    ck = "SKIPPED (not published: post-merge gate failed in canonical)"
                    entry["publication_state"] = "failed"
                else:
                    ck = checkpoint(name, _workdir, round_no)
                    entry["publication_state"] = ("published" if ck.startswith("published")
                                                  else "failed")
                    # the branch is merged and the dir content lives in canonical:
                    # delete the worktree regardless of push outcome.
                    _rm = _remove_worktree(_workdir, name, round_no)
                    entry["worktree_removed"] = _rm
                    if not _rm.startswith(("worktree removed", "worktree already gone")):
                        log("%s round %d: worktree cleanup failed: %s"
                            % (name, round_no, _rm))
                    entry["worktree"] = None
    else:
        _pub_why = list(_why)
        if campaign_commit_error is not None:
            _pub_why.append("campaign commit failed")
        if not state_persisted:
            _pub_why.append("state not persisted")
        ck = "SKIPPED (not published: %s)" % (", ".join(_pub_why) or "unknown")
        log("%s round %d: checkpoint NOT taken -- %s. A round that was not "
            "accepted, or whose state did not persist, must not publish a tag."
            % (name, round_no, ", ".join(_pub_why)))
        if _round_ok and state_persisted:
            # accepted but the campaign commit failed: the worktree holds the
            # work -- preserve it (branch + dir) for a human instead of
            # deleting a commit nobody recorded.
            _wt_keep = True
    if _wt_keep:
        log("%s round %d: worktree PRESERVED at %s (branch %s) -- inspect it; "
            "recovery will quarantine the repo after %ss"
            % (name, round_no, wt, _branch_for(name, round_no), WT_ABANDON_SECS))

    entry["last_checkpoint"] = ck
    entry["state_persisted"] = state_persisted
    entry["publication_detail"] = ck
    # T1-2 pattern guard (2026-09-30): last_checkpoint / publication_* were set
    # AFTER the F1-5 pre-publish save, so they lived only in memory until the
    # NEXT round of this project saved again -- the same class of bug Saul
    # flagged when quarantine fields never reached disk. One small post-publish
    # save keeps the publication verdict durable without changing the F2-8
    # ordering guarantee (publish still happens only after the F1-5 save).
    try:
        def _mark_pub(reg):
            reg[name] = entry
        threads.update(_mark_pub)
    except Exception as e:
        log("%s round %d: post-publish state save failed: %s: %s"
            % (name, round_no, type(e).__name__, e))

    log("%s round %d rc=%s ckpt[%s]" % (name, round_no, rc, ck))
    try:
        dn.progress(name, "round %d done (rc=%s) checkpoint: %s" % (round_no, rc, ck),
                    round_no=round_no)
    except SystemExit as _e:
        log("%s: discord token missing (round end notify skipped): %s" % (name, _e))
    except Exception as _e:
        # F0-4: the round is FINISHED here -- an outage must not discard its
        # bookkeeping by escaping after everything already ran.
        log("%s: discord notify failed (round end): %s: %s"
            % (name, type(_e).__name__, _e))
    return rc


def main() -> int:
    # F3.7: validate the model seats BEFORE taking the single-flight lock, so
    # a dead seat (the 2026-09-30 incident: children launched against a
    # hidden provider for hours) can never start the loop.
    try:
        _validate_seats(loopctl.load())
    except Exception as e:
        log("SEATS INVALID -- runner not started: %s" % e)
        print("SEATS INVALID -- runner not started: %s" % e, file=sys.stderr)
        return 2

    # Single runner guarantee (atomic O_EXCL -- two Start clicks cannot both win).
    if not _acquire_single_flight():
        log("runner already alive -- exiting")
        return 0

    log("runner started (pid %d) round_timeout=%ss" % (os.getpid(), ROUND_TIMEOUT))
    heartbeat("started")
    # F3.5: sweep for worktrees orphaned by a crashed runner -- quarantine
    # their repos before anything is scheduled (never auto-delete).
    try:
        _n = _recover_abandoned_worktrees()
        if _n:
            log("recovery: %d repo(s) quarantined for abandoned worktrees" % _n)
    except Exception as e:
        log("worktree recovery sweep failed: %s" % e)
    try:
        while True:
            lc = loopctl.load()
            if not lc.get("running"):
                log("loop paused -- runner exiting cleanly")
                return 0

            # W5 (2026-10-25): the caps must be enforced HERE, in the loop, not
            # only in loopctl's status/pause CLI. Before this, max_rounds and
            # max_hours were decoration: a 10-round supervised run kept
            # dispatching past the cap because nothing in the runner ever read
            # them. Reaching a cap pauses the loop (persisted) and exits.
            _mr = int(lc.get("max_rounds") or 0)
            _done = int(lc.get("rounds_done") or 0)
            if _mr and _done >= _mr:
                log("CAP REACHED: max_rounds %d/%d -- pausing the loop"
                    % (_done, _mr))
                lc["running"] = False
                lc["paused_reason"] = "max_rounds reached (%d/%d)" % (_done, _mr)
                lc["paused_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                time.gmtime())
                loopctl.save(lc)
                return 0
            _mh = float(lc.get("max_hours") or 0)
            if _mh:
                _started = lc.get("started_at") or ""
                try:
                    _t0 = datetime.fromisoformat(_started.replace("Z", "+00:00"))
                    if _t0.tzinfo is None:
                        _t0 = _t0.replace(tzinfo=timezone.utc)
                    _elapsed_h = (datetime.now(timezone.utc) - _t0).total_seconds() / 3600.0
                    if _elapsed_h >= _mh:
                        log("CAP REACHED: max_hours %.1f/%.1f -- pausing the loop"
                            % (_elapsed_h, _mh))
                        lc["running"] = False
                        lc["paused_reason"] = ("max_hours reached (%.1f/%.1f h)"
                                               % (_elapsed_h, _mh))
                        lc["paused_at"] = time.strftime(
                            "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
                        loopctl.save(lc)
                        return 0
                except (ValueError, TypeError) as _te:
                    log("max_hours check skipped -- bad started_at %r (%s)"
                        % (_started, _te))

            m = I.load_manifest()
            # Picker wrote a name into loop.json that improve.yaml lacks? Upsert a
            # minimal enabled block for it, else the rotator never sees the repo
            # (manifest-only read) and the pick silently gets no turn.
            try:
                added = I.upsert_missing(m, lc)
                if added:
                    log("manifest upserted for loop.json picks: %s" % ", ".join(added))
            except Exception as e:
                log("manifest upsert failed: %r" % e)
            projs = I.enabled(m)
            if not projs:
                log("no enabled projects -- sleeping 5m")
                time.sleep(300)
                continue

            try:
                reg = threads.load()
            except threads.RegistryCorrupt as _ce:
                # W4: FAIL CLOSED. The old behavior (empty registry) forgot every
                # quarantine and resumed running on repos whose trees are in an
                # unknown state. Pause and surface instead.
                log("REGISTRY CORRUPT -- PAUSING THE LOOP: %s" % _ce)
                try:
                    lc = loopctl.load()
                    lc["running"] = False
                    lc["paused_reason"] = ("threads.json corrupt: %s" % _ce)[:400]
                    lc["paused_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                    time.gmtime())
                    loopctl.save(lc)
                except Exception as _pe:
                    log("could not record the pause: %s" % _pe)
                try:
                    dn.stuck("threads.json corrupt",
                             "the loop PAUSED rather than run on unknown state: %s" % _ce)
                except Exception:
                    pass
                return 2
            # A QUARANTINED repo must not be selected. The quarantine is set when a
            # rejected round could not be rolled back cleanly (Saul's blocker #6): the
            # tree is in an unknown state, so continuing to run there risks publishing
            # residue nobody inspected. Setting the flag without honouring it HERE
            # would make the whole guard decorative.
            _runnable = [p for p in projs
                         if not reg.get(p["name"], {}).get("quarantined")]
            if not _runnable:
                _why_q = "; ".join(
                    "%s (%s)" % (p["name"],
                                  reg.get(p["name"], {}).get("quarantine_reason") or "?")
                    for p in projs)
                log("every enabled project is QUARANTINED -- no repo is safe to run; "
                    "sleeping 15m. Inspect: %s" % _why_q)
                time.sleep(900)
                continue
            # A quarantined repo never has its last_nudge advanced, so the sort stays
            # meaningful for the runnable set. (Quarantined repos are excluded above,
            # not merely sorted last -- a decorative flag is worse than none.)
            projs = pick_next_project(_runnable, reg)
            proj = projs[0]
            entry = reg.setdefault(proj["name"], {})
            # B2 hold bookkeeping: bump when the HOLD selected this repo; log the
            # cap release loudly (open campaign re-enters normal rotation).
            _held, _holds = _hold_state(entry)
            if _held:
                entry["campaign_holds"] = _holds + 1
                log("%s: campaign hold #%d -- %s"
                    % (proj["name"], _holds + 1,
                       scope.summary_line(entry.get("campaign"))))
            elif isinstance(entry.get("campaign"), dict) and \
                    (entry["campaign"].get("state") or "open") == "open":
                log("%s: CAMPAIGN HOLD CAP reached (%d) -- repo re-enters normal "
                     "rotation while its campaign stays open"
                     % (proj["name"], _safe_int(entry.get("campaign_holds"))))

            heartbeat("round-started", project=proj["name"],
                      round_no=(entry.get("rounds") or 0) + 1)
            # ---- W1 LANE PREFLIGHT + CIRCUIT BREAKER ----------------------
            # The Oct 15 death spiral: rounds 174-180 burned into a dead lane
            # because nothing probed the seats before dispatch. Probe BOTH seats
            # now, record the ACTUAL model each endpoint serves, and halt the
            # loop when a seat has failed 3 rounds running at the transport layer.
            _dispatch_ok, _preflight = _lane_gate(lc)
            if not _dispatch_ok:
                heartbeat("idle", project=proj["name"])
                log("LANE GATE: dispatch halted -- %s" % _preflight.get("reason"))
                try:
                    dn.progress(proj["name"], "LOOP HALTED (lane-down): %s"
                                % _preflight.get("reason"))
                except Exception:
                    pass
                return 3          # runner exits; watchdog honours running:false
            try:
                run_round(proj, entry, preflight=_preflight)
            except Exception as e:
                log("%s: round CRASHED: %s" % (proj["name"], e))
                try:
                    dn.progress(proj["name"], "round crashed: %s" % e)
                except SystemExit:
                    pass
                except Exception as _e2:
                    log("%s: discord notify failed (crash report): %s"
                        % (proj["name"], _e2))
            heartbeat("idle", project=proj["name"])
            # Immediately rotate to the next project. No waiting.
    finally:
        PID_FILE.unlink(missing_ok=True)
        log("runner stopped")


if __name__ == "__main__":
    if "--check-alive" in sys.argv:
        # T2-7: a read-only liveness probe for cron reporting. Never starts
        # anything; exit 0 = a runner process is alive, 1 = not.
        raise SystemExit(0 if is_running() else 1)
    raise SystemExit(main())