#!/usr/bin/env python
"""
project-improver / control_panel.py

Builds panel.html — the CONTROL PANEL for the whole improver system.

This is the base platform Gene asked for: the full inventory as the working
surface, with the projects pinned into the rotation at the top, and the
rotation ledger (done / in-flight / planned) visible in one screen.

It reads ONLY real state:
  ai-project-index/raw/rows.json     the inventory (225 rows, category/status/
                                     purpose/run-cmds/thread counts)
  ai-project-index/corrections.json  disk-verified run commands + entry points
  improve.yaml                       what is pinned into the rotation
  state/rotation.json                rotation position + turn history
  state/plan.json                    the per-project improvement PLAN
  questions/pending.json             the open question, if any
  kanban boards (via improver.py)    live card status per project

Writes a single self-contained HTML file. No server, no build step, no CDN.

Usage:
  python control_panel.py          build panel.html
  python control_panel.py --open   build then open in the browser
"""
from __future__ import annotations

import html
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INDEX = Path(r"C:\CODING\ai-project-index")
OUT = ROOT / "panel.html"
PANEL_STATE = ROOT / "state" / "panel_state.json"
PLANDIR = ROOT / "state" / "plan"


def norm_path(s) -> str:
    """Normalise a path for joining manifest entries to inventory rows.
    Manifest uses forward slashes (D:/ZCODE/X); the inventory uses backslashes
    (D:\\ZCODE\\X) with inconsistent case. Compare on a canonical form."""
    return str(s or "").replace("/", "\\").rstrip("\\").lower()


# ------------------------------------------------------------------ loading

def jload(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_inventory() -> list[dict]:
    """rows.json is the enriched inventory (name/purpose/category/status/run)."""
    rows = jload(INDEX / "raw" / "rows.json", [])
    if not isinstance(rows, list):
        return []
    corrections = jload(INDEX / "corrections.json", {})
    for r in rows:
        key = (r.get("path") or "").lower()
        c = corrections.get(key) or {}
        if c:
            if c.get("run"):
                r["run_commands"] = c["run"]
            if c.get("url"):
                r["urls"] = c["url"]
            if c.get("entry"):
                r["entry_point"] = c["entry"]
        r["_exists"] = Path(r.get("path", "")).exists()
        r["_git"] = (Path(r.get("path", "")) / ".git").exists() if r["_exists"] else False
        r["_tests"] = _detect_tests(Path(r.get("path", ""))) if r["_exists"] else None
    return rows


def _detect_tests(p: Path) -> str | None:
    """Best-guess gate command. Returns None when the project has no tests."""
    try:
        if not p.is_dir():
            return None
    except OSError:
        return None
    has_tests_dir = (p / "tests").is_dir() or (p / "test").is_dir()
    pyproj = (p / "pyproject.toml").exists() or (p / "pytest.ini").exists() \
             or (p / "setup.py").exists() or (p / "requirements.txt").exists()
    if has_tests_dir and pyproj:
        return "python -m pytest -q"
    pkg = p / "package.json"
    if pkg.exists():
        try:
            j = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
            scripts = j.get("scripts") or {}
            if "test" in scripts:
                return "npm test"
        except Exception:
            pass
    if (p / "Makefile").exists() or (p / "makefile").exists():
        return "make test"
    return None


def load_manifest() -> dict:
    try:
        import yaml
        m = yaml.safe_load((ROOT / "improve.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        m = {}
    return m


def load_plan() -> dict:
    """Per-project improvement plan: the explicit process, not blind iteration."""
    return jload(ROOT / "state" / "plan.json", {})


def load_questions():
    pend = jload(ROOT / "questions" / "pending.json", None)
    park = jload(ROOT / "questions" / "parked.json", None)
    ans_dir = ROOT / "questions" / "answered"
    answered = []
    if ans_dir.exists():
        for f in sorted(ans_dir.glob("*.json")):
            answered.append(jload(f, {}))
    return pend, park, answered


def board_status(board: str) -> list[dict]:
    try:
        import improver
        return improver.list_tasks(board)
    except Exception:
        return []


# ------------------------------------------------------------------ helpers

def slugify(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:48]


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def human(n) -> str:
    try:
        n = int(n)
    except Exception:
        return "0"
    for unit in ("B", "K", "M", "G"):
        if n < 1024 or unit == "G":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return str(n)


def ago(ts) -> str:
    if not ts:
        return "never"
    try:
        d = int(time.time() - float(ts))
    except Exception:
        return "?"
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


# ------------------------------------------------------------------ build

def build() -> str:
    inv = load_inventory()
    man = load_manifest()
    loop = man.get("loop") or {}
    projects = man.get("projects") or []
    pinned = [p for p in projects if p.get("enabled")]
    parked = [p for p in projects if not p.get("enabled")]
    rot = jload(ROOT / "state" / "rotation.json", {"idx": 0, "history": []})
    plan = load_plan()
    pend, park, answered = load_questions()
    panel_state = jload(PANEL_STATE, {})

    # index the inventory by lowercased path so we can join on the manifest
    by_path = {norm_path(r.get("path")): r for r in inv}

    # ---- live status per pinned project
    live = {}
    for p in projects:
        tasks = board_status(p["board"])
        open_tasks = [t for t in tasks
                      if str(t.get("status", "")).lower()
                      in {"running", "ready", "todo", "blocked", "review"}]
        live[p["board"]] = open_tasks

    idx = rot.get("idx", 0) % len(pinned) if pinned else 0
    nxt_name = pinned[idx]["name"] if pinned else None

    # ---------- top: rotation board
    rot_rows = []
    for p in projects:
        on = bool(p.get("enabled"))
        ot = live.get(p["board"], [])
        if not on:
            badge, bcls = "PARKED", "mut"
            detail = "not in rotation"
        elif ot:
            st = str(ot[0].get("status", "?")).lower()
            badge, bcls = st.upper(), "run"
            detail = f"card {ot[0].get('id', '?')} · {(ot[0].get('title') or '')[:52]}"
        elif p["name"] == nxt_name:
            badge, bcls = "NEXT UP", "nxt"
            detail = f"queued · {p.get('loops', 5)} loops"
        else:
            badge, bcls = "IDLE", "mut"
            detail = "waiting its turn"

        pl = plan.get(p["name"]) or {}
        plan_pct = pl.get("percent", 0)
        stage_now = pl.get("stage") or "—"
        done_stages = pl.get("done") or {}
        stage_line = " · ".join(sorted(done_stages)) if done_stages else "not started"
        nxt_stage = ""
        try:
            import method
            for s in method.STAGES:
                if s[1] not in done_stages:
                    nxt_stage = f"{s[0]} {s[1]}"
                    break
            else:
                nxt_stage = "cycle complete — re-scout"
        except Exception:
            pass
        inv_row = by_path.get(norm_path(p.get("path"))) or {}
        gate = p.get("gate") or "(no gate — judge only)"

        rot_rows.append(f"""<div class="pcard {'on' if on else 'off'}">
  <div class="phead">
    <span class="badge {bcls}">{badge}</span>
    <span class="pname">{esc(p['name'])}</span>
    <span class="ploops">{p.get('loops', 5)} loops</span>
  </div>
  <div class="ppath">{esc(p.get('path'))}</div>
  <div class="pdetail">{esc(detail)}</div>
  <div class="pobj">{esc((p.get('objective') or '').strip()[:230])}</div>
  <div class="pmeta">
    <span class="tag {'ok' if p.get('gate') else 'warn'}">gate: {esc(gate[:58])}</span>
    <span class="tag">impl: {esc(str(p.get('implementer','')).split(':')[-1])}</span>
    <span class="tag rev">rev: {esc(str(p.get('reviewer','')).split(':')[-1])}</span>
    <span class="tag">{esc(inv_row.get('category') or '?')}</span>
    <span class="tag">{esc(inv_row.get('status') or '?')}</span>
  </div>
  <div class="prog"><div class="pfill" style="width:{plan_pct}%"></div></div>
  <div class="pdetail">stage {esc(stage_now)} · {plan_pct}% · done: {esc(stage_line)}</div>
  <div class="pdetail">next stage: <b class="nxt">{esc(nxt_stage or '—')}</b></div>
</div>""")

    # ---------- rotation ledger
    hist = rot.get("history", [])
    # Each project's stage-completion events are also part of "what's been done"
    stage_events = []
    for pname, st in plan.items():
        for skey, sv in (st.get("done") or {}).items():
            stage_events.append({
                "at": sv.get("at", 0), "project": pname,
                "stage": skey, "ok": sv.get("ok", True),
            })
    stage_events.sort(key=lambda e: e["at"], reverse=True)

    led = []
    for e in stage_events[:25]:
        ts = time.strftime("%m-%d %H:%M", time.localtime(e["at"])) if e["at"] else "—"
        cls = "done" if e["ok"] else "bad"
        led.append(f"""<tr>
  <td class="pa">{ts}</td>
  <td><b>{esc(e['project'])}</b></td>
  <td class="pa">stage</td>
  <td class="num">—</td>
  <td class="{cls}">{esc(e['stage'])}</td>
  <td class="pa">{'completed' if e['ok'] else 'failed'}</td>
</tr>""")
    for h in reversed(hist[-25:]):
        ts = time.strftime("%m-%d %H:%M", time.localtime(h.get("at", 0)))
        outcome = h.get("outcome") or "created"
        ocls = {"done": "done", "created": "run", "failed": "bad",
                "parked": "warn"}.get(outcome, "mut")
        led.append(f"""<tr>
  <td class="pa">{ts}</td>
  <td><b>{esc(h.get('project'))}</b></td>
  <td class="pa">{esc(h.get('task_id') or '—')}</td>
  <td class="num">{esc(h.get('loops'))}</td>
  <td class="{ocls}">{esc(outcome)}</td>
  <td class="pa">{esc((h.get('note') or '')[:74])}</td>
</tr>""")

    # ---------- stage artifacts (the real evidence on disk)
    artifact_blocks = []
    for p in projects:
        d = PLANDIR / p["name"]
        if not d.exists():
            continue
        files = []
        for f in sorted(d.glob("*")):
            try:
                sz = f.stat().st_size
            except OSError:
                continue
            files.append(f"""<div class="afile">
  <span class="aname">{esc(f.name)}</span>
  <span class="pa">{human(sz)}</span>
</div>""")
        if not files:
            continue
        artifact_blocks.append(
            f'<div class="pdetail"><b>{esc(p["name"])}</b></div>' + "".join(files))

    # planned = pinned, in rotation order, from current index
    planned = []
    if pinned:
        for i in range(len(pinned)):
            p = pinned[(idx + i) % len(pinned)]
            when = "now" if i == 0 else f"+{i * int(loop.get('poll_interval_min', 30))}m"
            planned.append(
                f'<div class="planrow"><span class="pwhen">{esc(when)}</span>'
                f'<b>{esc(p["name"])}</b>'
                f'<span class="pa">{esc(p.get("loops", 5))} loops</span></div>')

    # ---------- open question
    if pend:
        waited = int(time.time() - pend.get("asked_at", time.time()))
        qhtml = f"""<div class="alert">
  <div class="ah">⏰ OPEN QUESTION · waiting {waited // 60}m {waited % 60}s ·
      reminder {pend.get('reminders', 0)}/{pend.get('max_repeat', 20)}</div>
  <div class="ab">{esc(pend.get('question'))}</div>
  <div class="ap">project <b>{esc(pend.get('project'))}</b> · answer in
      #project-reviewer</div></div>"""
    else:
        qhtml = '<div class="ok">No open question — nothing is waiting on you.</div>'

    # ---------- full inventory table
    cats = Counter(r.get("category") or "Uncategorised" for r in inv)
    pinset = {norm_path(p.get("path")) for p in pinned}
    parkedset = {norm_path(p.get("path")) for p in parked}

    irows = []
    for r in sorted(inv, key=lambda x: (x.get("category") or "zz", x.get("name") or "")):
        path_l = norm_path(r.get("path"))
        if path_l in pinset:
            mark, mcls = "● IN ROTATION", "pin"
        elif path_l in parkedset:
            mark, mcls = "○ parked", "mut"
        else:
            mark, mcls = "", ""
        tests = r.get("_tests")
        if not r["_exists"]:
            tcls, ttxt = "bad", "gone"
        elif tests:
            tcls, ttxt = "ok", esc(tests)
        elif r["_git"]:
            tcls, ttxt = "warn", "no tests"
        else:
            tcls, ttxt = "mut", "no git"
        run = (r.get("run_commands") or [])
        runtxt = esc(run[0]) if run else ""
        irows.append(f"""<tr class="inv" data-s="{esc(((r.get('name') or '')+' '+(r.get('path') or '')+' '+(r.get('category') or '')).lower())}"
     data-cat="{esc(r.get('category') or '')}" data-status="{esc(r.get('status') or '')}">
  <td class="mark {mcls}">{mark}</td>
  <td><b>{esc(r.get('name') or Path(r.get('path', '')).name)}</b>
      <div class="ppath">{esc(r.get('path'))}</div></td>
  <td class="pa">{esc(r.get('category'))}</td>
  <td class="st">{esc(r.get('status'))}</td>
  <td class="pa">{human(r.get('bytes') or 0)}</td>
  <td class="{tcls} pa">{ttxt}</td>
  <td class="pa">{runtxt[:78]}</td>
</tr>""")

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    gate_pct = round(100 * sum(1 for p in projects if p.get("gate")) / max(len(projects), 1))

    doc = f"""<!doctype html><meta charset="utf-8">
<title>Project Improver — Control Panel</title>
<style>
 :root{{--fg:#e8ecf7;--mut:#7d879f;--acc:#5b7cfa;--ok:#4ec9a0;--warn:#e0a94e;
        --bad:#e06c75;--bd:#262a38;--card:#171a24;--bg:#12141c}}
 *{{box-sizing:border-box}}
 body{{background:var(--bg);color:var(--fg);font:13px/1.55 -apple-system,"Segoe UI",sans-serif;margin:0;padding:20px 22px}}
 h1{{font-size:20px;margin:0}} h2{{font-size:12px;text-transform:uppercase;letter-spacing:.08em;
    color:#9db1ff;margin:24px 0 9px;padding-bottom:5px;border-bottom:1px solid var(--bd)}}
 .sub{{color:var(--mut);font-size:12px;margin:5px 0 0}}
 .grid{{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}}
 .kpi{{background:var(--card);border:1px solid var(--bd);border-radius:9px;padding:9px 13px;min-width:104px}}
 .kpi .v{{font-size:19px;font-weight:700;font-variant-numeric:tabular-nums}}
 .kpi .k{{color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.06em}}
 .cols{{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}}
 .col{{flex:1 1 420px;min-width:360px}}
 .pcard{{background:var(--card);border:1px solid var(--bd);border-left:3px solid var(--bd);
        border-radius:9px;padding:11px 13px;margin-bottom:9px}}
 .pcard.on{{border-left-color:var(--ok)}} .pcard.off{{opacity:.62}}
 .phead{{display:flex;align-items:center;gap:9px;margin-bottom:5px}}
 .pname{{font-weight:700;font-size:14px}} .ploops{{color:var(--mut);font-size:11px;margin-left:auto}}
 .badge{{font-size:10px;font-weight:700;letter-spacing:.05em;padding:2px 7px;border-radius:5px;
        background:#2a2f40;color:#c8d0e4}}
 .badge.run{{background:#1d2c4d;color:#8fb2ff}} .badge.nxt{{background:#4a3a12;color:#ffd479}}
 .badge.mut{{background:#232735;color:var(--mut)}}
 .ppath{{color:var(--mut);font-size:11px;font-family:ui-monospace,Consolas,monospace;
        word-break:break-all;margin-bottom:5px}}
 .pdetail{{color:var(--mut);font-size:11px;margin-bottom:5px}}
 .pobj{{font-size:12px;color:#c3cadb;margin-bottom:7px;line-height:1.5}}
 .pmeta{{display:flex;gap:5px;flex-wrap:wrap;margin-bottom:7px}}
 .tag{{background:#1e2230;color:#a8b0c6;font-size:10px;padding:2px 6px;border-radius:4px;
      font-family:ui-monospace,Consolas,monospace}}
 .tag.ok{{background:#16281f;color:#7fd6ac}} .tag.warn{{background:#2c2413;color:#e0b96e}}
 .tag.rev{{background:#1d2438;color:#9db1ff}}
 .prog{{height:4px;background:#1e2230;border-radius:3px;overflow:hidden;margin-bottom:4px}}
 .pfill{{height:100%;background:linear-gradient(90deg,var(--acc),var(--ok));width:0}}
 table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--bd);
       border-radius:9px;overflow:hidden;font-size:12px}}
 th{{text-align:left;color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.05em;
    padding:8px 10px;border-bottom:1px solid var(--bd);background:#141722}}
 td{{padding:7px 10px;border-bottom:1px solid #1e2230;vertical-align:top}}
 tr:last-child td{{border-bottom:0}} tr.inv:hover{{background:#1a1e2b}}
 .pa{{color:var(--mut);font-size:11px;font-family:ui-monospace,Consolas,monospace}}
 .num{{text-align:right;font-variant-numeric:tabular-nums}}
 .run{{color:var(--acc);font-weight:600}} .mut{{color:var(--mut)}} .warn{{color:var(--warn)}}
 .ok2,.ok{{color:var(--ok)}} .bad{{color:var(--bad)}} .done{{color:var(--ok);font-weight:600}}
 .pin{{color:var(--ok);font-weight:700}} .nxt{{color:var(--warn)}}
 .alert{{background:#2a1d1d;border:1px solid #6b3535;border-left:4px solid var(--bad);
        border-radius:9px;padding:12px 14px;margin:12px 0}}
 .ah{{color:#ffb3b3;font-weight:700;margin-bottom:6px;font-size:12px}}
 .ab{{font-size:14px;line-height:1.5;margin-bottom:5px}} .ap{{color:var(--mut);font-size:11px}}
 .ok{{background:#16241d;border:1px solid #2c5a45;border-left:4px solid var(--ok);
     border-radius:9px;padding:10px 14px;margin:12px 0;color:#a8e6cf}}
 .planrow{{display:flex;gap:10px;align-items:center;padding:6px 10px;border:1px solid #232735;
          border-radius:7px;margin-bottom:5px;background:var(--card)}}
 .pwhen{{color:var(--warn);font-size:11px;font-weight:700;min-width:52px}}
 .bar{{position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--bd);
      z-index:9;margin-bottom:8px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
 input[type=text]{{background:#1c1f2b;border:1px solid #2e3346;color:var(--fg);
   padding:7px 10px;border-radius:6px;width:260px;font-size:12px}}
 select{{background:#1c1f2b;border:1px solid #2e3346;color:var(--fg);padding:7px 9px;
        border-radius:6px;font-size:12px}}
 .c{{color:var(--mut);font-size:11px}}
 .artbox{{background:var(--card);border:1px solid var(--bd);border-radius:9px;padding:10px 12px}}
 .afile{{display:flex;justify-content:space-between;gap:10px;align-items:center;
   padding:5px 8px;border-bottom:1px solid #1b1f2b;border-radius:5px}}
 .afile:last-child{{border-bottom:0}}
 .afile:hover{{background:#1a1e2b}}
 .aname{{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;color:#c3cadb}}
 code{{background:#0d0f16;padding:1px 4px;border-radius:3px;font-size:11px}}
</style>

<h1>Project Improver — Control Panel</h1>
<div class="sub">built {ts} · inventory: ai-project-index ({len(inv)} projects) ·
  rotation: improve.yaml ({len(pinned)} pinned / {len(parked)} parked)</div>

<div class="grid">
  <div class="kpi"><div class="v">{len(pinned)}</div><div class="k">pinned in loop</div></div>
  <div class="kpi"><div class="v">{len(inv)}</div><div class="k">total projects</div></div>
  <div class="kpi"><div class="v">{len(hist)}</div><div class="k">turns taken</div></div>
  <div class="kpi"><div class="v">{'1' if pend else '0'}</div><div class="k">waiting on you</div></div>
  <div class="kpi"><div class="v">{gate_pct}%</div><div class="k">have a gate</div></div>
  <div class="kpi"><div class="v">{len(answered)}</div><div class="k">answered</div></div>
</div>

{qhtml}

<div class="cols">
  <div class="col">
    <h2>Rotation — pinned projects</h2>
    {''.join(rot_rows) or '<div class="mut">nothing pinned yet — add to improve.yaml</div>'}

    <h2>Planned order</h2>
    {''.join(planned) or '<div class="mut">no rotation configured</div>'}

    <h2>Stage artifacts — evidence on disk</h2>
    <div class="artbox">
      {''.join(artifact_blocks) or '<div class="mut">no artifacts yet — S1 scout writes the first one</div>'}
    </div>
  </div>

  <div class="col">
    <h2>Rotation ledger — what has been done</h2>
    <table>
      <tr><th>When</th><th>Project</th><th>Card</th><th>Loops</th><th>Outcome</th><th>Note</th></tr>
      {''.join(led) or '<tr><td colspan=6 class="mut">no turns yet</td></tr>'}
    </table>
  </div>
</div>

<h2>Full inventory — {len(inv)} projects</h2>
<div class="bar">
  <input type="text" id="q" placeholder="filter name / path / category…">
  <select id="fc"><option value="">all categories</option>
    {''.join(f'<option value="{esc(c)}">{esc(c)} ({n})</option>' for c, n in cats.most_common())}
  </select>
  <select id="fs"><option value="">all statuses</option>
    {''.join(f'<option value="{esc(s)}">{esc(s)}</option>' for s in sorted({r.get("status") or "" for r in inv}) if s)}
  </select>
  <select id="fv"><option value="">show all</option>
    <option value="pin">only in rotation</option>
    <option value="git">only git repos</option>
    <option value="tested">only with tests</option>
    <option value="live">only existing on disk</option>
  </select>
  <span class="c" id="cnt"></span>
</div>
<table>
  <tr><th></th><th>Project</th><th>Category</th><th>Status</th><th>Size</th><th>Gate</th><th>Run</th></tr>
  {''.join(irows)}
</table>

<script>
function apply(){{
  const q=document.getElementById('q').value.toLowerCase();
  const fc=document.getElementById('fc').value;
  const fs=document.getElementById('fs').value;
  const fv=document.getElementById('fv').value;
  let shown=0;
  document.querySelectorAll('tr.inv').forEach(tr=>{{
    let ok = !q || tr.dataset.s.includes(q);
    if(ok && fc) ok = tr.dataset.cat===fc;
    if(ok && fs) ok = tr.dataset.status===fs;
    if(ok && fv==='pin') ok = tr.querySelector('.pin')!==null;
    if(ok && fv==='git') ok = !/no git/.test(tr.textContent);
    if(ok && fv==='tested') ok = tr.querySelector('.ok2,td.ok')!==null;
    if(ok && fv==='live') ok = !/gone/.test(tr.textContent);
    tr.style.display = ok?'':'none';
    if(ok) shown++;
  }});
  document.getElementById('cnt').textContent = shown+' of '+document.querySelectorAll('tr.inv').length+' shown';
}}
['q','fc','fs','fv'].forEach(id=>document.getElementById(id).addEventListener('input',apply));
apply();
</script>
"""
    OUT.write_text(doc, encoding="utf-8")
    return str(OUT)


if __name__ == "__main__":
    p = build()
    print(f"wrote {p}  ({Path(p).stat().st_size:,} bytes)")
    if "--open" in sys.argv:
        subprocess.run(["cmd", "/c", "start", "", str(Path(p).resolve())], check=False)