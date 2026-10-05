#!/usr/bin/env python
"""
angles_store.py -- read/write access to the 42 loop angles, for the widget settings UI.

The base definitions live in angles.yaml (committed, 8 families). the operator's edits are
stored SEPARATELY in state/angle_overrides.yaml and merged on read, so:
  - the shipped defaults are never destroyed,
  - every edit is reviewable/diffable in one small file,
  - angle_pick picks up edits via load_angles(merged=True).
"""
from __future__ import annotations
import json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ANGLES_YAML = ROOT / "angles.yaml"
OVERRIDES = ROOT / "state" / "angle_overrides.yaml"
sys.path.insert(0, str(ROOT))

FIELDS = ("id", "family", "lens", "look_for", "evidence", "avoid_when")


def _parse_simple_yaml(text: str) -> dict:
    """Minimal parser for the overrides file (flat angle list, same shape as angles.yaml)."""
    angles: list[dict] = []
    cur: dict | None = None
    list_key: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            cur = None; list_key = None
            if s.startswith("angles:"):
                continue
            continue
        if s.startswith("- id:") and indent <= 2:
            cur = {"id": _scalar(s.split(":", 1)[1]), "look_for": []}
            angles.append(cur); list_key = None
            continue
        if cur is None:
            continue
        if s.startswith("- ") and list_key:
            cur[list_key].append(_scalar(s[2:].strip()))
            continue
        if ":" in s:
            k, _, v = s.partition(":")
            k = k.strip()
            if v.strip():
                cur[k] = _scalar(v); list_key = None
            else:
                cur[k] = []; list_key = k
    return {"angles": angles}


def _scalar(v: str):
    v = v.strip().strip('"').strip("'")
    return v


def _dump_overrides(angles: list[dict]) -> str:
    """JSON inside a YAML fence: zero escaping ambiguity, still a plain text file."""
    clean = [{k: a[k] for k in FIELDS if k in a} for a in angles]
    return ("# the operator's edits to the Karpathy Loop angles.\n"
            "# Only ids listed here override angles.yaml; all else uses the default.\n"
            "# Written by the widget settings panel.\n"
            "# JSON-PAYLOAD-BELOW\n"
            + json.dumps({"angles": clean}, indent=1) + "\n")


def load_base() -> list[dict]:
    """The 42 default angles from angles.yaml (via angle_pick's parser)."""
    import angle_pick
    return angle_pick.load_angles()["angles"]


def load_overrides() -> dict:
    """{id: angle_dict} of edits, or {}."""
    if not OVERRIDES.exists():
        return {}
    try:
        txt = OVERRIDES.read_text(encoding="utf-8")
        if "JSON-PAYLOAD-BELOW" in txt:
            data = json.loads(txt.split("JSON-PAYLOAD-BELOW\n", 1)[1])
        else:
            data = _parse_simple_yaml(txt)
    except Exception:
        return {}
    return {a.get("id"): a for a in data.get("angles", []) if a.get("id")}


def merged() -> list[dict]:
    """Base angles with overrides applied; each carries 'edited': bool."""
    ovr = load_overrides()
    out = []
    for a in load_base():
        b = dict(a)
        if a.get("id") in ovr:
            for k, v in ovr[a["id"]].items():
                if k in FIELDS and k != "id":
                    b[k] = v
            b["edited"] = True
        else:
            b["edited"] = False
        out.append(b)
    return out


def save_edits(edits: list[dict]) -> dict:
    """Merge the operator's edits into state/angle_overrides.yaml. Returns a summary."""
    base = {a["id"]: a for a in load_base()}
    cur = load_overrides()
    changed, skipped = [], []
    for e in edits or []:
        aid = (e or {}).get("id")
        if not aid or aid not in base:
            skipped.append(aid); continue
        b = base[aid]
        new = {}
        for k in FIELDS:
            if k in ("id", "family"):
                continue
            v = e.get(k)
            if v is None:
                continue
            if isinstance(v, str):
                v = v.strip()
                if not v or v == str(b.get(k, "")).strip():
                    continue
            elif isinstance(v, list):
                v = [str(x).strip() for x in v if str(x).strip()]
                if not v or v == [str(x).strip() for x in (b.get(k) or [])]:
                    continue
            if v:
                new[k] = v
        if not new:
            # empty edit == reset to default
            cur.pop(aid, None)
            changed.append((aid, "reset"))
            continue
        merged_a = dict(cur.get(aid, {}))
        merged_a.update(new)
        merged_a["id"] = aid
        merged_a["family"] = b.get("family", "")
        cur[aid] = merged_a
        changed.append((aid, ",".join(sorted(new))))
    OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES.write_text(_dump_overrides([cur[k] for k in sorted(cur)]), encoding="utf-8")
    return {"saved": len(changed), "changed": [f"{a}: {w}" for a, w in changed],
            "skipped": skipped, "path": str(OVERRIDES)}


def families() -> dict:
    """{family: [angle,...]} in merged view, preserving file order."""
    fams: dict = {}
    for a in merged():
        fams.setdefault(a.get("family", "?"), []).append(a)
    return fams


if __name__ == "__main__":
    print(json.dumps({"families": families()}, indent=1))
