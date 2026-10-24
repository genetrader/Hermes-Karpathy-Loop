#!/usr/bin/env python
"""Surface discovery for the Karpathy loop.

Reads a repo (READ-ONLY) and proposes what surfaces it contains.
A surface = a sub-category of the repo that can be worked on independently.

CLI:
    python surface_discover.py <repo-path> [--json] [--propose]

Output is human-readable findings, or JSON with --json, or a YAML snippet
compatible with improver.py surfaces_for() with --propose.

STUB — being fleshed out incrementally. Detection logic below is initial.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Surface:
    name: str
    kind: str          # e.g. "web-server", "android-app", "chrome-extension", "web-app", "library"
    where: str         # path relative to repo root ("" or "." means repo root)
    confidence: float  # 0..1
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "where": self.where,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
        }


BUILD_MARKERS = {
    "build.gradle.kts": "gradle-android?",   # ambiguous until we read it
    "build.gradle": "gradle-android?",
    "package.json": "node?",
    "requirements.txt": "python?",
    "pyproject.toml": "python?",
    "Dockerfile": "docker?",
    "manifest.json": "extension?",
}
ANDROID_MARKERS = ("applicationId", "com.android.application")
EXTENSION_MARKERS = ("manifest_version", "chrome.", "background", "content_scripts")
SERVER_HINTS = ("uvicorn", "fastapi", "flask", "gunicorn", "express", "starlette", "django")


def _read_head(path: Path, limit: int = 20000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _classify_build_file(bf: Path) -> tuple[str, float, list[str]]:
    """Look inside a build file and decide what kind of surface it implies."""
    text = _read_head(bf)
    parent = bf.parent
    rel = str(parent)

    # Android?
    if any(m in text for m in ANDROID_MARKERS):
        return "android-app", 0.95, [f"{bf.name} contains applicationId/com.android.application"]

    # Chrome extension?
    if bf.name == "manifest.json" and any(m in text for m in EXTENSION_MARKERS):
        return "chrome-extension", 0.9, [f"{bf.name} has manifest_version/chrome keys"]

    # Node?
    if bf.name == "package.json":
        lower = text.lower()
        if any(s in lower for s in SERVER_HINTS):
            return "web-server", 0.7, [f"package.json mentions a server framework"]
        if (parent / "manifest.json").exists() or "extension" in lower:
            return "chrome-extension", 0.6, ["package.json hints extension"]
        return "web-app", 0.5, ["package.json present"]

    # Python?
    if bf.name in ("requirements.txt", "pyproject.toml"):
        lower = text.lower()
        if any(s in lower for s in SERVER_HINTS):
            return "web-server", 0.8, [f"{bf.name} lists a server framework"]
        return "python-lib", 0.4, [f"{bf.name} present, no server framework"]

    return "unknown", 0.2, [f"{bf.name} present but unreadable/unclassified"]


def discover(repo: Path) -> list[Surface]:
    if not repo.is_dir():
        return []

    surfaces: list[Surface] = []
    # Walk the tree but skip vendored/noise dirs.
    SKIP = {".git", "node_modules", "venv", ".venv", "__pycache__", ".gradle", "build", "dist", ".idea", ".vscode"}
    build_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for fn in filenames:
            if fn in BUILD_MARKERS:
                build_files.append(Path(dirpath) / fn)

    # Classify each, then dedupe (a parent dir's build file swallows children's same-kind).
    for bf in sorted(build_files):
        kind, conf, ev = _classify_build_file(bf)
        rel = str(bf.parent.relative_to(repo)) if bf.parent != repo else "."
        name = bf.parent.name if bf.parent != repo else repo.name
        surfaces.append(Surface(name=name, kind=kind, where=rel, confidence=conf, evidence=ev))

    return surfaces


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover surfaces in a repo (read-only).")
    ap.add_argument("repo", help="path to the repo root")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--propose", action="store_true", help="emit YAML snippet for improve.yaml")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        print(f"ERROR: not a directory: {repo}", file=sys.stderr)
        return 2

    found = discover(repo)
    if args.json:
        print(json.dumps([s.as_dict() for s in found], indent=2))
    elif args.propose:
        print("surfaces:")
        for s in found:
            print(f"  - name: {s.name}")
            print(f"    kind: {s.kind}")
            print(f"    where: {s.where}")
    else:
        if not found:
            print(f"No surfaces detected in {repo}")
        for s in found:
            print(f"[{s.confidence:.2f}] {s.kind:16} {s.where or '.'}")
            for e in s.evidence:
                print(f"        - {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
