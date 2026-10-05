#!/usr/bin/env python
"""
angles.py -- widget panel API for the angle settings screen.

  angles.py all  --json   -> {"families": {family: [angle,...]}, "total": N}   (read)
  angles.py save --json   -> reads {"edits":[angle,...]} on STDIN, saves, returns summary

Panel scripts must emit PURE JSON and stay small; family grouping is done here so the
widget renders 8 collapsible groups directly.
"""
from __future__ import annotations
import json, os, sys, base64
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import angles_store  # noqa: E402



VIEW_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Karpathy angle library</title>
<style>
  /* OPAQUE by design: this page runs in an iframe and paints nothing behind itself,
     so translucency made it unreadable (Gene: "blending in with the background").
     Palette matches the picker page so both modals look like one system. */
  :root {
    color-scheme: dark;
    --bg: #0b0f16; --panel: #121926; --panel2: #0e1520; --line: #1e2a3d; --line2: #2a3a52;
    --fg: #e8eef7; --mut: #8fa3bf; --acc: #4da3ff;
  }
  html, body { background: var(--bg); }
  body { font: 13px/1.45 -apple-system, "Segoe UI", sans-serif; margin: 0;
         padding: 14px 18px 40px; color: var(--fg); }
  h1 { font-size: 15px; margin: 0 0 4px; color: var(--fg); }
  .sub { color: var(--mut); font-size: 12px; margin-bottom: 14px; }
  .fam { border: 1px solid var(--line); border-radius: 8px; margin-bottom: 10px;
         overflow: hidden; background: var(--panel); }
  .famh { padding: 8px 12px; cursor: pointer; font-weight: 700; display: flex; gap: 8px;
          align-items: baseline; background: var(--panel2); color: var(--fg); }
  .famh .cnt { color: var(--mut); font-weight: 400; font-size: 12px; }
  .famb { display: none; }
  .fam.open .famb { display: block; }
  .ang { border-top: 1px solid var(--line); background: var(--panel); }
  .angh { padding: 7px 12px 7px 26px; cursor: pointer; display: flex; gap: 8px;
          align-items: baseline; }
  .angh:hover { background: var(--panel2); }
  .angh .id { font-weight: 600; min-width: 170px; color: var(--fg); }
  .angh .lens { color: var(--mut); font-size: 12px; flex: 1; white-space: nowrap;
                overflow: hidden; text-overflow: ellipsis; }
  .chip { font-size: 10px; color: #f0b429; border: 1px solid #f0b429; border-radius: 8px;
          padding: 0 6px; }
  .detail { display: none; padding: 4px 14px 14px 40px; background: var(--panel); }
  .ang.open .detail { display: block; }
  label { display: block; font-size: 11px; color: var(--mut); margin: 9px 0 3px; }
  textarea { width: 100%; box-sizing: border-box; font: 12.5px/1.45 ui-monospace, monospace;
             padding: 6px 8px; border-radius: 6px; border: 1px solid var(--line2);
             background: var(--panel2); color: var(--fg); resize: vertical; }
  textarea:focus { outline: 1px solid var(--acc); border-color: var(--acc); }
  .row { display: flex; gap: 8px; align-items: center; margin-top: 10px; }
  button { font: inherit; padding: 5px 12px; border-radius: 6px; border: 1px solid var(--line2);
           background: #0a66d0; color: #fff; cursor: pointer; }
  button:hover { background: #0b74e8; }
  button.ghost { background: transparent; color: var(--fg); }
  button.ghost:hover { background: var(--panel2); }
  .msg { font-size: 12px; }
  .ok { color: #2ecc8f; } .err { color: #ff6b6b; }
</style></head><body>
<h1>Angle library — 8 families, every working prompt</h1>
<div class="sub">edits take effect on the next picked round · saved per angle</div>
<div id="root"></div>
<script>
const DATA = __DATA__;
const root = document.getElementById("root");
const famNames = Object.keys(DATA.families);
famNames.forEach(function(fn){
  const fam = document.createElement("div"); fam.className = "fam";
  const h = document.createElement("div"); h.className = "famh";
  h.innerHTML = '<span>▸</span><span>' + fn + '</span><span class="cnt">' + DATA.families[fn].length + ' angles</span>';
  h.onclick = function(){ fam.classList.toggle("open"); const c = h.firstChild; c.textContent = fam.classList.contains("open") ? "▾" : "▸"; };
  const body = document.createElement("div"); body.className = "famb";
  DATA.families[fn].forEach(function(a){
    const wrap = document.createElement("div"); wrap.className = "ang";
    const ah = document.createElement("div"); ah.className = "angh";
    ah.innerHTML = '<span class="id">' + a.id + '</span><span class="lens">' + (a.lens||"") + '</span>' +
                   (a.edited ? '<span class="chip">edited</span>' : '');
    ah.onclick = function(){ wrap.classList.toggle("open"); };
    const det = document.createElement("div"); det.className = "detail";
    const cur = function(k){ const v = a[k]; return Array.isArray(v) ? v.join("\\n") : (v||""); };
    det.appendChild(mkField("Lens (the one-line instruction)", "lens", cur("lens"), 2));
    det.appendChild(mkField("Look for (one per line)", "look_for", cur("look_for"), 5));
    det.appendChild(mkField("Evidence required", "evidence", cur("evidence"), 2));
    det.appendChild(mkField("Avoid when", "avoid_when", cur("avoid_when"), 2));
    const row = document.createElement("div"); row.className = "row";
    const b1 = document.createElement("button"); b1.textContent = "Save angle";
    b1.onclick = function(){ save(a, false, b1); };
    const b2 = document.createElement("button"); b2.className = "ghost"; b2.textContent = "Reset to default";
    b2.onclick = function(){ save(a, true, b2); };
    const msg = document.createElement("span"); msg.className = "msg";
    row.appendChild(b1); row.appendChild(b2); row.appendChild(msg);
    det.appendChild(row);
    wrap.appendChild(ah); wrap.appendChild(det); body.appendChild(wrap);
  });
  fam.appendChild(h); fam.appendChild(body); root.appendChild(fam);
});
function mkField(label, key, val, rows){
  const lab = document.createElement("label"); lab.textContent = label;
  const ta = document.createElement("textarea"); ta.rows = rows; ta.value = val; ta.dataset.k = key;
  lab.appendChild(ta);
  const wrap = document.createElement("div"); wrap.appendChild(lab); wrap.appendChild(ta);
  wrap.dataset.field = key;
  return wrap;
}
function collect(a){
  const det = Array.prototype.find.call(document.querySelectorAll(".ang.open .detail"),
    function(d){ return d.parentNode.querySelector(".id").textContent === a.id; });
  const out = { id: a.id };
  det.querySelectorAll("[data-field]").forEach(function(w){
    const k = w.dataset.field, v = w.querySelector("textarea").value;
    out[k] = (k === "look_for") ? v.split("\\n").map(function(x){return x.trim();}).filter(Boolean) : v.trim();
  });
  return out;
}
function save(a, reset, btn){
  const row = btn.parentNode; const msg = row.querySelector(".msg");
  msg.className = "msg"; msg.textContent = "saving…";
  const edit = reset ? { id: a.id, lens: "", look_for: [], evidence: "", avoid_when: "" } : collect(a);
  parent.postMessage({ source: "karpathy-angles", action: reset ? "reset-angle" : "save-angle", edit: edit }, "*");
  const t0 = Date.now();
  const iv = setInterval(function(){
    const st = (window.__lastSave && window.__lastSave.id === a.id) ? window.__lastSave : null;
    if (st) { clearInterval(iv);
      msg.className = "msg " + (st.ok ? "ok" : "err");
      msg.textContent = st.ok ? ("saved: " + (st.changed || "no changes")) : ("failed: " + (st.error || "unknown"));
    } else if (Date.now() - t0 > 60000) { clearInterval(iv); msg.className = "msg err"; msg.textContent = "timed out"; }
  }, 200);
}
window.addEventListener("message", function(ev){
  let m = ev.data; if (typeof m === "string") { try { m = JSON.parse(m); } catch(e){ return; } }
  if (m && m.source === "karpathy-angles" && m.id) window.__lastSave = m;
});
parent.postMessage({ source: "karpathy-angles", action: "ready" }, "*");
</script></body></html>"""


def write_view() -> dict:
    """Write the self-contained editor page; return a tiny JSON the bridge can carry."""
    fams = angles_store.families()
    html = (VIEW_TEMPLATE
            .replace("__DATA__", json.dumps({"families": fams, "total": sum(len(v) for v in fams.values())})))
    out = ROOT / "state" / "angles_view.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    # atomic: the plugin re-renders this page on every heartbeat tick while the modal is
    # open, and the iframe may be mid-read -- never let it see a half-written file.
    tmp = out.with_suffix(".html.tmp")
    tmp.write_text(html, encoding="utf-8")
    os.replace(str(tmp), str(out))
    return {"ok": True, "path": str(out), "total": sum(len(v) for v in fams.values())}


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode == "view":
        print(json.dumps(write_view()))
        return 0
    if mode == "save":
        # The desktop bridge (shell.exec) has no stdin: edits arrive as --edits <json>,
        # URL-encoded by the widget (quotes/braces would break cmd.exe quoting).
        raw = ""
        if "--edits-b64" in sys.argv:
            # Preferred: base64 survives cmd.exe (no %, quotes or braces).
            i = sys.argv.index("--edits-b64")
            if i + 1 < len(sys.argv):
                try:
                    raw = base64.b64decode(sys.argv[i + 1]).decode("utf-8")
                except Exception as e:
                    print(json.dumps({"error": "bad base64 edits: %s" % e})); return 1
        if not raw and "--edits" in sys.argv:
            i = sys.argv.index("--edits")
            if i + 1 < len(sys.argv):
                raw = sys.argv[i + 1]
        if not raw:
            raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        if not raw:
            print(json.dumps({"error": "no edits given (--edits or stdin)"})); return 1
        try:
            import urllib.parse
            if "%" in raw and "{" not in raw[:1]:
                raw = urllib.parse.unquote(raw)
            payload = json.loads(raw)
        except Exception as e:
            print(json.dumps({"error": "bad edits json: %s" % e})); return 1
        res = angles_store.save_edits(payload.get("edits") or [])
        print(json.dumps(res))
        return 0
    fams = angles_store.families()
    print(json.dumps({
        "families": fams,
        "total": sum(len(v) for v in fams.values()),
        "edited": sum(1 for v in fams.values() for a in v if a.get("edited")),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
