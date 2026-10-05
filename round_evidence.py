#!/usr/bin/env python
"""
round_evidence.py -- Layer 2 of the Repo Brief / Round Evidence / See-It build
(PLAN-repo-brief-evidence-seeit.md, locked by the operator 2026-09-28).

Do NOT ask the model to describe its work -- DERIVE the round's facts:
the commit and the gate output ARE the evidence; prose is a caption on top.

Sources, all already available at zero model cost:
  git -C <path> show --stat --format=%h|%s HEAD  -> files/insertions/deletions/
                                                    commit/subject
  gate run at the end of the round               -> rc + output tail
  (the child's own output tail is captured separately by the runner in
   entry['last_output'] and logs/rounds/<repo>.rNNN.log -- reused, not
   duplicated here)

Robustness contract: a repo with NO commits, no remote, a detached HEAD, a
dirty tree, or git missing entirely must each return a PARTIAL dict -- never
raise. A missing value renders as missing/"unknown", never as success.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

# Belt AND braces, same recipe as the runner's checkpoint(): a headless loop
# must never be able to open a credential dialog (the 200s hang on a Windows
# credential-selector dialog). GIT_TERMINAL_PROMPT alone was NOT enough; pin
# the helper to an EMPTY value per-invocation and point askpass at a no-op.
_NO_PROMPT_ENV = dict(os.environ)
_NO_PROMPT_ENV.update({
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
_GIT_TIMEOUT = 60            # every subprocess call carries a timeout
_CREATE_NO_WINDOW = 0x08000000  # suppress console windows on Windows


def _git(workdir: str, *args: str) -> dict:
    """
    Run one read-only git command against workdir. Returns
    {"rc": int, "stdout": str, "stderr": str}; never raises.
    """
    try:
        r = subprocess.run(
            ["git", "-c", "credential.helper=", "-C", workdir, *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT,
            errors="replace", env=_NO_PROMPT_ENV,
            creationflags=_CREATE_NO_WINDOW)
        return {"rc": r.returncode, "stdout": r.stdout or "",
                "stderr": r.stderr or ""}
    except FileNotFoundError:
        return {"rc": -1, "stdout": "", "stderr": "git executable not found"}
    except subprocess.TimeoutExpired:
        return {"rc": -1, "stdout": "", "stderr": "git timed out"}
    except Exception as e:                              # noqa: BLE001
        return {"rc": -1, "stdout": "", "stderr": repr(e)}


def _int(m: re.Match | None) -> int | None:
    if not m:
        return None
    try:
        return int(m.group(1))
    except (IndexError, ValueError):
        return None


def collect(proj: dict, round_no: int) -> dict:
    """
    READ-ONLY. Derive one round's evidence facts from git.

    Returns {"round": N, "collected_at": epoch, "files", "insertions",
    "deletions", "commit", "subject", "dirty", "git_available", "git_error"}.
    Fields the repo cannot supply are None (renders "unknown") -- never
    fabricated, never raised.
    """
    workdir = (proj or {}).get("path") or ""
    ev: dict = {
        "round": round_no,
        "collected_at": time.time(),
        "files": None,
        "insertions": None,
        "deletions": None,
        "commit": None,
        "subject": None,
        "dirty": None,
        "git_available": True,
        "git_error": None,
    }
    if not workdir:
        ev["git_available"] = False
        ev["git_error"] = "no path in project config"
        return ev
    if not os.path.isdir(workdir):
        ev["git_available"] = False
        ev["git_error"] = "path does not exist: %s" % workdir
        return ev

    # -- is git itself even installed? --------------------------------------
    probe = _git(workdir, "rev-parse", "--is-inside-work-tree")
    if probe["rc"] == -1:
        # executable missing / timed out -- distinguish from "not a repo"
        ev["git_available"] = False
        ev["git_error"] = probe["stderr"]
        return ev
    if probe["rc"] != 0:
        # inside-work-tree rc!=0 = not a repo (or bare); git works but has
        # nothing to say about this path. Treat as available-but-empty.
        ev["git_error"] = (probe["stderr"] or "").strip().splitlines()[:1]
        ev["git_error"] = ev["git_error"][0] if ev["git_error"] else None
        return ev

    # -- HEAD: short hash + subject (a repo with no commits fails here) -----
    head = _git(workdir, "show", "--stat", "--format=%h|%s", "HEAD")
    if head["rc"] != 0:
        # No commits yet (unborn HEAD), detached oddities, etc. -- partial dict.
        ev["git_error"] = (head["stderr"] or "").strip().splitlines()[:1]
        ev["git_error"] = ev["git_error"][0] if ev["git_error"] else None
        return ev

    lines = (head["stdout"] or "").splitlines()
    if lines:
        h, _, s = lines[0].partition("|")
        ev["commit"] = h.strip() or None
        ev["subject"] = s.strip() or None
    # `git show --stat` footer looks like:
    #    3 files changed, 120 insertions(+), 44 deletions(+)
    m = re.search(r"(\d+)\s+files? changed", head["stdout"] or "")
    ev["files"] = _int(m)
    m = re.search(r"(\d+)\s+insertions?\(\+\)", head["stdout"] or "")
    ev["insertions"] = _int(m)
    m = re.search(r"(\d+)\s+deletions?\(-\)", head["stdout"] or "")
    ev["deletions"] = _int(m)

    # -- dirty tree flag (does not affect anything; purely informational) ---
    st = _git(workdir, "status", "--porcelain")
    if st["rc"] == 0:
        ev["dirty"] = bool((st["stdout"] or "").strip())

    return ev


def attach(entry: dict, ev: dict) -> None:
    """
    Merge collected facts into the registry row's NEWEST angle_history entry
    (entry["angle_history"][0]). Only non-None facts are written, so an
    unknown never overwrites anything, never reads as success. Never raises.
    """
    if not entry or not isinstance(ev, dict):
        return
    hist = entry.get("angle_history") or []
    if not hist:
        return
    row = hist[0] if isinstance(hist[0], dict) else {}
    for key in ("files", "insertions", "deletions", "commit", "subject",
                "gate", "surface"):
        val = ev.get(key)
        if val is not None:
            row[key] = val
    # housekeeping keys for debugging (not part of the plan's render keys)
    if ev.get("dirty") is not None:
        row["dirty"] = ev["dirty"]
    if ev.get("git_error"):
        row["git_error"] = ev["git_error"]
    hist[0] = row
    entry["angle_history"] = hist


def show_rows(name: str, n: int = 10) -> list:
    """Read-only: the last N round-evidence rows for a repo, newest first."""
    reg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "state", "threads.json")
    try:
        with open(reg_path, encoding="utf-8") as f:
            reg = json.load(f)
    except Exception:
        return []
    entry = reg.get(name) or {}
    rows = entry.get("angle_history") or []
    return rows[:max(0, n)]


def _fmt_row(row: dict) -> str:
    def g(k, dash="--"):
        v = row.get(k)
        return dash if v is None else v

    gate = row.get("gate")
    if isinstance(gate, dict):
        gate_s = "%s rc=%s detail=%s" % (
            gate.get("cmd") or "?", g("ok", "unknown") if isinstance(
                gate.get("ok"), bool) else "unknown",
            (gate.get("detail") or "")[:60])
    elif gate is None:
        gate_s = "unknown (not captured)"
    else:
        gate_s = str(gate)[:60]
    return ("r%-3s rc=%-4s commit=%-8s files=%-4s +%-4s/-%-4s gate[%s] %s"
            % (g("round"), g("rc"), g("commit"), g("files"),
               g("insertions"), g("deletions"), gate_s,
               (g("subject") or "")[:70]))


def main() -> int:
    ap = argparse.ArgumentParser(description="round evidence viewer")
    ap.add_argument("--show", metavar="REPO",
                    help="print the last N round-evidence rows for REPO")
    ap.add_argument("--num", type=int, default=10)
    ap.add_argument("--collect", nargs=2, metavar=("YAML_PATH", "ROUND"),
                    help="collect facts for a repo path (debug helper)")
    args = ap.parse_args()

    if args.collect:
        proj = {"path": args.collect[0]}
        ev = collect(proj, int(args.collect[1]))
        print(json.dumps(ev, indent=2))
        return 0

    if not args.show:
        ap.error("--show REPO (or --collect PATH ROUND) is required")
        return 2
    rows = show_rows(args.show, args.num)
    if not rows:
        print("no angle_history rows for %r" % args.show)
        return 0
    for row in rows:
        print(_fmt_row(row if isinstance(row, dict) else {}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
