#!/usr/bin/env python3
"""
loopctl.py -- the on/off switch for the Karpathy Loop.

Single source of truth: state/loop.json. Everything else (rotator, nudger,
desktop page) reads it. Nothing starts work unless running is true.

  python loopctl.py status
  python loopctl.py start
  python loopctl.py pause  --reason "reviewing"
  python loopctl.py stop
  python loopctl.py config --projects project-a,project-b
  python loopctl.py config --angles-per-visit 2
  python loopctl.py config --max-rounds 20
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
import settings as _S  # noqa: E402  settings.py is the single source for machine config

# Mirror of karpathy_runner.ROUND_TIMEOUT (seconds). Used only to decide when a
# RUNNING flag has gone stale. Keep the two in step -- both read it from
# settings (runtime.round_timeout, env KL_ROUND_TIMEOUT overrides).
ROUND_TIMEOUT_S = _S.round_timeout()
STATE = ROOT / "state"
LOOP = STATE / "loop.json"
ROTATION = STATE / "rotation.json"

DEFAULTS = {
    "running": False,
    "paused_reason": None,
    "projects": [],
    "angles_per_visit": 2,
    "max_rounds": 0,        # 0 = forever
    "max_hours": 0,         # 0 = forever
    # Two model seats. They MUST differ: the implementer writes the change, the
    # reviewer reads the diff with a different brain. Same model in both seats
    # means the same blind spot twice.
    # F3.7 (2026-09-30): the previous defaults named providers HIDDEN on
    # 2026-09-30 (glm53-2x EXL3, deepseek-v41-3x) -- the loop then launched
    # children against dead endpoints. These are the LIVE fleet seats:
    # implementer -> the GLM-5.3-Flash-FP8 TP4 bottle, reviewer -> qwen3.8
    # on d754 (different family AND different box).
    "implementer": "custom:glm53-flash-4x-spark-tp4:GLM-5.3-Flash-FP8",
    "reviewer": "custom:d754-mia-flashnext:qwen3.8-flash-next",
    "sweep_minutes": 360,
    "rounds_done": 0,
    "pushed_to_github": True,
    "started_at": None,
    "paused_at": None,
    "updated_at": None,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load() -> dict:
    cfg = dict(DEFAULTS)
    if LOOP.exists():
        try:
            cfg.update(json.loads(LOOP.read_text(encoding="utf-8")))
        except Exception as e:
            # Corrupt: preserve the damaged file before defaults clobber it.
            # (F0-3, 2026-09-30: a torn loop.json + any loopctl command used to
            # silently reset projects/max_rounds to defaults.)
            try:
                salvage = LOOP.parent / ("loop.json.corrupt-%s"
                                         % datetime.now().strftime("%Y%m%d-%H%M%S"))
                LOOP.replace(salvage)
                print(f"WARN: loop.json CORRUPT ({e}) -- moved to {salvage.name}; "
                      "using defaults. The projects list was in the moved file.",
                      file=sys.stderr)
            except Exception:
                print(f"WARN: loop.json unreadable ({e}); using defaults", file=sys.stderr)
    return cfg


def save(cfg: dict) -> None:
    """Atomic write (F0-3): temp + os.replace so a torn loop.json can never
    exist. A torn file previously reset the whole loop config on next read."""
    STATE.mkdir(parents=True, exist_ok=True)
    cfg["updated_at"] = _now()
    tmp = LOOP.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, LOOP)


def _sync_rotation(cfg: dict) -> None:
    """Mirror the project list into rotation.json so the rotator agrees."""
    rot = {"projects": [], "order": []}
    if ROTATION.exists():
        try:
            rot.update(json.loads(ROTATION.read_text(encoding="utf-8")))
        except Exception:
            pass
    rot["projects"] = cfg["projects"]
    rot["enabled"] = cfg["running"]
    rot["angles_per_visit"] = cfg["angles_per_visit"]
    tmp = ROTATION.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rot, indent=2), encoding="utf-8")
    os.replace(tmp, ROTATION)


# --------------------------------------------------------------------------
def _liveness(cfg: dict) -> dict:
    """Intent vs reality, as DATA.

    `cfg['running']` is INTENT (what the operator asked for). This computes
    REALITY: is a round actually in flight, how long since one landed, and is the
    process that wrote the heartbeat still alive.

    Extracted 2026-09-28: the verdict used to be computed only on the text path,
    AFTER the `--json` early return -- so the widget (which parses exactly
    `status --json`) could never see a wedge. The dashboard reported healthy on a
    wedged loop, which is the precise failure this detection exists to prevent.
    Returns a plain dict so BOTH paths print the same computed truth.
    """
    hb_path = ROOT / "state" / "runner_heartbeat.json"
    reg_path = ROOT / "state" / "threads.json"
    try:
        hb = json.loads(hb_path.read_text(encoding="utf-8")) if hb_path.exists() else {}
    except Exception:
        hb = {}
    try:
        reg = json.loads(reg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        reg = {}

    last_round = max([(e.get("last_nudge") or 0) for e in reg.values()] or [0])
    since_round = (time.time() - last_round) / 60.0 if last_round else None
    hb_age = (time.time() - hb.get("ts", 0)) / 60.0 if hb.get("ts") else None

    # A heartbeat is only meaningful if the process that wrote it still exists:
    # after a kill the file survives (a killed runner never runs its `finally`),
    # so a stale file can otherwise read as "a round is in flight".
    hb_pid = hb.get("pid")
    hb_pid_alive = None
    if hb_pid:
        try:
            import subprocess as _sp
            # Review fix L5 (2026-10-01): match the PID FIELD in CSV output
            # exactly, not a substring of the table (a recycled/similar pid
            # or header text could otherwise read as alive).
            _o = _sp.run(["tasklist", "/FI", f"PID eq {hb_pid}", "/FO", "CSV",
                          "/NH"],
                         capture_output=True, text=True, timeout=20).stdout or ""
            hb_pid_alive = any(
                _row.split(",")[1].strip('"') == str(hb_pid)
                for _row in _o.splitlines() if _row.count(",") >= 1)
        except Exception:
            hb_pid_alive = None

    stale_min = (ROUND_TIMEOUT_S / 60.0) * 2
    on = bool(cfg.get("running"))
    wedge_why = None
    if on:
        hb_missing = hb_age is None
        round_stale = since_round is not None and since_round > stale_min
        hb_stale = hb_age is not None and hb_age > stale_min
        # F-O (2026-10-02): a FRESH round-started heartbeat with a LIVE pid is
        # positive evidence that a round is in flight RIGHT NOW. After a
        # restart following a long pause, `since_round` (time since the last
        # LANDED round) legitimately exceeds the window while the current round
        # is still working — firing "wedged" there was a false alarm that
        # survived the entire in-flight round. A stale-or-missing heartbeat
        # still wedges: only the round_stale arm is overridden, and only while
        # the heartbeat is fresh AND its process is verifiably alive.
        hb_works = (
            hb.get("state") == "round-started"
            and hb_age is not None
            and hb_age <= stale_min
            and hb_pid_alive is True
        )
        if hb_missing or hb_stale or (round_stale and not hb_works):
            if hb_missing:
                wedge_why = "no heartbeat file (no runner is driving the loop)"
            elif hb_stale:
                wedge_why = "the runner heartbeat is stale"
            else:
                wedge_why = "no round has landed"

    return {
        "running": on,
        "wedged": wedge_why is not None,
        "wedge_why": wedge_why,
        "heartbeat_state": hb.get("state"),
        "heartbeat_project": hb.get("project"),
        "heartbeat_pid": hb_pid,
        "heartbeat_pid_alive": hb_pid_alive,
        "heartbeat_age_min": None if hb_age is None else round(hb_age, 1),
        "last_round_age_min": None if since_round is None else round(since_round, 1),
        # A stale heartbeat on a PAUSED loop is not a wedge (nothing is supposed to
        # be running) but it is still misleading, so it is reported separately.
        "heartbeat_stale_on_paused": (
            not on and (hb_pid_alive is False
                        or (hb_age is not None and hb_age > stale_min))
        ),
        "stale_after_min": round(stale_min, 1),
    }


def _pid_alive(pid) -> bool:
    """Is a PID a live process? (loopctl copy of monitor's guard --
    the in-flight timer must not tick on a stale heartbeat.)"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


def _avg_round_secs(entry: dict):
    """Average of the repo's recent round durations, or None.

    Telemetry helper (the operator, 2026-10-01): entry["round_seconds_hist"] is a
    bounded list the runner stamps at each round's end. Garbage values are
    filtered, never trusted."""
    if not isinstance(entry, dict):
        return None
    vals = [s for s in (entry.get("round_seconds_hist") or [])
            if isinstance(s, (int, float)) and 0 <= s <= 200000]
    if not vals:
        return None
    return int(round(sum(vals) / len(vals)))


def _fmt_mmss(secs) -> str:
    """Human duration: 45m, 1h12m, 42s."""
    try:
        s = int(secs)
    except (TypeError, ValueError):
        return "-"
    if s < 0:
        return "-"
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


def _timing_rows(now=None) -> list:
    """Per-project timing snapshot for status/monitor: (name, rounds,
    avg_secs, n, last_secs, in_flight_secs). in_flight_secs is set only for
    the repo whose round the heartbeat says is running."""
    import threads as _t
    reg = _t.load()
    _hb_path = ROOT / "state" / "runner_heartbeat.json"
    hb = _hb_path.read_text(encoding="utf-8") if _hb_path.exists() else "{}"
    try:
        hb = json.loads(hb) or {}
    except Exception:
        hb = {}
    now = now or time.time()
    rows = []
    for name, e in sorted(reg.items()):
        if not isinstance(e, dict):
            continue
        avg = _avg_round_secs(e)
        hist = [s for s in (e.get("round_seconds_hist") or [])
                if isinstance(s, (int, float))]
        in_flight = None
        if hb.get("state") == "round-started" and hb.get("project") == name \
                and _pid_alive(hb.get("pid")):
            st = (e.get("current_angle") or {}).get("started")
            if isinstance(st, (int, float)) and st > 0:
                in_flight = max(0, int(now - st))
        rows.append({"name": name,
                     "rounds": int(e.get("rounds") or 0),
                     "avg_secs": avg,
                     "n": len(hist),
                     "last_secs": e.get("last_round_seconds"),
                     "in_flight_secs": in_flight})
    return rows


def cmd_status(a) -> int:
    cfg = load()
    live = _liveness(cfg)
    # --json must emit PURE JSON: the plugin parses this output and the
    # human-readable preamble made it un-parseable. Keep it machine-only.
    # It carries the SAME computed liveness as the text path (see _liveness).
    if getattr(a, "json", False):
        out = dict(cfg)
        out["liveness"] = live
        print(json.dumps(out, indent=2))
        return 0
    on = live["running"]
    print(f"KARPATHY LOOP: {'RUNNING' if on else 'PAUSED'}")
    if not on and cfg.get("paused_reason"):
        print(f"  reason      : {cfg['paused_reason']}")
    if not on and cfg.get("paused_at"):
        print(f"  paused at   : {cfg['paused_at']}")
    print(f"  projects    : {', '.join(cfg['projects']) or '(none set)'}")
    print(f"  angles/visit: {cfg['angles_per_visit']}")
    print(f"  rounds done : {cfg['rounds_done']}")
    if cfg.get("max_rounds"):
        print(f"  max rounds  : {cfg['max_rounds']} "
              f"({max(0, cfg['max_rounds'] - cfg['rounds_done'])} left)")
    if cfg.get("max_hours"):
        print(f"  max hours   : {cfg['max_hours']}")
    print(f"  push to GH  : {'yes' if cfg.get('pushed_to_github') else 'no'}")
    print(f"  implementer : {cfg.get('implementer')}")
    print(f"  reviewer    : {cfg.get('reviewer')}")
    if cfg.get("implementer") == cfg.get("reviewer"):
        print("  WARNING: implementer and reviewer are the SAME model "
              "(same blind spot twice)")
    if cfg.get("started_at"):
        print(f"  started at  : {cfg['started_at']}")

    # ── LIVENESS: intent vs reality ────────────────────────────────────────
    # Computed once by _liveness() and shared with the --json path, so the widget
    # and the terminal can never disagree about whether the loop is healthy.
    if live.get("heartbeat_state"):
        _alive_tag = ""
        if live.get("heartbeat_pid_alive") is False:
            _alive_tag = "  [process GONE - stale]"
        elif live.get("heartbeat_pid_alive") is True:
            _alive_tag = "  [alive]"
        print(f"  runner      : {live['heartbeat_state']}"
              + (f" {live['heartbeat_project']}" if live.get("heartbeat_project") else "")
              + (f" (pid {live['heartbeat_pid']})" if live.get("heartbeat_pid") else "")
              + _alive_tag)
    if live.get("heartbeat_age_min") is not None:
        print(f"  heartbeat   : {live['heartbeat_age_min']:.0f} min ago")
    if live.get("last_round_age_min") is not None:
        print(f"  last round  : {live['last_round_age_min']:.0f} min ago")

    # Per-repo round timing (the operator, 2026-10-01): avg runtime per repo and
    # a live timer for the round in flight.
    _rows = _timing_rows()
    if _rows:
        print("  per-repo rounds:")
        for _r in _rows:
            _avg = ("avg %s (n%d)" % (_fmt_mmss(_r["avg_secs"]), _r["n"])
                    if _r["avg_secs"] is not None else "avg -")
            _last = ("last %s" % _fmt_mmss(_r["last_secs"])
                     if _r["last_secs"] is not None else "last -")
            _run = ("IN FLIGHT %s" % _fmt_mmss(_r["in_flight_secs"])
                    if _r["in_flight_secs"] is not None else "")
            _rno = _r["rounds"] + 1 if _r["in_flight_secs"] is not None \
                else _r["rounds"]
            print("    %-24s r%-4s %-14s %-12s %s"
                  % (_r["name"], _rno, _avg, _last, _run))

    if live.get("wedged"):
        print(f"  WEDGED      : flag says RUNNING but {live['wedge_why']} "
              f"({live.get('last_round_age_min') or 0:.0f} min)")
        print("                fix: loopctl.py stop && loopctl.py start")
    elif not on and live.get("heartbeat_stale_on_paused"):
        # PAUSED is legitimate, but a stale heartbeat left over from a killed
        # runner must not look like a round still in flight (2026-09-28: this
        # showed "round-started project-a (pid 48600)" 49 min after
        # that PID had died).
        print("  note        : heartbeat is stale/gone from a previous run; "
              "no round is in flight")
    return 0


def cmd_start(a) -> int:
    cfg = load()
    if not cfg["projects"] and not a.projects:
        print("REFUSING to start: no projects in rotation.")
        print("Set them first:  python loopctl.py config --projects a,b,c")
        return 2
    if a.projects:
        cfg["projects"] = [p.strip() for p in a.projects.split(",") if p.strip()]
    if cfg.get("implementer") == cfg.get("reviewer"):
        print("REFUSING to start: implementer and reviewer are the same model. "
              "Set one of them differently first.")
        return 2
    cfg["running"] = True
    cfg["paused_reason"] = None
    cfg["paused_at"] = None
    cfg["started_at"] = cfg.get("started_at") or _now()
    save(cfg)
    _sync_rotation(cfg)
    # Start = work starts NOW. Spawn the continuous runner if not already alive
    # (the operator, 2026-09-23: "It's either running or it's not running.")
    import subprocess
    try:
        import karpathy_runner
        if karpathy_runner._acquire_single_flight():
            karpathy_runner.PID_FILE.unlink(missing_ok=True)  # hand the lock to the child
            do_spawn = True
        else:
            do_spawn = False
        if do_spawn:
            subprocess.Popen(
                [r"<LOCALAPPDATA>\..\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe",
                 r"C:\CODING\project-improver\karpathy_runner.py"],
                cwd=r"C:\CODING\project-improver",
                creationflags=0x00000008,  # DETACHED_PROCESS
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("runner spawned -- first round starts immediately")
        else:
            print("Loop is armed and the runner is ALREADY working -- no second runner started. Check the Thread column: a round should be in flight.")
    except Exception as e:
        print("runner spawn FAILED (%s) -- cron watchdog will retry" % e)
    print(f"STARTED. {len(cfg['projects'])} project(s), "
          f"{cfg['angles_per_visit']} angle(s) per visit.")
    for p in cfg["projects"]:
        print(f"  - {p}")
    return 0


def _process_lines_csv() -> list:
    """python.exe PID+CommandLine lines (wmic first, PowerShell fallback).

    T3-8 / F3.10: wmic is being removed from newer Win11 images. When wmic
    is absent or fails, the SAME shape is emitted from PowerShell
    (CommandLine FIRST, so the last CSV field is still the ProcessId).
    """
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=60, errors="replace",
            creationflags=0x08000000)
        if r.returncode == 0 and (r.stdout or "").strip():
            return (r.stdout or "").splitlines()
    except Exception:
        pass
    try:
        ps = ('Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" '
              "| Select-Object CommandLine,ProcessId "
              "| ConvertTo-Csv -NoTypeInformation")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=90,
                           errors="replace", creationflags=0x08000000)
        if r.returncode == 0:
            return (r.stdout or "").splitlines()
    except Exception:
        pass
    return []


# F3.9 / T2-8 (2026-09-30): the old kill matched ANY python whose command line
# mentioned --resume + hermes_cli.main -- pause --now could kill the USER's own
# headless hermes session. The kill list is now scoped to the session ids the
# loop owns (threads.json: session_id, tip, and every chain id).
_RESUME_RE = re.compile(r'--resume(?:=|\s+)([^\s",]+)')


def _known_session_ids() -> set:
    """Session ids this loop owns, read from threads.json. Never raises."""
    try:
        import threads as _t
        reg = _t.load()
    except Exception:
        return set()
    sids = set()
    for e in (reg or {}).values():
        if not isinstance(e, dict):
            continue
        for k in ("session_id", "tip"):
            v = e.get(k)
            if isinstance(v, str) and v.strip():
                sids.add(v.strip())
        ch = e.get("chain")
        if isinstance(ch, list):
            sids.update(str(x).strip() for x in ch if str(x).strip())
    return sids


def _cmd_field_start(line: str) -> int:
    """Character index where the CommandLine FIELD begins.

    wmic/PS CSV rows look like node,Node,CommandLine,ProcessId with the
    command wrapped in quotes when it contains commas. The command starts
    right after the first quote (or after the last header comma when the
    field is unquoted)."""
    q = line.find(chr(34))
    if q == -1:
        # unquoted command field: it begins after the LAST comma that
        # precedes the numeric PID field; be conservative and use the first
        # comma after the node column (rows are node,<maybe>,cmd,pid).
        first = line.find(",")
        return first + 1 if first != -1 else 0
    return q + 1


def _csv_cmd_and_pid(line: str):
    """Decode one CSV process row -> (command_text, pid_str).

    F-B2 (2026-10-02): PowerShell ConvertTo-Csv DOUBLES inner quotes ('""'),
    so quote-parity checks on the RAW row misjudge top-level positions (each
    "" pair reads as two quotes and keeps parity even), and the PID arrives
    quoted. Parsing with real CSV rules fixes both shapes at once: wmic rows
    decode to themselves; PS rows undouble.
    """
    try:
        import csv as _csv
        import io as _io
        row = next(_csv.reader([line]))
    except Exception:
        return None, None
    if len(row) < 2:
        return None, None
    pid = str(row[-1]).strip()
    cmd = row[-2] if len(row) >= 2 else ""
    return cmd, pid


def _outside_quotes(line: str, pos: int) -> bool:
    """True when `pos` sits at the command's TOP LEVEL (not inside a
    quoted -z payload). Parity is measured from the command field start:
    even = top level; odd = inside a quoted span. (F5 finding D: an
    unrelated process whose -z ARGUMENT TEXT quotes the loop child's
    command shape reproduced an innocent-PID kill.)"""
    start = _cmd_field_start(line)
    return line.count(chr(34), start, pos) % 2 == 0


def _pids_for_known_sessions(lines: list, known: set) -> list:
    """PIDs of hermes_cli.main children resumed onto a KNOWN session id.

    F5 review hardening (2026-09-30): the -m hermes_cli.main invocation AND
    the --resume <known-sid> flag must BOTH sit at the command's top level
    (outside any quoted payload); the real child has both, a quoted prompt
    fragment does not.
    """
    pids = []
    for line in lines:
        cmd, pid = _csv_cmd_and_pid(line)
        if not cmd or not pid:
            continue
        if "--resume" not in cmd:
            continue
        # hermes-module guard (restored F-B3): the tail anchor alone would
        # match ANY tool that ends with `--resume <sid>`; require the loop's
        # own launcher shape too.
        if not re.search(r"-m\s+hermes_cli\.main", cmd):
            continue
        # F-B3 (2026-10-02): quote-parity CANNOT decide "is --resume at top
        # level" — list2cmdline escapes payload quotes as \", which flips
        # parity arbitrarily. The robust anchor: OUR launcher always puts
        # `--resume <sid>` as the FINAL argument, so match the tail. A payload
        # that merely CONTAINS "--resume x" never matches (it is not at the
        # tail); the known-sid set then decides kill-or-spare.
        m = re.search(r'--resume\s+([\w-]+)\s*$', cmd)
        if not m:
            # A quoted tail token means the --resume sat inside the -z payload
            # (our launcher emits the sid bare [\w-]+) — not ours.
            continue
        if m.group(1) in known:
            try:
                pids.append(int(pid))
            except Exception:
                pass
    return pids


def _kill_inflight_round() -> int:
    """
    HARD-STOP: kill the loop's OWN in-flight hermes round process tree.

    the operator, 2026-09-23: "It's either running or it's not running." Waiting ~40 min
    for an in-flight round after Pause reads as 'pause is broken'. The work in a
    round lives in the round's disposable worktree (F3), so killing mid-round
    loses nothing -- containment deletes the worktree at the next sweep.
    F3.9: only children resumed onto a threads.json session id are killed; an
    unrelated `python --resume <other-session>` must NEVER die from our pause.
    Returns the number of processes killed.
    """
    known = _known_session_ids()
    if not known:
        return 0
    pids = _pids_for_known_sessions(_process_lines_csv(), known)
    killed = 0
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=60, creationflags=0x08000000)
            killed += 1
        except Exception:
            pass
    return killed


def cmd_pause(a) -> int:
    cfg = load()
    cfg["running"] = False
    cfg["paused_reason"] = a.reason or "paused by user"
    cfg["paused_at"] = _now()
    # drain semantics (the operator, 2026-09-24: "we should let it finish always"): pause stops NEW
    # rounds; the in-flight round runs to its commit/checkpoint. --now keeps the old hard-stop
    # for the rare case something must die immediately.
    if getattr(a, "now", False):
        killed = _kill_inflight_round()
    else:
        killed = 0
    # ── STOPPING THE RUNNER: --now HARD-KILLS, DRAIN LETS IT EXIT ─────────
    # BUG FIXED 2026-09-28: this used to kill karpathy_runner.py on BOTH paths,
    # including drain mode -- and then print "any in-flight round finishes and
    # checkpoints as usual". But the RUNNER is the process that checkpoints
    # (karpathy_runner.run_round -> checkpoint()), tags, pushes, and updates
    # threads.json. Killing it mid-round means the orphaned child finishes and
    # NOTHING records it: no tag, no push, no round count, no evidence -- while
    # status shows "FINISHING". That is the mechanism behind the 2026-09-28
    # "paused but a round still says in-flight, and rounds never advance" report.
    #
    # The runner's main loop already re-reads `running` from disk before each
    # round and exits on its own, so in drain mode the correct action is to set
    # the flag (done above, via save() below) and let it walk away cleanly after
    # the current round lands. Only --now kills processes.
    runner_killed = 0
    if getattr(a, "now", False):
        # Kill by COMMAND-LINE MATCH, not pid file: the venv python is a
        # trampoline (parent dies, real worker becomes an orphan that /T misses).
        try:   # F3.10: same PowerShell fallback as the round kill
            def _runner_at_top_level(line: str) -> bool:
                # Review fix L6 (2026-10-01): the same quote-aware rule as
                # the round-kill filter (F5 finding D) -- a process whose
                # ARGUMENT TEXT quotes the runner path must not be killed.
                # F-B2 (2026-10-02): decode the CSV row FIRST (PowerShell
                # doubles inner quotes), then measure parity on the decoded
                # command text. On the raw row the doubled quotes kept parity
                # even inside quoted payloads, so BOTH failure directions were
                # possible: the real runner unmatched, and an argument-text
                # mention matched.
                cmd2, _pid2 = _csv_cmd_and_pid(line)
                if not cmd2:
                    return False
                # F-B3: the runner is identified by argv[0] ENDING in
                # karpathy_runner.py — an argument-text mention (a -z payload)
                # is never argv[0], so parity heuristics are not needed at all.
                first = cmd2.split()[0] if cmd2.split() else ""
                return first.rstrip('"').lower().endswith("karpathy_runner.py")

            for line in _process_lines_csv():
                if _runner_at_top_level(line):
                    try:
                        rpid = int(line.rstrip().rstrip(",").split(",")[-1].strip().strip('"'))
                    except Exception:
                        continue
                    subprocess.run(["taskkill", "/F", "/PID", str(rpid)],
                                   capture_output=True, text=True, timeout=60, creationflags=0x08000000)
                    runner_killed += 1
        except Exception:
            pass
        try:
            import karpathy_runner
            karpathy_runner.PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    # PERSIST THE PAUSE. Without this save the flag only ever lived in memory:
    # the command printed "PAUSED" and the next runner tick read running=True from
    # disk and started another round -- exactly the "I paused it but it kept
    # rotating through all the repos" report. Save before announcing success.
    save(cfg)
    _sync_rotation(cfg)
    if getattr(a, "now", False):
        if killed:
            print(f"PAUSED ({cfg['paused_reason']}). In-flight round KILLED. "
                  f"Runner stopped ({runner_killed} runner proc).")
        else:
            print(f"PAUSED ({cfg['paused_reason']}). Nothing was in flight. "
                  f"Runner stopped ({runner_killed} runner proc).")
    else:
        print("PAUSED (%s). No new rounds will start." % cfg["paused_reason"])
        print("  The runner is LEFT ALIVE so it can finish the in-flight round: "
              "it checkpoints, tags and pushes that round, then exits by itself.")
        print("  Watch it with:  loopctl.py status     (heartbeat goes idle, "
              "then the runner disappears)")
        print("  Kill it now instead:  loopctl.py pause --now")
    return 0


def cmd_stop(a) -> int:
    cfg = load()
    cfg["running"] = False
    cfg["paused_reason"] = a.reason or "stopped by user"
    cfg["paused_at"] = _now()
    cfg["started_at"] = None  # a stop ends the run; start is a fresh run
    save(cfg)
    _sync_rotation(cfg)
    print("STOPPED. No new rounds. Running card (if any) is left in place.")
    if a.reclaim:
        print("(--reclaim requested: run  improver.py reclaim  to release the card)")
    return 0


def cmd_config(a) -> int:
    cfg = load()
    changed = []
    if a.projects is not None:
        cfg["projects"] = [p.strip() for p in a.projects.split(",") if p.strip()]
        changed.append(f"projects={cfg['projects']}")
    if a.angles_per_visit is not None:
        if a.angles_per_visit < 1:
            print("angles_per_visit must be >= 1")
            return 2
        cfg["angles_per_visit"] = a.angles_per_visit
        changed.append(f"angles_per_visit={a.angles_per_visit}")
    if a.max_rounds is not None:
        cfg["max_rounds"] = a.max_rounds
        changed.append(f"max_rounds={a.max_rounds}")
    if a.max_hours is not None:
        cfg["max_hours"] = a.max_hours
        changed.append(f"max_hours={a.max_hours}")
    if a.push_github is not None:
        cfg["pushed_to_github"] = a.push_github
        changed.append(f"pushed_to_github={a.push_github}")
    # Validate the FINAL pair, not each arg against the other's OLD value.
    # Checking mid-update made a legitimate SWAP impossible: setting
    # implementer=DS while reviewer was still DS tripped the guard, even though
    # the very next arg moved reviewer to GLM. Apply both, then check.
    if a.implementer is not None:
        cfg["implementer"] = a.implementer
        changed.append(f"implementer={a.implementer}")
    if a.reviewer is not None:
        cfg["reviewer"] = a.reviewer
        changed.append(f"reviewer={a.reviewer}")
    if cfg.get("implementer") and cfg.get("implementer") == cfg.get("reviewer"):
        print("REFUSING: implementer and reviewer are the SAME model "
              "(a model cannot review its own work).")
        return 2
    if a.sweep_minutes is not None:
        cfg["sweep_minutes"] = a.sweep_minutes
        changed.append(f"sweep_minutes={a.sweep_minutes}")
    save(cfg)
    _sync_rotation(cfg)
    print("updated: " + (", ".join(changed) if changed else "(nothing)"))
    return 0


def cmd_should_run(a) -> int:
    """Exit 0 = go. Exit 1 = do not start a round (and why)."""
    cfg = load()
    if not cfg["running"]:
        print(f"NO: paused ({cfg.get('paused_reason') or 'not started'})")
        return 1
    if not cfg["projects"]:
        print("NO: no projects in rotation")
        return 1
    if cfg.get("max_rounds") and cfg["rounds_done"] >= cfg["max_rounds"]:
        print(f"NO: max_rounds reached ({cfg['rounds_done']}/{cfg['max_rounds']})")
        if a.autopause:
            cfg["running"] = False
            cfg["paused_reason"] = "max_rounds reached"
            cfg["paused_at"] = _now()
            save(cfg)
            print("  -> auto-paused")
        return 1
    if cfg.get("max_hours") and cfg.get("started_at"):
        try:
            started = datetime.fromisoformat(cfg["started_at"])
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            hrs = (datetime.now(timezone.utc) - started).total_seconds() / 3600
            if hrs >= cfg["max_hours"]:
                print(f"NO: max_hours reached ({hrs:.1f}/{cfg['max_hours']})")
                if a.autopause:
                    cfg["running"] = False
                    cfg["paused_reason"] = "max_hours reached"
                    cfg["paused_at"] = _now()
                    save(cfg)
                    print("  -> auto-paused")
                return 1
        except Exception:
            pass
    print(f"YES ({len(cfg['projects'])} project(s), "
          f"{cfg['angles_per_visit']} angle(s)/visit)")
    return 0


def bump() -> int:
    """Record one completed (ACCEPTED) round. Returns the new rounds_done.

    F3.8 / T2-4: this existed only as a CLI verb with ZERO callers, so
    rounds_done never moved and max_rounds/autopause could never trip. The
    runner now calls this on every accepted round. Raises on I/O failure --
    the caller owns the policy (the runner logs and continues).
    """
    cfg = load()
    cfg["rounds_done"] = int(cfg.get("rounds_done") or 0) + 1
    save(cfg)
    return cfg["rounds_done"]


def cmd_show_campaign(a) -> int:
    # B5 (2026-09-30): READ-ONLY observability for the surfaces scheduler
    # (design 2026-09-28_surfaces.md 6.2: loopctl never WRITES scope --
    # entry["campaign"] in the threads registry is the only live home).
    import json as _json
    import threads as _threads
    reg = _threads.load()
    import scope as _scope
    rows = []
    for name in sorted(reg.keys()):
        e = reg.get(name) or {}
        if not isinstance(e, dict):
            continue
        camp = e.get("campaign") if isinstance(e.get("campaign"), dict) else None
        q = e.get("scope_question") if isinstance(e.get("scope_question"), dict) else None
        try:
            holds = int(e.get("campaign_holds") or 0)   # F-N: corrupt 'x' must not crash the operator command
        except (TypeError, ValueError):
            holds = 0
        if camp is None and q is None and holds == 0:
            continue
        rows.append("%s: %s | holds=%d%s"
                    % (name,
                       _scope.summary_line(camp),
                       holds,
                       (" | OPEN QUESTION r%s: %s"
                        % (q.get("round"), (q.get("note") or "")[:80])) if q else ""))
    if not rows:
        print("no campaign / hold / scope-question state on any project")
    else:
        for r in rows:
            print(r)
    return 0


def cmd_bump(a) -> int:
    """Record that one round completed (called by the rotator)."""
    n = bump()
    print(f"rounds_done={n}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Karpathy Loop on/off switch")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("start"); s.add_argument("--projects"); s.set_defaults(fn=cmd_start)
    s = sub.add_parser("pause")
    s.add_argument("--now", action="store_true",
                   help="hard-stop the in-flight round instead of letting it finish")
    s.add_argument("--reason"); s.set_defaults(fn=cmd_pause)
    s = sub.add_parser("stop"); s.add_argument("--reason"); s.add_argument("--reclaim", action="store_true"); s.set_defaults(fn=cmd_stop)
    s = sub.add_parser("config")
    s.add_argument("--projects"); s.add_argument("--angles-per-visit", type=int, dest="angles_per_visit")
    s.add_argument("--max-rounds", type=int, dest="max_rounds"); s.add_argument("--max-hours", type=int, dest="max_hours")
    s.add_argument("--push-github", type=lambda v: v.lower() in ("1", "true", "yes", "on"), dest="push_github")
    s.add_argument("--implementer")
    s.add_argument("--reviewer")
    s.add_argument("--sweep-minutes", type=int, dest="sweep_minutes")
    s.set_defaults(fn=cmd_config)
    s = sub.add_parser("should-run"); s.add_argument("--autopause", action="store_true"); s.set_defaults(fn=cmd_should_run)
    s = sub.add_parser("bump"); s.set_defaults(fn=cmd_bump)
    s = sub.add_parser("show-campaign"); s.set_defaults(fn=cmd_show_campaign)  # read-only

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())