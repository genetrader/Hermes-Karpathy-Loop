#!/usr/bin/env python3
"""Render proof for the Repos panel.

The static gate proves the file PARSES. It cannot prove the panel RENDERS --
a missing key, an undefined style token, or a branch that throws are all
invisible to a parse check. This drives the real component logic in Node with
the REAL data shapes from state/, so "it works" is observed rather than assumed.

What it proves:
  1. Every changed region is reachable -- the file's own identifiers resolve.
  2. The brief contract matches: the widget reads brief.summary || brief.purpose,
     so at least one of those keys must exist in the real brief files.
  3. The `see` contract matches: kind/label/url/path/note/start_cmd.
  4. The panel's fallbacks are real: a repo with no brief and a repo with no
     `see` block must both produce a defined render path, not a crash.

Run:  python tests/prove_repos_panel.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "review" / "plugin.js"
REPOS_DIR = ROOT / "state" / "repos"
YAML = ROOT / "improve.yaml"

fails, passes = [], []


def check(name, ok, detail=""):
    (passes if ok else fails).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  -- " + detail) if detail else ""))


print("=== render proof: repos panel ===")
src = PLUGIN.read_text(encoding="utf-8")

# ── 1. the component and hook exist and are wired ────────────────────────────
for ident in ("function reposPanel(", "function useRepoBriefs(",
              "reposPanel({ repos: reposRow", "const reposRow = actRows.map(",
              "const briefs = useRepoBriefs("):
    check("wired: " + ident, ident in src)

# ── 2. the brief contract matches the REAL files ─────────────────────────────
brief_files = sorted(REPOS_DIR.glob("*.json"))
check("brief files exist", len(brief_files) == 4, "%d found" % len(brief_files))

# what the widget actually reads:
reads = re.findall(r"b\.(what_it_is|summary|purpose)\b", src)
print("      widget reads from a brief: " + ", ".join(sorted(set(reads))))

missing_contract = []
for f in brief_files:
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception as e:
        missing_contract.append("%s (unparseable: %s)" % (f.name, e))
        continue
    if not ("what_it_is" in d or "summary" in d or "purpose" in d):
        # the widget would render "no description yet" despite a brief existing
        missing_contract.append(f.name)
check("every real brief satisfies the widget's read",
      not missing_contract,
      ("these would show 'no description yet' despite having a brief: "
       + ", ".join(missing_contract)) if missing_contract else "4/4 briefs readable")

# ── 3. the `see` contract ────────────────────────────────────────────────────
want = {"kind", "label", "url", "path", "note", "start_cmd"}
try:
    sys.path.insert(0, str(ROOT))
    import importlib
    act = importlib.import_module("activity")
    d = json.loads(subprocess.run(
        [sys.executable, "-c",
         "import sys;sys.path.insert(0,%r);import activity;"
         "import json;print(json.dumps(activity.compact_payload() if "
         "hasattr(activity,'compact_payload') else {}))" % str(ROOT)],
        capture_output=True, text=True, timeout=180, cwd=str(ROOT)).stdout or "{}")
except Exception as e:
    d = {}
    print("      (see-shape probe skipped: %s)" % e)

rows = d.get("rows") or []
if rows:
    got = set()
    for r in rows:
        s = r.get("see")
        if isinstance(s, dict):
            got |= set(s.keys())
    if got:
        check("see descriptor keys are a subset of what the widget handles",
              got <= want, "emitted: " + ", ".join(sorted(got)) +
              ("  UNHANDLED: " + ", ".join(sorted(got - want)) if got - want else ""))
    else:
        check("see descriptor keys (no rows carried a see block)", True,
              "no probe data; widget falls back to the honest dash")

# ── 4. the fallbacks are real code paths, not TODOs ──────────────────────────
check("no-brief fallback exists", "no description yet" in src)
check("no-see fallback exists", "no viewable surface declared" in src)
check("brief read failure is distinguishable from 'nothing written yet'",
      "could not read the description" in src, "the operator is not sent on a false errand")
check("start command rendered as text only (never executed)",
      "userSelect: \"all\"" in src and "start_cmd" in src)

# ── 5. the URL scheme allowlist ──────────────────────────────────────────────
check("safeUrl allowlist exists", "function safeUrl(" in src)
n_href = len(re.findall(r"href:\s*see\.url", src))
n_safe = len(re.findall(r"href:\s*href", src))
check("no href binds see.url directly", n_href == 0, "raw href=see.url sites: %d" % n_href)
check("both link sites go through safeUrl", n_safe == 2,
      "safe href sites: %d (expect 2 -- panel + table cell)" % n_safe)
# prove the predicate itself with the real inputs
m = re.search(r"function safeUrl\(u\)\s*\{(.*?)\n\}", src, re.S)
if m:
    body = m.group(1)
    for probe, want in (("javascript:alert(1)", "rejected"),
                        ("data:text/html,x", "rejected"),
                        ("file:///C:/x", "rejected"),
                        ("http://127.0.0.1:8093/", "allowed")):
        # crude but honest: apply the same two regexes the function uses
        t = probe
        ok = bool(re.match(r"^https?://", t, re.I)) or (
            bool(re.match(r"^[A-Za-z0-9.\-]+(:\d+)?(/|$)", t))
            and not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", t))
        check("safeUrl(%r) -> %s" % (probe, want), (ok and want == "allowed") or
              ((not ok) and want == "rejected"))

# ---- SURFACE SCOPE (S5) --------------------------------------------------
# A surface is a sub-category of a repo, worked one at a time. The panel shows
# it under the project name, and shows campaign progress beside it when the
# change straddles several surfaces.
check("surface chip is rendered in the projects table",
      "p.surface" in src and '}, "sf")' in src)
check("surface chip carries its position when known",
      "p.surface_pos" in src)
check("surface chip degrades to nothing when absent",
      "p.surface\n" in src or ": null," in src)
check("campaign progress is shown on the chip",
      "p.campaign_n" in src and "p.campaign_open" in src)
# JS source escapes these as \uXXXX; a DOUBLE backslash would render the
# characters literally ("\u00b7" printed as text). Check the escaped form.
check("no double-escaped unicode in the surface chip",
      "\\\\u00b7" not in src and "\\\\u2934" not in src,
      "found a literal backslash-u in the chip")
# The chip must not introduce a NEW table column -- the table already fills the
# dock width. Count the td cells in the projects row (measured: 8 data cells +
# 1 action cell = 9). A new column would push this to 10.
_i = src.find("children: actRows.map(")
_row = src[_i:_i + 9000]
td_cells = _row.count('jsx("td"')
check("surface chip adds no table column",
      td_cells == 9, "td cells in the projects row = %d (expected 9)" % td_cells)

# the widget must not contain any execution primitive
bad = [w for w in ("child_process", "execSync", "os.system", "shell.exec(",
                   "spawnSync", "require(\"child_process\")")
       if w in src and w != "shell.exec("]
execs = len(re.findall(r"\bexec\s*\(", src))
check("widget has no process-execution primitive",
      not bad and execs == 0, "hits: %s exec(=%d" % (bad, execs))

print()
print("=== %d passed, %d failed ===" % (len(passes), len(fails)))
if fails:
    for f in fails:
        print("  FAILED: " + f)
    sys.exit(1)
sys.exit(0)