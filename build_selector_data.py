#!/usr/bin/env python3
"""
build_selector_data.py -- merge the AI-project inventory with live repo facts
and the current loop state, and emit a single JSON payload for the selector UI.

Inputs:
  C:/CODING/ai-project-index/raw/rows.json   (225 inventoried projects)
  state/loop.json                            (rotation config)
  angles.yaml                                (angle library)
Output:
  state/selector_data.json
  selector.html
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = Path(r"C:\CODING\ai-project-index\raw\rows.json")
OUT_JSON = ROOT / "state" / "selector_data.json"

sys.path.insert(0, str(ROOT))
import angle_pick as ap          # noqa: E402
import loopctl                   # noqa: E402
import manifest_sync as ms       # noqa: E402


def sh(args, cwd=None, timeout=20):
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, errors="replace")
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def git_facts(path: str) -> dict:
    """Commit count, branch, dirty flag, remote, last commit date."""
    if not path or not os.path.isdir(os.path.join(path, ".git")):
        return {}
    out = {}
    rc, so, _ = sh(["git", "-C", path, "rev-list", "--count", "HEAD"])
    if rc == 0 and so.isdigit():
        out["commits"] = int(so)
    rc, so, _ = sh(["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0:
        out["branch"] = so
    rc, so, _ = sh(["git", "-C", path, "status", "--porcelain"])
    if rc == 0:
        out["dirty"] = len([l for l in so.splitlines() if l.strip()])
    rc, so, _ = sh(["git", "-C", path, "remote", "get-url", "origin"])
    if rc == 0 and so:
        out["remote"] = so
    rc, so, _ = sh(["git", "-C", path, "log", "-1", "--format=%cI"])
    if rc == 0 and so:
        out["last_commit"] = so[:10]
    # test command detection
    for cand in ("pytest.ini", "pyproject.toml", "setup.cfg"):
        if os.path.exists(os.path.join(path, cand)):
            out["test_runner"] = "pytest"
            break
    if os.path.isdir(os.path.join(path, "tests")) and "test_runner" not in out:
        out["test_runner"] = "pytest?"
    return out


# git_facts runs 5 subprocess calls per repo; serially that is ~20s over 225 projects and
# blew the desktop bridge's 30s shell.exec ceiling ("discovery timed out after 30 seconds",
# 2026-09-24). The repos are independent, so fan out; disk-bound work likes a modest cap.
from concurrent.futures import ThreadPoolExecutor
_GIT_CACHE: dict[str, dict] = {}


def _cached_git_facts(path: str) -> dict:
    if path not in _GIT_CACHE:
        _GIT_CACHE[path] = git_facts(path)
    return _GIT_CACHE[path]


def build() -> dict:
    rows = json.loads(INDEX.read_text(encoding="utf-8"))
    if isinstance(rows, dict):
        rows = rows.get("rows", [])

    # Pre-warm git facts in parallel (paths only; missing dirs stay {}).
    _paths = [r.get("path") or "" for r in rows]
    _want = [p for p in _paths if p and os.path.isdir(p) and os.path.isdir(os.path.join(p, ".git"))]
    if _want:
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(_cached_git_facts, _want))

    cfg = loopctl.load()
    data = ap.load_angles()
    angles = data["angles"]

    history = ap.load_history()
    stats = history.get("angles", {})

    projects = []
    for r in rows:
        path = r.get("path") or ""
        exists = bool(path) and os.path.isdir(path)
        g = _cached_git_facts(path) if exists else {}
        is_git = bool(g)
        projects.append({
            "path": path,
            "name": r.get("name") or Path(path).name,
            # The canonical slug the manifest and loop.json use. Without it the
            # page compared a display name ("Tombs & Tyrants AI RPG") against a
            # slug ("tombs-and-tyrants"), found no match, and showed saved picks
            # as unselected -- which looked exactly like "my save didn't stick".
            "slug": ms.slugify(r.get("name") or Path(path).name),
            "purpose": r.get("purpose") or "",
            "category": r.get("category") or "",
            "status": r.get("status") or "",
            "files": r.get("files") or 0,
            "bytes": r.get("bytes") or 0,
            "size_h": r.get("size_h") or "",
            "first": r.get("first") or "",
            "last": r.get("last") or "",
            "exts": r.get("exts") or {},
            "run_commands": r.get("run_commands") or [],
            "urls": r.get("urls") or [],
            "entry_point": r.get("entry_point") or "",
            "codex_sessions": r.get("codex_sessions"),
            "threads": r.get("threads"),
            "exists": exists,
            "is_git": is_git,
            "git": g,
            "selected": ms.slugify(r.get("name") or Path(path).name) in cfg.get("projects", [])
                        or path in cfg.get("projects", []),
        })

    # angle library shaped for the UI, with live usage stats
    families = {}
    for a in angles:
        families.setdefault(a["family"], []).append({
            "id": a["id"],
            "lens": a.get("lens", ""),
            "look_for": a.get("look_for", []),
            "evidence": a.get("evidence", ""),
            "avoid_when": a.get("avoid_when", ""),
            "used": stats.get(a["id"], {}).get("used", 0),
            "accepted": stats.get(a["id"], {}).get("accepted", 0),
            "failed": stats.get(a["id"], {}).get("failed", 0),
            "retired": stats.get(a["id"], {}).get("retired", False),
        })

    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "index_source": str(INDEX),
        "index_mtime": datetime.fromtimestamp(
            INDEX.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
        "loop": cfg,
        "counts": {
            "total": len(projects),
            "git": sum(1 for p in projects if p["is_git"]),
            "exists": sum(1 for p in projects if p["exists"]),
            "selected": sum(1 for p in projects if p["selected"]),
            "by_status": _count(projects, "status"),
            "by_category": _count(projects, "category"),
        },
        "families": families,
        "angle_count": len(angles),
        "projects": projects,
    }


def _count(items, key):
    c = {}
    for it in items:
        c[it.get(key) or "?"] = c.get(it.get(key) or "?", 0) + 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    d = build()
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(d, indent=2), encoding="utf-8")
    print(f"wrote {OUT_JSON} ({OUT_JSON.stat().st_size:,} bytes)")
    print(f"projects {d['counts']['total']} · git {d['counts']['git']} · "
          f"selected {d['counts']['selected']} · angles {d['angle_count']}")