#!/usr/bin/env python
"""
project-improver / pick.py

Reads the EXISTING inventory (C:\\CODING\\ai-project-index, 396 rows) and
produces a tickable HTML picker + the improve.yaml manifest skeleton.

No re-scan: the index was built 2026-09-18/19 and is good enough to choose from.
"""
from __future__ import annotations

import glob
import html
import json
import re
from pathlib import Path

INDEX = Path(r"C:\CODING\ai-project-index")
ROOT = Path(__file__).resolve().parent
OUT_HTML = ROOT / "pick.html"
OUT_MANIFEST = ROOT / "improve.yaml"

# Noisy/irrelevant paths — folders that are not improvable projects
SKIP_RE = re.compile(
    r"(\.git|node_modules|__pycache__|\.venv|venv|site-packages|\.hermes|"
    r"\\AppData\\|\\Windows\\|\\Program Files|\.cache|dist\\|build\\)",
    re.I,
)


def load_rows() -> list[dict]:
    """Merge the enriched batches into one deduped list, best record per path."""
    best: dict[str, dict] = {}
    # prefer *.enriched.json, then *-annotated, then bare
    order = sorted(glob.glob(str(INDEX / "batches" / "*.json")))
    rank = {"enriched": 3, "annotated": 2, "": 1}

    def score(p: Path) -> int:
        n = p.stem
        if "enriched" in n:
            return 3
        if "annotated" in n:
            return 2
        return 1

    for f in sorted(order, key=lambda x: score(Path(x))):
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for r in data:
            if not isinstance(r, dict):
                continue
            p = r.get("path")
            if not p or SKIP_RE.search(p):
                continue
            prev = best.get(p)
            if prev is None or score(Path(f)) > prev["_score"]:
                r = dict(r)
                r["_score"] = score(Path(f))
                best[p] = r
    return sorted(best.values(), key=lambda r: (r.get("category") or "zz", r.get("path") or ""))


def main():
    rows = load_rows()
    # annotate with a has-git flag (checkpointability)
    for r in rows:
        p = Path(r["path"])
        r["_git"] = (p / ".git").exists()
        r["_exists"] = p.exists()
    good = [r for r in rows if r["_exists"]]
    print(f"rows loaded: {len(rows)}  |  existing on disk: {len(good)}  |  git repos: {sum(1 for r in good if r['_git'])}")

    cats = {}
    for r in good:
        cats.setdefault(r.get("category") or "Uncategorised", []).append(r)

    # ---------------- HTML picker
    parts = ["""<!doctype html><meta charset="utf-8">
<title>Project Improver — pick projects</title>
<style>
 body{background:#12141c;color:#e8ecf7;font:14px/1.5 -apple-system,Segoe UI,sans-serif;margin:0;padding:24px}
 h1{font-size:20px;margin:0 0 4px}
 .sub{color:#8a93ad;margin-bottom:18px}
 .bar{position:sticky;top:0;background:#12141c;padding:12px 0;border-bottom:1px solid #262a38;z-index:5;margin-bottom:14px}
 input[type=text]{background:#1c1f2b;border:1px solid #2e3346;color:#e8ecf7;padding:8px 10px;border-radius:6px;width:280px}
 button{background:#3b5bdb;color:#fff;border:0;padding:9px 14px;border-radius:6px;cursor:pointer;font-weight:600}
 button.sec{background:#2a2f40}
 .cat{margin:18px 0 8px;font-weight:700;color:#9db1ff;font-size:13px;text-transform:uppercase;letter-spacing:.06em}
 .row{display:flex;gap:10px;align-items:flex-start;padding:8px 10px;border:1px solid #232735;border-radius:8px;margin-bottom:6px;background:#171a24}
 .row:hover{border-color:#3b5bdb}
 .row input{margin-top:3px}
 .nm{font-weight:600}
 .pa{color:#7d879f;font-size:12px;font-family:ui-monospace,Consolas,monospace;word-break:break-all}
 .meta{color:#8a93ad;font-size:12px;margin-top:2px}
 .git{color:#4ec9a0;font-weight:600}
 .nogit{color:#c9a04e}
 #out{white-space:pre-wrap;background:#0d0f16;border:1px solid #2a2f40;padding:12px;border-radius:8px;margin-top:16px;font-family:ui-monospace,Consolas,monospace;font-size:12px;max-height:340px;overflow:auto}
 .hide{display:none}
 label.chk{display:flex;gap:8px;align-items:center;color:#c8d0e4}
</style>
<h1>Project Improver — pick the projects</h1>
<div class="sub">From your existing inventory: %d projects on disk. Tick what the loop should rotate through, then copy the YAML.</div>
<div class="bar">
  <input type="text" id="q" placeholder="filter by name / path / category…" oninput="flt()">
  <button class="sec" onclick="all(false)">Clear</button>
  <button class="sec" onclick="all(true)">Select all visible</button>
  <button onclick="gen()">Generate improve.yaml</button>
  <span id="cnt" style="margin-left:10px;color:#9db1ff"></span>
</div>
""" % len(good)]

    for cat in sorted(cats):
        parts.append(f'<div class="cat">{html.escape(cat)} ({len(cats[cat])})</div>')
        for r in cats[cat]:
            nm = html.escape(r.get("name") or Path(r["path"]).name)
            pa = html.escape(r["path"])
            purpose = html.escape((r.get("purpose") or "")[:200])
            status = html.escape(r.get("status") or "?")
            g = ('<span class="git">git ✓</span>' if r["_git"]
                 else '<span class="nogit">no git</span>')
            run = ", ".join(r.get("run_commands") or [])[:80]
            parts.append(f"""<div class="row" data-s="{html.escape((nm+' '+r['path']+' '+cat).lower())}">
 <input type="checkbox" data-name="{nm}" data-path="{pa}" data-git="{int(r['_git'])}">
 <div style="flex:1">
  <div class="nm">{nm} &nbsp;{g}</div>
  <div class="pa">{pa}</div>
  <div class="meta">{status} · {purpose}</div>
  {f'<div class="meta">run: <code>{html.escape(run)}</code></div>' if run else ''}
 </div></div>""")

    parts.append("""
<div id="out" class="hide"></div>
<script>
function vis(){return [...document.querySelectorAll('.row')].filter(r=>!r.classList.contains('hide'));}
function flt(){const q=document.getElementById('q').value.toLowerCase();
 document.querySelectorAll('.row').forEach(r=>r.classList.toggle('hide',!r.dataset.s.includes(q)));
 document.querySelectorAll('.cat').forEach((c,i,arr)=>{let n=c.nextElementSibling,any=false;
   while(n&&!n.classList.contains('cat')){if(n.classList.contains('row')&&!n.classList.contains('hide'))any=true;n=n.nextElementSibling;}
   c.classList.toggle('hide',!any);});
 document.getElementById('cnt').textContent=vis().length+' shown';}
function all(v){vis().forEach(r=>r.querySelector('input').checked=v);}
function gen(){
 const sel=[...document.querySelectorAll('.row input:checked')].map(i=>({name:i.dataset.name,path:i.dataset.path,git:i.dataset.git==='1'}));
 if(!sel.length){alert('Tick at least one project.');return;}
 let y=`# generated from ai-project-index — edit objectives, then run: python improver.py run
loop:
  poll_interval_min: 30
  max_project_runtime_h: 12
  ask_repeat_min: 5
  ask_max_repeat: 20

projects:
`;
 sel.forEach(s=>{y+=`  - name: ${s.name.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}
    path: ${s.path.replace(/\\\\/g,'/')}
    board: ${s.name.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')}
    loops: 5
    objective: >
      TODO — what does "better" mean for this project? Be specific.
    acceptance: >
      TODO — checkable criteria. The judge only sees this.
    gate: ""            # e.g. python -m pytest -q   (leave empty if no tests)
    boundary: "This repo only."
    implementer: glm53-flash-2x-spark:GLM-5.3-Flash-EXL3
    reviewer:    deepseek-v41-flash-3x-spark:deepseek-v4.1-flash
    enabled: true
${s.git?'':'    # WARNING: no .git — checkpointing disabled until git init\\n'}
`;});
 const o=document.getElementById('out');o.classList.remove('hide');o.textContent=y;
 navigator.clipboard&&navigator.clipboard.writeText(y);
}
flt();
</script>""")

    OUT_HTML.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {OUT_HTML}  ({OUT_HTML.stat().st_size:,} bytes)")

    # also emit a starter manifest with the top git repos that look like real apps
    cand = [r for r in good if r["_git"] and (r.get("status") or "").lower() in
            {"active", "in progress", "wip", "ongoing"}]
    cand = cand[:0]  # keep manifest hand-written; picker is the source
    return len(good)


if __name__ == "__main__":
    main()