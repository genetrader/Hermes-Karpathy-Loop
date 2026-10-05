#!/usr/bin/env python3
"""
angle_pick.py -- weighted-random pull of an ANGLE from angles.yaml.

This is the piece that makes the loop non-tunneling: instead of walking a fixed
stage order, every round pulls a lens at random (but fairly) and applies it to
the project currently in rotation.

Rules implemented (all from angles.yaml `picker:`):
  * family_rotation  -- choose a FAMILY first, then an angle inside it, so a
                        long run does not collapse onto one family.
  * cooldown_rounds  -- do not reuse an angle used in the last N rounds
                        (across all projects), unless that would starve us.
  * retire_after_failures -- an angle that produces no accepted commit K times
                        in a row is retired.
  * avoid_when       -- a per-angle predicate; the caller passes repo facts
                        (file count, languages, has_tests ...) and angles whose
                        predicate says "not applicable" are filtered out.

Usage:
  python angle_pick.py pull --project project-a [--seed N] [--json]
  python angle_pick.py list [--family gameplay]
  python angle_pick.py stats
  python angle_pick.py mark --angle lying-test --project X --result accepted|failed
  python angle_pick.py note --angle lying-test --note "why it was skipped"
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ANGLES_YAML = ROOT / "angles.yaml"
STATE = ROOT / "state"
HISTORY = STATE / "angle_history.json"

# --------------------------------------------------------------------------
# tiny YAML loader (avoids a PyYAML dependency; our file is a known shape)
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


def _apply_overrides(data: dict) -> dict:
    """Overlay state/angle_overrides.yaml (the operator's widget edits) onto the base angles."""
    try:
        from angles_store import load_overrides  # local: angles_store imports this module
        ovr = load_overrides()
    except Exception:
        return data
    if not ovr:
        return data
    out = []
    for a in data.get("angles", []):
        b = dict(a)
        o = ovr.get(a.get("id"))
        if o:
            for k in ("lens", "look_for", "evidence", "avoid_when"):
                if o.get(k) is not None and o.get(k) != "":
                    b[k] = o[k]
        out.append(b)
    data["angles"] = out
    return data


def load_angles(path: Path = ANGLES_YAML, merged: bool = True) -> dict:
    """Parse angles.yaml into {'picker': {...}, 'angles': [ {...}, ... ]}."""
    picker: dict = {}
    angles: list[dict] = []
    cur: dict | None = None
    in_picker = False
    list_key: str | None = None

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        if indent == 0:
            in_picker = stripped.startswith("picker:")
            list_key = None
            if stripped.startswith("angles:"):
                cur = None
            continue

        if in_picker:
            if ":" in stripped:
                k, _, v = stripped.partition(":")
                picker[k.strip()] = _strip_scalar(v)
            continue

        # angle entries start with "- id:"
        if stripped.startswith("- id:"):
            cur = {"id": _strip_scalar(stripped.split(":", 1)[1]),
                   "look_for": []}
            angles.append(cur)
            list_key = None
            continue

        if cur is None:
            continue

        if stripped.startswith("- ") and list_key:
            cur.setdefault(list_key, []).append(_strip_scalar(stripped[2:]))
            continue

        if ":" in stripped:
            k, _, v = stripped.partition(":")
            k, v = k.strip(), v.strip()
            if v == "":
                list_key = k
                cur.setdefault(k, [])
            else:
                cur[k] = _strip_scalar(v)
                list_key = None

    out = {"picker": picker, "angles": angles}
    if merged:
        out = _apply_overrides(out)
    return out


# --------------------------------------------------------------------------
# history / retirement bookkeeping
# --------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_history() -> dict:
    if HISTORY.exists():
        try:
            return json.loads(HISTORY.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"rounds": [], "angles": {}, "notes": []}


def save_history(h: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(h, indent=2), encoding="utf-8")


def angle_stats(h: dict, angle_id: str) -> dict:
    return h.get("angles", {}).get(angle_id, {"used": 0, "accepted": 0,
                                              "failed": 0, "streak_fail": 0,
                                              "retired": False})


# --------------------------------------------------------------------------
# applicability -- turn avoid_when prose into a cheap check
# --------------------------------------------------------------------------
# We do not try to interpret free prose. Instead each angle id can be gated by
# a small explicit predicate over repo facts the caller supplies. Anything not
# listed here is always applicable (matching "avoid_when: None").
def _min_files(n):
    return lambda f: f.get("file_count", 0) >= n


def _is_code(f):
    return f.get("language") not in (None, "", "unknown")


DEFERRED_GATES = {
    "lying-test": lambda f: f.get("test_files", 0) >= 5,
    "swallowed-exception": _is_code,
    "off-by-one": _is_code,
    "race-condition": lambda f: not f.get("single_threaded", False),
    "resource-leak": _is_code,
    "unsafe-none": lambda f: f.get("language") in ("python", "javascript", "typescript", "ruby", "php") or True,
    "state-round-trip": lambda f: f.get("has_persistence", True),
    "network-failure": lambda f: f.get("has_network", True),
    "unbounded-growth": lambda f: f.get("is_library", False) is False,
    "restart-mid-write": lambda f: f.get("has_persistence", True),
    "self-ingestion": lambda f: f.get("has_structured_output", True),
    "collapse-duplication": _min_files(3),
    "inline-abstraction": _min_files(3),
    "dead-config": lambda f: f.get("has_config", True),
    "accidental-quadratic": _is_code,
    "unbounded-memory": lambda f: f.get("has_large_input", True),
    "slow-startup": lambda f: f.get("is_service", True),
    "log-every-refusal": lambda f: f.get("is_game", False),
    "dead-end-action": lambda f: f.get("is_game", False),
    "choice-that-doesnt-matter": lambda f: f.get("is_game", False),
    "untelegraphed-punishment": lambda f: f.get("is_game", False),
    "reward-pacing": lambda f: f.get("is_game", False),
    "world-inconsistency": lambda f: f.get("is_game", False),
    "inert-npc": lambda f: f.get("is_game", False),
    "unactionable-error": lambda f: f.get("has_ui", True),
    "too-many-steps": lambda f: f.get("has_ui", True),
    "invisible-state": lambda f: f.get("has_ui", True),
    "unreadable-text": lambda f: f.get("has_ui", True),
    "unsafe-destructive": lambda f: f.get("has_ui", True),
    "missing-architecture-note": lambda f: not f.get("has_architecture_doc", False),
    "dependency-audit": lambda f: f.get("has_dependency_manifest", True),
}


def is_applicable(angle: dict, facts: dict) -> bool:
    gate = DEFERRED_GATES.get(angle["id"])
    if gate is None:
        return True
    try:
        return bool(gate(facts))
    except Exception:
        return True  # never let a gate bug starve the picker


# --------------------------------------------------------------------------
# the pick
# --------------------------------------------------------------------------
# PROMPT LAYER (the operator, 2026-09-27 -- locked design).
#
# An angle alone is a lens, not a work order. Each angle now carries 4 PROMPTS
# (angle_prompts.yaml), and a round consumes ONE angle + ONE unused prompt.
#
# Cycle rules:
#   * PER-REPO cycle. Each repo walks its own pass over angle x prompt.
#   * CONSUME ON ATTEMPT. A prompt is spent when issued, including when the round
#     comes back not-applicable -- a dead prompt must not be re-issued forever or
#     the cycle never closes.
#   * ONE USABLE ROUND PER REPO. Unusable prompts are skipped INSIDE the same
#     round (advance until one applies). A skip never burns a round.
#   * Full coverage wipes the slate and starts a fresh cycle.
def _prompt_layer():
    """Import the prompt table lazily so this module stays usable without it."""
    try:
        import angle_prompts
        return angle_prompts
    except Exception:
        return None


def _next_unused_prompt(project: str, angle_ids: list[str]) -> dict | None:
    """Advance through this repo's cycle and return the first unused prompt.

    Walks the whole prompt table for this repo, preferring angles in `angle_ids`
    order and then the remainder. The pool passed in is NARROWED (family rotation,
    cooldown, recency), so walking only it stalls the cycle as soon as that subset
    is spent -- the repo then looks "finished" at ~20% coverage and never rolls
    over. Ordering preference is still honoured; exclusion is not.

    Skips only ALREADY-SPENT prompts. Whether a prompt APPLIES is decided by the
    agent running the round, via the prompt's own applies_when / not_applicable
    text: a not-applicable verdict consumes the prompt and the next round moves
    on, which is how "1,2,3 not usable, take the 4th" plays out without any of
    them wasting a round.

    Returns None only when every prompt id has been spent (true cycle end).
    """
    PN = _prompt_layer()
    if PN is None:
        return None
    rec = PN.cycle_for(project)
    used = set(rec.get("used", []))
    table = PN.load_prompts()

    # Preferred angles first, then everything else -- no angle is ever excluded.
    seen_order, order = set(), []
    for aid in list(angle_ids) + sorted(table):
        if aid not in seen_order:
            seen_order.add(aid)
            order.append(aid)

    for aid in order:
        for p in table.get(aid, []):
            if p["id"] not in used:
                return p
    return None


def pick(project: str, facts: dict | None = None, seed: int | None = None) -> dict:
    data = load_angles()
    angles = data["angles"]
    cfg = data["picker"]
    hist = load_history()
    facts = facts or {}

    rng = random.Random(seed) if seed is not None else random.Random()

    # --- filter ---------------------------------------------------------
    def usable(a: dict) -> bool:
        st = angle_stats(hist, a["id"])
        if st.get("retired"):
            return False
        retire_k = int(cfg.get("retire_after_failures", 0) or 0)
        if retire_k and st.get("streak_fail", 0) >= retire_k:
            return False
        return is_applicable(a, facts)

    pool = [a for a in angles if usable(a)]
    if not pool:
        # everything retired/inapplicable -- fall back to all, so we never
        # deadlock the loop on an empty pool
        pool = list(angles)

    # --- cooldown: drop angles used in the last N rounds ----------------
    cooldown = int(cfg.get("cooldown_rounds", 0) or 0)
    if cooldown:
        recent = {r.get("angle") for r in hist.get("rounds", [])[-cooldown:]}
        cooled = [a for a in pool if a["id"] not in recent]
        if cooled:
            pool = cooled
        # else: the pool is smaller than the cooldown window -> allow repeats

    # --- family rotation ------------------------------------------------
    if cfg.get("family_rotation", True):
        fam_hist = {}
        for r in hist.get("rounds", []):
            fam_hist[r.get("family", "?")] = fam_hist.get(r.get("family", "?"), 0) + 1
        fams = sorted({a["family"] for a in pool})
        # weight toward the least-used families
        least = min(fam_hist.get(f, 0) for f in fams)
        candidates = [f for f in fams if fam_hist.get(f, 0) == least]
        fam = rng.choice(candidates)
        pool = [a for a in pool if a["family"] == fam] or pool

    # --- prompt layer: prefer an angle that still has an unspent prompt -----
    # Shuffling the angle pool keeps the choice random while the prompt-layer
    # scan guarantees forward progress through the cycle.
    shuffled = list(pool)
    rng.shuffle(shuffled)
    ordered_ids = [a["id"] for a in shuffled]

    prompt = _next_unused_prompt(project, ordered_ids)
    chosen = None
    if prompt is not None:
        # Look up the owning angle in the FULL angle list, never in `pool`.
        # `pool` is narrowed by family-rotation and cooldown, so the angle that
        # owns the next unspent prompt may not be in it. Searching `pool` raised
        # StopIteration, and because that landed inside the try-less lookup it was
        # silently reinterpreted as "cycle exhausted" -- pick() then returned no
        # prompt_id on a cycle that was only ~20% spent. Search `angles`.
        _aid_want = _angle_for_prompt(prompt)
        chosen = next((a for a in angles if a["id"] == _aid_want), None)
        if chosen is None:
            # A prompt exists for an angle that is not in angles.yaml. Do not
            # pretend the cycle is over; fall through to the plain pick and log it.
            prompt = None

    if chosen is None:
        # Genuinely nothing left: either the cycle is fully spent, or a prompt
        # exists for an angle that is not in angles.yaml. Wipe the slate ONLY when
        # the table is truly exhausted, so a mismatch cannot reset a live cycle.
        PN = _prompt_layer()
        if PN is not None:
            spent = set(PN.cycle_for(project).get("used", []))
            if len(spent) >= len(PN.all_prompt_ids()):
                PN.mark_used(project, "__cycle_complete__", "cycle-complete")
        chosen = rng.choice(pool)

    out = {
        "angle": chosen["id"],
        "family": chosen["family"],
        "lens": chosen.get("lens", ""),
        "evidence_required": chosen.get("evidence", ""),
        "look_for": chosen.get("look_for", []),
        "project": project,
        "seed": seed,
        "picked_at": _now(),
        "pool_size": len(pool),
        "checkpoint_tag": f"kp/{project}/<round>/{chosen['id']}",
    }
    if prompt:
        out["prompt_id"] = prompt["id"]
        out["prompt"] = {
            "applies_when": prompt.get("applies_when", ""),
            "hunt": prompt.get("hunt", ""),
            "evidence": prompt.get("evidence", ""),
            "not_applicable": prompt.get("not_applicable", ""),
        }
    return out


def _angle_for_prompt(prompt: dict) -> str:
    """Recover the angle id from a prompt id ('lying-test.1' -> 'lying-test')."""
    pid = prompt.get("id") or ""
    return pid.rsplit(".", 1)[0] if "." in pid else pid


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------
def cmd_pull(a) -> int:
    facts = json.loads(a.facts) if a.facts else {}
    res = pick(a.project, facts, a.seed)
    if a.json:
        print(json.dumps(res, indent=2))
    else:
        print(f"ANGLE      {res['angle']}  ({res['family']})")
        print(f"LENS       {res['lens']}")
        print(f"PROJECT    {res['project']}")
        print(f"TAG        {res['checkpoint_tag']}")
        print(f"EVIDENCE   {res['evidence_required']}")
        if res["look_for"]:
            print("LOOK FOR")
            for it in res["look_for"]:
                print(f"  - {it}")
    return 0


def cmd_list(a) -> int:
    data = load_angles()
    angles = data["angles"]
    if a.family:
        angles = [x for x in angles if x["family"] == a.family]
    for x in angles:
        print(f"{x['id']:32} {x['family']:16} {x.get('lens','')}")
    print(f"\n{len(angles)} angle(s)")
    return 0


def cmd_stats(a) -> int:
    data = load_angles()
    hist = load_history()
    print(f"{'angle':32} {'family':16} {'used':>5} {'ok':>4} {'fail':>5} {'streak':>7}")
    print("-" * 76)
    for x in data["angles"]:
        st = angle_stats(hist, x["id"])
        flag = " RETIRED" if st.get("retired") else ""
        print(f"{x['id']:32} {x['family']:16} {st.get('used',0):>5} "
              f"{st.get('accepted',0):>4} {st.get('failed',0):>5} "
              f"{st.get('streak_fail',0):>7}{flag}")
    print(f"\nrounds recorded: {len(hist.get('rounds', []))}")
    return 0


def cmd_mark(a) -> int:
    hist = load_history()
    st = hist.setdefault("angles", {}).setdefault(
        a.angle, {"used": 0, "accepted": 0, "failed": 0, "streak_fail": 0, "retired": False})
    st["used"] = st.get("used", 0) + 1
    if a.result == "accepted":
        st["accepted"] = st.get("accepted", 0) + 1
        st["streak_fail"] = 0
    else:
        st["failed"] = st.get("failed", 0) + 1
        st["streak_fail"] = st.get("streak_fail", 0) + 1
        cfg = load_angles()["picker"]
        k = int(cfg.get("retire_after_failures", 0) or 0)
        if k and st["streak_fail"] >= k:
            st["retired"] = True
            print(f"NOTE: {a.angle} retired after {st['streak_fail']} failures")
    hist.setdefault("rounds", []).append({
        "angle": a.angle, "project": a.project, "family": a.family or "?",
        "result": a.result, "at": _now()})
    save_history(hist)
    print(f"marked {a.angle} -> {a.result} (used={st['used']} "
          f"ok={st['accepted']} fail={st['failed']})")
    return 0


def cmd_note(a) -> int:
    hist = load_history()
    hist.setdefault("notes", []).append({"angle": a.angle, "note": a.note, "at": _now()})
    save_history(hist)
    print("noted")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="pull an improvement angle")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("pull", help="pull one angle")
    pp.add_argument("--project", required=True)
    pp.add_argument("--facts", help="JSON of repo facts for avoid_when gates")
    pp.add_argument("--seed", type=int)
    pp.add_argument("--json", action="store_true")
    pp.set_defaults(fn=cmd_pull)

    pl = sub.add_parser("list")
    pl.add_argument("--family")
    pl.set_defaults(fn=cmd_list)

    ps = sub.add_parser("stats")
    ps.set_defaults(fn=cmd_stats)

    pm = sub.add_parser("mark")
    pm.add_argument("--angle", required=True)
    pm.add_argument("--project", required=True)
    pm.add_argument("--result", choices=["accepted", "failed"], required=True)
    pm.add_argument("--family")
    pm.set_defaults(fn=cmd_mark)

    pn = sub.add_parser("note")
    pn.add_argument("--angle", required=True)
    pn.add_argument("--note", required=True)
    pn.set_defaults(fn=cmd_note)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())