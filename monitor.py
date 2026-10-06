#!/usr/bin/env python
"""
karpathy-loop / monitor.py

Generates monitor.html — the "special area" you watch. Reads real state:
  state/rotation.json   rotation position + turn history
  questions/pending.json the open question (if any)
  logs/notify.log       recent messages
  kanban boards         live card status per project

Run standalone or let the cron task regenerate it each tick.
"""
from __future__ import annotations

import html
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "monitor.html"
# Recent activity follows the LIVE runner feed (notify.log is only
# written when Discord notifications fire -- runner.log always moves).
LOG = ROOT / "logs" / "runner.log"


def jload(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _pid_alive(pid) -> bool:
    """Is a PID a live process? The in-flight timer must not tick on a
    stale heartbeat from a dead runner."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)
        if not h:
            return False
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    except Exception:
        return False


def manifest() -> dict:
    try:
        import yaml
        return yaml.safe_load((ROOT / "improve.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def board_status(board: str) -> list[dict]:
    """Reuse improver's CLI wrapper so there's one code path."""
    try:
        import improver
        return improver.list_tasks(board)
    except Exception:
        return []


def recent_log(n: int = 14) -> list[str]:
    if not LOG.exists():
        return []
    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:][::-1]


def build() -> str:
    m = manifest()
    projs = [p for p in (m.get("projects") or []) if p.get("enabled")]
    allp = m.get("projects") or []
    loop = m.get("loop") or {}
    rot = jload(ROOT / "state" / "rotation.json", {"idx": 0, "history": []})
    pend = jload(ROOT / "questions" / "pending.json", None)
    answered = sorted((ROOT / "questions" / "answered").glob("*.json")) \
        if (ROOT / "questions" / "answered").exists() else []
    parked = sorted((ROOT / "questions" / "parked.json").parent.glob("parked.json"))

    # T3-9 (2026-09-30): rotation.json is the RETIRED kanban-engine file --
    # the thread runner never writes it, so `idx` here was a fiction (the
    # panel's "next up" did not track reality). The LIVE selector is the
    # threads.json LRU: least-recently-nudged enabled project next.
    try:
        reg = json.loads((ROOT / "state" / "threads.json")
                         .read_text(encoding="utf-8")) or {}
    except Exception:
        reg = {}
    def _lru_key(p):
        e = reg.get(p.get("name") or "", {}) or {}
        return e.get("last_nudge") or 0
    quar = {n for n, e in reg.items() if isinstance(e, dict) and e.get("quarantined")}

    # Round timing (the operator, 2026-10-01): live elapsed for the round in flight
    # (heartbeat project + the round's angle-start stamp) and the per-repo
    # average the runner logs into round_seconds_hist at each round end.
    try:
        _hb = json.loads((ROOT / "state" / "runner_heartbeat.json")
                         .read_text(encoding="utf-8")) or {}
    except Exception:
        _hb = {}
    _now = time.time()
    def _fmt_mmss(secs):
        try:
            s = int(secs)
        except (TypeError, ValueError):
            return "-"
        if s < 0:
            return "-"
        if s < 60:
            return "%ds" % s
        if s < 3600:
            return "%dm" % (s // 60)
        return "%dh%02dm" % (s // 3600, (s % 3600) // 60)
    def _round_cell(pname):
        avg, nh, last, infl = _timing(pname)
        if infl is not None:
            num = (((reg.get(pname) or {}).get("rounds")) or 0) + 1
            return f'<span class="run" title="round in flight">{num} · running {_fmt_mmss(infl)}</span>'
        if last is not None:
            num = ((reg.get(pname) or {}).get("rounds")) or 0
            return f'<span class="mut" title="last round duration">{num} · last {_fmt_mmss(last)}</span>'
        num = ((reg.get(pname) or {}).get("rounds")) or 0
        return f'<span class="mut">{num}</span>'

    def _avg_cell(pname):
        avg, nh, last, infl = _timing(pname)
        if avg is None:
            return '<span class="mut">—</span>'
        return f'{_fmt_mmss(avg)} <span class="pa">(n{nh})</span>'

    def _timing(pname):
        e = reg.get(pname) or {}
        hist = [s for s in (e.get("round_seconds_hist") or [])
                if isinstance(s, (int, float)) and 0 <= s <= 200000]
        avg = (int(round(sum(hist) / len(hist))) if hist else None)
        last = e.get("last_round_seconds")
        infl = None
        if _hb.get("state") == "round-started" and _hb.get("project") == pname \
                and _pid_alive(_hb.get("pid")):
            st = (e.get("current_angle") or {}).get("started")
            if isinstance(st, (int, float)) and st > 0:
                infl = max(0, int(_now - st))
        return avg, (len(hist) or None), last, infl
    runnable = [p for p in projs if p.get("name") not in quar]
    nxt = (min(runnable, key=_lru_key)["name"] if runnable
           else ("ALL QUARANTINED" if projs else "—"))

    # -------- in-flight round KPI
    infl_proj, infl_secs, infl_round = None, None, None
    for p2 in allp:
        avg2, n2, l2, infl2 = _timing(p2["name"])
        if infl2 is not None:
            infl_proj, infl_secs = p2["name"], infl2
            infl_round = ((reg.get(p2["name"]) or {}).get("rounds") or 0) + 1
    infl_kpi = (f'  <div class="kpi"><div class="v run" id="live-elapsed">{_fmt_mmss(infl_secs)}'
                f'</div><div class="k">current round · {html.escape(str(infl_proj))} r{infl_round}</div></div>'
                if infl_secs is not None else '')

    # Live-data payload: the page re-fetches every 30s but the TIMER ticks
    # client-side every second from the embedded start epoch (the file only
    # changes when monitor.py regenerates it -- a static reload can never
    # move a timer). the operator, 2026-10-01: "elapsed time didn't move".
    _live = {"now": int(_now),
             "inflight": ({"project": infl_proj,
                           "round": infl_round,
                           "started": int(((reg.get(infl_proj) or {})
                                           .get("current_angle") or {})
                                          .get("started") or 0)}
                          if infl_secs is not None else None)}
    _live_json = json.dumps(_live)

    # -------- open question banner
    if pend:
        waited = int(time.time() - pend.get("asked_at", time.time()))
        q_banner = f"""
    <div class="alert">
      <div class="alert-h">⏰ OPEN QUESTION — waiting {waited//60}m {waited%60}s
        · reminders {pend.get('reminders',0)}/{pend.get('max_repeat',20)}</div>
      <div class="alert-b">{html.escape(pend.get('question',''))}</div>
      <div class="alert-p">project <b>{html.escape(pend.get('project',''))}</b>
        · reply in the Discord channel to answer</div>
    </div>"""
    else:
        q_banner = '<div class="ok">No open question — the loop is unblocked.</div>'

    # -------- project rows
    rows = []
    for p in allp:
        tasks = board_status(p["board"]) if p.get("enabled") else []
        running = [t for t in tasks
                   if str(t.get("status", "")).lower() in {"running", "ready", "todo", "blocked", "review"}]
        quar_e = reg.get(p["name"]) or {}
        if quar_e.get("quarantined"):
            # F-L (2026-10-02): a quarantined repo rendered as plain "idle" --
            # the operator could not see WHY it was excluded from rotation.
            state = "QUARANTINED — " + str(quar_e.get("quarantine_reason") or "unspecified")[:80]
            cls = "bad"
        elif not p.get("enabled"):
            state, cls = "parked", "mut"
        elif running:
            st = str(running[0].get("status", "")).lower()
            state, cls = (f"{st} ({running[0].get('id','')})", "run")
        elif (reg.get(p["name"]) or {}).get("session_id"):
            # Thread engine (2026-10-01): the loop is session-based, not
            # kanban-card-based. "no card open" was retired-engine text that
            # rendered for every idle repo and read as a stuck state.
            state, cls = "idle — no round in flight", "mut"
        else:
            state, cls = "idle — no round in flight", "mut"
        rows.append(f"""<tr>
  <td><b>{html.escape(p['name'])}</b><div class="pa">{html.escape(p['path'])}</div></td>
  <td>{'✓' if p.get('gate') else '<span class="warn">none</span>'}</td>
  <td class="num">{p.get('loops', 5)}</td>
  <td class="{cls}">{html.escape(state)}</td>
  <td class="num">{_round_cell(p['name'])}</td>
  <td class="num mut">{_avg_cell(p['name'])}</td>
  <td class="pa">{html.escape(str(p.get('implementer','')).split(':')[-1])}</td>
  <td class="pa">{html.escape(str(p.get('reviewer','')).split(':')[-1])}</td>
</tr>""")

    # the history rows are the retired engine's record; label them as such
    hist = []
    for h in reversed(rot.get("history", [])[-12:]):
        ts = time.strftime("%m-%d %H:%M", time.localtime(h["at"]))
        hist.append(f"<tr><td class='pa'>{ts}</td><td>{html.escape(h['project'])}</td>"
                    f"<td class='pa'>{html.escape(str(h.get('task_id')))}</td>"
                    f"<td class='num'>{h.get('loops')}</td></tr>")

    logpaths = []
    for l in recent_log():
        m2 = re.match(r"^\[([^\]]+)\]\s*(.*)$", l)
        if m2:
            logpaths.append(
                f'<div class="lg"><span class="pa">{html.escape(m2.group(1))}</span> '
                f'{html.escape(m2.group(2))}</div>')
        else:
            logpaths.append(f'<div class="lg">{html.escape(l)}</div>')
    loglines = "".join(logpaths)

    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    doc = f"""<!doctype html><meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<title>Project Improver — monitor</title>
<style>
 :root{{--fg:#e8ecf7;--mut:#7d879f;--acc:#5b7cfa;--ok:#4ec9a0;--warn:#e0a94e;--bad:#e06c75;--bd:#262a38;--card:#171a24}}
 body{{background:#12141c;color:var(--fg);font:13px/1.55 -apple-system,Segoe UI,sans-serif;margin:0;padding:22px}}
 h1{{font-size:19px;margin:0}} h2{{font-size:13px;text-transform:uppercase;letter-spacing:.07em;color:#9db1ff;margin:26px 0 8px}}
 .sub{{color:var(--mut);margin:4px 0 16px;font-size:12px}}
 .grid{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:6px}}
 .kpi{{background:var(--card);border:1px solid var(--bd);border-radius:9px;padding:10px 14px;min-width:120px}}
 .kpi .v{{font-size:19px;font-weight:700}} .kpi .k{{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
 table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--bd);border-radius:9px;overflow:hidden}}
 th{{text-align:left;color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.05em;padding:9px 11px;border-bottom:1px solid var(--bd)}}
 td{{padding:9px 11px;border-bottom:1px solid #1e2230;vertical-align:top}}
 tr:last-child td{{border-bottom:0}}
 .pa{{color:var(--mut);font-size:11px;font-family:ui-monospace,Consolas,monospace}}
 .num{{text-align:right;font-variant-numeric:tabular-nums}}
 .run{{color:var(--acc);font-weight:600}} .mut{{color:var(--mut)}} .warn{{color:var(--warn)}}
 .alert{{background:#2a1d1d;border:1px solid #6b3535;border-left:4px solid var(--bad);border-radius:9px;padding:13px 15px;margin:14px 0}}
 .alert-h{{color:#ffb3b3;font-weight:700;margin-bottom:7px}}
 .alert-b{{font-size:15px;line-height:1.5;margin-bottom:6px}}
 .alert-p{{color:var(--mut);font-size:11px}}
 .ok{{background:#16241d;border:1px solid #2c5a45;border-left:4px solid var(--ok);border-radius:9px;padding:11px 15px;margin:14px 0;color:#a8e6cf}}
 .lg{{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:#c3cadb;padding:3px 0;border-bottom:1px solid #1b1f2b}}
 code{{background:#0d0f16;padding:1px 5px;border-radius:4px}}
</style>
<h1>Project Improver</h1>
<div class="sub">rotation monitor · refreshed {ts} · auto-reload 30s</div>

<div class="grid">
  <div class="kpi"><div class="v">{len(projs)}</div><div class="k">enabled</div></div>
  {infl_kpi}
  <div class="kpi"><div class="v">{sum(int((e or {}).get('rounds') or 0) for e in reg.values())}</div><div class="k">turns taken</div></div>
  <div class="kpi"><div class="v">{'Q' if pend else '—'}</div><div class="k">open question</div></div>
  <div class="kpi"><div class="v">{len(answered)}</div><div class="k">answered</div></div>
  <div class="kpi"><div class="v">{html.escape(nxt)}</div><div class="k">next up</div></div>
  <div class="kpi"><div class="v">{loop.get('ask_repeat_min', 5)}m × {loop.get('ask_max_repeat', 20)}</div><div class="k">ping policy</div></div>
</div>

{q_banner}

<h2>Queue — rotation order</h2>
<table><tr><th>Project</th><th>Gate</th><th>Loops</th><th>State</th><th>Round</th><th>Avg/round</th><th>Implementer</th><th>Reviewer</th></tr>
{''.join(rows) or '<tr><td colspan=8 class=mut>no projects in improve.yaml</td></tr>'}
</table>

<h2>Turn history <span class="pa">(legacy kanban engine, pre-2026-09-23 — live rounds are in the queue table above)</span></h2>
<table><tr><th>When</th><th>Project</th><th>Task</th><th>Loops</th></tr>
{''.join(hist) or '<tr><td colspan=4 class=mut>no turns yet — the loop has not started</td></tr>'}
</table>

<h2>Recent activity</h2>
<div style="background:var(--card);border:1px solid var(--bd);border-radius:9px;padding:11px 13px;max-height:300px;overflow:auto">
{loglines or '<div class="mut">no log entries yet</div>'}
</div>

<script>
// Client-side round timer: ticks every second from the embedded start
// epoch so the elapsed time MOVES between page regenerations.
(function() {{
  const live = {json.dumps(_live)};
  const fmt = (s) => {{
    if (s < 0) return '-';
    if (s < 60) return s + 's';
    if (s < 3600) return Math.floor(s / 60) + 'm';
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    return h + 'h' + String(m).padStart(2, '0') + 'm';
  }};
  const tick = () => {{
    if (!live.inflight) return;
    const el = Math.max(0, Math.floor(Date.now() / 1000) - live.inflight.started);
    const v = document.getElementById('live-elapsed');
    if (v) v.textContent = fmt(el);
  }};
  tick();
  setInterval(tick, 1000);
}})();
</script>
"""
    OUT.write_text(doc, encoding="utf-8")
    return str(OUT)


if __name__ == "__main__":
    p = build()
    print(f"wrote {p}  ({Path(p).stat().st_size:,} bytes)")