#!/usr/bin/env python3
"""
build_selector.py -- generate selector.html: the project/model/prompt picker.

Reads state/selector_data.json (+ discovery additions) and emits a single
self-contained HTML page. No server, no build step: open it, tick boxes, and
"Save rotation" writes state/loop.json via the plugin bridge (or a copy-out
JSON block when opened as a plain file).

The page is also what the Hermes desktop plugin embeds, so it must work both
(a) standalone in a browser and (b) inside the app frame.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
OUT = ROOT / "selector.html"

MODELS = [
    # label, provider:model  (from the fleet; used for the implementer seat)
    ("GLM-5.3-Flash-EXL3  (2x Spark)", "custom:glm53-flash-2x-spark:GLM-5.3-Flash-EXL3"),
    ("DeepSeek V4.1 Flash (3x Spark TP3)", "custom:deepseek-v41-flash-3x-spark:deepseek-v4.1-flash"),
    ("Qwen3.8-Flash-Next (e98b)", "custom:qwen38-flash-next:qwen3.8-flash-next"),
    ("Qwen3.8-27B MTP (Bosgame M5)", "custom:qwen38-27b-mtp:qwen3.8-27b-mtp"),
    ("Ternary Bonsai 2 27B (M5 ROCm)", "custom:bosgame-bonsai2:bonsai2"),
]
REVIEWERS = [
    ("DeepSeek V4.1 Flash (3x Spark TP3)", "custom:deepseek-v41-flash-3x-spark:deepseek-v4.1-flash"),
    ("GLM-5.3-Flash-EXL3  (2x Spark)", "custom:glm53-flash-2x-spark:GLM-5.3-Flash-EXL3"),
    ("Qwen3.8-Flash-Next (e98b)", "custom:qwen38-flash-next:qwen3.8-flash-next"),
]


def build() -> str:
    data_path = STATE / "selector_data.json"
    if not data_path.exists():
        raise SystemExit("run build_selector_data.py first (no selector_data.json)")
    data = json.loads(data_path.read_text(encoding="utf-8"))

    adds_path = STATE / "index_additions.json"
    adds = json.loads(adds_path.read_text(encoding="utf-8")) if adds_path.exists() else []

    disc_path = STATE / "discovery.json"
    disc = json.loads(disc_path.read_text(encoding="utf-8")) if disc_path.exists() else {}

    payload = {
        "data": data,
        "additions": adds,
        "discovery": {k: v for k, v in disc.items()
                      if k in ("runs", "last_run", "thread_scan_at", "sweep_at")},
        "models": MODELS,
        "reviewers": REVIEWERS,
        "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    html = TEMPLATE.replace("/*__PAYLOAD__*/", json.dumps(payload, separators=(",", ":")))
    OUT.write_text(html, encoding="utf-8")
    return str(OUT)


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Karpathy Loop — Project Selector</title>
<style>
  :root{
    --bg:#0b0f16; --panel:#121926; --panel2:#0e1520; --line:#1e2a3d; --line2:#2a3a52;
    --fg:#e8eef7; --mut:#8fa3bf; --acc:#4da3ff; --acc2:#7c5cff;
    --act:#2ecc8f; --dor:#f0b429; --arc:#7a8aa0; --bad:#ff6b6b;
    --radius:11px;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
    font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
  header{padding:26px 26px 18px;border-bottom:1px solid var(--line);
    background:linear-gradient(140deg,#0d1420,#131c2c 55%,#0d1420);
    position:sticky;top:0;z-index:30;backdrop-filter:blur(9px)}
  h1{margin:0 0 4px;font-size:22px;font-weight:650;letter-spacing:-.01em}
  h1 span{background:linear-gradient(92deg,var(--acc),var(--acc2));
    -webkit-background-clip:text;background-clip:text;color:transparent}
  .sub{color:var(--mut);font-size:12.5px}
  .stats{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
    padding:9px 13px;min-width:96px}
  .stat b{display:block;font-size:19px;color:var(--acc);font-weight:650;line-height:1.2}
  .stat i{color:var(--mut);font-style:normal;font-size:10.5px;text-transform:uppercase;
    letter-spacing:.06em}
  .wrap{padding:18px 26px 80px;max-width:1500px}
  .controls{display:flex;gap:9px;flex-wrap:wrap;align-items:center;
    padding:13px 0;border-bottom:1px solid var(--line);margin-bottom:16px}
  input[type=search],input[type=text],select{background:var(--panel2);color:var(--fg);
    border:1px solid var(--line);border-radius:8px;padding:7px 11px;font-size:13px;outline:none;
    font-family:inherit}
  input:focus,select:focus{border-color:var(--acc)}
  input[type=search]{min-width:280px}
  button{background:var(--panel2);border:1px solid var(--line);color:var(--fg);
    border-radius:8px;padding:7px 13px;font-size:13px;cursor:pointer;font-family:inherit;
    transition:.12s}
  button:hover{border-color:var(--acc);color:#fff}
  button.primary{background:linear-gradient(92deg,var(--acc),var(--acc2));border:none;
    color:#fff;font-weight:600}
  button.primary:hover{filter:brightness(1.12)}
  button.ghost{background:transparent}
  .seg{display:flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
  .seg button{border:none;border-radius:0;border-right:1px solid var(--line)}
  .seg button:last-child{border-right:none}
  .seg button.on{background:var(--acc);color:#fff}
  .tabs{display:flex;gap:4px;margin:16px 0 14px;border-bottom:1px solid var(--line)}
  .tab{padding:9px 15px;cursor:pointer;color:var(--mut);font-size:13.5px;
    border-bottom:2px solid transparent;margin-bottom:-1px;font-weight:500}
  .tab:hover{color:var(--fg)}
  .tab.on{color:var(--fg);border-bottom-color:var(--acc)}
  .tab .n{color:var(--mut);font-size:11px;margin-left:5px}
  .grid{display:grid;gap:11px;grid-template-columns:repeat(auto-fill,minmax(430px,1fr))}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
    padding:14px;cursor:pointer;transition:.13s;position:relative;overflow:hidden}
  .card:hover{border-color:var(--line2);transform:translateY(-1px)}
  .card.sel{border-color:var(--act);box-shadow:0 0 0 1px var(--act) inset}
  .card.sel::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--act)}
  .card h3{margin:0 0 5px;font-size:14.5px;font-weight:620;display:flex;
    align-items:center;gap:8px;flex-wrap:wrap}
  .card p{margin:0 0 9px;color:var(--mut);font-size:12.5px;line-height:1.55;
    display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
  .card p.open{-webkit-line-clamp:unset;display:block}
  .meta{display:flex;gap:6px;flex-wrap:wrap;font-size:11px;color:var(--mut);
    align-items:center;margin-top:7px}
  .tag{background:var(--panel2);border:1px solid var(--line);border-radius:999px;
    padding:2px 9px;font-size:10.5px;white-space:nowrap}
  .tag.act{color:var(--act);border-color:#1c4a38}
  .tag.dor{color:var(--dor);border-color:#4a3d16}
  .tag.arc{color:var(--arc);border-color:#2c3646}
  .tag.git{color:var(--acc);border-color:#1c3a57}
  .tag.no{color:var(--bad);border-color:#4a2222}
  .chk{width:17px;height:17px;border-radius:5px;border:1.5px solid var(--line2);
    display:grid;place-items:center;flex:0 0 auto;font-size:11px;color:transparent;
    transition:.12s}
  .card.sel .chk{background:var(--act);border-color:var(--act);color:#04140d}
  .path{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10.5px;
    color:#5f7290;word-break:break-all;margin-top:6px}
  .fam{margin:0 0 22px}
  .fam h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);
    margin:0 0 10px;padding-bottom:7px;border-bottom:1px solid var(--line);
    display:flex;align-items:center;gap:9px}
  .fam h2 .c{background:var(--panel2);border:1px solid var(--line);border-radius:999px;
    padding:1px 9px;font-size:10.5px;color:var(--mut);letter-spacing:0}
  .angle{background:var(--panel);border:1px solid var(--line);border-radius:10px;
    padding:12px 14px;margin-bottom:9px}
  .angle .top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
  .angle code{color:var(--acc);font-size:12.5px;background:var(--panel2);
    padding:2px 7px;border-radius:5px;border:1px solid var(--line)}
  .angle .lens{color:var(--fg);font-size:13px}
  .angle .detail{color:var(--mut);font-size:12px;margin-top:8px;line-height:1.55}
  .angle ul{margin:7px 0 0 0;padding-left:18px;color:var(--mut);font-size:12px}
  .angle li{margin:2px 0}
  .angle .ev{margin-top:9px;padding:8px 11px;background:var(--panel2);border-radius:8px;
    border-left:2px solid var(--acc2);font-size:12px;color:#b9c8de}
  .angle .ev b{color:var(--acc2);font-weight:600}
  .angle .avoid{margin-top:7px;font-size:11.5px;color:#6f8199;font-style:italic}
  .angle .usage{display:flex;gap:6px;margin-top:9px}
  .rowbar{display:flex;gap:14px;align-items:center;padding:11px 14px;background:var(--panel);
    border:1px solid var(--line);border-radius:9px;margin-bottom:9px;flex-wrap:wrap}
  .rowbar label{color:var(--mut);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
  .rowbar select{min-width:260px}
  .seat{display:flex;align-items:center;gap:9px}
  .warn{background:#2a1d10;border:1px solid #4a3517;color:#f0b429;border-radius:9px;
    padding:10px 14px;font-size:12.5px;margin-top:11px}
  .ok{background:#0e2a20;border:1px solid #1c4a38;color:var(--act);border-radius:9px;
    padding:10px 14px;font-size:12.5px;margin-top:11px}
  .empty{color:var(--mut);padding:40px;text-align:center;font-size:13px}
  /* Action bar lives at the TOP of the page (sticky) so Save/Start are always
     reachable without scrolling. It was a fixed bottom bar, which pushed the
     buttons off-screen inside the plugin iframe. */
  .save{position:sticky;top:0;z-index:40;display:flex;gap:12px;align-items:center;
    flex-wrap:wrap;margin:14px 0 0;padding:11px 0 11px;
    border-top:1px solid var(--line);border-bottom:1px solid var(--line);
    background:rgba(11,15,22,.94);backdrop-filter:blur(9px)}
  .save .n{color:var(--mut);font-size:12.5px}
  pre{background:var(--panel2);border:1px solid var(--line);border-radius:9px;
    padding:13px;overflow:auto;font-size:11.5px;max-height:340px;color:#b9c8de}
  .hidden{display:none}
  a{color:var(--acc)}
</style>
</head>
<body>
<header>
  <h1>Karpathy Loop <span>Project Selector</span></h1>
  <div class="sub">Pick what gets improved, who improves it, and which angles get used. Built from the AI-project inventory.</div>
  <div class="save" id="savebar">
    <button class="primary" id="save">Save rotation</button>
    <button class="ghost" id="start">Start loop</button>
    <button class="ghost" id="pause">Pause</button>
    <span class="n" id="saveMsg"></span>
    <span class="n" id="liveState"></span>
  </div>
  <div class="stats" id="stats"></div>
</header>

<div class="wrap">
  <div class="tabs" id="tabs">
    <div class="tab on" data-tab="projects">Projects <span class="n" id="nProj"></span></div>
    <div class="tab" data-tab="models">Models <span class="n">seats</span></div>
    <div class="tab" data-tab="angles">Angles <span class="n" id="nAng"></span></div>
    <div class="tab" data-tab="discovery">Discovery <span class="n" id="nDisc"></span></div>
  </div>

  <!-- ================= PROJECTS ================= -->
  <section id="tab-projects">
    <div class="controls">
      <input type="search" id="q" placeholder="Search name, purpose, path, category…">
      <select id="fStatus">
        <option value="">All statuses</option>
      </select>
      <select id="fCat"><option value="">All categories</option></select>
      <div class="seg" id="fRot">
        <button data-rot="all" class="on">All</button>
        <button data-rot="git">Git only</button>
        <button data-rot="selected">Selected</button>
        <button data-rot="active">Active</button>
      </div>
      <select id="sortBy">
        <option value="selected">Sort: selected first</option>
        <option value="last">Sort: most recently worked</option>
        <option value="commits">Sort: git commits</option>
        <option value="name">Sort: name A→Z</option>
        <option value="name-desc">Sort: name Z→A</option>
        <option value="size">Sort: folder size</option>
        <option value="files">Sort: file count</option>
        <option value="category">Sort: category</option>
        <option value="status">Sort: status</option>
        <option value="first">Sort: oldest first seen</option>
      </select>
      <div class="seg" id="view">
        <button data-v="cards" class="on">Cards</button>
        <button data-v="list">List</button>
      </div>
      <button class="ghost" id="clearSel">Clear selection</button>
      <button class="ghost" id="gitOnly">Select all git repos</button>
    </div>
    <div id="projNote"></div>
    <div class="grid" id="cards"></div>
    <div id="listView" class="hidden"></div>
  </section>

  <!-- ================= MODELS ================= -->
  <section id="tab-models" class="hidden">
    <div class="rowbar">
      <label>Implementer</label>
      <select id="mImpl"></select>
      <label>Reviewer</label>
      <select id="mRev"></select>
    </div>
    <div class="warn" id="seatWarn"></div>
    <div style="color:var(--mut);font-size:12.5px;margin-top:14px;line-height:1.7">
      <b style="color:var(--fg)">Why two seats.</b> The implementer writes the change; the reviewer
      reads the diff with a different brain. Same model in both seats means the same blind spot
      twice, so the save button refuses it.<br>
      <b style="color:var(--fg)">Routing doctrine.</b> Heavy reasoning on GLM, high-frequency
      volume on DeepSeek, parallel compute on the Qwen workers. Judge/rotator always stay on
      DeepSeek V4.1 Flash so a cloud model can never grade local work.
    </div>
  </section>

  <!-- ================= ANGLES ================= -->
  <section id="tab-angles" class="hidden">
    <div class="rowbar">
      <label>Angles per visit</label>
      <select id="apv">
        <option value="1">1 — move between projects fast</option>
        <option value="2" selected>2 — the default rhythm</option>
        <option value="3">3 — go deeper on fewer projects</option>
      </select>
      <label>Cooldown (rounds)</label>
      <select id="cool">
        <option value="4">4</option>
        <option value="8" selected>8</option>
        <option value="12">12</option>
        <option value="16">16</option>
      </select>
      <label>Retire after N failures</label>
      <select id="retire">
        <option value="0">never</option>
        <option value="4" selected>4</option>
        <option value="6">6</option>
      </select>
    </div>
    <div id="angles"></div>
  </section>

  <!-- ================= DISCOVERY ================= -->
  <section id="tab-discovery" class="hidden">
    <div class="rowbar">
      <label>Scheduled sweep</label>
      <select id="sweepFreq">
        <option value="60">Every hour</option>
        <option value="360" selected>Every 6 hours (4×/day)</option>
        <option value="720">Every 12 hours</option>
        <option value="1440">Once a day</option>
      </select>
      <button id="runDisc">Run discovery now</button>
    </div>
    <div id="discStats"></div>
    <h2 style="font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:var(--mut);
      margin:22px 0 10px;padding-bottom:7px;border-bottom:1px solid var(--line)">
      Threads not yet attached to a project</h2>
    <div style="color:var(--mut);font-size:12.5px;margin-bottom:12px">
      Each of these is a real session. A thread that names a codebase is attached to it;
      anything still standalone is its own workable item — you can pull it into rotation
      on its own.
    </div>
    <div class="grid" id="discCards"></div>
  </section>
</div>

<div class="save" id="bottombar" style="display:none"></div>

<script>
const PAYLOAD = /*__PAYLOAD__*/;
const D = PAYLOAD.data, ADD = PAYLOAD.additions || [];
const FAMILY_LABEL = {
  correctness:"Correctness", robustness:"Robustness", simplification:"Simplification",
  performance:"Performance", gameplay:"Gameplay", interface:"Interface",
  comprehension:"Comprehension", meta:"Meta / Adversarial"
};
const FAMILY_ORDER = ["correctness","robustness","simplification","performance",
                      "gameplay","interface","comprehension","meta"];

let sel = new Set(D.projects.filter(p=>p.selected).map(p=>p.path));
let rotFilter = "all", viewMode = "cards";
let cfg = Object.assign({angles_per_visit:2, cooldown_rounds:8,
                         retire_after_failures:4, projects:[]}, D.loop||{});

/* ---------------------------------------------------------------------------
   LIVE STATE RE-SYNC.
   selector_data.json bakes a SNAPSHOT of loop.json at BUILD time, but the page
   is rebuilt far less often than the config changes. Result: you save, disk
   updates, the page re-renders from its frozen bake, and your change looks
   lost. So we re-read the live config whenever the page opens.

   Two channels, because this page runs both standalone and inside the plugin:
     - inside the plugin: ask the parent for `loopctl status --json`
     - standalone: fetch state/loop.json next to this file
--------------------------------------------------------------------------- */
function applyCfg(live){
  if(!live || typeof live !== "object") return;
  cfg = Object.assign(cfg, live);
  const want = new Set((cfg.projects||[]).map(String));
  if(want.size){
    // Match on SLUG, not display name: the config stores "tombs-and-tyrants"
    // while the card is labelled "Tombs & Tyrants AI RPG".
    //
    // Several dead copies share a name stem with the real repo (the inventory
    // lists six "Tombs & Tyrants" rows), so prefer a GIT repo when more than
    // one row matches -- otherwise we tick a dead folder.
    const hits = D.projects.filter(p => p.is_git && (want.has(p.slug) || want.has(p.name)));
    sel = new Set(hits.map(p=>p.path));
    // Fall back to a non-git match only if no git row matched at all.
    if(!hits.length){
      sel = new Set(D.projects.filter(p => want.has(p.slug) || want.has(p.name)).map(p=>p.path));
    }
  }
  try{
    if(document.getElementById("apv")) document.getElementById("apv").value = cfg.angles_per_visit ?? 2;
    if(document.getElementById("cool")) document.getElementById("cool").value = cfg.cooldown_rounds ?? 8;
    if(document.getElementById("retire")) document.getElementById("retire").value = cfg.retire_after_failures ?? 4;
  }catch(e){}
  try{ render(); renderStats(); renderMsg(); }catch(e){}
  // Show what is actually on disk, so "did my save stick?" is answerable at a glance.
  try{
    const el = document.getElementById("liveState");
    if(el){
      const n = (cfg.projects||[]).length;
      el.textContent = "on disk: " + (n ? n + " project" + (n===1?"":"s") + " · " + cfg.projects.join(", ") : "no projects")
        + (cfg.running ? " · RUNNING" : " · paused");
    }
  }catch(e){}
}
async function resyncLive(){
  // 1. plugin bridge (authoritative when running inside Hermes desktop)
  try{
    if(window.parent && window.parent !== window){
      window.parent.postMessage(JSON.stringify({action:"load"}), "*");
      return;                    // parent replies with {live:{...}}
    }
  }catch(e){}
  // 2. standalone: read the file directly.
  //    fetch() cannot read file:// URLs (Chrome blocks the scheme), so when the
  //    page is opened by double-click we fall back to the build-time snapshot,
  //    which the builder now refreshes from live loop.json on every rebuild.
  try{
    const r = await fetch("state/loop.json?_=" + Date.now(), {cache:"no-store"});
    if(r.ok){ applyCfg(await r.json()); return; }
  }catch(e){}
  // 3. last resort: the snapshot baked into this page.
  if(D.loop) applyCfg(D.loop);
}

/* Standalone Save: when there is no parent to run commands, we cannot write
   state/loop.json from the browser. Say so plainly instead of implying success. */
function standaloneSave(msg){
  try{ localStorage.setItem("karpathy_cfg", msg); }catch(e){}
  try{ renderStats(); renderMsg(); }catch(e){}
  const n = (cfg.projects||[]).length;
  const el = document.getElementById("saveMsg");
  if(el){
    el.textContent = "Copied to clipboard — this page is open standalone, so use "
      + "\"Load saved config\" below to apply, or open it from the Karpathy Loop panel.";
  }
  blink("Standalone page: config copied, not written");
}

/* ---------------- stats ---------------- */
/* transient toast for rejected actions */
function blink(t){
  let b = document.getElementById("blinkbar");
  if(!b){
    b = document.createElement("div");
    b.id = "blinkbar";
    b.style.cssText = "position:fixed;left:50%;bottom:26px;transform:translateX(-50%);"+
      "background:var(--card);border:1px solid var(--border);color:var(--foreground);"+
      "padding:9px 16px;border-radius:8px;font-size:12.5px;z-index:99;opacity:0;"+
      "transition:opacity .18s;pointer-events:none;box-shadow:0 6px 22px rgba(0,0,0,.35)";
    document.body.appendChild(b);
  }
  b.textContent = t;
  b.style.opacity = "1";
  clearTimeout(blink._t);
  blink._t = setTimeout(()=>{ b.style.opacity = "0"; }, 1800);
}
function renderStats(){
  const C = D.counts;
  const rows = [
    [C.total, "projects indexed"],
    [C.git, "git repos (rollback-able)"],
    [sel.size, "in rotation"],
    [D.angle_count, "angles"],
    [ADD.filter(a=>a.kind==='thread').length, "threads found"],
    [ADD.filter(a=>a.kind==='codebase').length, "new codebases"],
  ];
  document.getElementById("stats").innerHTML = rows.map(
    r=>`<div class="stat"><b>${r[0]}</b><i>${r[1]}</i></div>`).join("");
  document.getElementById("nProj").textContent = C.total;
  document.getElementById("nAng").textContent = D.angle_count;
  document.getElementById("nDisc").textContent = ADD.length;
}

/* ---------------- filters ---------------- */
function fillSelects(){
  const st = document.getElementById("fStatus");
  Object.keys(D.counts.by_status).forEach(k=>{
    st.insertAdjacentHTML("beforeend", `<option value="${k}">${k} (${D.counts.by_status[k]})</option>`);
  });
  const ct = document.getElementById("fCat");
  Object.keys(D.counts.by_category).forEach(k=>{
    ct.insertAdjacentHTML("beforeend", `<option value="${k}">${k} (${D.counts.by_category[k]})</option>`);
  });
}

function sortProjects(list){
  const s = document.getElementById("sortBy").value;
  const by = {
    selected:(a,b)=>(b.selected?1:0)-(a.selected?1:0) || (b.git?.commits||0)-(a.git?.commits||0),
    last:(a,b)=>(b.last||"").localeCompare(a.last||""),
    commits:(a,b)=>(b.git?.commits||0)-(a.git?.commits||0),
    "name":(a,b)=>a.name.localeCompare(b.name),
    "name-desc":(a,b)=>b.name.localeCompare(a.name),
    size:(a,b)=>b.bytes-a.bytes,
    files:(a,b)=>b.files-a.files,
    category:(a,b)=>(a.category||"").localeCompare(b.category||"")||a.name.localeCompare(b.name),
    status:(a,b)=>(a.status||"").localeCompare(b.status||"")||a.name.localeCompare(b.name),
    first:(a,b)=>(a.first||"9999").localeCompare(b.first||"9999"),
  }[s];
  return list.slice().sort(by);
}

function filtered(){
  const q = document.getElementById("q").value.trim().toLowerCase();
  const st = document.getElementById("fStatus").value;
  const ct = document.getElementById("fCat").value;
  let list = D.projects.filter(p=>{
    if(st && p.status !== st) return false;
    if(ct && p.category !== ct) return false;
    if(rotFilter==="git" && !p.is_git) return false;
    if(rotFilter==="selected" && !sel.has(p.path)) return false;
    if(rotFilter==="active" && p.status!=="Active") return false;
    if(q){
      const hay = (p.name+" "+p.purpose+" "+p.path+" "+p.category).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });
  return sortProjects(list);
}

function tagFor(p){
  const m = {Active:"act", Dormant:"dor", Archive:"arc", Unknown:"arc"};
  return `<span class="tag ${m[p.status]||"arc"}">${p.status||"?"}</span>`;
}

/* ---------------- cards ---------------- */
function renderProjects(){
  const list = filtered();
  if(!list.length){ document.getElementById("cards").innerHTML =
    `<div class="empty">Nothing matches those filters.</div>`; return; }
  const cards = document.getElementById("cards");
  cards.innerHTML = list.map(p=>{
    const s = sel.has(p.path);
    const g = p.git||{};
    const bits = [];
    if(p.is_git) bits.push(`<span class="tag git">git · ${g.commits||0} commits${g.dirty?` · ${g.dirty} dirty`:""}</span>`);
    else if(p.exists) bits.push(`<span class="tag">no git</span>`);
    if(!p.exists) bits.push(`<span class="tag no">path missing</span>`);
    if(p.size_h) bits.push(`<span class="tag">${p.size_h}</span>`);
    if(p.files) bits.push(`<span class="tag">${p.files.toLocaleString()} files</span>`);
    if(p.last) bits.push(`<span class="tag">last ${p.last}</span>`);
    if(p.threads?.n) bits.push(`<span class="tag">${p.threads.n} threads</span>`);
    return `<div class="card ${s?"sel":""}" data-path="${encodeURIComponent(p.path)}">
      <h3><span class="chk">✓</span>${esc(p.name)}</h3>
      ${p.category?`<div style="font-size:11px;color:var(--acc);margin-bottom:6px">${esc(p.category)}</div>`:""}
      <p>${esc(p.purpose||"(no description captured)")}</p>
      <div class="meta">${tagFor(p)}${bits.join("")}</div>
      <div class="path">${esc(p.path)}</div>
    </div>`;
  }).join("");
  cards.querySelectorAll(".card").forEach(el=>{
    el.onclick = ev=>{
      const path = decodeURIComponent(el.dataset.path);
      if(ev.target.tagName==="P"){ ev.target.classList.toggle("open"); return; }
      // Only real git repos are loopable: the checkpoint engine needs git for
      // tags/rollback. Without this, a dead copy like 'D:\ZCODE\TOMBS & TYRANTS'
      // could be ticked and silently do nothing.
      const o = D.projects.find(x=>x.path===path);
      if(o && !o.is_git){ blink("Not a git repo — can't be looped"); return; }
      if(sel.has(path)) sel.delete(path); else sel.add(path);
      el.classList.toggle("sel"); renderStats(); renderMsg();
    };
  });
  document.getElementById("nProj").textContent = list.length;
}

function renderList(){
  const list = filtered();
  const el = document.getElementById("listView");
  el.innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:12.5px">
    <thead><tr style="text-align:left;color:var(--mut);border-bottom:1px solid var(--line)">
      <th style="padding:8px"></th><th style="padding:8px">Name</th>
      <th style="padding:8px">Status</th><th style="padding:8px">Category</th>
      <th style="padding:8px">Git</th><th style="padding:8px">Size</th>
      <th style="padding:8px">Last</th></tr></thead><tbody>
    ${list.map(p=>`<tr data-path="${encodeURIComponent(p.path)}"
        style="border-bottom:1px solid var(--line);cursor:pointer">
      <td style="padding:7px 8px"><input type="checkbox" ${sel.has(p.path)?"checked":""}
        style="pointer-events:none"></td>
      <td style="padding:7px 8px">${esc(p.name)}</td>
      <td style="padding:7px 8px">${tagFor(p)}</td>
      <td style="padding:7px 8px;color:var(--mut)">${esc(p.category)}</td>
      <td style="padding:7px 8px;color:var(--mut)">${p.is_git?`${p.git.commits||0} commits`:"—"}</td>
      <td style="padding:7px 8px;color:var(--mut)">${p.size_h||"—"}</td>
      <td style="padding:7px 8px;color:var(--mut)">${p.last||"—"}</td>
    </tr>`).join("")}
    </tbody></table>`;
  el.querySelectorAll("tr[data-path]").forEach(tr=>{
    tr.onclick = ()=>{
      const path = decodeURIComponent(tr.dataset.path);
      const o = D.projects.find(x=>x.path===path);
      if(o && !o.is_git){ blink("Not a git repo — can't be looped"); return; }
      if(sel.has(path)) sel.delete(path); else sel.add(path);
      renderList(); renderStats(); renderMsg();
    };
  });
}

/* ---------------- models ---------------- */
function fillModels(){
  const pairs = [["mImpl", PAYLOAD.models, 0], ["mRev", PAYLOAD.reviewers, 0]];
  pairs.forEach(([id, opts])=>{
    const el = document.getElementById(id);
    el.innerHTML = opts.map((o,i)=>`<option value="${o[1]}">${o[0]}</option>`).join("");
  });
  if(cfg.implementer) document.getElementById("mImpl").value = cfg.implementer;
  if(cfg.reviewer) document.getElementById("mRev").value = cfg.reviewer;
  ["mImpl","mRev"].forEach(id=>document.getElementById(id).onchange = seatWarn);
  seatWarn();
}
function seatWarn(){
  const i = document.getElementById("mImpl").value;
  const r = document.getElementById("mRev").value;
  const el = document.getElementById("seatWarn");
  if(i === r){
    el.className = "warn";
    el.textContent = "Same model in both seats — the reviewer would share the implementer's blind spot. Pick a different reviewer or Save will refuse.";
  } else {
    el.className = "ok";
    el.textContent = `Implementer ${short(i)} · Reviewer ${short(r)} — different families, good.`;
  }
}
function short(m){ return (m||"").split(":").pop(); }

/* ---------------- angles ---------------- */
function renderAngles(){
  const F = D.families;
  document.getElementById("angles").innerHTML = FAMILY_ORDER.filter(f=>F[f]).map(f=>{
    const items = F[f];
    return `<div class="fam"><h2>${FAMILY_LABEL[f]||f}<span class="c">${items.length}</span></h2>
      ${items.map(a=>`<div class="angle">
        <div class="top"><code>${a.id}</code><span class="lens">${esc(a.lens)}</span></div>
        ${a.look_for?.length?`<ul>${a.look_for.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>`:""}
        ${a.evidence?`<div class="ev"><b>Evidence required:</b> ${esc(a.evidence)}</div>`:""}
        ${a.avoid_when?`<div class="avoid">Skip when: ${esc(a.avoid_when)}</div>`:""}
        <div class="usage">
          <span class="tag">used ${a.used}</span>
          <span class="tag act">${a.accepted} accepted</span>
          ${a.failed?`<span class="tag dor">${a.failed} failed</span>`:""}
          ${a.retired?`<span class="tag no">retired</span>`:""}
        </div>
      </div>`).join("")}</div>`;
  }).join("");
}

/* ---------------- discovery ---------------- */
function renderDiscovery(){
  const ds = PAYLOAD.discovery||{};
  const th = ADD.filter(a=>a.kind==="thread");
  const cb = ADD.filter(a=>a.kind==="codebase");
  const attached = th.filter(a=>a.attached_to).length;
  document.getElementById("discStats").innerHTML = `
    <div class="stat"><b>${ds.runs||0}</b><i>discovery runs</i></div>
    <div class="stat"><b>${th.length}</b><i>threads found</i></div>
    <div class="stat"><b>${attached}</b><i>attached to a project</i></div>
    <div class="stat"><b>${th.length-attached}</b><i>standalone (workable)</i></div>
    <div class="stat"><b>${cb.length}</b><i>new codebases</i></div>
    <div class="stat"><b>${(ds.last_run||"never").slice(0,16).replace("T"," ")}</b><i>last run</i></div>`;
  const standalone = th.filter(a=>!a.attached_to).slice(0,60);
  document.getElementById("discCards").innerHTML = standalone.map(a=>`
    <div class="card"><h3><span class="chk">✓</span>${esc(a.slug)}</h3>
      <div class="meta"><span class="tag">${esc(a.agent)}</span>
      ${a.date?`<span class="tag">${esc(a.date)}</span>`:""}
      <span class="tag git">thread</span></div>
      <div class="path">${esc(a.path)}</div></div>`).join("")
    || `<div class="empty" id="emptyDisc">No standalone threads yet — run discovery.</div>`;
}

/* ---------------- save ---------------- */
function renderMsg(){
  document.getElementById("saveMsg").textContent =
    `${sel.size} project${sel.size===1?"":"s"} selected`;
}
function collect(){
  // The manifest keys off NAMES (tombs-and-tyrants), not paths. Selection is
  // tracked by path internally, so translate here -- sending paths produced
  // manifest_sync errors and let 'D:\ZCODE\TOMBS & TYRANTS' (a dead copy whose
  // '&' also breaks the shell) into the command line.
  const names = [...sel].map(p=>{
    const o = D.projects.find(x=>x.path===p);
    return o ? (o.slug || o.name) : p;
  });
  return {
    projects: names,
    angles_per_visit: +document.getElementById("apv").value,
    angles_cooldown: +document.getElementById("cool").value,
    retire_after_failures: +document.getElementById("retire").value,
    implementer: document.getElementById("mImpl").value,
    reviewer: document.getElementById("mRev").value,
    sweep_minutes: +document.getElementById("sweepFreq").value,
  };
}
async function callBackend(action, extra){
  // The Hermes desktop plugin bridge. Falls back to clipboard/localStorage
  // when this page is opened as a plain file.
  const payload = Object.assign({action}, collect(), extra||{});
  const msg = JSON.stringify(payload);
  try{
    // The picker runs inside an IFRAME in the plugin page, so it must use
    // postMessage: browsers block a child frame from calling parent.hermes
    // directly. The plugin listens for "message" events and runs shell commands.
    if(window.parent && window.parent !== window){
      window.parent.postMessage(msg, "*");
      return "sent to Hermes";
    }
    if(window.hermes?.send){ window.hermes.send(msg); return "sent to Hermes"; }
  }catch(e){}
  localStorage.setItem("karpathy_cfg", msg);
  try{ await navigator.clipboard.writeText(JSON.stringify(payload,null,2)); }
  catch(e){}
  return "copied";
}
document.getElementById("save").onclick = async ()=>{
  const i = document.getElementById("mImpl").value, r = document.getElementById("mRev").value;
  if(i===r){ alert("Implementer and reviewer must be different models."); return; }
  if(!sel.size){ alert("Select at least one project. Note: only git repos can be rolled back."); return; }
  const nonGit = [...sel].filter(p=>{ const o=D.projects.find(x=>x.path===p); return o && !o.is_git; });
  if(nonGit.length){ alert(`${nonGit.length} selected project(s) are not git repos — the loop can't checkpoint those, so I'd keep them out. Save anyway to include them.`); }
  const how = await callBackend("save");
  if(how === "sent to Hermes"){
    // The plugin replies with {action:"saved"} -> the message handler resyncs.
    document.getElementById("saveMsg").textContent = `Saved — ${sel.size} projects`;
  }else{
    // Standalone page: no shell access, so be honest rather than say "Saved".
    document.getElementById("saveMsg").textContent =
      `Not written — standalone page. ${sel.size} projects copied to clipboard.`;
    blink("Standalone page: nothing written to disk");
  }
};
document.getElementById("start").onclick = async ()=>{
  const how = await callBackend("start");
  document.getElementById("saveMsg").textContent = `Start requested (${how})`;
};
document.getElementById("pause").onclick = async ()=>{
  const how = await callBackend("pause");
  document.getElementById("saveMsg").textContent = `Pause requested (${how})`;
};
document.getElementById("runDisc").onclick = async ()=>{
  const how = await callBackend("discover");
  document.getElementById("saveMsg").textContent = `Discovery run requested (${how})`;
};

/* ---------------- wiring ---------------- */
document.getElementById("q").oninput = render;
document.getElementById("fStatus").onchange = render;
document.getElementById("fCat").onchange = render;
document.getElementById("sortBy").onchange = render;
document.getElementById("fRot").onclick = ev=>{
  if(ev.target.tagName!=="BUTTON") return;
  rotFilter = ev.target.dataset.rot;
  [...ev.currentTarget.children].forEach(b=>b.classList.toggle("on", b===ev.target));
  render();
};
document.getElementById("view").onclick = ev=>{
  if(ev.target.tagName!=="BUTTON") return;
  viewMode = ev.target.dataset.v;
  [...ev.currentTarget.children].forEach(b=>b.classList.toggle("on", b===ev.target));
  render();
};
document.getElementById("clearSel").onclick = ()=>{ sel.clear(); render(); renderStats(); renderMsg(); };
document.getElementById("gitOnly").onclick = ()=>{
  // Hand-picking 5-8 is the intended mode; blanket-selecting ~41 repos would
  // flood the rotation. Warn rather than silently overfill.
  const g = D.projects.filter(p=>p.is_git);
  if(g.length > 12){ blink(`That's ${g.length} repos — the loop wants 5-8 hand-picked`); }
  D.projects.forEach(p=>{ if(p.is_git) sel.add(p.path); });
  render(); renderStats(); renderMsg();
};
document.getElementById("tabs").onclick = ev=>{
  const t = ev.target.closest(".tab"); if(!t) return;
  [...document.querySelectorAll(".tab")].forEach(x=>x.classList.toggle("on", x===t));
  ["projects","models","angles","discovery"].forEach(n=>{
    document.getElementById("tab-"+n).classList.toggle("hidden", n!==t.dataset.tab);
  });
};
document.getElementById("apv").onchange = e=>cfg.angles_per_visit=+e.target.value;
document.getElementById("cool").onchange = e=>cfg.cooldown_rounds=+e.target.value;
document.getElementById("retire").onchange = e=>cfg.retire_after_failures=+e.target.value;

function render(){
  if(viewMode==="cards"){
    document.getElementById("cards").classList.remove("hidden");
    document.getElementById("listView").classList.add("hidden");
    renderProjects();
  } else {
    document.getElementById("cards").classList.add("hidden");
    document.getElementById("listView").classList.remove("hidden");
    renderList();
  }
}
function esc(s){ return String(s??"").replace(/[&<>"']/g,
  c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

/* the plugin replies to a {action:"load"} request with the live config */
window.addEventListener("message", ev=>{
  let m = ev.data;
  if(typeof m === "string"){ try{ m = JSON.parse(m); }catch(e){ return; } }
  if(m && m.live) applyCfg(m.live);
  if(m && m.action === "saved"){
    // The plugin has finished writing loop.json + improve.yaml: re-read so the
    // UI shows what is actually on disk (this is what made saves look lost).
    resyncLive();
    blink(m.ok === false ? ("Save failed: " + (m.error || "unknown")) : "Saved");
  }
});

fillSelects(); fillModels(); renderAngles(); renderDiscovery();
renderStats(); render(); renderMsg();
resyncLive();          /* pull live state over the baked-in snapshot */
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print("wrote", build())