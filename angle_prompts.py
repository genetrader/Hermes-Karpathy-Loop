#!/usr/bin/env python3
"""
angle_prompts.py -- the PROMPT layer under each angle.

Why this exists
---------------
`angles.yaml` gives an angle a LENS (what to look at) and `look_for` bullets
(what to hunt). Those are detectors, not work orders: an 8-word bullet cannot
tell a model where to look in a Python service, a JS game, and a C daemon -- nor
what artifact to produce, nor when to declare "not applicable".

So each angle gets 4 PROMPTS. A prompt is a full, prescriptive work order:

  applies_when  -- the precondition, written so a model can check it in seconds
  hunt          -- what to look for, with cross-language tells
  evidence      -- the exact artifact the round must produce
  not_applicable-- the clean exit (consume the prompt, invent nothing)

Cycle semantics (Gene, 2026-09-27 -- locked):
  * PER-REPO cycle. Each repo walks its own pass over angle x prompt.
  * CONSUME ON ATTEMPT. A prompt is spent when it is issued, including when the
    round comes back not-applicable. A dead prompt must not be re-issued forever
    or the cycle never closes.
  * ONE USABLE ROUND PER REPO. Unusable prompts are skipped INSIDE the same
    round (advance, advance, ... until one applies). A skip never burns a round.
  * When every angle x prompt combination for a repo is spent, the slate wipes
    and a fresh cycle starts.

This module is DATA + LOOKUP only. The picker (`angle_pick.py`) owns selection;
these helpers expose the prompt table and the cycle math.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROMPTS_YAML = ROOT / "angle_prompts.yaml"
# Indent of a prompt's own keys ("      applies_when: |"); anything deeper is
# block-scalar body text and must never be parsed as a key.
KEY_INDENT = 6
STATE = ROOT / "state"
CYCLE_STATE = STATE / "prompt_cycles.json"


# --------------------------------------------------------------------------
# loader -- same tiny hand-rolled YAML reader angle_pick.py uses, because the
# project deliberately avoids a PyYAML dependency and the file is a known shape.
# --------------------------------------------------------------------------
def _strip_scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1]
    if v in ("true", "false"):
        return v == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def load_prompts(path: Path = PROMPTS_YAML) -> dict:
    """Parse angle_prompts.yaml -> {angle_id: [ {id, applies_when, hunt, evidence,
    not_applicable}, ... ]}.

    Shape:
        angles:
          lying-test:
            - id: lying-test.1
              applies_when: |
                <text>
              hunt: |
                <text>
              ...
    """
    out: dict[str, list[dict]] = {}
    if not path.exists():
        return out

    cur_angle: str | None = None
    cur: dict | None = None
    block_key: str | None = None
    block_lines: list[str] = []

    def _flush():
        nonlocal block_key, block_lines, cur
        if cur is not None and block_key:
            cur[block_key] = "\n".join(block_lines).strip()
        block_key, block_lines = None, []

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        # angle heading: two-space indent, "id:" and not a list item
        if indent == 2 and stripped.endswith(":") and not stripped.startswith("-"):
            _flush()
            if cur is not None:
                out.setdefault(cur_angle, []).append(cur)
                cur = None
            cur_angle = stripped[:-1].strip()
            out.setdefault(cur_angle, [])
            continue

        # prompt entry start: "- id: x"
        if stripped.startswith("- id:"):
            _flush()
            if cur is not None:
                out.setdefault(cur_angle, []).append(cur)
            cur = {"id": _strip_scalar(stripped.split(":", 1)[1])}
            continue

        if cur is None:
            continue

        # KEY vs CONTENT -- the crucial distinction.
        # Inside a prompt, keys sit at indent 6 ("      applies_when: |") and their
        # block-scalar bodies sit at indent 8+.  A body line can itself contain a
        # colon (e.g. "The repo reads ... more than once: a config file,"), so
        # partitioning on ":" alone mis-read that prose as a new key and blanked
        # the field. Only treat a line as a key when its indent is ≤ the entry
        # indent AND it carries an inline value or a block-scalar marker.
        if block_key and indent > KEY_INDENT:
            block_lines.append(stripped)
            continue

        if ":" in stripped:
            k, _, v = stripped.partition(":")
            k, v = k.strip(), v.strip()
            if v in ("|", ">"):
                _flush()
                block_key = k
                block_lines = []
                continue
            if v == "":
                _flush()
                cur[k] = []
                continue
            _flush()
            cur[k] = _strip_scalar(v)
            continue

        if stripped.startswith("- ") and block_key:
            block_lines.append(stripped[2:])
            continue

    _flush()
    if cur is not None:
        out.setdefault(cur_angle, []).append(cur)
    return {k: v for k, v in out.items() if v}


def prompts_for(angle_id: str) -> list[dict]:
    return load_prompts().get(angle_id, [])


# --------------------------------------------------------------------------
# cycle bookkeeping -- PER REPO
# --------------------------------------------------------------------------
def load_cycles() -> dict:
    if CYCLE_STATE.exists():
        try:
            return json.loads(CYCLE_STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cycles(d: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = CYCLE_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    os.replace(tmp, CYCLE_STATE)


def cycle_for(project: str, unused_only: bool = True) -> dict:
    """Return this repo's cycle record, creating it on first use.

    `used` is the set of spent prompt ids in the CURRENT cycle.
    `cycle` is the 1-based cycle number, for display.
    """
    d = load_cycles()
    rec = d.get(project)
    if not rec:
        rec = {"cycle": 1, "used": [], "skipped": [], "history": []}
        d[project] = rec
        save_cycles(d)
    rec.setdefault("used", [])
    rec.setdefault("skipped", [])
    rec.setdefault("history", [])
    return rec


def all_prompt_ids() -> list[str]:
    out = []
    for aid, items in sorted(load_prompts().items()):
        for p in items:
            out.append(p["id"])
    return out


def cycle_progress(project: str) -> dict:
    """Coverage of this repo's current cycle."""
    rec = cycle_for(project)
    total = len(all_prompt_ids())
    used = len(set(rec.get("used", [])))
    return {"project": project, "cycle": rec.get("cycle", 1),
            "used": used, "total": total,
            "remaining": max(0, total - used),
            "pct": round(100.0 * used / total, 1) if total else 0.0}


def mark_used(project: str, prompt_id: str, result: str = "issued") -> dict:
    """Spend a prompt. Returns the (possibly reset) cycle record.

    Reset rule: when every prompt id has been spent, the slate wipes and the
    cycle number increments -- so the repo starts a clean pass.
    """
    d = load_cycles()
    rec = d.setdefault(project, {"cycle": 1, "used": [], "skipped": [], "history": []})
    rec.setdefault("used", [])
    rec.setdefault("history", [])
    if prompt_id not in rec["used"]:
        rec["used"].append(prompt_id)
    rec["history"].append({"prompt": prompt_id, "result": result, "cycle": rec.get("cycle", 1)})

    total = len(all_prompt_ids())
    if total and len(set(rec["used"])) >= total:
        rec["cycle"] = int(rec.get("cycle", 1)) + 1
        rec["used"] = []
        rec["history"] = []
        rec["reset_at"] = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds")
    d[project] = rec
    save_cycles(d)
    return rec


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="angle prompt layer")
    ap.add_argument("cmd", choices=["list", "show", "progress", "mark", "count"])
    ap.add_argument("--angle")
    ap.add_argument("--project")
    ap.add_argument("--prompt")
    ap.add_argument("--result", default="issued")
    a = ap.parse_args()

    if a.cmd == "count":
        table = load_prompts()
        tot = sum(len(v) for v in table.values())
        print(f"{len(table)} angle(s), {tot} prompt(s)")
        for k in sorted(table):
            print(f"  {k:32} {len(table[k])}")
    elif a.cmd == "list":
        for aid, items in sorted(load_prompts().items()):
            print(f"{aid}:")
            for p in items:
                print(f"    {p['id']}")
    elif a.cmd == "show":
        for p in prompts_for(a.angle):
            print(f"=== {p['id']} ===")
            for k in ("applies_when", "hunt", "evidence", "not_applicable"):
                print(f"  {k}: {p.get(k,'')[:200]}")
            print()
    elif a.cmd == "progress":
        print(json.dumps(cycle_progress(a.project), indent=2))
    elif a.cmd == "mark":
        print(json.dumps(mark_used(a.project, a.prompt, a.result), indent=2))