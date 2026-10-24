#!/usr/bin/env python3
"""
discover.py -- periodic discovery pass for the Karpathy Loop.

Three jobs, all idempotent, all safe to run 3-4x/day:

  1. NEW THREADS     scan every agent home (Codex, Claude, ZCode, Pi, OpenCode,
                     OpenClaude, ...) for threads/sessions not yet in the index.
                     A thread that names a codebase gets ATTACHED to it; a
                     thread that stands alone becomes its OWN workable item
                     ("thread grain" -- one-off sessions are still work).
  2. CODEBASE SWEEP  re-walk the known project roots for directories that look
                     like a project but are not in the index yet, and add them.
  3. REFRESH         update last-seen / size / git facts for everything already
                     known, so staleness is visible.

Writes state/discovery.json and appends to state/index_additions.json (it never
rewrites the original inventory -- that stays the historical record).

  python discover.py run [--threads] [--sweep] [--refresh]
  python discover.py report
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
DISC = STATE / "discovery.json"
ADDS = STATE / "index_additions.json"

CODEX_HOME = Path(os.path.expanduser("~")) / ".codex"
AGENT_HOMES = [
    ("Codex", Path(os.path.expanduser("~")) / ".codex"),
    ("Claude", Path(os.path.expanduser("~")) / ".claude"),
    ("ZCode", Path(os.path.expanduser("~")) / ".zcode"),
    ("Pi", Path(os.path.expanduser("~")) / ".pi"),
    ("OpenCode", Path(os.path.expanduser("~")) / ".opencode"),
    ("OpenClaude", Path(os.path.expanduser("~")) / ".openclaude"),
    ("Codex Desktop docs", Path(os.path.expanduser("~")) / "Documents" / "Codex"),
]

# roots worth sweeping for new codebases
SWEEP_ROOTS = [
    Path("C:/CODING"), Path("D:/coding"), Path("D:/ZCODE"),
    Path(os.path.expanduser("~")) / "Documents",
    Path(os.path.expanduser("~")),
    Path("D:/"),
]

PROJECT_MARKERS = ("requirements.txt", "package.json", "pyproject.toml", "go.mod",
                   "Cargo.toml", "composer.json", "Gemfile", "pom.xml", "build.gradle")
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next",
             "dist", "build", "target", ".terraform", ".cache", "site-packages",
             "AppData", "Windows", "$Recycle.Bin", "System Volume Information"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def save(path: Path, obj) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# 1. threads
# --------------------------------------------------------------------------
def scan_threads() -> list[dict]:
    """Find thread/session directories and files under every agent home."""
    found: dict[str, dict] = {}

    for label, home in AGENT_HOMES:
        if not home.is_dir():
            continue
        try:
            for entry in home.iterdir():
                name = entry.name
                low = name.lower()
                if entry.is_dir():
                    # Codex-style: ~/.codex/sessions/<date>/<slug>, or docs/Codex/<date>/<slug>
                    if low in ("sessions", "projects", "threads", "conversations", "history"):
                        for day in entry.iterdir():
                            if not day.is_dir():
                                continue
                            for slug in day.iterdir():
                                if slug.is_dir() or slug.suffix in (".json", ".jsonl", ".md"):
                                    if not is_meaningful_thread(slug.stem):
                                        continue
                                    key = f"{label}|{name}/{day.name}/{slug.stem}"
                                    found.setdefault(key, {
                                        "agent": label, "kind": "thread",
                                        "path": str(slug), "date": day.name,
                                        "slug": _readable(slug.stem, day.name),
                                        "home": str(home),
                                    })
                    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", name):
                        for slug in entry.iterdir():
                            if slug.is_dir() or slug.suffix in (".json", ".jsonl", ".md"):
                                if not is_meaningful_thread(slug.stem):
                                    continue
                                key = f"{label}|{name}/{slug.stem}"
                                found.setdefault(key, {
                                    "agent": label, "kind": "thread",
                                    "path": str(slug), "date": name,
                                    "slug": _readable(slug.stem, name),
                                    "home": str(home),
                                })
                elif entry.suffix in (".jsonl", ".json", ".md") and entry.stat().st_size > 200:
                    if low.startswith(("session", "thread", "conversation", "rollout")) and \
                       is_meaningful_thread(entry.stem):
                        key = f"{label}|{name}"
                        found.setdefault(key, {
                            "agent": label, "kind": "session",
                            "path": str(entry), "date": "",
                            "slug": _readable(entry.stem, ""),
                            "home": str(home),
                        })
        except (PermissionError, OSError):
            continue

    return list(found.values())


# Slugs that are noise, not threads: numeric fragments, UUID fragments, memory
# dumps, index files. Scanning them produces hundreds of fake "workable items"
# and drowns the real ones.
JUNK_SLUG = re.compile(
    r"^(\d{1,4}|[0-9a-f]{8}[ -][0-9a-f]{4}.*|memory|session index|index|"
    r"state|log|config|settings|cache|tmp|temp|test|new|untitled|"
    r"[0-9a-f-]{16,})$", re.I)


def is_meaningful_thread(slug: str) -> bool:
    """A slug is a real thread only if it reads like something a human titled."""
    s = (slug or "").strip()
    if len(s) < 12:
        return False
    if JUNK_SLUG.match(s):
        return False
    # needs at least 2 real words
    words = [w for w in re.split(r"[^A-Za-z]+", s) if len(w) > 2]
    return len(words) >= 3


def _readable(slug: str, date: str) -> str:
    """Turn an on-disk slug into something a human reads."""
    s = slug.replace("-", " ").replace("_", " ").strip()
    s = re.sub(r"\s+", " ", s)
    return (s[:110] + ("…" if len(s) > 110 else "")) or f"thread {date}"


def match_thread_to_project(th: dict, projects: list[dict]) -> str | None:
    """Best-effort: does this thread name a known codebase?"""
    hay = (th.get("slug") or "").lower()
    if not hay:
        return None
    best, best_score = None, 0
    for p in projects:
        nm = (p.get("name") or "").lower()
        if not nm:
            continue
        # score by longest matching name token run
        toks = [t for t in re.split(r"[^a-z0-9]+", nm) if len(t) > 3]
        if not toks:
            continue
        score = sum(1 for t in toks if t in hay)
        if score > best_score:
            best, best_score = p.get("path"), score
    return best if best_score >= 2 else None


# --------------------------------------------------------------------------
# 2. codebase sweep
# --------------------------------------------------------------------------
def looks_like_project(d: Path) -> bool:
    try:
        names = {f.name for f in d.iterdir()}
    except (PermissionError, OSError):
        return False
    if names & set(PROJECT_MARKERS) or ".git" in names:
        return True
    # A pile of loose scripts is not a project -- require a README or a real
    # source tree with structure, so the sweep adds codebases, not folders.
    has_readme = any(n.lower().startswith("readme") for n in names)
    src = sum(1 for n in names if Path(n).suffix in
              (".py", ".js", ".ts", ".php", ".rb", ".cs", ".go", ".rs", ".java"))
    return has_readme and src >= 3


def sweep(known: set[str], limit: int = 4000, known_roots: list[str] | None = None) -> list[dict]:
    """
    Walk the sweep roots for codebases not already in the index.

    `known_roots` are the inventory's own project paths: any directory INSIDE
    one of those is part of that project, not a new project. Without this the
    sweep "discovers" dozens of subfolders (waku-agent/scripts,
    flipbook/includes, ...) and buries the real finds.
    """
    found, seen = [], 0
    roots_norm = [str(Path(r)).rstrip("\\/").lower() for r in (known_roots or [])]

    def inside_known(p: Path) -> bool:
        ps = str(p).rstrip("\\/").lower()
        return any(ps.startswith(r + os.sep) or ps == r for r in roots_norm)
    for root in SWEEP_ROOTS:
        if not root.is_dir():
            continue
        for dirpath, dirnames, _ in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith("$")]
            seen += 1
            if seen > limit:
                break
            depth = str(Path(dirpath)).count(os.sep) - str(root).count(os.sep)
            if depth > 2:
                dirnames[:] = []
                continue
            p = Path(dirpath)
            key = str(p).lower()
            if key in known:
                continue
            if inside_known(p):
                continue          # part of a project we already track
            if looks_like_project(p) and depth >= 1:
                found.append({"path": str(p), "name": p.name,
                              "date": datetime.fromtimestamp(
                                  p.stat().st_mtime).strftime("%Y-%m-%d"),
                              "agent": "sweep", "kind": "codebase"})
        if seen > limit:
            break
    return found


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------
def run(do_threads=True, do_sweep=True, do_refresh=True) -> dict:
    disc = load(DISC, {"first_run": _now(), "runs": 0,
                       "threads_seen": [], "codebases_seen": []})
    disc["runs"] = disc.get("runs", 0) + 1
    disc["last_run"] = _now()

    rows = load(Path(r"C:\CODING\ai-project-index\raw\rows.json"), [])
    if isinstance(rows, dict):
        rows = rows.get("rows", [])
    known_paths = {str(r.get("path", "")).lower() for r in rows if r.get("path")}
    known_paths |= {str(a.get("path", "")).lower() for a in load(ADDS, [])}
    known_roots = [r.get("path") for r in rows if r.get("path")]

    new_threads, new_codebases = [], []

    if do_threads:
        seen = set(disc.get("threads_seen", []))
        for th in scan_threads():
            if th["path"].lower() in seen:
                continue
            seen.add(th["path"].lower())
            th["first_seen"] = _now()
            th["attached_to"] = match_thread_to_project(th, rows)
            th["workable"] = True   # a lone thread IS work, attached or not
            new_threads.append(th)
        disc["threads_seen"] = sorted(seen)
        disc["thread_scan_at"] = _now()

    if do_sweep:
        seen_cb = set(disc.get("codebases_seen", []))
        for cb in sweep(known_paths, known_roots=known_roots):
            if cb["path"].lower() in seen_cb:
                continue
            seen_cb.add(cb["path"].lower())
            cb["first_seen"] = _now()
            new_codebases.append(cb)
        disc["codebases_seen"] = sorted(seen_cb)
        disc["sweep_at"] = _now()

    if do_refresh:
        disc["refresh_at"] = _now()

    # append additions (never rewrite the original inventory)
    adds = load(ADDS, [])
    have = {str(a.get("path", "")).lower() for a in adds}
    for item in new_threads + new_codebases:
        if str(item["path"]).lower() in have:
            continue
        adds.append(item)
    save(ADDS, adds)
    save(DISC, disc)

    return {"new_threads": len(new_threads), "new_codebases": len(new_codebases),
            "threads": new_threads, "codebases": new_codebases,
            "totals": {"threads_tracked": len(disc.get("threads_seen", [])),
                       "codebases_tracked": len(disc.get("codebases_seen", [])),
                       "additions": len(adds)}}


def cmd_run(a) -> int:
    r = run(do_threads=not a.sweep_only, do_sweep=not a.threads_only)
    print(f"run #{load(DISC, {}).get('runs', 0)}  {_now()}")
    print(f"  new threads   : {r['new_threads']}")
    print(f"  new codebases : {r['new_codebases']}")
    print(f"  tracked       : {r['totals']['threads_tracked']} threads, "
          f"{r['totals']['codebases_tracked']} codebases")
    for t in r["threads"][:12]:
        att = f"  -> {Path(t['attached_to']).name}" if t.get("attached_to") else "  (standalone)"
        print(f"    [{t['agent']}] {t['slug'][:64]}{att}")
    if len(r["threads"]) > 12:
        print(f"    … +{len(r['threads']) - 12} more")
    for c in r["codebases"][:12]:
        print(f"    [new codebase] {c['path']}")
    if len(r["codebases"]) > 12:
        print(f"    … +{len(r['codebases']) - 12} more")
    if a.json:
        print(json.dumps(r, indent=2))
    return 0


def cmd_report(a) -> int:
    disc = load(DISC, {})
    adds = load(ADDS, [])
    attached = [a for a in adds if a.get("attached_to")]
    standalone = [a for a in adds if a.get("kind") == "thread" and not a.get("attached_to")]
    stats = {
        "runs": disc.get("runs", 0),
        "last_run": disc.get("last_run", "never"),
        "threads_tracked": len(disc.get("threads_seen", [])),
        "codebases_seen": len(disc.get("codebases_seen", [])),
        "additions": len(adds),
        "attached": len(attached),
        "standalone": len(standalone),
    }
    # --json must be PURE JSON on stdout: the desktop panel parses it. Human
    # labels printed first made json.loads fail, which surfaced in the widget
    # as "unexpected output: <fragment>".
    #
    # Compact by default -- the panel only renders `stats`. The full additions
    # list is ~65 KB, which is wasteful to ship through the desktop bridge on
    # every refresh; `--full` keeps it for CLI use.
    if a.json:
        payload = {"stats": stats}
        if getattr(a, "full", False):
            payload["discovery"] = disc
            payload["additions"] = adds
        print(json.dumps(payload, indent=2))
        return 0
    print("DISCOVERY")
    print(f"  runs           : {stats['runs']}")
    print(f"  last run       : {stats['last_run']}")
    print(f"  threads tracked: {stats['threads_tracked']}")
    print(f"  codebases seen : {stats['codebases_seen']}")
    print(f"  additions      : {stats['additions']}")
    print(f"    attached to a project : {stats['attached']}")
    print(f"    standalone (workable) : {stats['standalone']}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="discovery pass for the Karpathy Loop")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run")
    s.add_argument("--threads-only", action="store_true")
    s.add_argument("--sweep-only", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("report")
    s.add_argument("--json", action="store_true")
    s.add_argument("--full", action="store_true",
                   help="include the full additions list (large)")
    s.set_defaults(fn=cmd_report)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())