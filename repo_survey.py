#!/usr/bin/env python
"""
repo_survey.py -- bounded, deterministic repo evidence collector.

The point: give a model ENOUGH to describe a repo accurately, WITHOUT dumping the
whole tree into a prompt. This module does no model work at all -- it just reads
files. That split matters: the facts here are cheap, repeatable and verifiable,
so the brief's grounding can be re-checked later even if the prose drifts.

Everything is bounded (caps on files walked, bytes read, entries returned) because
repos in this fleet range from a Next.js site to a 292 GB game tree.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist",
    "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", "site-packages",
    "target", "out", ".gradle", ".idea", ".vs", "coverage", ".tox",
}
# Asset / scratch / vcs-worktree dirs. BULK, no signal about what the software
# DOES. Measured: T&T's artwork/ alone holds 13,673 PNGs, which consumed the whole
# walk budget before the source tree was reached and made a 2,779-file Python
# project look like a PNG dump.
SKIP_DIRS |= {
    "artwork", "artwork_preview", "art-preview", "assets", "artgen",
    ".worktrees", "images", "media", "sprites", "fonts",
    # Backup/vendor dumps: project-c carries data/backups/ copies of its own
    # source, which would otherwise appear as duplicate entry points and inflate
    # the file count with stale code.
    "backups", "backup", "old", "archive", "vendor", "third_party",
}
# Media extensions: counted as assets, never as source language signal.
ASSET_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
              ".mp3", ".wav", ".ogg", ".mp4", ".mov", ".webm", ".ttf",
              ".otf", ".woff", ".woff2", ".psd", ".aseprite", ".tmx"}
MANIFEST_NAMES = (
    "package.json", "pyproject.toml", "requirements.txt", "Cargo.toml",
    "go.mod", "composer.json", "Gemfile", "build.gradle.kts", "build.gradle",
    "pom.xml", "pubspec.yaml", "manifest.json",
)
DOC_NAMES = ("README.md", "README.rst", "README.txt", "ARCHITECTURE.md",
             "ROADMAP.md", "PRD.md", "DESIGN.md", "AGENTS.md", "CLAUDE.md")
ENTRY_HINTS = (
    "main.py", "app.py", "server.py", "manage.py", "index.js", "index.ts",
    "main.js", "main.ts", "main.go", "main.rs", "app.js", "app.ts",
)
MAX_WALK = 20000          # cap on files stat'd
MAX_DOC_BYTES = 6000      # per doc file
MAX_MANIFEST_BYTES = 4000
MAX_ENTRIES = 120         # entry-ish files returned


def _read_capped(p: Path, cap: int) -> str:
    """Read at most `cap` bytes; never raise. Decodes defensively (repos carry
    mixed encodings and a survey must not die on one bad byte)."""
    try:
        with p.open("rb") as f:
            raw = f.read(cap)
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _walk(root: Path) -> dict:
    """One bounded pass: counts by extension, test files, and the biggest dirs."""
    ext_counts: dict[str, int] = {}
    assets: dict[str, int] = {}
    top_dirs: dict[str, int] = {}
    files = 0
    skipped = False
    for f in root.rglob("*"):
        if files >= MAX_WALK:
            skipped = True
            break
        try:
            if not f.is_file():
                continue
        except OSError:
            continue
        parts = set(f.parts)
        if parts & SKIP_DIRS:
            continue
        files += 1
        ext = f.suffix.lower() or "(none)"
        if ext in ASSET_EXTS:
            # Counted for context but excluded from the language signal: a
            # repo with 13k sprites is not "an image project".
            assets[ext] = assets.get(ext, 0) + 1
            continue
        ext_counts[ext] = ext_counts.get(ext, 0) + 1
        try:
            rel = f.relative_to(root)
            top = rel.parts[0] if len(rel.parts) > 1 else "(root files)"
        except Exception:
            top = "(unknown)"
        top_dirs[top] = top_dirs.get(top, 0) + 1
    return {"file_count": files, "ext_counts": ext_counts, "assets": assets,
            "top_dirs": top_dirs, "walk_truncated": skipped}


def _find_manifests(root: Path) -> dict:
    """Dependency manifests, capped. Also pulls the npm scripts block, which is
    usually HOW you run the project."""
    out: dict[str, str] = {}
    for name in MANIFEST_NAMES:
        for cand in (root / name, root / "server" / name, root / "android" / name):
            if cand.is_file():
                txt = _read_capped(cand, MAX_MANIFEST_BYTES)
                key = str(cand.relative_to(root)).replace("\\", "/")
                out[key] = txt
                break
    return out


def _find_docs(root: Path) -> dict:
    out: dict[str, str] = {}
    for name in DOC_NAMES:
        cand = root / name
        if cand.is_file():
            out[name] = _read_capped(cand, MAX_DOC_BYTES)
    # a docs/ dir is common; take the top few markdown files
    d = root / "docs"
    if d.is_dir():
        try:
            for f in sorted(d.glob("*.md"))[:5]:
                out["docs/" + f.name] = _read_capped(f, 1500)
        except OSError:
            pass
    return out


def _entry_points(root: Path) -> list[str]:
    """Files that look like an entry point / server, so the brief can say HOW
    it runs without inventing it."""
    hits: list[str] = []
    for f in root.rglob("*"):
        if len(hits) >= MAX_ENTRIES:
            break
        try:
            if not f.is_file():
                continue
        except OSError:
            continue
        if set(f.parts) & SKIP_DIRS:
            continue
        if f.name in ENTRY_HINTS:
            hits.append(str(f.relative_to(root)).replace("\\", "/"))
    return hits


def _port_hints(root: Path, manifests: dict) -> list[str]:
    """Ports mentioned in configs/docs -- used to seed the See-It probe."""
    blobs = list(manifests.values())
    for name in ("README.md", "ARCHITECTURE.md", ".env.example"):
        p = root / name
        if p.is_file():
            blobs.append(_read_capped(p, 3000))
    ports: set[str] = set()
    for b in blobs:
        for m in re.finditer(r"\b(?:port|PORT|localhost|127\.0\.0\.1)[^\d]{0,12}(\d{2,5})\b", b):
            n = m.group(1)
            if 1024 <= int(n) <= 65535:
                ports.add(n)
        for m in re.finditer(r"127\.0\.0\.1:(\d{2,5})|localhost:(\d{2,5})", b):
            ports.add(m.group(1) or m.group(2))
    return sorted(ports)


def _multi_surface(root: Path, top_dirs: dict) -> list[str]:
    """Detect repos that ship MORE THAN ONE deliverable (Project B: server/ +
    android/ + chrome-extension/). This is why a round can improve one surface
    while another sits untouched -- and the operator needs to SEE that."""
    known = ("server", "android", "chrome-extension", "web", "app", "client",
             "backend", "frontend", "mobile", "extension", "api")
    return [d for d in known if d in top_dirs and top_dirs[d] > 0]


def survey(path: str) -> dict:
    """Collect everything the brief writer needs. Never raises."""
    root = Path(path)
    if not root.is_dir():
        return {"ok": False, "error": "path is not a directory", "path": str(path)}

    walk = _walk(root)
    manifests = _find_manifests(root)
    docs = _find_docs(root)
    entries = _entry_points(root)
    surfaces = _multi_surface(root, walk["top_dirs"])

    # dominant language by file count (a fact, not a guess)
    lang_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "typescript react", ".jsx": "javascript react",
        ".kt": "kotlin", ".java": "java", ".cs": "csharp", ".go": "go",
        ".rs": "rust", ".rb": "ruby", ".php": "php", ".swift": "swift",
        ".html": "html", ".css": "css", ".sh": "shell",
    }
    by_lang: dict[str, int] = {}
    for ext, n in walk["ext_counts"].items():
        lang = lang_map.get(ext)
        if lang:
            by_lang[lang] = by_lang.get(lang, 0) + n
    top_langs = sorted(by_lang.items(), key=lambda kv: -kv[1])[:5]

    return {
        "ok": True,
        "path": str(path).replace("\\", "/"),
        "file_count": walk["file_count"],
        "asset_count": sum(walk["assets"].values()),
        "walk_truncated": walk["walk_truncated"],
        "top_dirs": dict(sorted(walk["top_dirs"].items(), key=lambda kv: -kv[1])[:15]),
        "ext_counts": dict(sorted(walk["ext_counts"].items(), key=lambda kv: -kv[1])[:15]),
        "languages": top_langs,
        "manifests": manifests,
        "docs": docs,
        "entry_points": entries[:40],
        "port_hints": _port_hints(root, manifests),
        "surfaces": surfaces,
    }


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="bounded repo survey (no model calls)")
    ap.add_argument("path")
    ap.add_argument("--compact", action="store_true",
                    help="omit manifest/doc bodies (just the shape)")
    a = ap.parse_args()
    s = survey(a.path)
    if a.compact and s.get("ok"):
        s = dict(s)
        s["manifests"] = {k: "<%d bytes>" % len(v) for k, v in s["manifests"].items()}
        s["docs"] = {k: "<%d bytes>" % len(v) for k, v in s["docs"].items()}
    print(json.dumps(s, indent=1)[:12000])
    return 0 if s.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(_cli())