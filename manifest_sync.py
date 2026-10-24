#!/usr/bin/env python3
"""Write improve.yaml from the picker's selection.

THE MISSING LINK. The picker wrote state/loop.json, but the rotator reads
improve.yaml -- so ticking repos changed a file the loop never read, and
extra projects were silently dropped (only one ever ran). This closes that.

Behaviour:
  * add    -> a new project block (name/path/board/loops/enabled/objective)
  * enable -> enabled: true for the selected ones
  * disable-> enabled: false for previously-enabled ones NOT selected
              (never deletes a block -- objectives and gates are preserved)
  * NEVER touches a hand-written objective, acceptance, gate or boundary.

Slug: the picker shows human names ("Hermes Plans Folder"); the manifest keys
off a slug ("tombs-and-tyrants"). slugify() derives one, and known aliases
are honoured so we never re-add a project under a second name.

Verbs:
    sync  --projects a,b,c      rewrite the manifest to match this selection
    check --projects a,b,c      report the diff without writing
    list                        show manifest projects + enabled state
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "improve.yaml"
SELECTOR_DATA = HERE / "state" / "selector_data.json"

# Human name / folder name -> the slug already used in the manifest.
#
# Several inventory rows are DEAD COPIES sharing a name stem with the live repo
# (six "Tombs & Tyrants" rows exist). Map the live repo's own row to the same
# slug so the picker, loop.json and improve.yaml all agree on ONE identity --
# otherwise the page ticks a dead folder while the manifest points at the real
# repo, and the two silently disagree about what "tombs-and-tyrants" means.
ALIASES = {
    # The LIVE repo (git, D:\ZCODE\TOMBS AND TYRANTS) owns the canonical slug.
    "tombs & tyrants ai rpg": "tombs-and-tyrants",
    "tombs and tyrants ai rpg": "tombs-and-tyrants",
    "tombs-and-tyrants": "tombs-and-tyrants",
    # Dead copies get their OWN slugs. They are in the inventory for context but
    # must never answer to the live repo's slug, or the picker ticks a dead
    # folder while improve.yaml points somewhere else.
    "tombs and tyrants": "tombs-and-tyrants-dead-copy",
    "tombs & tyrants (placeholder)": "tombs-tyrants-dead-placeholder",
    "android notes like genspark": "secondbrain",
    "secondbrain": "secondbrain",
    "zip code ranker": "zip-code-ranker",
}


def slugify(name: str) -> str:
    s = (name or "").strip().lower()
    if s in ALIASES:
        return ALIASES[s]
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "project"


def load_manifest_text() -> str:
    return MANIFEST.read_text(encoding="utf-8")


def parse_manifest() -> list[dict]:
    """Parse the projects list, preserving order and any hand-written fields."""
    try:
        import yaml  # type: ignore
        doc = yaml.safe_load(load_manifest_text()) or {}
        return list(doc.get("projects") or [])
    except Exception as e:
        print(f"cannot parse manifest: {e}", file=sys.stderr)
        return []


def selector_projects() -> dict:
    """name/path lookup from the picker's own data file."""
    if not SELECTOR_DATA.exists():
        return {}
    try:
        d = json.loads(SELECTOR_DATA.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for p in d.get("projects", []):
        for key in (p.get("name"), Path(str(p.get("path") or "")).name):
            if key:
                out[str(key)] = p
    return out


def resolve(sel_name: str, known: dict, byname: dict) -> dict | None:
    """Turn a picker name into a manifest-shaped project dict."""
    slug = slugify(sel_name)
    if slug in known:
        return {"name": slug, "path": known[slug].get("path"),
                "board": known[slug].get("board") or slug, "existed": True}

    src = byname.get(sel_name) or byname.get(slug) or byname.get(sel_name.title())
    if src is None:
        # last resort: match on slugified folder name
        for k, v in byname.items():
            if slugify(k) == slug:
                src = v
                break
    if src is None:
        return None
    return {"name": slug, "path": str(src.get("path") or ""),
            "board": slug, "existed": False,
            "human": src.get("name"), "purpose": (src.get("purpose") or "")[:300]}


def cmd_sync(a, write=True):
    wanted = [s.strip() for s in a.projects.split(",") if s.strip()]
    existing = parse_manifest()
    known = {p.get("name"): p for p in existing if p.get("name")}
    byname = selector_projects()

    resolved, missing = [], []
    for n in wanted:
        r = resolve(n, known, byname)
        if r is None:
            missing.append(n)
        else:
            resolved.append(r)

    if missing:
        print(json.dumps({
            "ok": False,
            "error": "cannot resolve these to a path",
            "unresolved": missing,
            "hint": "add them to state/selector_data.json or name them as they appear in the picker",
        }, indent=2))
        return 2

    want_slugs = {r["name"] for r in resolved}
    added, enabled, disabled = [], [], []

    # 1. existing blocks: toggle enabled, keep every hand-written field
    for p in existing:
        nm = p.get("name")
        if nm in want_slugs:
            if not p.get("enabled"):
                p["enabled"] = True
                enabled.append(nm)
        else:
            if p.get("enabled"):
                p["enabled"] = False
                disabled.append(nm)

    # 2. new blocks
    have = {p.get("name") for p in existing}
    for r in resolved:
        if r["name"] in have:
            continue
        n = int(a.loops)
        obj = (a.objective or "").strip()
        block = {
            "name": r["name"],
            "path": (r.get("path") or "").replace("\\", "/"),
            "board": r.get("board") or r["name"],
            "loops": n,
            # No objective yet: leave a TODO marker rather than inventing one,
            # so `improver.py plan` shows it plainly instead of the loop
            # wandering with no goal.
            "objective": obj or f"TODO: objective not set for {r['name']} — add it before starting.",
            "enabled": True,
        }
        existing.append(block)
        added.append(r["name"])

    doc = {"projects": existing}

    if not write:
        print(json.dumps({"ok": True, "dry_run": True, "would_add": added,
                          "would_enable": enabled, "would_disable": disabled,
                          "total": len(existing)}, indent=2))
        return 0

    # Rewrite only the `projects:` list; keep the header comment block intact.
    text = load_manifest_text()
    head = text.split("projects:", 1)[0]
    try:
        import yaml  # type: ignore
        body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True,
                              default_flow_style=False, width=100)
        new_text = head + "projects:\n\n" + body.split("projects:", 1)[-1].lstrip("\n")
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"yaml dump failed: {e}"}, indent=2))
        return 3

    if a.backup:
        bak = MANIFEST.with_suffix(".yaml.bak-pre-sync")
        if not bak.exists():
            bak.write_text(text, encoding="utf-8")

    MANIFEST.write_text(new_text, encoding="utf-8")

    print(json.dumps({
        "ok": True, "added": added, "enabled": enabled, "disabled": disabled,
        "total_projects": len(existing),
        "enabled_projects": [p["name"] for p in existing if p.get("enabled")],
        "manifest": str(MANIFEST),
    }, indent=2))
    return 0


def cmd_list(a):
    rows = []
    for p in parse_manifest():
        rows.append({"name": p.get("name"), "enabled": bool(p.get("enabled")),
                     "path": p.get("path"), "loops": p.get("loops"),
                     "objective_set": not str(p.get("objective") or "").strip().startswith("TODO")})
    print(json.dumps({"count": len(rows), "projects": rows}, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Sync the picker selection into improve.yaml")
    sub = ap.add_subparsers(dest="verb", required=True)

    s = sub.add_parser("sync", help="rewrite improve.yaml to match a selection")
    s.add_argument("--projects", required=True, help="comma-separated picker names")
    s.add_argument("--loops", type=int, default=8)
    s.add_argument("--objective", default="")
    s.add_argument("--no-backup", dest="backup", action="store_false", default=True)
    s.set_defaults(fn=cmd_sync)

    s = sub.add_parser("check", help="show the diff without writing")
    s.add_argument("--projects", required=True)
    s.add_argument("--loops", type=int, default=8)
    s.add_argument("--objective", default="")
    s.set_defaults(fn=lambda a: cmd_sync(a, write=False))

    s = sub.add_parser("list", help="manifest projects + enabled state")
    s.set_defaults(fn=cmd_list)

    a = ap.parse_args()
    sys.exit(a.fn(a))


if __name__ == "__main__":
    main()