#!/usr/bin/env python
"""
repo_brief.py -- ONE-TIME per-repo description, generated from a bounded survey.

the operator's decision (2026-09-28): the description is a **one-time pass per repo**, not
refreshed per round. Refreshing it every round would spend a quarter of the round
budget re-describing a project that has not changed.

Design rule that keeps this honest: `repo_survey.py` produces FACTS. This module
only adds PROSE on top, and the prose must not contradict the facts. If the model
call fails, we still emit a deterministic brief built from the survey alone --
a factual brief with no prose beats no brief, and a fabricated brief is worse
than both.

    python repo_brief.py --one project-a
    python repo_brief.py --all
    python repo_brief.py --show project-b
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BRIEFS = ROOT / "state" / "repos"
sys.path.insert(0, str(ROOT))

import repo_survey  # noqa: E402
import improver as I  # noqa: E402


def _py() -> str:
    return str(Path(sys.executable))


def _hermes_py() -> Path:
    import settings as _S
    return _S.hermes_python()


# One-time briefs use the coordinator-class model: this is analysis over a
# summary, not a volume job, and it runs ONCE per repo. The model name comes
# from settings (models.brief_model / env KL_BRIEF_MODEL) -- nothing here
# ships with anyone's fleet topology. Empty brief_model => no model pass,
# deterministic facts-only brief.
import settings as _S
BRIEF_MODEL = _S.brief_model()
PROFILE = _S.brief_profile()

PROMPT = """You are writing a ONE-PAGE brief about a software project, for its owner.

You are given FACTS gathered from the repository. Use ONLY these facts. Do not
invent features, frameworks, endpoints, or intentions that the evidence does not
support. If something important is unknown, say plainly that it is not determined
by the evidence -- an honest gap is far more useful than a confident guess.

Return STRICT JSON only (no markdown fence, no commentary), with exactly these keys:

{
  "what_it_is":       "2-3 sentences. What this software IS, in plain language.",
  "supposed_to_do":   "2-3 sentences. The product intent -- what it is FOR.",
  "how_to_run":       "The concrete steps to start it locally, from the evidence. If the evidence does not show how, write 'Not determined from the repo -- no run instructions or scripts found.'",
  "what_working_looks_like": "How the owner would SEE that it works. Name the surface (URL/port/window) if the evidence provides one.",
  "surfaces":         [{"name":"server","kind":"server|static-html|built-site|cli|library|mobile|extension|unknown","where":"path or url or port from evidence"}],
  "stack":            ["languages and frameworks evidenced"],
  "notes":            "Anything the owner should know: oddities, dead areas, things that look unfinished. 1-2 sentences."
}

Rules:
- "surfaces" = the DISTINCT things a user would open. A repo with a server AND an
  android app AND a browser extension has THREE surfaces. List each separately.
- Never claim a surface works; you are describing what exists, not its status.
- Keep every string under 400 characters.

FACTS:
"""


def _facts_block(s: dict) -> str:
    """Compact, token-bounded rendering of the survey for the prompt."""
    def clip(t, n=1200):
        t = str(t or "")
        return t[:n] + ("\n...[truncated]" if len(t) > n else "")

    parts = [
        "path: %s" % s.get("path"),
        "source file count: %s (assets: %s)" % (s.get("file_count"), s.get("asset_count")),
        "languages by file count: %s" % (s.get("languages") or []),
        "top-level dirs: %s" % (list((s.get("top_dirs") or {}).items())[:10]),
        "detected multi-surface dirs: %s" % (s.get("surfaces") or []),
        "entry-point-looking files: %s" % (s.get("entry_points") or [])[:15],
        "ports mentioned in config/docs: %s" % (s.get("port_hints") or []),
    ]
    for name, body in (s.get("manifests") or {}).items():
        parts.append("\n--- manifest %s ---\n%s" % (name, clip(body, 2500)))
    for name, body in (s.get("docs") or {}).items():
        parts.append("\n--- doc %s ---\n%s" % (name, clip(body, 2500)))
    return "\n".join(parts)


def _fallback_brief(proj: dict, s: dict) -> dict:
    """Deterministic brief from facts alone. Used when the model is unavailable.

    Deliberately says what it does NOT know rather than smoothing over it.
    """
    langs = ", ".join(l for l, _ in (s.get("languages") or [])[:4]) or "unknown"
    surfaces = []
    for d in (s.get("surfaces") or []):
        surfaces.append({"name": d, "kind": "unknown", "where": d + "/"})
    if not surfaces:
        surfaces.append({"name": Path(proj.get("path") or "repo").name,
                         "kind": "unknown", "where": proj.get("path") or ""})
    ports = s.get("port_hints") or []
    return {
        "name": proj.get("name"),
        "generated_at": time.time(),
        "generated_by": "fallback (model unavailable) -- facts only",
        "what_it_is": ("%s. Evidence: %s source files, primarily %s."
                       % (proj.get("objective", "")[:200] or "No objective recorded.",
                          s.get("file_count"), langs)),
        "supposed_to_do": (proj.get("objective") or
                           "Not determined -- no objective set in improve.yaml."),
        "how_to_run": ("Not determined from the repo -- brief generated without a "
                       "model pass. Entry points seen: %s"
                       % (", ".join((s.get("entry_points") or [])[:5]) or "none")),
        "what_working_looks_like": ("Not determined. Ports seen in config/docs: %s"
                                    % (", ".join(ports) or "none")),
        "surfaces": surfaces,
        "stack": [l for l, _ in (s.get("languages") or [])[:5]],
        "notes": "Generated without a model pass; verify before trusting.",
        "facts": {"file_count": s.get("file_count"),
                  "languages": s.get("languages"),
                  "entry_points": (s.get("entry_points") or [])[:10],
                  "port_hints": ports},
    }


def _ask_model(facts: str) -> dict | None:
    """One headless call. Returns parsed JSON or None -- never raises."""
    hp = _hermes_py()
    if not hp.exists() or not BRIEF_MODEL:
        return None
    try:
        r = subprocess.run(
            [str(hp), "-m", "hermes_cli.main", "-p", PROFILE,
             "--model", BRIEF_MODEL, "-z", PROMPT + facts],
            cwd=str(_hermes_py().parent.parent),
            capture_output=True, text=True, timeout=900, errors="replace",
            creationflags=0x08000000)
    except Exception:
        return None
    out = (r.stdout or "").strip()
    # The child may wrap JSON in a fence or add prose; extract the first {...}.
    start, end = out.find("{"), out.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(out[start:end + 1])
    except Exception:
        return None


def generate(proj: dict, force: bool = False) -> dict:
    name = proj.get("name") or "unknown"
    BRIEFS.mkdir(parents=True, exist_ok=True)
    dest = BRIEFS / ("%s.json" % name)

    s = repo_survey.survey(proj.get("path") or "")
    if not s.get("ok"):
        brief = {"name": name, "generated_at": time.time(),
                 "generated_by": "error", "error": s.get("error"),
                 "what_it_is": "Could not survey this repo: %s" % s.get("error")}
        dest.write_text(json.dumps(brief, indent=1), encoding="utf-8")
        return brief

    model = _ask_model(_facts_block(s))
    if model and isinstance(model.get("what_it_is"), str):
        brief = {
            "name": name,
            "generated_at": time.time(),
            "generated_by": "model:%s" % BRIEF_MODEL,
            "what_it_is": model.get("what_it_is", ""),
            "supposed_to_do": model.get("supposed_to_do", ""),
            "how_to_run": model.get("how_to_run", ""),
            "what_working_looks_like": model.get("what_working_looks_like", ""),
            "surfaces": model.get("surfaces") or [],
            "stack": model.get("stack") or [],
            "notes": model.get("notes", ""),
            # Keep the facts alongside the prose so a later reader can check the
            # prose against the evidence instead of trusting it.
            "facts": {"file_count": s.get("file_count"),
                      "languages": s.get("languages"),
                      "entry_points": (s.get("entry_points") or [])[:10],
                      "port_hints": s.get("port_hints") or [],
                      "surfaces_detected": s.get("surfaces") or []},
        }
    else:
        brief = _fallback_brief(proj, s)

    dest.write_text(json.dumps(brief, indent=1), encoding="utf-8")
    return brief


def load(name: str) -> dict | None:
    p = BRIEFS / ("%s.json" % name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="one-time repo briefs")
    ap.add_argument("--one", metavar="NAME")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--show", metavar="NAME")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if a brief already exists")
    a = ap.parse_args()

    if a.show:
        b = load(a.show)
        if not b:
            print("no brief for %r (run --one %s)" % (a.show, a.show))
            return 1
        print(json.dumps(b, indent=1))
        return 0

    projects = I.enabled(I.load_manifest())
    if a.one:
        projects = [p for p in projects if p.get("name") == a.one]
        if not projects:
            print("no enabled project named %r" % a.one)
            return 1
    elif not a.all:
        print("pass --one NAME, --all, or --show NAME")
        return 2

    rc = 0
    for p in projects:
        name = p.get("name")
        if load(name) and not a.force:
            print("%-26s brief already exists (use --force to regenerate)" % name)
            continue
        t0 = time.time()
        b = generate(p, force=a.force)
        src = b.get("generated_by", "?")
        ok = bool(b.get("what_it_is")) and not b.get("error")
        print("%-26s %5.1fs  %-28s %s" % (name, time.time() - t0, src,
                                          "OK" if ok else "INCOMPLETE"))
        if not ok:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(_cli())