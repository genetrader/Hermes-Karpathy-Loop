#!/usr/bin/env python
"""
karpathy_runner.py -- the continuous round runner (Start/Pause model).

the operator's model (2026-09-23, verbatim intent): "It starts when I start it and it
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
import re
import subprocess
import msvcrt
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
import scope                       # noqa: E402
import discord_notify as dn        # noqa: E402

import settings as _S            # noqa: E402  machine config: the ONE place

# Everything machine-specific comes from settings.py (defaults -> settings.yaml
# -> settings.local.yaml -> env). Nothing here may hardcode a home dir, an
# interpreter path, a model seat, or a server.
HERMES_HOME = _S.hermes_home()
HERMES_PY = _S.hermes_python()

# --- model seats -----------------------------------------------------------------
# F3.7 / T2-3 (2026-09-30): seats are NOT module constants anymore. The implementer
# seat is read from state/loop.json every round (loopctl.load()["implementer"]); the
# reviewer seat is handed to the round prompt the same way (round_prompt.build(seats=)).
# The old constants hard-coded seats whose providers were HIDDEN on 2026-09-30
# (deepseek-v41-flash-3x-spark, glm53-flash-2x-spark) -- the loop kept launching
# children against a dead endpoint for hours because nothing validated the seat.
# main() now validates both seats at startup (_validate_seats) and refuses to run
# the loop on a dead or malformed one.
BUILDER_FALLBACK = (os.environ.get("KL_BUILDER_MODEL")
                    or _S.builder_fallback())
# Test isolation (2026-10-01): tests that drive run_round() must NOT pollute the
# LIVE runner.log -- 367 "t round 1" junk lines from a test run leaked into the
# monitor's Recent activity feed. Any process can point the log elsewhere with
# KL_RUNNER_LOG; the live runner and the watchdog keep the default.
LOG = Path(os.environ.get("KL_RUNNER_LOG", str(ROOT / "logs" / "runner.log")))
# Per-round wall-clock ceiling. Rounds normally take 50-60 min; this is generous
# but bounded so a single hung child cannot stall the rotation for hours (it was
# 7200s with no kill).
ROUND_TIMEOUT = _S.round_timeout()
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
        reg = threads.load()
        reg[name] = entry
        threads.save(reg)
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
    (2026-10-06 incident: an old zombie runner predating the lock kept
    rotating repos while a fresh runner held runner.pid -- two rounds ran at
    once and collided on worktrees). The backstop scans real process command
    lines and refuses to start alongside any sibling.
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
    mine = set()
    pid = os.getpid()
    for _ in range(4):
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
    2. PROCESS-SCAN BACKSTOP: refuse if any other karpathy_runner.py image is
       running, whatever it thinks about locks (catches zombies from before
       this fix, and any future code path that forgets to take the lock).

    runner.pid is still written for diagnostics/back-compat readers.
    """
    global _lock_handle
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        others = _other_runner_pids()
        if others:
            log("START REFUSED -- another karpathy_runner.py is already alive "
                "(pid %s). One runner at a time." % ", ".join(map(str, sorted(others))))
            return False
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
    # zombie that never held runner.pid would otherwise read as dead and the
    # watchdog would respawn alongside it.
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
WT_ABANDON_SECS = _S.worktree_abandon_secs()
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
    """Remove a directory junction WITHOUT following it into its target."""
    for fn in (p.rmdir, p.unlink):
        try:
            fn()
            return "removed junction %s" % p
        except OSError:
            continue
        except Exception:
            continue
    try:
        r = subprocess.run(["cmd", "/c", "rmdir", "/q", str(p)],
                           capture_output=True, timeout=30,
                           creationflags=_CREATE_NO_WINDOW)
        if r.returncode == 0:
            return "removed junction %s (cmd)" % p
    except Exception:
        pass
    return "COULD NOT remove junction %s" % p


def _discover_dep_dirs(canonical: str, wt: Path) -> list:
    """Dependency dirs to junction, at ANY depth (review HIGH-2, 2026-10-01).

    The old code junctioned only TOP-LEVEL .venv/venv/node_modules, so
    project-b's `cd server && .venv/...` gate could never pass in a
    worktree (its venv lives at server/.venv) and every round on that repo
    rejected forever. Discovery asks GIT for the repo's own ignored,
    untracked directories (`ls-files --others --ignored --directory`) and
    keeps those whose basename is a dep-dir name -- each repo's own ignore
    rules decide, which is exactly the safety property the old tracked-check
    enforced. Bounded: max 12 dirs, depth <= 4. Git failure falls back to a
    bounded rglob over the canonical tree (top-level first). A dir that
    already exists in the worktree (tracked checkout content) is skipped by
    the caller's dst.exists() check."""
    import os
    can = Path(canonical)
    found = []
    try:
        r = subprocess.run(
            ["git", "-C", canonical, "ls-files", "--others", "--ignored",
             "--directory", "--exclude-standard", "--", "*"],   # F-E: "--"+chr(42) made ONE token `--*`, which git rejects (rc 129)
            capture_output=True, text=True, timeout=120,
            errors="replace", creationflags=_CREATE_NO_WINDOW)
        if r.returncode == 0:
            for line in (r.stdout or "").splitlines():
                line = line.strip().rstrip("/")
                if not line or not line.endswith(_WT_DEP_DIRS):
                    continue
                # git --directory prints dirs WITH a trailing slash; only
                # directories match the --others --ignored --directory form
                # for our names, but confirm on disk anyway.
                if not (can / line).is_dir():
                    continue
                rel = line.replace("\\", "/")
                if rel.count("/") > 4 or rel in found:
                    continue
                found.append(rel)
                if len(found) >= 12:
                    break
            return found
    except Exception:
        pass
    # Fallback: bounded rglob (git unavailable / non-git layout).
    try:
        for d in _WT_DEP_DIRS:
            top = can / d
            if top.is_dir() and top not in found:
                found.append(d)
        depth = 0
        for p in sorted(can.rglob("*")):
            rel = p.relative_to(can)
            if len(rel.parts) > 5 or p.name not in _WT_DEP_DIRS or not p.is_dir():
                continue
            if any(part in (".git",) for part in rel.parts):
                continue
            rels = "/".join(rel.parts)
            if rels not in found:
                found.append(rels)
            if len(found) >= 12:
                break
    except Exception:
        pass
    return found


def _provision_worktree_deps(canonical: str, wt: Path) -> list:
    """Junction the repo's untracked dependency dirs into the worktree.

    Gates (the runner's AND the child's own gate run) reference repo-local
    interpreters like `.venv/Scripts/python.exe` or node_modules bins, which a
    fresh checkout lacks; junctioning keeps every gate command verbatim instead
    of translating gate strings. SAFETY: junctions are recorded in an r01.deps.json
    sidecar and _rmdir_link'd BEFORE any worktree removal -- a recursive delete
    must never follow a junction into the canonical checkout. A dep dir that is
    not gitignored is skipped (a junction that shows up as an untracked tree
    would let the child commit the whole venv).
    """
    notes = []
    made = []
    for rel in _discover_dep_dirs(canonical, wt):
        d = rel                      # path relative to the repo root
        src = Path(canonical) / d
        dst = wt / d
        if not src.is_dir() or dst.exists():
            continue
        try:
            tracked = subprocess.run(["git", "-C", canonical, "ls-files", "--", d],
                                     capture_output=True, text=True, timeout=60,
                                     errors="replace", creationflags=_CREATE_NO_WINDOW)
            if (tracked.stdout or "").strip():
                continue        # tracked: the checkout already carries it
        except Exception:
            pass
        try:
            # Gitignored parents (server/, .venv parents) may not exist in a
            # fresh checkout -- mklink needs the full parent chain.
            dst.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)],
                               capture_output=True, text=True, timeout=30,
                               creationflags=_CREATE_NO_WINDOW)
            if r.returncode != 0:
                notes.append("junction %s refused: %s"
                             % (d, ((r.stdout or r.stderr or "").strip()[-120:])))
                continue
        except Exception as e:
            notes.append("junction %s failed: %s" % (d, e))
            continue
        made.append(d)
        notes.append("junction %s -> %s" % (d, src))
        # If the junction is NOT gitignored, git status now sees a huge untracked
        # tree -- undo it rather than risk the child staging the whole venv.
        try:
            st = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"],
                                capture_output=True, text=True, timeout=120,
                                errors="replace", creationflags=_CREATE_NO_WINDOW)
            if (st.stdout or "").strip():
                notes.append(_rmdir_link(dst) + " (not gitignored; untracked in status)")
                made.remove(d)
        except Exception:
            pass
    try:
        (wt.parent / (wt.name + ".deps.json")).write_text(
            json.dumps({"junctions": made}), encoding="utf-8")
    except Exception:
        pass
    return notes


def _is_reparse_point(p: Path) -> bool:
    """True if the path carries a reparse point (junction/symlink).

    Dep cleanup must distinguish a LINK (rmdir the link itself, never
    follow) from a REAL directory (npm sometimes replaces our junction
    with a fresh install -- that real dir is worktree-local data).
    """
    try:
        import ctypes
        FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(p))
        return attrs != -1 and bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    except Exception:
        return False


def _cleanup_junctions(wt: Path) -> list:
    notes = []
    side = wt.parent / (wt.name + ".deps.json")
    made = []
    try:
        made = (json.loads(side.read_text(encoding="utf-8"))
                or {}).get("junctions") or []
    except Exception:
        made = list(_WT_DEP_DIRS)   # best effort: the known dep names
    for d in made:
        p = wt / str(d)
        if p.exists() or p.is_symlink():
            note = _rmdir_link(p)
            if "COULD NOT" in note and p.exists() \
                    and not _is_reparse_point(p):
                # npm/uv sometimes REPLACES our junction with a real
                # directory (it removes node_modules and reinstalls).
                # That dir is worktree-local disposable data -- delete it
                # so containment can finish (the operator, r14 quarantine case).
                import shutil as _sh
                try:
                    _sh.rmtree(p)
                    note = "removed real dep dir %s (junction was replaced)" % p
                except OSError as _e:
                    note = ("COULD NOT remove real dep dir %s: %s"
                            % (p, _e))
            notes.append(note)
    try:
        side.unlink(missing_ok=True)
    except Exception:
        pass
    return notes


def _remove_worktree(canonical: str, name: str, round_no: int) -> str:
    """Containment: delete the round's worktree + branch. Never raises."""
    wt = _wt_path(name, round_no)
    branch = _branch_for(name, round_no)
    if not wt.exists():
        return "worktree already gone (%s)" % wt
    # F-T: kill anything still running FROM this worktree (leaked dev
    # servers) BEFORE touching the filesystem, or the delete fails with
    # Permission denied and quarantines the repo.
    _nk = _sweep_worktree_processes(str(wt))
    if _nk:
        log("%s: swept %d orphaned process(es) holding the worktree"
            % (name, _nk))
        time.sleep(2)
    jn = _cleanup_junctions(wt)
    if any("COULD NOT" in n for n in jn):
        return ("worktree removal ABORTED -- a deps junction could not be "
                "removed; manual inspection required: %s" % wt)
    try:
        r = subprocess.run(["git", "-C", canonical, "worktree", "remove",
                            "--force", str(wt)],
                           capture_output=True, text=True, timeout=300,
                           errors="replace", creationflags=_CREATE_NO_WINDOW)
        if r.returncode != 0:
            return ("worktree remove failed rc=%s: %s"
                    % (r.returncode,
                       ((r.stderr or r.stdout or "").strip()[-300:])))
    except Exception as e:
        return "worktree remove could not run: %s" % e
    try:
        rb = subprocess.run(["git", "-C", canonical, "branch", "-D", branch],
                            capture_output=True, text=True, timeout=60,
                            errors="replace", creationflags=_CREATE_NO_WINDOW)
        bnote = ("branch %s deleted" % branch if rb.returncode == 0
                 else "branch %s rc=%s %s" % (branch, rb.returncode,
                                              (rb.stderr or "").strip()[-120:]))
    except Exception as e:
        bnote = "branch delete could not run: %s" % e
    try:
        (wt.parent / (wt.name + ".started.json")).unlink(missing_ok=True)
    except Exception:
        pass
    return "worktree removed (%s); %s" % (wt, bnote)


def _reset_canonical(workdir: str, anchor: str) -> str:
    """Emergency restore of the canonical tree to the recorded anchor.

    NOT the old child-work rollback: this only ever runs on a tree the RUNNER
    just ff-merged itself (post-merge gate disagreement) or as the reject-path
    safety net when the isolation somehow failed. Verify or say so loudly.
    """
    if not (workdir and anchor):
        return "RESET SKIPPED (no anchor)"
    notes = []
    for args in (["reset", "--hard", anchor], ["clean", "-ffd"]):
        try:
            r = subprocess.run(["git", "-C", workdir] + args,
                               capture_output=True, text=True, timeout=300,
                               errors="replace", creationflags=_CREATE_NO_WINDOW)
            notes.append("%s rc=%s" % (args[0], r.returncode))
        except Exception as e:
            return "RESET FAILED at %s: %s" % (args[0], e)
    if _head_full_sha(workdir) != anchor:
        return "RESET UNVERIFIED (head != anchor) :: " + "; ".join(notes)
    if _is_dirty(workdir) is not False:
        return "RESET UNVERIFIED (tree still dirty) :: " + "; ".join(notes)
    return "canonical reset to %s (verified; %s)" % (anchor[:8], "; ".join(notes))


# --------------------------------------------------------------------------
# B2 (2026-09-30): CAMPAIGN ROTATION HOLD -- burst+LRU at the repo level.
#
# A campaign is 'N consecutive rounds sharing one checklist' (surfaces design
# 6.2). The pure last_nudge LRU sort sends a repo to the BACK of the queue
# after every round, which would space its own campaign rounds hours apart.
# So an OPEN campaign holds the rotation; everything else stays byte-for-byte
# LRU. BLOCKED campaigns do NOT hold (exhausted work must not monopolize),
# and the hold is bounded by scope.MAX_CAMPAIGN_HOLDS so a wedged-open
# campaign cannot own the loop. The counter is persisted on the entry and
# reset when the campaign closes or blocks.
def _safe_int(v, default=0) -> int:
    """int() that never raises (review fix L2, 2026-10-01): a corrupted
    registry value like campaign_holds='x' previously crashed the sort
    key inside the selection loop, killing the runner -- and the 15-min
    watchdog respawned into the same crash forever."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _hold_state(entry: dict) -> tuple:
    """(held: bool, holds: int) for a registry entry.

    held = an OPEN, un-blocked campaign AND the consecutive-hold cap not yet
    reached. Pure read; never raises on a sparse entry."""
    camp = entry.get("campaign") if isinstance(entry.get("campaign"), dict) else None
    if not camp:
        return False, _safe_int(entry.get("campaign_holds"))
    if (camp.get("state") or "open") != "open":
        return False, _safe_int(entry.get("campaign_holds"))
    holds = _safe_int(entry.get("campaign_holds"))
    if holds >= scope.MAX_CAMPAIGN_HOLDS:
        return False, holds
    return True, holds


def pick_next_project(projs: list, reg: dict) -> list:
    """The rotation sort. Held (open-campaign) repos first, LRU within each
    partition. Expects the runnable set (quarantine already excluded)."""
    def _key(p):
        e = reg.get(p["name"], {}) if isinstance(reg, dict) else {}
        held, _ = _hold_state(e if isinstance(e, dict) else {})
        return (0 if held else 1, (e.get("last_nudge") or 0))
    return sorted(projs, key=_key)


def _recover_abandoned_worktrees() -> int:
    """F3.5: startup sweep for worktrees orphaned by a crashed runner.

    A worktree whose .started.json sidecar is older than WT_ABANDON_SECS means
    a round died mid-flight. The REPO is quarantined (never auto-deleted) so a
    human inspects the abandoned tree; the round branch is deleted only AFTER
    the quarantine note. Returns the number of repos newly quarantined.
    """
    root = ROOT / "state" / "worktrees"
    if not root.is_dir():
        return 0
    now = time.time()
    try:
        reg = threads.load()
    except Exception:
        return 0
    changed = False
    quarantined = 0
    for proj_dir in sorted(root.iterdir()):
        if not proj_dir.is_dir():
            continue
        for wt in sorted(proj_dir.iterdir()):
            if not wt.is_dir():
                continue
            side = wt.parent / (wt.name + ".started.json")
            ref = side if side.exists() else wt
            try:
                age_h = (now - ref.stat().st_mtime) / 3600.0
            except Exception:
                continue
            if age_h * 3600.0 < WT_ABANDON_SECS:
                continue
            name = proj_dir.name
            entry = reg.get(name) if isinstance(reg.get(name), dict) else {}
            if entry.get("quarantined"):
                continue
            entry["quarantined"] = True
            entry["quarantine_reason"] = (
                "abandoned worktree %s (age %.1fh) -- inspect it, then clear "
                "the quarantine flag manually" % (wt, age_h))
            reg[name] = entry
            changed = True
            quarantined += 1
            log("%s: QUARANTINED -- %s" % (name, entry["quarantine_reason"]))
            # branch deletion AFTER the quarantine note (plan F3.5)
            try:
                mroot = None
                import improver as _I
                for p in _I.enabled(_I.load_manifest()):
                    if _slugify(p.get("name") or "") == name:
                        mroot = p.get("path")
                if mroot:
                    rnd = int(_re.match(r"r(\d+)$", wt.name).group(1))
                    subprocess.run(["git", "-C", mroot, "branch", "-D",
                                    _branch_for(name, rnd)],
                                   capture_output=True, text=True, timeout=60,
                                   creationflags=_CREATE_NO_WINDOW)
            except Exception:
                pass
    # F5 review hardening (2026-09-30): a crash between the ff-merge and
    # the post-merge gate/reset leaves canonical moved with NO published
    # tag and no recorded verdict. Detect that exact state and quarantine
    # for a human, so the window can never be silent.
    try:
        import improver as _im
        _paths = {}
        for _p in _im.enabled(_im.load_manifest()):
            _paths[_p.get("name")] = _p.get("path")
        for _name, _entry in (reg or {}).items():
            if not isinstance(_entry, dict) or _entry.get("quarantined"):
                continue
            # Review HIGH-3 (2026-10-01): 'skipped' only reaches disk AFTER
            # the ff-merge/post-gate/publish sequence it targets, so that
            # requirement made this sweep dead code. In the real crash
            # window the on-disk entry has round_accepted=True and NO
            # publication_state yet -- accept absent OR 'skipped'.
            _pub = _entry.get("publication_state")
            if not (_entry.get("round_accepted")
                    and (_pub is None or _pub == "skipped")
                    and _entry.get("post_merge_gate") is None
                    and _entry.get("merge_error") is None):
                continue
            _wtpath = _paths.get(_name)
            _pre = _entry.get("pre_round_head")
            if not (_wtpath and _pre):
                continue
            _now = _head_full_sha(_wtpath)
            if _now and _now != _pre and _is_ancestor(_wtpath, _pre, _now) is True:
                _entry["quarantined"] = True
                _entry["quarantine_reason"] = (
                    "canonical HEAD moved past %s with no published tag and "
                    "no post-merge gate verdict (crash during integration?) "
                    "-- inspect, then clear the quarantine flag manually"
                    % _pre[:8])
                changed = True
                quarantined += 1
                log("%s: QUARANTINED -- %s" % (_name, _entry["quarantine_reason"]))
    except Exception as _e:
        log("integration-window sweep failed: %s" % _e)
    if changed:
        try:
            threads.save(reg)
        except Exception as e:
            log("worktree recovery could not persist quarantine: %s" % e)
    return quarantined


_BUILTIN_PROVIDERS = {
    "custom", "openrouter", "openai", "openai-codex", "anthropic", "google",
    "gemini", "zai", "ollama", "lmstudio", "openai-compatible", "nous",
    "bedrock", "azure", "groq", "mistral", "xai", "deepseek", "together",
    "fireworks", "github-copilot",
}


def _seat_provider(seat: str):
    """The provider segment of a `provider:model` seat, or None if malformed."""
    parts = [p for p in str(seat or "").split(":") if p]
    if not parts:
        return None
    if parts[0].lower() == "custom":
        return parts[1] if len(parts) >= 3 else None
    return parts[0] if len(parts) >= 2 else None


def _validate_seats(cfg: dict, config_path: Path | None = None) -> None:
    """F3.7: refuse to run the loop on a dead or malformed model seat.

    A seat must be `provider:model` (or `custom:<slug>:<model>` for a custom
    provider defined in config.yaml). A bare model name is refused: it
    mis-parses and silently routes to a cloud provider. Unknown custom slugs
    are refused with the exact fix in the message. A config that cannot be
    READ is logged and the check continues unverified -- that is a different
    fact from a seat that is known-dead.
    """
    path = config_path or (HERMES_HOME / "config.yaml")
    slugs = None
    try:
        import yaml
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        slugs = set(doc.get("providers") or {})
    except Exception as e:
        log("seat validation: providers unreadable from %s (%s) -- "
            "continuing unverified" % (path, e))
    for role in ("implementer", "reviewer"):
        seat = (cfg.get(role) or "").strip()
        if not seat:
            raise RuntimeError(
                "%s seat is empty in loop.json -- set it with "
                "loopctl config --%s <provider:model>" % (role, role))
        prov = _seat_provider(seat)
        if prov is None:
            raise RuntimeError(
                "%s seat %r is not in provider:model form -- a bare model "
                "name mis-parses to a cloud provider" % (role, seat))
        if prov.lower() in _BUILTIN_PROVIDERS:
            continue
        if slugs is not None and prov not in slugs:
            raise RuntimeError(
                "%s seat %r names provider %r which is not configured in %s "
                "-- fix the seat (loopctl config) or the provider list"
                % (role, seat, prov, path))
        if slugs is None:
            log("seat validation: %s seat provider %r could not be checked "
                "(config unreadable)" % (role, prov))
    if ((cfg.get("implementer") or "").strip()
            == (cfg.get("reviewer") or "").strip()):
        raise RuntimeError(
            "implementer and reviewer seats are identical (same blind spot "
            "twice)")


def checkpoint(project: str, workdir: str, round_no: int) -> str:
    """
    End-of-round checkpoint: tag locally; push branch + tag to GitHub ONLY
    when settings allow it.

    github.enabled=false (the shipped default) = LOCAL TAGS ONLY: the tag is
    the checkpoint and the rollback anchor, the push is skipped silently --
    it is a supported mode, not a config error, so it must not log warnings
    or fail the round. github.push_branches=false still pushes the tag but
    leaves the working branch local. The runtime loopctl toggle
    (loopctl config --push-github false) is honoured on top of the settings
    master switch.

    Checkpoint policy when pushing (user-locked): push branches + tags every
    round. Public repos must already be your own fork; pushing a tag never
    creates a PR. Returns a short status string, never raises.
    """
    tag = "kp/%s/r%02d" % (project, round_no)
    out = []
    env = dict(os.environ)   # hardening block below; needed by the tag check too
    # ---- settings gate: WHAT may leave this machine -------------------------
    try:
        _lc_ckpt = loopctl.load()
    except Exception:
        _lc_ckpt = {}
    _push_ok = _S.push_enabled(_lc_ckpt)
    _push_branches = _S.push_branches()
    if not _S.github_enabled():
        # GitHub is OFF: tag locally and stop. The F1-6 authorship guard still
        # applies -- a tag that exists at a DIFFERENT sha is someone else's
        # and is never moved, even locally. One quiet note in the status,
        # never a warning, never a retry, never a network call.
        _intended_off = _head_full_sha(workdir)
        try:
            _t = subprocess.run(
                ["git", "-c", "credential.helper=", "-C", workdir,
                 "rev-parse", "refs/tags/%s" % tag],
                capture_output=True, text=True, timeout=60,
                errors="replace", creationflags=0x08000000, env=env)
        except Exception as e:
            return "failed: tag check could not run: %s" % e
        _existing_off = (_t.stdout or "").strip() if _t.returncode == 0 else ""
        if _existing_off:
            if not (_intended_off and _existing_off == _intended_off):
                return ("failed: tag %s already exists at %s (HEAD is %s) -- "
                        "refusing to move a tag the runner did not create"
                        % (tag, (_existing_off or "?")[:8],
                           (_intended_off or "?")[:8]))
            return ("published: tag already at this sha; "
                    "push skipped (github disabled)")
        r = subprocess.run(["git", "-c", "credential.helper=", "-C", workdir,
                            "tag", tag],
                           capture_output=True, text=True, timeout=300,
                           errors="replace", creationflags=0x08000000, env=env)
        if r.returncode != 0:
            return "failed at tag: %s" % ((r.stderr or r.stdout or "").strip()[:200])
        return "published: tag rc=0; push skipped (github disabled)"
    # F1-6 AUTHORSHIP (2026-09-30): the runner is the only author of checkpoint
    # tags. Before touching anything, read the tag's CURRENT sha. A tag that
    # already exists is either (a) this round's earlier publish attempt -- its
    # sha must equal the HEAD we are about to publish -- or (b) someone else's
    # tag (a stray child tag, a previous round under a colliding number). Case
    # (b) is T2-1: 88 of 112 logged checkpoints were rc=128 "already exists",
    # and kp/project-a/r34 pointed at a commit that was NOT round 34's
    # state. A mismatched tag is NEVER moved and NEVER pushed over.
    _intended = _head_full_sha(workdir)
    try:
        _cur = subprocess.run(
            ["git", "-c", "credential.helper=", "-C", workdir,
             "rev-parse", "refs/tags/%s" % tag],
            capture_output=True, text=True, timeout=60,
            errors="replace", creationflags=0x08000000, env=env)
    except Exception as e:
        return "failed: tag check could not run: %s" % e
    _existing = (_cur.stdout or "").strip() if _cur.returncode == 0 else ""
    if _existing:
        if not (_intended and _existing == _intended):
            return ("failed: tag %s already exists at %s (HEAD is %s) -- "
                    "refusing to move a tag the runner did not create"
                    % (tag, (_existing or "?")[:8], (_intended or "?")[:8]))
        # The tag already names exactly the state we would publish. Do NOT
        # tag again -- `git tag` on an existing name exits rc=128, which is
        # exactly the false-failure that polluted 88 of 112 logged
        # checkpoints (T2-1). Record the skip and publish only the
        # possibly-missing push.
        out.append("tag already at this sha")
    # NEVER let git open a credential dialog. This runs headless, so a missing
    # credential must FAIL FAST rather than pop Windows Git's `helper-selector`
    # GUI and block a round on a prompt nobody is watching (the operator saw that dialog
    # and reasonably asked whether it was ours).
    # Belt AND braces. GIT_TERMINAL_PROMPT alone was NOT enough: with
    # credential.helper=helper-selector configured, git still invoked the helper
    # and blocked on /dev/tty ("No such device or address"), leaving a
    # git-credential-helper-selector.exe dialog on screen and hanging the round
    # for ~200s before failing rc=128. Pin the helper to an EMPTY value per-invocation
    # (the command-line -c wins over global config, and an empty helper means
    # "do not prompt, do not store -- just fail"), and point askpass at a
    # no-op so nothing can ever open a window from a headless round.
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "GIT_ASKPASS": "true",
        "SSH_ASKPASS": "true",
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "core.askpass",
        "GIT_CONFIG_VALUE_1": "",
    })
    # Skip the push when the repo has no remote at all: it can never succeed, it
    # burns 300s of timeout budget, and it is the thing that triggers auth
    # prompts. Report it once so the config gap stays visible.
    has_remote = False
    _need_tag = not _existing   # same-sha re-publish: skip the tag step
    try:
        rp = subprocess.run(["git", "-c", "credential.helper=", "-C", workdir, "remote"],
                            capture_output=True, text=True, timeout=60,
                            errors="replace", creationflags=0x08000000, env=env)
        has_remote = bool((rp.stdout or "").strip())
    except Exception:
        pass
    # With a token in settings (env / settings.local.yaml), hand git and gh a
    # NON-interactive bearer header instead of relying on any stored
    # credential; without one the fail-fast hardening above stands.
    if _push_ok and has_remote:
        try:
            if _S.github_token():
                env.update(_S.push_env(env))
        except Exception:
            pass
    steps = [["tag", tag]] if _need_tag else []
    if has_remote and _push_ok:
        # branch + tag when github.push_branches (default); tag only otherwise
        if _push_branches:
            steps.append(["push", "origin", "HEAD", tag])
        else:
            steps.append(["push", "origin", tag])
    elif has_remote:
        out.append("push skipped (github disabled)")
    else:
        out.append("push skipped (no git remote)")
    _failed_step = None
    for args in steps:
        try:
            r = subprocess.run(["git", "-c", "credential.helper=", "-C", workdir] + args,
                               capture_output=True, text=True, timeout=300,
                               errors="replace", creationflags=0x08000000, env=env)
            # F-P (2026-10-04): an https push with an EMPTY credential helper
            # fails rc=128 every round ("could not read Username") -- the local
            # tag still lands, so work is never lost, but nothing reaches
            # GitHub and the widget says "failed at push" forever. Retry ONCE
            # with the gh CLI's non-interactive credential helper (verified:
            # `git -c credential.helper='!gh auth git-credential' push` succeeds
            # with no dialog). If that also fails, the original failure stands.
            if (r.returncode != 0 and args[0] == "push"
                    and "could not read Username" in (r.stderr or "")):
                try:
                    r = subprocess.run(
                        ["git", "-c", "credential.helper=!gh auth git-credential",
                         "-C", workdir] + args,
                        capture_output=True, text=True, timeout=300,
                        errors="replace", creationflags=0x08000000, env=env)
                    out.append("push retried via gh credential helper rc=%s"
                               % r.returncode)
                except Exception as e:
                    out.append("push gh-retry ERR %s" % e)
            out.append("%s rc=%s" % (args[0], r.returncode))
            if r.returncode != 0 and _failed_step is None:
                _failed_step = args[0]
        except Exception as e:
            out.append("%s ERR %s" % (args[0], e))
            if _failed_step is None:
                _failed_step = args[0]
    if _failed_step is not None:
        return "failed at %s: %s" % (_failed_step, "; ".join(out))
    return "published: %s" % "; ".join(out)


def _run_gate(proj: dict) -> dict | None:
    """
    Run the project's gate command and capture rc + a short output tail.

    The round prompt already instructs the child to run the gate itself; this
    is the runner's own INDEPENDENT measurement, so the row's gate verdict is
    a fact, not the child's claim. The gate string is the project owner's
    trusted config (improve.yaml), so running it here is not widget-style
    command execution. Never raises; returns None only when there is no gate
    command to run -- a missing verdict must read "unknown", never "ok".
    """
    gate_cmd = (proj.get("gate") or "").strip()
    if not gate_cmd:
        return None
    workdir = proj.get("path") or ""
    timeout = int(proj.get("gate_timeout") or _S.gate_timeout_default())
    env = dict(os.environ)
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
    })
    try:
        r = subprocess.run(gate_cmd, shell=True, cwd=workdir or None,
                           capture_output=True, text=True, timeout=timeout,
                           errors="replace", env=env,
                           creationflags=0x08000000)
        rc = r.returncode
        out = ((r.stdout or "") + (r.stderr or ""))[-600:]
    except subprocess.TimeoutExpired:
        rc = 124
        out = "gate TIMEOUT after %ss" % timeout
    except Exception as e:                              # noqa: BLE001
        return {"cmd": gate_cmd, "ok": False, "detail": "gate could not run: %r" % (e,)}
    detail = (out or "").strip().splitlines()
    detail = detail[-3:] if detail else ["(no output)"]
    return {"cmd": gate_cmd, "ok": (rc == 0), "detail": "rc=%s :: %s"
            % (rc, " | ".join(detail))[-400:]}


def _parse_scope(text: str) -> dict:
    """Read this round's SURFACE-DONE / CROSS_CUTTING lines.

    Called with the child's FULL final message, before the 2000 B tail slice.
    Never raises: a round must not fail because it phrased a marker oddly.
    """
    try:
        return scope.parse(text)
    except Exception as e:
        log("scope parse failed: %s" % e)
        return {"surfaces": {}, "cross_cutting": None}


# ---------------------------------------------------------------------------
# T2-6 (2026-09-30): the human-answer pipeline, closed. watch_answers.py
# and dn.remind write state/answer.json ({qid, answer, at[, assumed]});
# until now NOTHING in the live round path read it -- the prompt's
# "continue with the best-reasoned default" promise had no reader. The
# runner now renders any unconsumed answer into the round prompt and moves
# the file to answers_consumed/ so an answer can never replay for rounds
# after the one it was given to.
# ---------------------------------------------------------------------------
ANSWERS_DIR = ROOT / "state"
ANSWERS_CONSUMED = ROOT / "state" / "answers_consumed"


def _take_answer() -> dict | None:
    """Consume state/answer.json if present. Returns the answer dict or
    None. The consume (move to answers_consumed/) happens BEFORE the child
    launches: an answer is delivered exactly once, even across a crash.
    Never raises (a missing/corrupt answer must not break a round)."""
    try:
        f = ANSWERS_DIR / "answer.json"
        if not f.exists():
            return None
        d = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(d, dict) or not (d.get("answer") or "").strip():
            f.unlink(missing_ok=True)   # junk/empty: gone, not replayed
            return None
        ANSWERS_CONSUMED.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        f.replace(ANSWERS_CONSUMED / ("%s-%s.json" % (stamp, (d.get("qid") or "q"))))
        return d
    except Exception as e:
        # F-A (2026-10-02): a file that cannot be PARSED can never be delivered
        # -- leaving it in place replayed the failure every round (44 live
        # "answer intake failed" stains). Delete the poison pill; the loss is
        # logged here once, which is the honest record.
        log("answer intake failed (%s) -- deleting the unreadable answer file "
            "so it is not replayed every round" % e)
        try:
            (ANSWERS_DIR / "answer.json").unlink(missing_ok=True)
        except Exception:
            pass
        return None


def _answer_block(ans: dict | None, project: str) -> str:
    """The prompt block carrying the human's answer (or assumption)."""
    if not ans:
        return ""
    assumed = bool(ans.get("assumed"))
    head = ("HUMAN ANSWER (assumed after the ping budget expired -- override by"
            " replying in the channel)" if assumed
            else "HUMAN ANSWER -- the operator replied to this round's question")
    lines = ["", head, "    %s" % ((ans.get("answer") or "").strip()[:800]), ""]
    return "\n".join(lines) + "\n"


def run_round(proj: dict, entry: dict) -> int:
    """One round in this project's thread. Returns the hermes rc.

    F3 ISOLATION BOUNDARY (2026-09-30): the child runs inside a disposable
    git worktree branched from the pre-round anchor. It never touches the
    canonical checkout; on reject the worktree (and its branch) are deleted,
    and on accept the runner -- the only integrator and the only publisher --
    ff-merges the round branch into canonical, re-gates there, then tags and
    pushes. The child cannot push: its env carries
    remote.origin.pushurl=DISABLED via GIT_CONFIG_* entries (_child_env).
    """
    name = proj["name"]
    sid = threads.resolve(name)
    if not sid:
        log("%s: could not resolve/create thread" % name)
        return 1

    round_no = int(entry.get("rounds") or 0) + 1
    facts = I.repo_facts(proj)
    angle = angle_pick.pick(name, facts=facts)

    # MODEL SEATS (F3.7): the implementer seat comes from loop.json, validated
    # at startup by _validate_seats(); the reviewer seat rides to the prompt.
    _lc = loopctl.load()
    # Priority: loopctl config (loopctl config --implementer) > settings.yaml /
    # settings.local.yaml / env (models.implementer) > BUILDER_FALLBACK.
    _builder = ((_lc.get("implementer") or "").strip()
                or _S.implementer_seat() or _S.builder_fallback())
    _seats = {"reviewer": ((_lc.get("reviewer") or "").strip()
                           or _S.reviewer_seat())}

    # Record the repo's HEAD BEFORE the child touches anything, so the end of the
    # round can prove a commit actually appeared. Without this the "did the round
    # produce a delta?" question is unanswerable, and an unanswerable question
    # must not be allowed to authorise a checkpoint (Saul, 2026-09-29).
    #
    # Also record whether the tree started CLEAN. This is the reject-contamination
    # guard (Saul's blocker #6): if a round is rejected we must be able to put the
    # tree back exactly as we found it, or a later accepted round inherits -- and
    # PUBLISHES -- the rejected round's commit and untracked files.
    _workdir = proj.get("path") or ""
    entry["pre_round_head"] = _head_full_sha(_workdir)
    entry["pre_round_dirty"] = _is_dirty(_workdir)
    if entry["pre_round_head"] is None:
        # No commit to anchor to => we cannot verify a delta, and we cannot roll
        # back. HARD REFUSAL (F0-1, 2026-09-30): this used to be a log line and
        # the round launched anyway -- a round that cannot be verified cannot be
        # contained, and an uncontained child must never touch the repo.
        _why_refuse = "no pre-round HEAD readable for %r" % (_workdir or "?")
    elif entry["pre_round_dirty"] is not False:
        # A dirty starting tree makes rollback DESTROYIVE: `reset --hard` +
        # `clean -ffd` would erase PRE-EXISTING tracked/staged/untracked work
        # that the loop did not create and must never delete (Saul, 2026-09-30:
        # "risk of proceeding: irreversible deletion of human work ... not
        # comparable. Refusal is the correct default"). HARD REFUSAL.
        _why_refuse = ("working tree is %s at round start -- refusing so "
                       "rollback can never destroy pre-existing work"
                       % ("DIRTY" if entry["pre_round_dirty"] else "UNKNOWN"))
    else:
        _why_refuse = None
    if _why_refuse:
        log("%s: ROUND REFUSED -- %s. The child was NOT launched. Clear the "
            "working tree (commit/stash/clean) and the next nudge will retry."
            % (name, _why_refuse))
        entry["round_refused"] = _why_refuse
        entry["last_rc"] = 1
        # Review finding (B round, 2026-09-30): a refusal must consume the
        # scheduling slot. last_nudge was NOT advanced on this path, so a
        # persistently-refusing repo stayed the oldest and was re-picked
        # EVERY tick -- the 2026-09-28 hot-loop review item, now fixed.
        entry["last_nudge"] = time.time()
        try:
            dn.progress(name, "round REFUSED (child NOT launched): %s" % _why_refuse)
        except Exception:
            pass  # notification is best-effort; the refusal itself already logged
        _persist_entry(name, entry)   # HIGH-1: refusal state must reach disk
        return 1

    # ---- F3.1: the round's disposable worktree -----------------------------
    # Created from the anchor we just captured, so containment = deletion and
    # the canonical checkout is never exposed to the child at all.
    wt, wt_note = _make_worktree(_workdir, name, round_no, entry["pre_round_head"])
    if wt is None:
        log("%s: ROUND REFUSED -- %s. The child was NOT launched." % (name, wt_note))
        entry["round_refused"] = wt_note
        entry["last_rc"] = 1
        entry["last_nudge"] = time.time()   # refusal consumes the slot (see above)
        try:
            dn.progress(name, "round REFUSED (no worktree): %s" % wt_note)
        except Exception:
            pass
        _persist_entry(name, entry)   # HIGH-1: refusal state must reach disk
        return 1
    entry["worktree"] = str(wt)
    # F-C (2026-10-02): a successful worktree launch means the PREVIOUS
    # refusal no longer describes this repo. The key used to stick forever,
    # so live entries carried "working tree is DIRTY..." next to rounds that
    # ran fine -- the panel lied about 3 of 4 repos.
    entry.pop("round_refused", None)
    for _n in _provision_worktree_deps(_workdir, wt):
        log("%s: worktree %s" % (name, _n))

    # ---- SURFACE SCOPE (the operator, 2026-09-28) ---------------------------------
    # A surface is a sub-category of a repo that can be worked on independently.
    # Surfaces ROUND-ROBIN inside a project, so the project still counts as one
    # visit: `last_nudge` below is untouched and a surface is never a rotation
    # slot. Fewer than two surfaces returns None and every line of this is a
    # no-op, which is what keeps single-surface repos byte-identical.
    surface = None
    campaign = entry.get("campaign") if isinstance(entry.get("campaign"), dict) else None
    try:
        import surface_pick
        # Review fix C (2026-10-01): a held campaign round must not be
        # assigned an already-terminal surface -- rotate among the
        # campaign's OUTSTANDING surfaces.
        _prefer = None
        if isinstance(campaign, dict) and (campaign.get("state") or "open") == "open":
            _prefer = scope.outstanding_surfaces(campaign) or None
        surface = surface_pick.pick(name, proj, prefer=_prefer)
    except Exception as e:
        log("%s: surface pick failed (%s); continuing unscoped" % (name, e))
        surface = None

    # Record the surface the moment it is chosen, beside current_angle, for the
    # same reason: the panel says "working on X right now", and a 40-minute round
    # would otherwise display the previous round's surface for its whole run.
    if surface:
        entry["current_surface"] = {
            "id": surface.get("id"), "index": surface.get("index"),
            "total": surface.get("total"), "started": time.time(),
        }

    # T2-6: consume any human answer BEFORE building the prompt so it rides
    # THIS round (the consume is the delivery; there is no second reader).
    _ans = _take_answer()
    if _ans:
        log("%s: human answer consumed: %s"%(name, ((_ans.get("answer") or "")[:80])))
    prompt = round_prompt.build(name, angle, proj.get("gate") or "", round_no, entry,
                                surface=surface, campaign=campaign,
                                scope_question=(entry.get("scope_question")
                                                if isinstance(entry.get("scope_question"), dict)
                                                else None),
                                surface_see=(I.surface_see_for(proj)
                                             if hasattr(I, "surface_see_for") else None),
                                seats=_seats, worktree=str(wt),
                                canonical=_workdir, worktree_branch=_branch_for(name, round_no),
                                human_answer=_answer_block(_ans, name))

    # CONSUME THE PROMPT the moment it is issued (the operator, 2026-09-27 -- locked).
    # `pick()` already selected an UNUSED prompt for this repo's cycle; recording
    # it here means it can never be re-issued, INCLUDING when the round comes back
    # not-applicable. That is deliberate: a dead prompt must not be retried forever
    # or the cycle never closes. When the cycle is exhausted, `mark_used` wipes the
    # slate and increments the cycle number, so the next round starts a fresh pass.
    # Best-effort only -- a bookkeeping failure must never break a round.
    _pid = (angle or {}).get("prompt_id")
    if _pid:
        try:
            import angle_prompts as _P2  # noqa
        except Exception:
            _P2 = None
        if _P2 is not None:
            try:
                _rec = _P2.mark_used(name, _pid, "issued")
                log("%s: prompt %s consumed (cycle %s, %d/%d used)" %
                    (name, _pid, _rec.get("cycle"), len(set(_rec.get("used", []))),
                     len(_P2.all_prompt_ids())))
            except Exception as e:
                log("%s: prompt bookkeeping failed: %s" % (name, e))

    # Record the angle the moment it is chosen, NOT after the round: the panel shows
    # "working on <angle> right now", and a 40-minute round would otherwise display
    # the PREVIOUS round's angle for its whole duration.
    # `angle_pick.pick()` returns the angle id under the key `angle` -- NOT `id`.
    # Mapping only `id` left this blank and the panel showed no angle at all.
    _aid = ((angle or {}).get("id") or (angle or {}).get("angle")
            or (angle or {}).get("name") or "")
    entry["current_angle"] = {
        "id": _aid,
        "family": (angle or {}).get("family") or "",
        "lens": ((angle or {}).get("lens") or (angle or {}).get("text")
                 or (angle or {}).get("evidence_required") or ""),
        "prompt_id": _pid or "",
        "round": round_no,
        "started": time.time(),
        "prompt": prompt,
    }
    entry["last_angle"] = _aid
    entry["session_id"] = sid
    try:
        _reg = threads.load()
        _reg[name] = entry
        threads.save(_reg)
    except Exception as e:
        log("%s: could not persist current_angle: %s" % (name, e))

    try:
        dn.progress(name, "round %d: angle %s -- starting" %
                    (round_no, (angle or {}).get("id")), round_no=round_no)
    except SystemExit as _e:
        log("%s: discord token missing (round start notify skipped): %s" % (name, _e))
    except Exception as _e:
        # F0-4: a Discord outage must never abort a round. Notification is
        # bookkeeping, not control flow.
        log("%s: discord notify failed (round start): %s: %s"
            % (name, type(_e).__name__, _e))

    cmd = [str(HERMES_PY), "-m", "hermes_cli.main",
           "-p", _S.hermes_profile() or threads.PROFILE, "--model", _builder,
           "-z", prompt, "--resume", sid]
    # Popen (not subprocess.run) so a TIMEOUT gives us the child's PID to kill.
    # With subprocess.run the handle is discarded and `_kill_tree(None)` was a
    # silent no-op -- the hang guard could never actually kill anything.
    # F3.2: cwd is the WORKTREE, not the hermes checkout -- the child's file
    # tools default to the isolated copy. hermes_cli stays importable via
    # PYTHONPATH in _child_env(); the egress boundary rides in the same env.
    _proc = subprocess.Popen(cmd, cwd=str(wt), env=_child_env(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, errors="replace",
                             creationflags=0x08000000)
    try:
        _out, _err = _proc.communicate(timeout=ROUND_TIMEOUT)
        rc = _proc.returncode
        # PARSE THE FULL OUTPUT, BEFORE THE TRUNCATION (adversarial review H1).
        # `_out` holds the child's entire final message; the `tail` slice below
        # keeps only the last 2000 B, and `last_output` keeps 600 B. A real
        # round's final message measured 6,148 B, so a marker parsed from `tail`
        # is found roughly when it is least needed -- i.e. almost never.
        # Scope markers are therefore read from the whole string, here, at the
        # only point where the whole string still exists.
        #
        # NAME CAREFULLY: this MUST NOT be called `scope`, because `scope` is the
        # imported MODULE. Calling it `scope` shadowed the module, so the
        # campaign block below called `scope.next_campaign(...)` on a DICT and
        # raised AttributeError -- which the broad handler then swallowed. Net
        # effect: campaigns never advanced, while unit tests of scope.py passed,
        # because they never exercised this call site. (Found by an external
        # review, 2026-09-29; reproduced before fixing.)
        round_scope = _parse_scope(_out or "")
        tail = ((_out or "") + (_err or ""))[-2000:]
    except subprocess.TimeoutExpired:
        # A hung child must not block the whole rotation. Kill the CHILD's tree
        # (the runner keeps going) and record why, so the next round still runs.
        rc = 124
        killed = _kill_tree(_proc.pid)
        # A TIMEOUT MUST NEVER ADD A VERDICT. Without this the name is unbound
        # below and the campaign block raises into its own handler; with a stale
        # binding it could fold a previous round's verdict twice.
        round_scope = {"surfaces": {}, "cross_cutting": None}
        try:
            _proc.communicate(timeout=30)       # reap so no zombie is left behind
        except Exception:
            pass
        tail = "TIMEOUT after %ss -- %s" % (ROUND_TIMEOUT, killed)
        log("%s round %d TIMED OUT after %ss -- %s, rotating on"
            % (name, round_no, ROUND_TIMEOUT, killed))

    entry["rounds"] = round_no
    entry["last_nudge"] = time.time()
    entry["last_rc"] = rc
    entry["last_angle"] = _aid
    # Round-duration telemetry (the operator, 2026-10-01): how long THIS round
    # ran, plus a bounded per-repo history for the avg/round display.
    # The clock starts at the angle pick (current_angle.started) -- the
    # child's actual working window. Refusals return before the angle
    # exists, so they never pollute the history; timeouts count whole
    # (they are real runtime).
    _rt_start = (entry.get("current_angle") or {}).get("started")
    if isinstance(_rt_start, (int, float)) and _rt_start > 0:
        _rt_secs = max(0, int(time.time() - _rt_start))
        entry["last_round_seconds"] = _rt_secs
        _rt_hist = [s for s in (entry.get("round_seconds_hist") or [])
                    if isinstance(s, (int, float))][-29:]
        _rt_hist.append(_rt_secs)
        entry["round_seconds_hist"] = _rt_hist
    entry["session_id"] = sid
    entry["title"] = threads.title_for(name)

    # ---- CAMPAIGN PROPOSAL (surfaces, the operator 2026-09-28) ---------------------
    # PURE. This block only computes what the campaign WOULD become; it changes
    # no state. (External review / Saul, 2026-09-29: the previous version mutated
    # campaign state HERE -- before the runner knew whether the round had passed
    # its gate -- so a child could mark a surface done, or open/close a campaign,
    # on a round that then failed. Campaign state must follow the runner's
    # ACCEPTANCE of the round, never the child's claim.)
    #
    # The mutation now happens further down, after evidence + gate are known.
    # `round_scope` was parsed from the child's FULL output above, before the
    # 2000 B truncation, so the markers are complete.
    campaign_prop = None
    proposal_state = None   # F1-3: None = never successfully evaluated. The old
                            # code left campaign_prop=None on exception and the
                            # round could STILL be accepted -- which made the
                            # propose() failure modes invisible to acceptance
                            # and neutralized scope.py's "programming errors
                            # propagate loudly" contract.
    try:
        _declared = None
        try:
            _declared = I.surfaces_for(proj)      # manifest slugs: the ONLY
        except Exception:                        # authority on surface names
            _declared = None
        _before = scope.summary_line(entry.get("campaign"))
        # B4: the citation predicate is bound to THIS round's worktree -- the
        # tree the child's claims describe. Fail-closed inside the predicate.
        try:
            _cite_ok = scope.default_cite_check(wt)
        except Exception as _ce:
            log("%s: cite-check build failed (%s) -- closing gate disabled for "
                 "this round" % (name, _ce))
            _cite_ok = None
        campaign_prop = scope.propose(entry, round_scope, name, declared=_declared,
                                      cite_ok=_cite_ok)
        if isinstance(campaign_prop, dict) and campaign_prop.get("proposal_ok") is not False:
            proposal_state = True
        else:
            proposal_state = False
            log("%s: campaign proposal REJECTED (proposal_ok=False): %s"
                % (name, (campaign_prop or {}).get("reason") or "?"))
        _would = campaign_prop.get("campaign")
        if campaign_prop.get("closed"):
            _would_txt = "CLOSED %s" % (campaign_prop["closed"].get("id") or "")
        else:
            _would_txt = scope.summary_line(_would)
        if _would_txt != _before:
            log("%s: campaign would go %s -> %s" % (name, _before, _would_txt))
        if campaign_prop.get("refused"):
            log("%s: REFUSED undeclared surface(s): %s -- a CROSS_CUTTING marker "
                "may only name surfaces the manifest declares"
                % (name, ", ".join(campaign_prop["refused"])))
        if round_scope.get("surfaces") or round_scope.get("cross_cutting"):
            log("%s: scope markers: %d verdict(s), crossing=%s"
                % (name, len(round_scope.get("surfaces") or {}),
                   (round_scope.get("cross_cutting") or {}).get("surfaces")))
        # T3-4: a dropped CROSS_CUTTING line is a child signal that vanished;
        # it must at least be visible in the log.
        if round_scope.get("cross_dropped"):
            log("%s: scope markers: %d extra CROSS_CUTTING line(s) DROPPED -- "
                "only the first was parsed" % (name, round_scope["cross_dropped"]))
    except Exception as e:
        # NOTE: this handler is why the shadowing bug hid for so long. A silent
        # swallow turned a hard failure into "nothing happened". Log at a level
        # that is greppable, and name the exception type so an AttributeError
        # (a CODE bug) is distinguishable from an I/O error (an environment one).
        log("%s: campaign proposal failed: %s: %s"
            % (name, type(e).__name__, e))

    # Persist the child's output tail. Without this a failed round is INVISIBLE:
    # 99 of the first 114 rounds exited rc=1 and left no evidence anywhere,
    # because `tail` was captured and thrown away. Keep the last 2 KB of the
    # child's stdout+stderr on disk per round (rotating, one file per repo) plus
    # a short tail in the registry so the widget/brief can show WHY it failed.
    try:
        entry["last_output"] = tail[-600:]
        _od = ROOT / "logs" / "rounds"
        _od.mkdir(parents=True, exist_ok=True)
        (_od / ("%s.r%03d.log" % (name, round_no))).write_text(
            "rc=%s\n%s\n" % (rc, tail), encoding="utf-8", errors="replace")
        # keep only the 5 newest round logs per repo
        _mine = sorted(_od.glob("%s.r*.log" % name), key=lambda p: p.stat().st_mtime)
        for _old in _mine[:-5]:
            try:
                _old.unlink()
            except OSError:
                pass
    except Exception as _e:
        log("%s: could not persist round output: %s" % (name, _e))

    # rc != 0 is a FAILED round and must be loud. Log the child's own words at
    # the point of failure -- one line the operator can actually act on.
    if rc != 0:
        _first = ""
        for _ln in (tail or "").splitlines():
            if _ln.strip():
                _first = _ln.strip()[:200]
                break
        log("%s round %d FAILED rc=%s :: %s" % (name, round_no, rc, _first))
    # Bounded angle history (newest first) so the panel can show what this thread has
    # already worked, which stops a reader assuming it is re-doing the same angle.
    hist = list(entry.get("angle_history") or [])
    hist.insert(0, {
        "id": _aid,
        "family": (angle or {}).get("family") or "",
        "lens": (angle or {}).get("lens") or "",
        "round": round_no, "rc": rc, "at": time.time(),
        "summary": (entry.get("last_summary") or "")[:200],
    })
    # F1-2 (2026-09-30, off-by-one): reassign entry["angle_history"] BEFORE the
    # evidence block. round_evidence.attach() writes into entry["angle_history"][0]
    # -- if we reassign AFTER attach, attach mutates the OLD list (previous round's
    # row) and line X then DISCARDS it. The gate/commit evidence landed on the
    # wrong round and acceptance read nothing. Verified by replay before fixing.
    entry["angle_history"] = hist[:20]
    # Layer 2 -- Round Evidence: the commit and the gate output ARE the
    # evidence. Derive the git facts (read-only) and the runner's OWN gate
    # measurement, and merge them into THIS row before the registry is saved.
    # A missing value stays missing (renders "unknown"), never fabricated.
    _ev = None      # F1-1: kept in a local so the acceptance block can read the
                    # gate WITHOUT a round-trip through the registry. The old
                    # code read entry["evidence"] -- a key NOTHING writes -- so
                    # _gate_state was always None and every round was rejected.
    try:
        import round_evidence
        # F3.4: evidence and the pre-merge gate measure the WORKTREE (a proj
        # copy with path=wt); the gate command is unchanged because the
        # canonical dep dirs (.venv / node_modules) are junctioned in.
        _wt_proj = dict(proj, path=str(wt))
        _ev = round_evidence.collect(_wt_proj, round_no)
        # F1-4: do not spend up to 900s running the gate on a tree the child
        # left behind after a FAILED round -- containment comes first.
        _ev["gate"] = _run_gate(_wt_proj) if rc == 0 else None   # None only when skipped/no cmd
        round_evidence.attach(entry, _ev)
        if _ev.get("git_error"):
            log("%s: round %d evidence partial: %s" % (name, round_no,
                                                       _ev["git_error"]))
    except Exception as _e:
        log("%s: round evidence collection failed: %s" % (name, _e))

    # F1-8: /compact via `-z` NEVER compacted (the command is only dispatched by
    # the interactive REPL; oneshot treats it as chat text). Every 8 rounds it
    # burned a model turn and logged false "compacted" bookkeeping. Auto-
    # compression already protects long threads -- drop the fake call entirely.
    # (compact_thread.py kept on disk for reference; nothing imports it now.)

    # End-of-run checkpoint: tag + GitHub push.
    #
    # GATED ON ROUND SUCCESS (external review, 2026-09-29). This used to run
    # UNCONDITIONALLY, so a timed-out or gate-failing round still received a
    # `kp/<repo>/rNN` tag and a GitHub push -- publishing a checkpoint that
    # declared a failed round legitimate, and moving the repo's visible history
    # forward on work that never passed. A checkpoint is a claim; it must only
    # be made when the round actually succeeded.
    #
    # "Succeeded" is deliberately narrow and runner-observed, never the child's
    # own claim:
    #   rc == 0                       -- the child exited cleanly
    #   gate definitely OK            -- the runner's OWN gate run must PASS.
    #                                    A MISSING gate result is NOT success: it
    #                                    is "no observed failure", which is a
    #                                    different fact. (Saul, 2026-09-29.)
    #   a commit actually appeared    -- a round claiming success that produced no
    #                                    delta is not a checkpointable round.
    # F1-1: the gate is read from THIS round's evidence local (`_ev`), which the
    # evidence block above just computed. The old code read entry["evidence"] /
    # entry["last_evidence"] -- keys NOTHING in the codebase writes -- so the
    # gate was never seen, _gate_state was always None, and EVERY round was
    # rejected (and then its good commit was destroyed by the rollback).
    # F1-4: skip the gate entirely when the child already failed -- a 900s
    # gate run on a dirty tree only delays the rollback.
    _gate = (_ev or {}).get("gate") if isinstance(_ev, dict) else None
    # TRI-STATE, because "we did not measure" must never read as "it passed":
    #   True  -> ran and passed        False -> ran and failed      None -> unknown
    if rc != 0:
        _gate_state = None                  # not measured: the round already failed
        if _gate is not None:
            log("%s round %d: gate skipped (rc=%s) -- containment first"
                % (name, round_no, rc))
    elif isinstance(_gate, dict):
        _gate_state = bool(_gate.get("ok"))
    else:
        _gate_state = None
    _gate_ok = _gate_state is True

    # HEAD must have advanced, and PROVABLY so.
    #
    # TWO BUGS LIVED HERE (found by Saul, 2026-09-29, after "84 tests pass"):
    #
    #   1. `round_evidence` records a SHORT sha (`git show --format=%h`) while the
    #      pre-round capture is FULL (`git rev-parse HEAD`). A 7-char string never
    #      equals a 40-char one, so `before != after` was ALWAYS TRUE -- the gate
    #      was dead code that waved through every no-op round.
    #   2. `_head_ok is not False` ACCEPTS None, contradicting the comment right
    #      above it. Unknown must never authorise.
    #
    # A bare "different sha" is also not enough on its own: an amend, rebase or
    # reset also changes the sha. So we now do it in git, comparing FULL shas and
    # requiring `before` to be a genuine ANCESTOR of `after` -- forward movement
    # from the recorded baseline, not merely a different string.
    # F3.4: the child committed in the WORKTREE, so the delta is measured
    # there -- canonical has not moved and must not have.
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
        reg = threads.load()
        reg[name] = entry
        threads.save(reg)
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
        _reg2 = threads.load()
        _reg2[name] = entry
        threads.save(_reg2)
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

            reg = threads.load()
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
            try:
                run_round(proj, entry)
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
