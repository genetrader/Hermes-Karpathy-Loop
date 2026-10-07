// Karpathy Loop — a Hermes desktop plugin.
//
// Adds "Karpathy Loop" to the sidebar (next to Artifacts / Scheduled / RSS)
// with a page that:
//   * shows live loop status (running|paused, projects in rotation, rounds)
//   * drives the loop from inside the app: Save rotation / Start / Pause /
//     Run discovery / Refresh
//   * embeds the project picker UI (selector.html) built by the Python side
//
// Data path: the loop's own localhost status server
//   (scripts/status_server.py) answers GET /meta.json with {root, python,
//   status_port}, so NOTHING about the operator's machine is baked into this
//   file. The card edits the loop's settings through the same server
//   (PUT /settings.json / POST /settings/secret). Shell fallback goes
//   through the same bridge the RSS plugin uses --
//   host.requestProfile(route, "shell.exec", { command })
// -- so we shell out to the Python tools in the loop root and read their
// JSON. If the server was never reachable on this machine, the page says so
// with the exact fix; it never silently falls back to someone else's paths.
//
// NOTE: disk plugins load precompiled. No raw JSX here; build elements with
// jsx()/jsxs() from react/jsx-runtime (see the hermes-rss plugin for the shape).

import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Fragment, jsx, jsxs } from "react/jsx-runtime";
import {
  Button,
  host,
  useValue,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  PALETTE_AREA,
} from "@hermes/plugin-sdk";
// The model catalog menu is the REAL Hermes picker (searchable,
// provider-grouped -- the composer's own). It exists in newer SDK builds;
// import defensively so older installs still load the card (the seat falls
// back to a typed provider:model string).
import * as HermesSDK from "@hermes/plugin-sdk";
// The catalog menu renders MenuItems and MUST sit inside a DropdownMenu +
// DropdownMenuContent (the same wrap core's composer and the kanban plugin
// use); rendering it bare throws "'MenuItem' must be used within 'Menu'".
const ModelCatalogMenu = HermesSDK.ModelCatalogMenu || null;
const DropdownMenu = HermesSDK.DropdownMenu || null;
const DropdownMenuTrigger = HermesSDK.DropdownMenuTrigger || null;
const DropdownMenuContent = HermesSDK.DropdownMenuContent || null;

var ID = "karpathy-loop";

// ---------------------------------------------------------------- server meta
// The ONE place root/python/port come from: the loop's own status server
// (GET /meta.json), cached in localStorage so a temporarily-down server
// still lets the shell.exec fallback work with what we learned last time.
// There is deliberately NO hardcoded author path below this line.

function lsGet(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } }
function lsSet(k, v) { try { window.localStorage.setItem(k, v); } catch (e) {} }

var SERVER_BASE = lsGet("kl.server.base.v1") || "http://127.0.0.1:8765";
var META = null;          // {root, python, status_port} once loaded
var META_AT = 0;          // epoch-ms of last meta attempt (retry gate)

function resetServerBase(base) {
  SERVER_BASE = String(base || "").replace(/\/+$/, "") || "http://127.0.0.1:8765";
  lsSet("kl.server.base.v1", SERVER_BASE);
  META = null; META_AT = 0;
  try { delete CACHE["settings"]; } catch (e) {}
}

/** GET/PUT/POST the loop server. Returns {ok, value, error} — never throws,
 *  and the error text NAMES the fix (server down vs bad payload vs refused
 *  origin), because an opaque failure is what makes a widget look dead. */
async function httpJson(method, path, body, timeoutMs) {
  let res;
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs || 20000);
    const init = { method: method, signal: ctl.signal, headers: {} };
    if (body !== undefined && body !== null) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    res = await fetch(SERVER_BASE + path, init);
    clearTimeout(t);
  } catch (e) {
    return { ok: false, value: null, error:
      "cannot reach the loop server at " + SERVER_BASE + " (" +
      String((e && e.message) || e) + "). Fix: start it with  python scripts/status_server.py  "
      + "(it runs under the loop's watcher)." };
  }
  let value = null;
  const text = await res.text().catch(function () { return ""; });
  try { value = JSON.parse(text); } catch (e) { value = null; }
  if (!res.ok) {
    const d = (value && (value.detail || value.error)) || text.slice(0, 200) || (res.status + " " + res.statusText);
    return { ok: false, value: value, error: method + " " + path + " -> " + d };
  }
  return { ok: true, value: value, error: null };
}

/** Ensure we know where the loop lives on this machine (root + python). */
async function ensureMeta(force) {
  if (META && !force) return META;
  const r = await httpJson("GET", "/meta.json", null, 15000);
  if (r.ok && r.value && r.value.root && r.value.python) {
    META = r.value; META_AT = pollNow();
    lsSet("kl.loop.meta.v1", JSON.stringify(META));
    return META;
  }
  const cached = lsGet("kl.loop.meta.v1");
  if (cached) {
    try {
      const m = JSON.parse(cached);
      if (m && m.root && m.python) { META = m; return m; }
    } catch (e) {}
  }
  throw new Error(r.error || "the loop server never answered /meta.json");
}

/** Sync accessor for render-time (iframe urls etc.): null until learned. */
function cachedMeta() {
  if (META) return META;
  const cached = lsGet("kl.loop.meta.v1");
  if (cached) { try { const m = JSON.parse(cached); if (m && m.root) return m; } catch (e) {} }
  return null;
}

// ---------------------------------------------------------------- bridge
// Route resolution copied from the PROVEN hermes-rss plugin, which is bundled
// and therefore known-good against this SDK build:
//   profile       must be a non-empty STRING (the SDK calls profile.trim())
//   connectionId  is a string, defaulting to "local"
//   the pair      must exist in host2.profileRoutes()
// Passing an undefined profile made the SDK throw
//   "Cannot read properties of undefined (reading 'trim')"
// inside retainProfile/requestProfile -- which surfaced as an error in every
// panel, because every panel goes through runPy().
async function currentRoute(host2) {
  const profile = host2.state.profile.get();
  const connectionId = host2.state.connectionId?.get() || "local";
  const routes = await host2.profileRoutes();
  const route =
    (routes || []).find(
      (r) => r.profile === profile && r.connectionId === connectionId
    ) || (routes || [])[0];
  if (!route) {
    throw new Error(
      "No connected Hermes profile. Open a connection first, then reopen this tab."
    );
  }
  return route;
}


/** Parse a shell result that must be PURE JSON.
 *  Returns { ok, value, error, raw }. Never throws, never silently returns
 *  garbage: if the payload is not valid JSON we say so and hand back the raw
 *  text so the panel can show the real cause instead of a mystery fragment.
 *
 *  Why not `out.slice(out.indexOf("{"))`: that silently mangles any output with
 *  a non-JSON prefix (a warning line, a stray log line) and, worse, it accepted
 *  a *truncated* payload as if it were fine whenever the tail happened to
 *  balance. Strict parsing is the only way to know the bridge delivered
 *  everything. */
// ── poll coordination ───────────────────────────────────────────────────────
// Two guards that exist because the widget polls faster than its own commands
// complete. Both were missing, and together they crashed the desktop app:
//
//   INFLIGHT  the `alive` flag inside a hook only suppresses setState after
//             unmount -- it does NOT cancel the running python process. A tick
//             arriving mid-call stacked another interpreter, each one reading
//             the multi-MB worker log.
//   CACHE     re-running activity.py / checkpoint.py on every single tick was
//             pointless work; the underlying data changes on the order of
//             seconds, not milliseconds.
//
// `pollNow` is injectable so the test harness can drive the clock without
// waiting on real time.
var _clock = function () { return Date.now(); };
var INFLIGHT = {};
var CACHE = {};
var ACTIVITY_TTL_MS = 2200;
var CHECKPOINT_TTL_MS = 30000;
// Questions asked by the loop change slowly; a longer TTL than the 2.2 s poll
// means the banner does not re-read the policy file on every heartbeat.
// FIXED 2026-09-28: this constant was REFERENCED but never declared (the other
// three TTLs above/below were). Reading an undeclared identifier throws a
// ReferenceError on every 2.5 s tick, which disabled the question banner
// entirely and corrupted the shared INFLIGHT guard (see useQuestions).
var QUESTIONS_TTL_MS = 30000;

function pollNow() { return _clock(); }

function cacheGet(key, ttlMs, now) {
  var e = CACHE[key];
  if (!e) return null;
  if ((now - e.t) > ttlMs) return null;
  return e.v;
}

function cacheSet(key, value, now) {
  CACHE[key] = { t: now, v: value };
}

function parseJsonOut(out, label) {
  const raw = String(out == null ? "" : out);
  const t = raw.trim();
  if (!t) return { ok: false, value: null, error: (label || "command") + " returned no output", raw: raw };
  try {
    return { ok: true, value: JSON.parse(t), error: null, raw: raw };
  } catch (e) {
    const msg = String((e && e.message) || e);
    // Name the likely culprit instead of echoing 300 mystery characters.
    const looksTruncated = !/[}\]]\s*$/.test(t);
    return {
      ok: false, value: null, raw: raw,
      error: (label || "command") + " did not return JSON (" + msg + ")"
        + (looksTruncated
            ? "\nThe output looks truncated (it does not end with } or ])."
              + "\nfirst 160: " + JSON.stringify(t.slice(0, 160))
              + "\nlast 160:  " + JSON.stringify(t.slice(-160))
            : "\nfirst 160: " + JSON.stringify(t.slice(0, 160))),
    };
  }
}

function assertOwner(host2, route) {
  // The route may change while a command runs (user switched profiles);
  // refuse to apply stale output. Same guard the RSS plugin uses.
  const nowConn = host2.state.connectionId?.get?.() || "local";
  const nowProf = host2.state.profile?.get?.();
  if (route.connectionId !== nowConn || route.profile !== nowProf) {
    throw new Error("Route changed during command");
  }
}

async function runPy(host2, route, args, timeoutMs) {
  assertOwner(host2, route);
  // root + python come from the loop server's /meta.json (cached), never from
  // a path baked into this file; if they were never learnable, fail loudly.
  const meta = await ensureMeta(false);
  // shell.exec takes ONLY { command } -- the proven contract used by the bundled
  // hermes-rss plugin. Passing extra fields (cwd, timeout_ms) made the call
  // return without `stdout`, and the SDK's own `result.stdout.trim()` then threw
  // "Cannot read properties of undefined (reading 'trim')" in every panel.
  //
  // The timeout is the FOURTH POSITIONAL argument of requestProfile, not a field
  // in params (see apps/desktop/src/sdk/index.ts requestProfile signature).
  //
  // cwd is folded into the command instead. Use `cd /d` so a drive change works
  // under cmd.exe, and backslash paths -- MSYS rewrites /c/... style arguments.
  const rootWin = String(meta.root).replace(/\//g, "\\");
  const pyWin = String(meta.python).replace(/\//g, "\\");
  const command = 'cd /d "' + rootWin + '" && "' + pyWin + '" ' + args;
  const result = await host2.requestProfile(
    route, "shell.exec", { command }, timeoutMs || 300000
  );
  assertOwner(host2, route);
  if (!result) throw new Error("No result from shell.exec for: " + command);
  if (result.code !== 0) {
    throw new Error(
      "Command failed: " + command + "\n" +
      String(result.stderr || result.stdout || "").slice(0, 400)
    );
  }
  return String(result.stdout == null ? "" : result.stdout).trim();
}

// ---------------------------------------------------------------- angles settings
/* ANGLE LIBRARY -- every family x its angles, each with its working prompt.
   Loaded on demand (button), NOT polled: it is reference + edit data, not telemetry.
   Edits POST through angles.py save --json as a JSON argument; only CHANGED fields
   are stored in state/angle_overrides.yaml and merged by angle_pick at pick time. */

const ANGLES_TTL_MS = 60000;
const ANGLES_CACHE_KEY = "angles:all";

function useAngles(host2, route, tick, enabled) {
  // The 42-angle payload is ~18.7 KB -- far past the ~4 KB bridge ceiling, and the
  // clip produced "angles did not return JSON (Unexpected token 'd'...)" in the
  // widget. The library now renders from a self-contained page (angles_view.html)
  // written by `angles.py view`, so NOTHING large crosses the bridge; this hook
  // only counts regenerations to bust the iframe cache after each save.
  const [regen, setRegen] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    runPy(host2, route, "angles.py view", 120000).catch(function () {});
  }, [host2, route && route.connectionId, route && route.profile, tick, enabled, regen]);
  return { loading: false, data: null, error: null, regen: regen,
           refresh: function () { setRegen(function (x) { return x + 1; }); } };
}

/** One editable angle row. `dirty` fields are held locally until Save. */
function angleEditor(props) {
  const a = props.a;
  const onEdit = props.onEdit;
  const open = props.open;
  const setOpen = props.setOpen;
  const edited = !!a.edited;
  const kids = [];
  // summary row (always visible)
  kids.push(jsxs("div", { style: Object.assign({}, S.angRow, open ? S.angRowOpen : null),
    onClick: function () { setOpen(!open); },
    children: [
      jsx("span", { style: S.angChev, children: open ? "\\u25BE" : "\\u25B8" }, "c"),
      jsx("span", { style: S.angId, children: a.id }, "i"),
      jsx("span", { style: S.angLensPreview, children: (a.lens || "").slice(0, 88) }, "l"),
      edited ? jsx("span", { style: S.angEdited, title: "you edited this angle", children: "edited" }, "e") : null,
    ] }, "row"));
  if (!open) return jsxs("div", { children: kids }, "ang-" + a.id);
  // detail editors
  const cur = (key) => {
    const d = a._draft[key];
    if (d != null) return Array.isArray(d) ? d.join("\n") : d;
    const v = a[key];
    return Array.isArray(v) ? v.join("\n") : (v || "");
  };
  const fld = (label, key, multiline) => jsx("div", { style: S.angField, children:
    jsxs("label", { style: S.angLabel, children: [
      jsx("span", { children: label }, "k"),
      jsx("textarea", { style: S.angText, value: cur(key),
        rows: key === "look_for" ? 5 : (key === "lens" ? 2 : 2),
        onChange: function (ev) { onEdit(a.id, key, ev.target.value); } }, "v"),
    ] }, key) }, key);
  kids.push(jsx("div", { style: S.angDetail, children: jsxs("div", { children: [
    fld("Lens (the one-line instruction)", "lens", true),
    fld("Look for (one per line)", "look_for", true),
    fld("Evidence required", "evidence", true),
    fld("Avoid when", "avoid_when", true),
    jsxs("div", { style: S.angActions, children: [
      jsx(Button, { onClick: function () { props.onSave(a); },
        children: props.saving ? "Saving\\u2026" : "Save angle" }, "save"),
      jsx(Button, { onClick: function () { props.onReset(a); }, children: "Reset to default" }, "reset"),
      jsx("span", { style: S.angHint,
        children: "look_for lines are split on new lines. Save writes state/angle_overrides.yaml; the picker and every future round prompt use it immediately." }, "h"),
    ] }, "acts"),
  ] }, "ed") }, "detail"));
  return jsxs("div", { children: kids }, "ang-" + a.id);
}

function anglesSettingsPanel(props) {
  const data = props.data;
  const fams = (data && data.families) || {};
  const famNames = Object.keys(fams);
  const [openFam, setOpenFam] = useState(famNames[0] || "");
  const [openAng, setOpenAng] = useState("");
  const [drafts, setDrafts] = useState({});
  const [saving, setSaving] = useState("");
  const [savedMsg, setSavedMsg] = useState("");

  const withDraft = (a) => Object.assign({}, a, { _draft: drafts[a.id] || {} });
  const onEdit = (id, key, val) => {
    setDrafts((d0) => {
      const nxt = Object.assign({}, d0);
      const cur = Object.assign({}, nxt[id] || {});
      if (key === "look_for") {
        cur[key] = String(val).split("\\n").map((x) => x.trim()).filter(Boolean);
      } else {
        cur[key] = val;
      }
      nxt[id] = cur;
      return nxt;
    });
  };
  const onSave = async (a) => {
    setSaving(a.id); setSavedMsg("");
    try {
      const edits = [Object.assign({ id: a.id }, drafts[a.id] || {})];
      const args = "angles.py save --edits " + encodeURIComponent(JSON.stringify({ edits: edits }));
      const out = await runPy(props.host2, props.route, args, 120000);
      const r = parseJsonOut(out, "angles save");
      if (!r.ok) throw new Error(r.error || "save failed");
      setSavedMsg("Saved: " + ((r.value.changed || []).join(", ") || "no changes"));
      setDrafts((d0) => { const n = Object.assign({}, d0); delete n[a.id]; return n; });
      delete CACHE[ANGLES_CACHE_KEY];  // force a re-read on next tick
      props.onRefresh();
    } catch (e) {
      setSavedMsg("Save failed: " + String(e.message || e).slice(0, 160));
    } finally { setSaving(""); }
  };
  const onReset = async (a) => {
    setSaving(a.id); setSavedMsg("");
    try {
      const edits = [{ id: a.id, lens: "", look_for: [], evidence: "", avoid_when: "" }];
      const args = "angles.py save --edits " + encodeURIComponent(JSON.stringify({ edits: edits }));
      await runPy(props.host2, props.route, args, 120000);
      setSavedMsg("Reset " + a.id + " to default");
      delete CACHE[ANGLES_CACHE_KEY];
      props.onRefresh();
    } catch (e) {
      setSavedMsg("Reset failed: " + String(e.message || e).slice(0, 160));
    } finally { setSaving(""); }
  };

  const kids = [];
  if (props.error) kids.push(jsx("div", { style: S.err, children: String(props.error) }, "err"));
  if (!data) kids.push(jsx("div", { style: S.kvEmpty, children: props.loading ? "loading angles\u2026" : "no data" }, "ld"));
  famNames.forEach((fn) => {
    const list = fams[fn] || [];
    const isOpen = openFam === fn;
    kids.push(jsxs("div", { children: [
      jsxs("div", { style: Object.assign({}, S.famHead, isOpen ? S.famHeadOpen : null),
        onClick: function () { setOpenFam(isOpen ? "" : fn); }, children: [
        jsx("span", { style: S.angChev, children: isOpen ? "\\u25BE" : "\\u25B8" }, "c"),
        jsx("span", { style: S.famName, children: fn }, "n"),
        jsx("span", { style: S.famCount, children: list.length + " angles" }, "cnt"),
      ] }, "fh"),
      isOpen ? jsx("div", { children: list.map((a0) =>
        angleEditor({ a: withDraft(a0), open: openAng === a0.id, setOpen: (v) => setOpenAng(v ? a0.id : ""),
          onEdit: onEdit, onSave: onSave, onReset: onReset, saving: saving === a0.id }))
      }, "fl") : null,
    ] }, "fam-" + fn));
  });
  return jsxs("div", { children: [
    kids,
    savedMsg ? jsx("div", { style: S.angSaved, children: savedMsg }, "sm") : null,
  ] }, "angles-all");
}

// The modal shell: full-width overlay so long prompts are readable while editing.
function anglesModal(props) {
  const m = cachedMeta();
  if (!m) return null;   // root is unknown until the loop server answered once
  const url = "file:///" + (m.root + "\\state\\angles_view.html").replace(/\\/g, "/")
    + "?v=" + (props.regen || 0);
  return jsx("div", { style: S.modalBack, onClick: function (ev) {
    if (ev.target === ev.currentTarget) props.onClose();
  }, children: jsx("div", { style: S.modalCard, children: jsxs(Panel, {
    title: "Angle library \\u2014 every family, every working prompt",
    note: "edits take effect on the next picked round",
    right: jsx(Button, { onClick: props.onClose, children: "Close" }),
    children: jsx("iframe", {
      src: url,
      style: S.iframe,
      title: "Karpathy Loop angle library",
    }),
  }) }) }, "angles-modal");
}

// ---------------------------------------------------------------- hooks
/* LIVE ACTIVITY -- what the loop is doing RIGHT NOW.
   The other panels show configuration; this one shows motion: which project is
   mid-round, which card is in flight, and how many rounds each has had. Without
   it the widget looks frozen while work is actually happening. */
function useActivity(host2, route, tick) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  useEffect(() => {
    // DO NOT cancel on cleanup. The 2.5 s heartbeat re-runs this effect every tick
    // while a call can take ~3 s; the old `alive = false` cleanup DISCARDED the
    // result each time, so the panel sat on its initial `loading: true` forever.
    // The INFLIGHT + TTL cache already prevent pileup, so a stale runner cannot
    // stack processes -- dropping `alive` only means a slightly older payload may
    // land once, which the next tick replaces.
    if (!route) return;
    (async () => {
      const key = "activity";
      const now = pollNow();
      // In-flight guard: `alive` only stops setState -- it does NOT stop the
      // python process. Without this, a tick arriving while a 90s call was
      // still running stacked another interpreter, each reading the worker
      // log. That process pileup (plus screenshotting) is what crashed the
      // desktop app.
      if (INFLIGHT[key]) return;
      // Result cache: only re-run when TTL has elapsed.
      const hit = cacheGet(key, ACTIVITY_TTL_MS, now);
      if (hit) {
        setState({ loading: false, data: hit, error: null });
        return;
      }
      INFLIGHT[key] = true;
      const viaHttp = await fetchJson("/activity.json", 15000);
      if (viaHttp && !viaHttp.error) {
        cacheSet(key, viaHttp, now); lastGood("activity", viaHttp);
        setState({ loading: false, data: viaHttp, error: null });
        INFLIGHT[key] = false;
        return;
      }
      try {
        const out = await runPy(host2, route, "activity.py", 120000);
        const r = parseJsonOut(out, "activity");
        if (r.ok) { cacheSet(key, r.value, now); lastGood("activity", r.value); }
        const prev = lastGood("activity");
        setState({ loading: false, data: r.ok ? r.value : (prev || null),
                   error: r.ok ? null : (prev ? null : r.error) });
      } catch (e) {
        const prev = lastGood("activity");
        setState({ loading: false, data: prev || null,
                   error: prev ? null : String(e.message || e) });
      } finally {
        INFLIGHT[key] = false;
      }
    })();
  }, [host2, route && route.connectionId, route && route.profile, tick]);
  return state;
}

function useCheckpoints(host2, route, tick) {
  const [state, setState] = useState({ loading: true, rows: [], error: null });
  useEffect(() => {
    // no cleanup-cancel: the heartbeat re-runs this effect every 2.5 s and a
    // cancelled in-flight call left the panel on `loading` forever.
    if (!route) return;
    (async () => {
      const key = "checkpoints";
      const now = pollNow();
      if (INFLIGHT[key]) return;
      const hit = cacheGet(key, CHECKPOINT_TTL_MS, now);
      if (hit) {
        setState({ loading: false, rows: hit, error: null });
        return;
      }
      INFLIGHT[key] = true;
      const viaHttp = await fetchJson("/checkpoints.json", 30000);
      if (viaHttp && !viaHttp.error) {
        const httpRows = (viaHttp.checkpoints || viaHttp.rows || []);
        cacheSet(key, httpRows, now); lastGood("ckpts", httpRows);
        setState({ loading: false, rows: httpRows, error: null });
        INFLIGHT[key] = false;
        return;
      }
      try {
        const out = await runPy(host2, route, "checkpoint.py all", 120000);
        const r = parseJsonOut(out, "checkpoints");
        const v = r.value;
        const rows = v ? (Array.isArray(v) ? v : (v.checkpoints || v.rows || [])) : [];
        if (r.ok) { cacheSet(key, rows, now); lastGood("ckpts", rows); }
        const prev = lastGood("ckpts");
        setState({ loading: false, rows: (r.ok && rows.length) ? rows : (prev || []),
                   error: r.ok ? null : (prev && prev.length ? null : r.error) });
      } catch (e) {
        const prev = lastGood("ckpts") || [];
        setState({ loading: false, rows: prev,
                   error: prev.length ? null : String(e.message || e) });
      } finally {
        INFLIGHT[key] = false;
      }
    })();
  }, [host2, route && route.connectionId, route && route.profile, tick]);
  return state;
}

function useQuestions(host2, route, tick) {
  // Red-dot data: the open question(s) from questions/pending.json. Empty
  // output (no file or no question) must parse to {open: null}, not throw.
  const [state, setState] = useState({ loading: true, open: null, error: null });
  useEffect(() => {
    // no cleanup-cancel: the heartbeat re-runs this effect every 2.5 s and a
    // cancelled in-flight call left the panel on `loading` forever.
    if (!route) return;
    (async () => {
      const key = "questions:" + (route.connectionId || "") + ":" + (route.profile || "");
      // Cache + in-flight checks live OUTSIDE the try on purpose. When they were
      // inside it, a throw here (e.g. the undeclared CACHE_TTL_MS that shipped in
      // an earlier revision) was caught and then the `finally` cleared INFLIGHT
      // for a run that never set it -- wiping the flag of a CONCURRENT invocation
      // and defeating the very guard that stops ask_policy.py processes stacking.
      // Keep the guard's own bookkeeping outside the block that can throw.
      if (CACHE[key] && Date.now() - CACHE[key].t < QUESTIONS_TTL_MS) {
        setState({ loading: false, open: CACHE[key].v, error: null });
        return;
      }
      if (INFLIGHT[key]) return;
      INFLIGHT[key] = true;
      try {
        const out = await runPy(host2, route, "ask_policy.py status --json", 30000);
        let v = null;
        try { v = JSON.parse(out); } catch (e) { v = null; }
        CACHE[key] = { t: Date.now(), v: v };
        setState({ loading: false, open: v, error: null });
      } catch (e) {
        setState({ loading: false, open: null, error: String(e && e.message || e) });
      } finally {
        INFLIGHT[key] = false;
      }
    })();
  }, [host2, route && route.connectionId, route && route.profile, tick]);
  return state;
}


// F-X (2026-10-05, the operator: "status unavailable / 90s timeout"): shell.exec calls
// share the profile agent's single tool queue with the human's own agent
// sessions. When that queue is busy, a poll times out -- and the old code
// blanked the panel on ONE failed poll. These wrappers keep the LAST GOOD
// payload on error and mark it stale, so a busy queue degrades to slightly
// old data instead of "status unavailable".
var LASTGOOD = {};
function lastGood(key, value) {
  if (value !== undefined && value !== null) LASTGOOD[key] = value;
  return LASTGOOD[key];
}


// F-Y (2026-10-05, the operator: "stays loading forever"): shell.exec polls share the
// profile agent's shell queue, and every agent spawn pays a 13-70s MCP
// connect storm -- under load the polls starved and the page never loaded.
// The loop now runs a local status server (scripts/status_server.py,
// 127.0.0.1:8765) serving the SAME producers. Try fetch() FIRST (no agent,
// no queue); fall back to shell.exec if the server is down.
var httpDown = 0;   // epoch-ms of last failure; retry HTTP every 60s anyway

async function fetchJson(path, timeoutMs) {
  const now = pollNow();
  if (now - httpDown < 60000) return null;
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs || 20000);
    const res = await fetch(SERVER_BASE + path, { signal: ctl.signal });
    clearTimeout(t);
    if (!res.ok) return null;
    return await res.json();
  } catch (e) {
    httpDown = pollNow();
    return null;
  }
}

function useLoopStatus(host2, route, tick) {
  const [state, setState] = useState({ loading: true, text: "", json: null, error: null });
  useEffect(() => {
    // no cleanup-cancel: the heartbeat re-runs this effect every 2.5 s and a
    // cancelled in-flight call left the panel on `loading` forever.
    if (!route) return;
    (async () => {
      const viaHttp = await fetchJson("/status.json", 15000);
      if (viaHttp && !viaHttp.error) {
        lastGood("status", viaHttp);
        setState({ loading: false, text: "", json: viaHttp, error: null });
        return;
      }
      try {
        const out = await runPy(host2, route, "loopctl.py status --json", 120000);
        const r = parseJsonOut(out, "loopctl status");
        if (r.ok) lastGood("status", r.value);
        const prev = lastGood("status");
        setState({ loading: false, text: out, json: r.ok ? r.value : (prev || null),
                   error: r.ok ? null : (prev ? null : r.error) });
      } catch (e) {
        const prev = lastGood("status");
        setState({ loading: false, text: "", json: prev || null,
                   error: prev ? null : String(e.message || e) });
      }
    })();
  }, [host2, route && route.connectionId, route && route.profile, tick]);
  return state;
}

/* Repo briefs — read ONCE per session, never on the poll.
 *
 * The briefs are ~3 KB of prose per repo. The poll payload shares a ~3.8 KB
 * bridge budget with the feed and the rows, so putting four briefs in the tick
 * would blow the bridge and truncate everything else. Fetch them once, cache
 * them, and let the panel render whatever it has.
 *
 * `repo_brief.py --show ALL` does not exist, so we ask for one repo at a time
 * inside a SINGLE bridge call (a shell loop), keeping it to one process.
 */
function useRepoBriefs(host2, route, names) {
  const [state, setState] = useState({ loading: true, data: {}, error: null });
  const key = (names || []).join(",");
  useEffect(() => {
    if (!route || !key) { setState({ loading: false, data: {}, error: null }); return; }
    const cacheKey = "briefs:" + key;
    const hit = cacheGet(cacheKey, 600000, pollNow());   // 10 min
    if (hit) { setState({ loading: false, data: hit, error: null }); return; }
    if (INFLIGHT[cacheKey]) return;
    INFLIGHT[cacheKey] = true;
    (async () => {
      const out = {};
      try {
        for (const n of names) {
          // Guard the name: it goes into a shell string. Anything outside a
          // conservative repo-name charset is skipped rather than escaped.
          if (!/^[A-Za-z0-9._-]+$/.test(n)) continue;
          const r = await runPy(host2, route, "repo_brief.py --show " + n, 30000);
          const p = parseJsonOut(r, "brief " + n);
          if (p.ok && p.value) out[n] = p.value;
        }
        cacheSet(cacheKey, out, pollNow());
        setState({ loading: false, data: out, error: null });
      } catch (e) {
        setState({ loading: false, data: out, error: String(e.message || e) });
      } finally {
        INFLIGHT[cacheKey] = false;
      }
    })();
  }, [host2, route && route.connectionId, route && route.profile, key]);
  return state;
}

function useDiscovery(host2, route, tick) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  useEffect(() => {
    // no cleanup-cancel: the heartbeat re-runs this effect every 2.5 s and a
    // cancelled in-flight call left the panel on `loading` forever.
    if (!route) return;
    (async () => {
      try {
        // --json keeps stdout PURE JSON; without it discover.py prints a human
        // table first and the parse fails.
        const out = await runPy(host2, route, "discover.py report --json", 60000);
        const r = parseJsonOut(out, "discovery report");
        setState({ loading: false, data: r.value, error: r.ok ? null : r.error });
      } catch (e) {
        setState({ loading: false, data: null, error: String(e.message || e) });
      }
    })();
  }, [host2, route && route.connectionId, route && route.profile, tick]);
  return state;
}

function shorten(s, n) {
  const t = String(s == null ? "" : s).replace(/\s+/g, " ").trim();
  if (t.length <= n) return t;
  const cut = t.slice(0, n);
  const sp = cut.lastIndexOf(" ");
  return (sp > n * 0.6 ? cut.slice(0, sp) : cut) + "…";
}

/** Strip the vault/worktree prefix so paths fit the panel. */
/** "custom:glm53-flash-2x-spark:GLM-5.3-Flash-EXL3" -> "GLM-5.3-Flash-EXL3".
 *  The provider prefix is noise in a settings table and makes the value wrap. */
function shortenModel(s) {
  const t = String(s == null ? "" : s);
  const parts = t.split(":");
  return parts.length > 1 ? parts[parts.length - 1] : t;
}

/** "34s" / "12m" / "2.1h" -- how long ago the worker last wrote to its log. */
function ago(sec) {
  const s = Math.max(0, Number(sec) || 0);
  if (s < 60) return Math.round(s) + "s ago";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  return (s / 3600).toFixed(1) + "h ago";
}

// Shorten a real FILESYSTEM PATH to its two most useful segments:
//   "...\.worktrees\t_42fa5ba4\backend\world\event_ledger.py" -> "world/event_ledger.py"
//
// This must NEVER be applied to a shell command: splitting a command on "/"
// chopped `grep -rn "x" backend --include=*.py | grep -v test` down to
// `grep -v test`, which is how garbled rows like `|def _person/b" backend`
// reached the panel. Only tidy when the text actually looks like a path.
function tidyPath(s) {
  const t = String(s || "").trim();
  if (!t || /\s/.test(t)) return t;              // commands have spaces -> leave alone
  if (!/^[A-Za-z]:[\\/]|^[\\/~.]/.test(t)) return t; // not path-like -> leave alone
  const wt = t.match(/\.worktrees[\\/][^\\/]+[\\/](.+)$/);
  const rel = wt ? wt[1] : t;
  const parts = rel.split(/[\\/]+/).filter(Boolean);
  return parts.length > 2 ? parts.slice(-2).join("/") : parts.join("/");
}


// ---------------------------------------------------------------- styles

// ------------------------------------------------------- design tokens
// One place for the visual language, so every panel reads as the same product
// instead of a pile of ad-hoc inline styles.
const T = {
  radius: 12, radiusSm: 8, gap: 16, pad: 14,
  card: "var(--card)", border: "var(--border)", fg: "var(--foreground)",
  muted: "var(--muted-foreground)", accent: "var(--accent)",
  live: "#2f9e44", liveBg: "rgba(47,158,68,0.14)", liveBd: "rgba(47,158,68,0.45)",
  wait: "#b8860b", waitBg: "rgba(184,134,11,0.14)", waitBd: "rgba(184,134,11,0.45)",
  idle: "#6b7280", idleBg: "rgba(107,114,128,0.14)", idleBd: "rgba(107,114,128,0.35)",
  err: "#c0392b", errBg: "rgba(192,57,43,0.12)", errBd: "rgba(192,57,43,0.45)",
  mono: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
  // T.idleBd was referenced by Chip's default tone but never declared -- an
  // undeclared style ref renders as literal `undefined`, which React DROPS, so
  // the idle Chip silently lost its border with no error anywhere. Found by
  // tests/check_styles.js, which now runs in the gate.
  idleBd: "var(--border)",
};

// Chat-log rows for the live thread. Colour carries meaning: reasoning is a tinted
// quote block, the operator's own prompt is a left-ruled band, tool lines are quiet
// monospace so the transcript reads as a conversation instead of a wall of shell.
const S = {
  wrap: { padding: "18px 22px", fontFamily: "inherit", color: "var(--foreground)" },
  hrow: { display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" },
  h2: { margin: 0, fontSize: 20, fontWeight: 650, letterSpacing: -0.2 },
  badgeOn: { fontSize: 10.5, padding: "3px 10px", borderRadius: 999,
             border: "1px solid #1c4a38", color: "#fff", background: "#2f9e44",
             fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase" },
  badgeOff: { fontSize: 10.5, padding: "3px 10px", borderRadius: 999,
              border: "1px solid #6b5217", color: "#fff", background: "#b8860b",
              fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase" },
  badgeWarn: { fontSize: 10.5, padding: "3px 10px", borderRadius: 999,
             background: "rgba(240,180,41,0.12)", color: "#f0b429",
             border: "1px solid rgba(240,180,41,0.45)", fontWeight: 600 },
  badgeIdle: { fontSize: 10.5, padding: "3px 10px", borderRadius: 999,
               border: "1px solid var(--border)", color: "var(--muted-foreground)",
               background: "color-mix(in srgb, var(--foreground) 6%, transparent)",
               fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase" },
  badgeErr: { fontSize: 10.5, padding: "3px 10px", borderRadius: 999,
              border: "1px solid #7a2c2c", color: "#fff", background: "#c0392b",
              fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase" },
  sub: { color: "var(--muted-foreground)", fontSize: 12.5, marginTop: 6 },
  btnRow: { display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap",
            alignItems: "center", paddingTop: 14, borderTop: "1px solid var(--border)" },
  msg: { marginTop: 12, padding: "9px 13px", borderRadius: 9, fontSize: 12.5,
         background: "var(--card)", border: "1px solid var(--border)" },
  grid: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginTop: 18 },
  label: { fontSize: 11.5, textTransform: "uppercase", letterSpacing: ".06em",
           color: "var(--muted-foreground)", marginBottom: 7 },
  mono: { padding: "5px 10px", marginBottom: 4, borderRadius: 7, background: "var(--card)",
          border: "1px solid var(--border)",
          fontFamily: "ui-monospace,Menlo,monospace", fontSize: 12 },
  pre: { background: "var(--card)", border: "1px solid var(--border)", borderRadius: 9,
         padding: 11, fontSize: 11.5, overflow: "auto", margin: 0 },
  iframe: { width: "100%", height: 780, border: "1px solid var(--border)",
            borderRadius: 11, background: "var(--card)" },
  empty: { color: "var(--muted-foreground)" },
  tdKey: { padding: "8px 10px", color: "var(--muted-foreground)", fontSize: 12.5,
           whiteSpace: "nowrap", width: "42%", borderTop: "1px solid var(--border)" },
  tdVal: { padding: "8px 10px", fontSize: 12.5, fontVariantNumeric: "tabular-nums",
           color: "var(--foreground)", wordBreak: "break-word", borderTop: "1px solid var(--border)" },
  panel: { background: "var(--card)", border: "1px solid var(--border)",
           borderRadius: 12, overflow: "hidden" },
  panelHead: { display: "flex", alignItems: "center", gap: 10,
               padding: "10px 14px", borderBottom: "1px solid var(--border)",
               background: "color-mix(in srgb, var(--foreground) 3%, transparent)" },
  panelTitle: { margin: 0, fontSize: 12, fontWeight: 700, letterSpacing: 0.7,
                textTransform: "uppercase", color: "var(--foreground)" },
  panelNote: { fontSize: 11.5, color: "var(--muted-foreground)" },
  panelBody: { padding: 14 },
  chip: { display: "inline-flex", alignItems: "center", gap: 5, padding: "2px 9px",
          borderRadius: 999, fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4,
          textTransform: "uppercase", border: "1px solid transparent" },
  ckRow: { display: "grid", gridTemplateColumns: "1fr auto auto", gap: 12,
           alignItems: "center", padding: "7px 10px", borderRadius: 8,
           background: "color-mix(in srgb, var(--foreground) 3%, transparent)",
           border: "1px solid var(--border)" },
  ckTag: { fontSize: 11.5, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
           fontWeight: 600, color: "var(--foreground)", overflow: "hidden",
           textOverflow: "ellipsis", whiteSpace: "nowrap" },
  ckSha: { fontSize: 10.5, color: "var(--foreground)", whiteSpace: "nowrap",
           padding: "1px 6px", borderRadius: 5,
           background: "color-mix(in srgb, var(--accent) 10%, transparent)",
           fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" },
  ckMeta: { fontSize: 10.5, color: "var(--muted-foreground)", whiteSpace: "nowrap",
            fontVariantNumeric: "tabular-nums" },
  ckAngle: { fontSize: 10, color: "var(--muted-foreground)", whiteSpace: "nowrap",
             padding: "1px 7px", borderRadius: 999,
             background: "color-mix(in srgb, var(--accent) 16%, transparent)",
             fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" },
  ckSum: { fontSize: 11, color: "var(--muted-foreground)", lineHeight: 1.35,
           paddingLeft: 2, overflow: "hidden", textOverflow: "ellipsis",
           display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" },
  angRow: { display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" },
  angId: { fontSize: 12.5, fontWeight: 600, color: "var(--foreground)",
           fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" },
  angFam: { fontSize: 10, color: "var(--muted-foreground)", padding: "1px 7px",
            borderRadius: 999, border: "1px solid var(--border)" },
  angLens: { fontSize: 12, color: "var(--muted-foreground)", lineHeight: 1.45,
             marginTop: 4 },
  angPrompt: { marginTop: 8, padding: "9px 11px", borderRadius: 8,
               border: "1px solid var(--border)", background: "var(--card)",
               fontSize: 11, lineHeight: 1.5, color: "var(--foreground)",
               fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
               maxHeight: 210, overflow: "auto", whiteSpace: "pre-wrap" },
  askBanner: { display: "flex", gap: 10, alignItems: "flex-start",
               border: "1px solid #e5484d55", borderRadius: 10,
               padding: "10px 12px", margin: "0 0 10px 0",
               background: "color-mix(in srgb, #e5484d 10%, transparent)" },
  askDot: { fontSize: 13, lineHeight: "18px", color: "#e5484d", flex: "0 0 auto" },
  askTitle: { fontSize: 12, fontWeight: 700, letterSpacing: 0.3 },
  askBody: { fontSize: 12, marginTop: 2, opacity: 0.95, overflowWrap: "anywhere" },
  askMeta: { fontSize: 10.5, marginTop: 3, color: "var(--muted-foreground)" },
  tblWrap: { border: "1px solid var(--border)", borderRadius: 10,
             overflow: "hidden", background: "var(--card)" },
  tbl: { width: "100%", borderCollapse: "collapse", fontSize: 12.5, tableLayout: "fixed" },
  th: { textAlign: "left", padding: "8px 10px", fontWeight: 600, fontSize: 10.5,
        letterSpacing: 0.6, textTransform: "uppercase", color: "var(--muted-foreground)",
        borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" },
  thR: { textAlign: "right", padding: "8px 10px", fontWeight: 600, fontSize: 10.5,
         letterSpacing: 0.6, textTransform: "uppercase", color: "var(--muted-foreground)",
         borderBottom: "1px solid var(--border)", whiteSpace: "nowrap" },
  thDot: { width: 26, padding: "8px 4px 8px 12px", borderBottom: "1px solid var(--border)" },
  thP: { width: 26, padding: "8px 4px 8px 12px", borderBottom: "1px solid var(--border)" },
  tr: { borderBottom: "1px solid var(--border)" },
  trLast: {},
  trLive: { borderBottom: "1px solid var(--border)",
            background: "color-mix(in srgb, var(--accent) 10%, transparent)" },
  td: { padding: "9px 10px", verticalAlign: "middle" },
  tdR: { padding: "9px 10px", textAlign: "right", verticalAlign: "middle",
         fontVariantNumeric: "tabular-nums", width: 72, whiteSpace: "nowrap",
         color: "var(--muted-foreground)" },
  tdREm: { padding: "9px 10px", textAlign: "right", verticalAlign: "middle",
           fontVariantNumeric: "tabular-nums", width: 72, whiteSpace: "nowrap",
           color: "var(--muted-foreground)" },
  tdName: { padding: "9px 10px", verticalAlign: "middle", color: "var(--muted-foreground)" },
  tdNameLive: { padding: "9px 10px", verticalAlign: "middle",
                color: "var(--foreground)", fontWeight: 600 },
  tdMono: { padding: "9px 10px", verticalAlign: "middle", fontSize: 11.5,
            color: "var(--muted-foreground)",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" },
  tdDot: { padding: "9px 4px 9px 12px", width: 26, verticalAlign: "middle" },
  pillRun: { display: "inline-block", padding: "2px 8px", borderRadius: 999,
             fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4,
             textTransform: "uppercase", color: "#fff", background: "#2f9e44" },
  pillWait: { display: "inline-block", padding: "2px 8px", borderRadius: 999,
              fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4,
              textTransform: "uppercase", color: "#fff", background: "#b8860b" },
  pillIdle: { display: "inline-block", padding: "2px 8px", borderRadius: 999,
              fontSize: 10.5, fontWeight: 700, letterSpacing: 0.4,
              textTransform: "uppercase", color: "var(--muted-foreground)",
              background: "var(--border)" },
  err: { marginTop: 10, padding: "9px 11px", borderRadius: 8, fontSize: 12,
         color: "var(--foreground)", background: "rgba(200,60,60,0.12)",
         border: "1px solid rgba(200,60,60,0.35)", whiteSpace: "pre-wrap",
         wordBreak: "break-word",
         fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" },
  cotCard: { background: "var(--card)", border: "1px solid var(--border)",
             borderRadius: 12, overflow: "hidden" },
  cotHead: { display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
             borderBottom: "1px solid var(--border)",
             background: "color-mix(in srgb, var(--foreground) 3%, transparent)" },
  cotTitle: { fontSize: 12, fontWeight: 700, letterSpacing: 0.7,
              textTransform: "uppercase", color: "var(--foreground)" },
  cotSub: { fontSize: 11.5, color: "var(--muted-foreground)" },
  cotScroll: { padding: "10px 10px 14px", display: "grid", gap: 4,
               maxHeight: 230, overflowY: "auto", scrollbarGutter: "stable" },
  cotStep: { display: "flex", gap: 9, padding: "3px 7px", borderRadius: 7,
             alignItems: "flex-start", background: "transparent" },
  cotThink: { display: "flex", gap: 9, padding: "7px 9px", borderRadius: 8,
              alignItems: "flex-start", margin: "4px 0",
              background: "color-mix(in srgb, var(--accent) 9%, transparent)",
              borderLeft: "2px solid var(--accent)" },
  cotPrompt: { display: "flex", gap: 9, padding: "5px 9px", borderRadius: 8,
               alignItems: "flex-start", margin: "2px 0",
               background: "color-mix(in srgb, var(--foreground) 5%, transparent)",
               borderLeft: "2px solid var(--border)" },
  cotIcon: { fontSize: 11, width: 15, textAlign: "center", lineHeight: "16px",
             flexShrink: 0, opacity: 0.85 },
  cotBody: { minWidth: 0, flex: 1 },
  cotStepText: { fontSize: 11, lineHeight: 1.45,
                 fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
                 wordBreak: "break-word", opacity: 0.92 },
  cotThinkText: { fontSize: 11.5, lineHeight: 1.5, fontStyle: "italic",
                  wordBreak: "break-word", opacity: 0.95 },
  cotPromptText: { fontSize: 11, lineHeight: 1.45, fontStyle: "normal",
                   wordBreak: "break-word", opacity: 0.75 },
  cotMeta: { fontSize: 10, color: "var(--muted-foreground)", marginTop: 1,
             letterSpacing: ".02em", opacity: 0.8 },
  kvGrid: { display: "grid", gap: 3, fontSize: 12.5 },
  kvRow: { display: "flex", gap: 10, padding: "3px 0",
           borderBottom: "1px solid color-mix(in srgb, var(--border) 45%, transparent)" },
  kvKey: { color: "var(--muted-foreground)", minWidth: 118, flexShrink: 0 },
  kvVal: { wordBreak: "break-word", textAlign: "right", marginLeft: "auto",
           fontVariantNumeric: "tabular-nums" },
  kvEmpty: { color: "var(--muted-foreground)", fontSize: 12.5 },
  dotOn: { color: "#3fb950" }, dotOff: { color: "var(--muted-foreground)" },
  dotLive: { color: "#3fb950", fontWeight: 700, width: 12, display: "inline-block" },
  dotIdle: { color: "var(--muted-foreground)", width: 12, display: "inline-block" },

  /* angle library */
  famHead: { display: "flex", alignItems: "baseline", gap: 8, padding: "7px 10px",
             cursor: "pointer", borderRadius: 6, border: "1px solid var(--border)",
             marginTop: 6, background: "var(--card)" },
  famHeadOpen: { borderBottomLeftRadius: 0, borderBottomRightRadius: 0 },
  famName: { fontWeight: 700, fontSize: 13, color: "var(--foreground)", textTransform: "capitalize" },
  famCount: { color: "var(--muted-foreground)", fontSize: 11.5 },
  angRow: { display: "flex", alignItems: "baseline", gap: 8, padding: "6px 10px 6px 18px",
            cursor: "pointer", borderLeft: "2px solid var(--border)" },
  angRowOpen: { background: "color-mix(in srgb, var(--accent) 7%, transparent)" },
  angChev: { width: 12, color: "var(--muted-foreground)", display: "inline-block" },
  angId: { fontWeight: 600, fontSize: 12.5, color: "var(--foreground)", minWidth: 150 },
  angLensPreview: { color: "var(--muted-foreground)", fontSize: 12 },
  angEdited: { fontSize: 10.5, color: "#d29922", border: "1px solid #d29922",
               borderRadius: 8, padding: "0 6px", marginLeft: "auto" },
  angDetail: { padding: "4px 10px 10px 34px", borderLeft: "2px solid var(--border)" },
  angField: { marginTop: 8 },
  angLabel: { display: "block", fontSize: 11, color: "var(--muted-foreground)", marginBottom: 3 },
  angText: { width: "100%", boxSizing: "border-box", fontSize: 12.5, lineHeight: 1.45,
             padding: "5px 7px", borderRadius: 5, border: "1px solid var(--border)",
             background: "var(--card)", color: "var(--foreground)",
             fontFamily: "inherit", resize: "vertical" },
  angActions: { display: "flex", gap: 8, alignItems: "center", marginTop: 10 },
  angHint: { fontSize: 10.5, color: "var(--muted-foreground)", maxWidth: 420 },
  angSaved: { marginTop: 10, fontSize: 12, color: "#3fb950" },
  modalBack: { position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)",
               display: "flex", alignItems: "flex-start", justifyContent: "center",
               zIndex: 50, padding: 24 },
  modalCard: { width: "min(860px, 94%)", maxHeight: "88vh", display: "flex",
                background: "#ffffff", color: "#111827",
                borderRadius: 12, border: "1px solid #d1d5db" },
  modalScroll: { overflowY: "auto", maxHeight: "calc(88vh - 64px)", paddingRight: 4 },

  /* settings card */
  helpDot: { display: "inline-flex", alignItems: "center", justifyContent: "center",
             width: 20, height: 20, marginLeft: 6, borderRadius: "50%",
             border: "1px solid var(--border)", fontSize: 13, fontWeight: 700,
             color: "var(--muted-foreground)", cursor: "pointer", flexShrink: 0,
             background: "var(--card)", userSelect: "none", position: "relative" },
  helpBubble: { position: "absolute", zIndex: 60, left: 0, top: "calc(100% + 6px)",
                width: 340, background: "#ffffff", color: "#111827",
                border: "1px solid #d1d5db", borderRadius: 10,
                boxShadow: "0 8px 24px rgba(0,0,0,.18)", padding: "10px 12px",
                fontSize: 12, lineHeight: 1.5, whiteSpace: "normal",
                fontWeight: 400, textAlign: "left", cursor: "default" },
  helpBubbleKey: { display: "block", fontSize: 10.5, fontWeight: 700,
                   letterSpacing: ".04em", color: "#6b7280",
                   textTransform: "uppercase", marginBottom: 3 },

  setSection: { marginTop: 14 },
  setSecHead: { display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 },
  setSecTitle: { fontSize: 11.5, fontWeight: 700, letterSpacing: ".06em",
                 textTransform: "uppercase", color: "var(--foreground)" },
  setSecNote: { fontSize: 11, color: "var(--muted-foreground)" },
  setRow: { display: "grid", gridTemplateColumns: "200px 1fr auto",
            gap: 10, alignItems: "center", padding: "4px 0",
            borderBottom: "1px solid color-mix(in srgb, var(--border) 45%, transparent)" },
  setKey: { fontSize: 12, color: "var(--muted-foreground)", minWidth: 0,
            overflow: "hidden", textOverflow: "ellipsis" },
  setInput: { width: "100%", boxSizing: "border-box", fontSize: 12.5,
              padding: "5px 8px", borderRadius: 6, border: "1px solid var(--border)",
              background: "var(--card)", color: "var(--foreground)",
              fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" },
  setInputDirty: { border: "1px solid var(--accent)" },
  setInputErr: { border: "1px solid #c0392b" },
  setSelect: { fontSize: 12.5, padding: "5px 8px", borderRadius: 6,
               border: "1px solid var(--border)", background: "var(--card)",
               color: "var(--foreground)" },
  setSrc: { fontSize: 10, color: "var(--muted-foreground)", whiteSpace: "nowrap",
            padding: "1px 6px", borderRadius: 999, border: "1px solid var(--border)" },
  setSecretSet: { fontSize: 11, color: "#3fb950", whiteSpace: "nowrap" },
  setIssue: { marginTop: 6, padding: "6px 9px", borderRadius: 7, fontSize: 11.5,
              whiteSpace: "pre-wrap", wordBreak: "break-word" },
  setIssueErr: { background: "rgba(200,60,60,0.12)",
                 border: "1px solid rgba(200,60,60,0.35)" },
  setIssueWarn: { background: "rgba(240,180,41,0.10)",
                  border: "1px solid rgba(240,180,41,0.40)",
                  color: "var(--muted-foreground)" },
  setNote: { fontSize: 11.5, color: "var(--muted-foreground)", lineHeight: 1.45,
             marginTop: 6, padding: "8px 10px", borderRadius: 8,
             background: "color-mix(in srgb, var(--accent) 7%, transparent)",
             border: "1px solid var(--border)" },
  setSaved: { marginTop: 10, fontSize: 12, color: "#3fb950" },
};

/** key/value rows for the Discovery panel (parsed, not a JSON dump). */
function discoRows(disc) {
  const d = (disc && disc.data) || null;
  if (!d) {
    return jsx("div", { style: S.kvEmpty,
      children: disc && disc.loading ? "\u2026" : "no data" });
  }
  // discover.py report --json emits { stats: {...} }.
  const s = d.stats || d;
  const rows = [
    ["runs", s.runs],
    ["last run", s.last_run ? String(s.last_run).slice(0, 16).replace("T", " ") : "\u2014"],
    ["threads tracked", s.threads_tracked],
    ["codebases seen", s.codebases_seen],
    ["new additions", s.additions],
    ["attached to a project", s.attached],
    ["standalone (workable)", s.standalone],
  ].filter(function (kv) { return kv[1] !== undefined && kv[1] !== null; });

  return kvTable(rows, "disco");
}

/** key/value rows for the loop settings panel. */
function settingsRows(status, act0) {
  const j = (status && status.json) || {};
  const rows = [
    ["angles per visit", j.angles_per_visit],
    ["rounds done", j.rounds_done],
    ["max rounds", j.max_rounds || "unlimited"],
    ["push to GitHub", j.pushed_to_github === undefined ? null
                        : (j.pushed_to_github ? "yes" : "no")],
    ["implementer", shortenModel(j.implementer)],
    ["reviewer", shortenModel(j.reviewer)],
    ["sweep every", j.sweep_minutes ? (j.sweep_minutes / 60) + " h" : null],
  ].filter(function (kv) { return kv[1] !== undefined && kv[1] !== null && kv[1] !== ""; });
  if (!rows.length) {
    return jsx("div", { style: S.kvEmpty,
      children: status && status.error ? String(status.error) : "\u2026" });
  }
  return kvTable(rows, "set");
}

/** Shared two-column key/value table so every panel looks the same. */
function kvTable(rows, key) {
  return jsx("div", { style: S.tblWrap, children: jsx("table", {
    style: S.tbl, children: jsx("tbody", { children: rows.map(function (kv, i) {
      return jsxs("tr", { style: i === rows.length - 1 ? S.trLast : S.tr,
                          children: [
        jsx("td", { style: S.tdKey, children: kv[0] }, "k"),
        jsx("td", { style: S.tdVal, children: shorten(String(kv[1]), 64) }, "v"),
      ] }, key + i);
    }) }),
  }) });
}

/** Is this a URL the widget is willing to turn into a live link?
 *
 *  `see.url` comes from improve.yaml via activity.py, which passes it through
 *  unvalidated. React will render `href="javascript:..."` and a click runs
 *  script in the desktop renderer's context, so the scheme is allowlisted here
 *  rather than trusted. A rejected URL is not hidden -- the caller falls through
 *  to the "no viewable surface" branch, so the operator sees that something was
 *  declared but is not linkable.
 */
function safeUrl(u) {
  const t = String(u == null ? "" : u).trim();
  if (!t) return "";
  // Only http(s). Blocks javascript:, data:, vbscript:, file: and friends.
  if (/^https?:\/\//i.test(t)) return t;
  // A bare host:port or host/path is common in this fleet's manifests.
  if (/^[A-Za-z0-9.\-]+(:\d+)?(\/|$)/.test(t) && !/^[A-Za-z][A-Za-z0-9+.-]*:/.test(t)) {
    return "http://" + t;
  }
  return "";
}

/** The chain-of-thought feed: the agent's reasoning + every tool call. */

/** What this repo IS, and how to look at it.
 *
 *  the operator, 2026-09-28: "whatever repos we select for iteration, is there a way when
 *  the AI gives a description, it's looking at all the code and gives a
 *  description of what that project is and what it's supposed to do. Can we also
 *  have a clickable URL to see the project."
 *
 *  Pure presentation: it renders whatever `brief`/`see` it is handed and never
 *  runs anything. The brief is a ONE-TIME per-repo pass (state/repos/<name>.json)
 *  -- refreshing it every round would re-describe an unchanged project and burn
 *  the round budget on prose nobody re-reads.
 *
 *  props.repos = [{ name, running, rounds, see, brief }]
 */
function reposPanel(props) {
  const repos = props.repos || [];
  if (!repos.length) return null;
  return jsx(Panel, {
    title: "Repos",
    note: "what each project IS \u00b7 one-time description, plus how to look at it",
    children: jsxs("div", { style: { display: "flex", flexDirection: "column", gap: 12 },
      children: repos.map(function (r, i) {
        const see = r.see || {};
        const b = r.brief || {};
        // The REAL brief keys are what_it_is / supposed_to_do (see
        // state/repos/*.json). Reading summary/purpose would show "no
        // description yet" for every repo while briefs sit on disk -- caught by
        // tests/prove_repos_panel.py, which is why it checks the real files.
        const blurb = b.what_it_is || b.summary || b.purpose || "";
        const picked = !!blurb;
        return jsxs("div", {
          style: { padding: "12px 14px", borderRadius: 10, background: "var(--card)",
                   border: "1px solid var(--border)" },
          children: [
            /* ── head: name + live state + the open link ─────────────── */
            jsxs("div", { style: { display: "flex", alignItems: "center", gap: 9,
                                  flexWrap: "wrap" }, children: [
              jsx("span", { style: { fontWeight: 650, fontSize: 13.5,
                                    color: "var(--foreground)" },
                            children: r.name }, "n"),
              r.running
                ? jsx(Chip, { tone: "live", children: "running" }, "c")
                : null,
              jsx("span", { style: S.ckMeta,
                            children: (r.rounds || 0) + " round" + ((r.rounds || 0) === 1 ? "" : "s") }, "r"),
              /* the link lives on the right so it is always in the same place */
              jsx("span", { style: { marginLeft: "auto" }, children: (function () {
                const href = safeUrl(see.url);
                if (href) {
                  return jsx("a", {
                    href: href, target: "_blank", rel: "noreferrer",
                    style: { color: "var(--accent)", textDecoration: "underline dotted",
                             fontSize: 12, cursor: "pointer" },
                    children: "see it \u2197",
                  }, "a");
                }
                if (see.url && !href) {
                  // declared, but not linkable -- say so rather than silently
                  // rendering a dead or dangerous href
                  return jsx("span", { style: S.ckMeta,
                    title: "this url was refused (only http/https is linkable): " + see.url,
                    children: "url not linkable" }, "a");
                }
                if (see.kind) {
                  return jsx("span", { style: S.ckMeta, title: see.note || "",
                    children: (see.path || see.kind) + " (no URL)" }, "a");
                }
                return jsx("span", { style: S.ckMeta,
                  children: "no viewable surface declared" }, "a");
              })() }, "open"),
            ] }),
            /* ── what it is ──────────────────────────────────────────── */
            jsx("div", { style: { marginTop: 8, fontSize: 12.5, lineHeight: 1.5,
                                  color: "var(--foreground)" }, children:
              picked
                ? blurb
                : jsx("span", { style: S.ckMeta, children: (props.briefsError
                    // A FAILED read must not masquerade as "nothing written yet".
                    // The old fallback told the operator to run repo_brief.py
                    // even when a brief already existed and the read had failed
                    // -- a false errand. Found by an independent reviewer.
                    ? "could not read the description: " + props.briefsError
                    : (props.briefsLoading
                        ? "reading the repo\u2026"
                        : "no description yet \u2014 it is written once per repo; open the picker or run repo_brief.py")) }) }, "s"),
            /* ── how to start it (a STRING to copy, never auto-run) ──── */
            see.start_cmd
              ? jsxs("div", { style: { marginTop: 8 }, children: [
                  jsx("span", { style: S.label, children: "if it is not running" }, "l"),
                  jsx("code", { style: Object.assign({}, S.mono, { display: "block",
                      marginBottom: 0, fontFamily: T.mono, fontSize: 11.5,
                      userSelect: "all" }), children: see.start_cmd }, "cmd"),
                ] })
              : null,
            see.note
              ? jsx("div", { style: Object.assign({}, S.ckMeta, { marginTop: 7 }),
                             children: see.note }, "note")
              : null,
          ],
        }, r.name || ("r" + i));
      }) }),
  });
}

/** A titled panel: consistent border, radius, padding and header across the page. */
function Panel(props) {
  return jsxs("section", { style: S.panel, children: [
    jsxs("header", { style: S.panelHead, children: [
      jsx("h3", { style: S.panelTitle, children: props.title }, "t"),
      props.note ? jsx("span", { style: S.panelNote, children: props.note }, "n") : null,
      props.right ? jsx("span", { style: { marginLeft: "auto" }, children: props.right }, "r") : null,
    ] }),
    jsx("div", { style: S.panelBody, children: props.children }),
  ] });
}

/** A small coloured status chip. */
function Chip(props) {
  const tone = props.tone === "live" ? { color: "#fff", background: T.live, borderColor: T.live }
    : props.tone === "wait" ? { color: "#3a2a00", background: T.wait, borderColor: T.wait,
                                 fontWeight: 600 }
    : props.tone === "err"  ? { color: "#fff", background: T.err,  borderColor: T.err }
    : { color: T.muted, background: "transparent", borderColor: T.idleBd };
  return jsx("span", { style: Object.assign({}, S.chip, tone), children: props.children });
}

function agoFromTs(ts) {
  if (!ts) return "";
  // Units differ by source: the session DB / thread_feed ship epoch SECONDS
  // (e.g. 1790602946.38) while Date.now() and Date.parse() are MILLISECONDS.
  // Treating the seconds value as ms made every CoT line read "~496894h ago"
  // (off by 1000x, ~56 years) -- which is what made the live feed look frozen.
  // Normalize: any value below 1e12 is seconds, so scale it to ms.
  let t;
  if (typeof ts === "number") {
    t = ts < 1e12 ? ts * 1000 : ts;
  } else {
    t = Date.parse(String(ts).replace(" ", "T"));
  }
  if (!t || isNaN(t)) return "";
  return ago(Math.max(0, Math.round((Date.now() - t) / 1000)));
}

// How many transcript lines the CoT panel shows. the operator asked for "just a few
// lines ... the last maybe five or so" -- a small fixed window that scrolls
// off, NOT an ever-growing transcript.
var FEED_VISIBLE = 5;

function cotCard(props) {
  const cot = props.cot || {};
  // cot.items is the LIVE TRANSCRIPT straight from the session DB: prompts,
  // assistant narration, every tool call with its arguments, and every result --
  // the same rows the chat window renders. `live` is when the session has a
  // round process running (activity.py sets it).
  // activity.py has shipped this feed as `items` and as `steps` across
  // revisions, and the panel silently rendered its empty branch whenever the
  // two ends disagreed -- that is the "chain of thought just says loading"
  // bug. Accept EITHER name so a future rename cannot blank the panel again.
  const feed = (cot.steps || cot.items || []).slice(-FEED_VISIBLE);
  const live = !!cot.running || (cot.task && (typeof cot.log_age_s !== "number" || cot.log_age_s < 900));
  const sid = cot.session_id || cot.thread_id || "";

  const note = live
    ? (cot.project || "") + " \u00b7 " + feed.length + " lines"
      + (cot.steps_total ? " of " + cot.steps_total + " messages" : "")
      + (typeof cot.log_age_s === "number" ? " \u00b7 updated " + ago(cot.log_age_s) : "")
    : (feed.length ? "last round \u00b7 " + feed.length + " lines" : "nothing in flight");

  // The button that gives the REAL thing: the actual Hermes session, opened as a
  // normal chat thread you can scroll and even talk to.
  const right = [];
  if (sid) {
    right.push(jsx(Button, {
      onClick: function () {
        try { props.host.openSession(sid, { profile: props.profile }); }
        catch (e) { props.onOpenError && props.onOpenError(String(e.message || e)); }
      },
      children: "Open live thread \u2197",
    }, "o"));
  }
  right.push(jsx(Chip, { tone: live ? "live" : "idle",
                         children: live ? "live" : "idle" }, "c"));

  let body;
  if (props.error) {
    body = jsx("div", { style: S.err, children: String(props.error) });
  } else if (!feed.length) {
    body = jsx("div", { style: Object.assign({}, S.empty, { padding: "18px 4px" }),
      children: live
        ? "Waiting for the first line\u2026"
        : "No round in flight. Press Start loop \u2014 the transcript appears here as the agent works." });
  } else {
    // Chat order: oldest at the top, newest at the BOTTOM, auto-scrolled there on
    // every update so it reads like the conversation window. A live "thinking"
    // line is pinned at the bottom while a round is in flight: a round spends most
    // of its wall clock waiting on the model, and without this the panel looks
    // frozen for minutes at a time.
    const rows = feed.map(function (s, i) { return cotStep(s, i); });
    if (live) {
      const age = typeof cot.head_age_s === "number" ? cot.head_age_s : null;
      rows.push(jsxs("div", {
        style: { display: "flex", gap: 9, alignItems: "center", padding: "6px 7px",
                 marginTop: 4, borderTop: "1px dashed var(--border)" },
        children: [
          jsx("span", { style: { color: T.live, fontSize: 11 }, children: "\u25cf" }, "d"),
          jsx("span", {
            style: { fontSize: 11.5, color: "var(--muted-foreground)" },
            children: age === null
              ? "agent is working\u2026"
              : "agent is working \u00b7 " + ago(age) + " since its last line",
          }, "w"),
        ],
      }, "live"));
    }
    body = jsx("div", {
      style: S.cotScroll,
      ref: function (el) { if (el) el.scrollTop = el.scrollHeight; },
      children: rows,
    });
  }

  return jsx(Panel, {
    title: "Live thread",
    note: note,
    right: jsxs(Fragment, { children: right }, "r"),
    children: body,
  });
}

function stepGlyph(s) {
  if (s.kind === "think") return "\u{1F9E0}";
  const t = String(s.text || "");
  if (/^sed\b|^cat\b|^head\b|^tail\b|^less\b/.test(t)) return "\u{1F4C4}"; // reading
  if (/^grep\b|^rg\b|^find\b|^ls\b/.test(t)) return "\u{1F50D}";           // searching
  if (/^git\b/.test(t)) return "\u{1F500}";                                  // vcs
  if (/^pytest\b|^npm\b|^node\b|^python\b/.test(t)) return "\u{1F9EA}";    // test/run
  if (/^patch\b|^apply_patch\b|^write_file\b/.test(t)) return "\u270F\uFE0F"; // edit
  if (/\.py\b|\.ts\b|\.js\b|\.json\b/.test(t)) return "\u{1F4C4}";      // file path
  return "\u25B8";                                                            // other
}

function useCurrentWork(host2, route, tick) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  useEffect(() => {
    if (!route) return;
    (async () => {
      const key = "currentwork";
      const now = pollNow();
      if (INFLIGHT[key]) return;
      const hit = cacheGet(key, 60000, now);   // 60s TTL: server caches per round
      if (hit) { setState({ loading: false, data: hit, error: null }); return; }
      INFLIGHT[key] = true;
      const viaHttp = await fetchJson("/current.json", 30000);
      if (viaHttp && !viaHttp.error) {
        cacheSet(key, viaHttp, now); lastGood("curwork", viaHttp);
        setState({ loading: false, data: viaHttp, error: null });
        INFLIGHT[key] = false;
        return;
      }
      try {
        const out = await runPy(host2, route, "checkpoint.py current", 120000);
        const r = parseJsonOut(out, "current work");
        if (r.ok) { cacheSet(key, r.value, now); lastGood("curwork", r.value); }
        const prev = lastGood("curwork");
        setState({ loading: false, data: r.ok ? r.value : (prev || null),
                   error: r.ok ? null : (prev ? null : r.error) });
      } catch (e) {
        const prev = lastGood("curwork");
        setState({ loading: false, data: prev || null,
                   error: prev ? null : String(e.message || e) });
      } finally { INFLIGHT[key] = false; }
    })();
  }, [host2, route, tick]);
  return state;
}

function cotStep(s, i) {
  // Real transcript entries, the same four things a chat window shows.
  const kind = s.kind || "tool";
  const isThink = kind === "think";
  const isPrompt = kind === "prompt";
  const isResult = kind === "result";
  // Reasoning and prompts are the substance; tool args/results are context and
  // stay short (the full text is one click away in the opened thread).
  const limit = isThink ? 600 : (isPrompt ? 300 : (isResult ? 300 : 260));
  const style = isThink ? S.cotThink : (isPrompt ? S.cotPrompt : S.cotStep);
  const textStyle = isThink ? S.cotThinkText
    : (isPrompt ? S.cotPromptText : S.cotStepText);
  const meta = (isThink ? "thinking" : isPrompt ? "you" : (s.label || "tool"))
    + (s.ts ? " \u00b7 " + agoFromTs(s.ts) : "");
  const inner = jsxs("div", {
    style: S.cotBody,
    children: [
      jsx("div", {
        style: textStyle,
        children: shorten(isThink || isResult ? s.text : tidyPath(s.text), limit),
      }, "t"),
      jsx("div", { style: S.cotMeta, children: meta }, "m"),
    ],
  }, "b");
  return jsxs("div", {
    style: style,
    children: [
      jsx("span", {
        style: Object.assign({}, S.cotIcon, isResult ? { color: T.muted } : null),
        children: isPrompt ? "\u25b6" : (isThink ? "\u273b" : (isResult ? "\u21b3" : "\u25b8")),
      }, "i"),
      inner,
    ],
  }, String(i));
}

function questionBanner(props) {
  const q = props.q || {};
  return jsxs("div", { style: S.askBanner, children: [
    jsx("span", { style: S.askDot, title: "question open", children: "\u25cf" }, "d"),
    jsxs("div", { style: { minWidth: 0, flex: 1 }, children: [
      jsx("div", { style: S.askTitle,
        children: "Question waiting \u2014 " + (q.project || "?") }, "t"),
      jsx("div", { style: S.askBody, children: shorten(q.question || "", 220) }, "b"),
      jsx("div", { style: S.askMeta,
        children: "pinged " + (q.pings_sent != null ? q.pings_sent : (q.reminders || 0)) + "/25 \u00b7 every 5m \u00b7 after 25: assumes \u2014 "
          + shorten(q.assumption || "best judgement", 140) }, "m"),
    ] }, "c"),
  ] });
}

// ================================================================ SETTINGS CARD
// Configure the loop from THIS page — GitHub, models, messaging, projects,
// runtime — without touching Hermes' own settings or hand-editing YAML.
//
// Data contract (scripts/status_server.py):
//   GET  /settings.json  -> {schema, values(redacted), sources, issues, rotation, meta}
//   PUT  /settings.json  -> validate + write settings.local.yaml ONLY
//   POST /settings/secret | /settings/secret/unset -> local-only secret store
//   POST /rotation.json  -> loop.json (angles/visit, cadence, caps, seats)
// The card renders FROM the server schema, so the UI and settings.py can
// never drift apart, and secret values only ever flow outbound as
// "<set via …>" — the raw token never comes back from the server at all.

const SETTING_SECTIONS = [                                                                 
  { id: "github", title: "GitHub",
    note: "checkpoints and rollback work WITHOUT GitHub — tags are local git "
        + "objects. Turning GitHub off = local-only mode: nothing is ever pushed.",
    keys: ["github.enabled", "github.owner", "github.token", "github.token_env",
           "github.gh_cli", "github.require_repo", "github.push_branches"] },
  { id: "models", title: "Models",
    note: "seats are provider:model (e.g. openrouter:anthropic/claude-sonnet-4 or "
        + "custom:mybox:mymodel) — a bare name mis-parses to a cloud provider. "
        + "Implementer and reviewer MUST differ: one brain cannot review itself.",
    keys: ["models.implementer", "models.reviewer", "models.builder_fallback",
           "models.brief_model", "models.brief_profile",
           "models.summary_url", "models.summary_model",
           "models.summary_url_2", "models.summary_model_2"] },
  { id: "notifications", title: "Messaging",
    note: "backend none = silent (safe default). discord posts round progress, "
        + "STUCK alarms and questions; the token is stored locally, never shown.",
    keys: ["notifications.enabled", "notifications.backend",
           "notifications.channel_id", "notifications.ping_user_id",
           "notifications.ping_on_stuck", "notifications.include_summaries",
           "notifications.bot_token", "notifications.bot_token_env",
           "notifications.env_file"] },
  { id: "projects", title: "Projects & rotation",
    note: "paths live in files on THIS machine — set them here, not in code. "
        + "Rotation values below write the live loop.json immediately.",
    keys: ["projects.manifest", "projects.index_rows", "projects.sweep_roots",
           "rot:angles_per_visit", "rot:sweep_minutes", "rot:max_rounds",
           "rot:max_hours", "rot:implementer", "rot:reviewer"] },
  { id: "hermes", title: "Hermes install",
    note: "where Hermes lives on THIS machine. The loop launches round "
        + "workers with this Python and profile. Defaults are auto-detected "
        + "\u2014 fix only if Hermes moved or you run several installs.",
    keys: ["hermes.home", "hermes.python", "hermes.profile",
           "hermes.config_file"] },
  { id: "runtime", title: "Runtime & machine",
    note: "timeouts are seconds. The loop's git identity is what authors its "
        + "checkpoint commits/tags.",
    keys: ["runtime.round_timeout", "runtime.worktree_abandon_secs",
           "runtime.gate_timeout", "git.author_name", "git.author_email",
           "widget.status_port"] },
];

// Field labels that need more than capitalising the leaf name.
const SETTING_LABELS = {
  "github.enabled": "Use GitHub",
  "github.token": "GitHub token (paste)",
  "github.token_env": "Token env var NAME",
  "github.gh_cli": "gh CLI path",
  "github.require_repo": "Require a remote repo",
  "github.push_branches": "Push branch each round",
  "notifications.enabled": "Send notifications",
  "notifications.backend": "Backend",
  "notifications.channel_id": "Channel id",
  "notifications.ping_user_id": "Ping user id (questions)",
  "notifications.ping_on_stuck": "Also ping on STUCK",
  "notifications.include_summaries": "Plain-English round summaries",
  "notifications.bot_token": "Discord bot token (paste)",
  "notifications.env_file": "Fallback env file path",
  "projects.sweep_roots": "Discovery sweep roots",
  "models.summary_url": "Summary LLM endpoint",
  "models.summary_url_2": "Fallback endpoint",
  "runtime.round_timeout": "Round timeout (s)",
  "runtime.worktree_abandon_secs": "Abandon worktree after (s)",
  "runtime.gate_timeout": "Gate timeout (s)",
  "widget.status_port": "Status server port",
  "hermes.home": "Hermes folder",
  "hermes.python": "Hermes Python",
  "hermes.profile": "Round-worker profile",
  "hermes.config_file": "Hermes config.yaml",
  "notifications.bot_token_env": "Bot token env var NAME",
};

function labelFor(key) {
  if (SETTING_LABELS[key]) return SETTING_LABELS[key];
  const leaf = key.split(".")[1] || key;
  return leaf.replace(/_/g, " ");
}

function schemaFor(schemaList, key) {
  const bare = key.indexOf("rot:") === 0 ? null : key;
  if (bare) {
    for (const f of schemaList) if (f.key === bare) return f;
  }
  return null;
}

/** Effective value for one key: redacted values, or the rotation block. */
function currentValue(doc, key) {
  if (key.indexOf("rot:") === 0) {
    return doc && doc.rotation ? doc.rotation[key.slice(4)] : null;
  }
  const parts = key.split(".");
  if (!doc || !doc.values) return null;
  const sec = doc.values[parts[0]];
  return sec ? sec[parts[1]] : null;
}

function isSecretVal(v) {
  return typeof v === "string" && v.indexOf("<set") === 0;
}

/** Is this dotted key declared secret in the server schema? */
function secretKeyIn(schemaList, key) {
  const f = schemaFor(schemaList, key);
  return !!(f && f.secret);
}

/** One editable row. Draft edits are held locally until Save; the source
 *  chip tells the operator which layer currently answers for this key. */
// The circled "?" on EVERY settings row. Hover or click opens a plain-English
// bubble: what the setting does, what to put there, required vs optional.
//
// Why the bubble is PLAIN DOM and not a React child (this killed v1): settings
// labels carry overflow:hidden (ellipsis) and the modal body is a scroll
// container, so an absolutely-positioned React child is CLIPPED INVISIBLE by
// the nearest ancestor no matter its z-index — and the runtime plugin sandbox
// exposes no react-dom, so createPortal is not available either (no useRef
// either). A hand-built element appended to document.body escapes every
// ancestor; handlers use event.currentTarget so no refs are needed.
// One bubble at a time; closed on mouseleave/click-out/scroll/resize.
var _klHelpEl = null;
function _klHelpClose() {
  if (_klHelpEl) {
    try { document.body.removeChild(_klHelpEl); } catch (e) {}
    _klHelpEl = null;
  }
}
function _klHelpOpen(anchor, settingKey, text) {
  _klHelpClose();
  var r = anchor.getBoundingClientRect();
  var box = document.createElement("div");
  box.setAttribute("role", "tooltip");
  box.style.cssText = "position:fixed;z-index:2147483000;width:340px;max-width:92vw;" +
    "background:#ffffff;color:#111827;border:1px solid #d1d5db;border-radius:10px;" +
    "box-shadow:0 8px 24px rgba(0,0,0,.18);padding:10px 12px;font-size:12.5px;" +
    "line-height:1.5;font-weight:400;text-align:left;pointer-events:none;";
  var head = document.createElement("div");
  head.style.cssText = "font-size:10.5px;font-weight:700;letter-spacing:.04em;" +
    "color:#6b7280;text-transform:uppercase;margin-bottom:3px;";
  head.textContent = settingKey || "";
  var body = document.createElement("div");
  body.textContent = text || "no help text for this setting";
  box.appendChild(head); box.appendChild(body);
  document.body.appendChild(box);
  // clamp INSIDE the viewport (measure after append so box size is real)
  var bw = box.offsetWidth || 340, bh = box.offsetHeight || 120;
  var left = Math.max(8, Math.min(r.left - 6, window.innerWidth - bw - 12));
  var top = r.bottom + 8;
  if (top + bh > window.innerHeight - 8) top = Math.max(8, r.top - bh - 8);
  box.style.left = left + "px"; box.style.top = top + "px";
  _klHelpEl = box;
  window.addEventListener("scroll", _klHelpClose, true);
  window.addEventListener("resize", _klHelpClose);
}
function HelpDot(props) {
  return jsx("span", {
    style: S.helpDot,
    onMouseEnter: function (ev) {
      _klHelpOpen(ev.currentTarget, props.settingKey, String(props.text || ""));
    },
    onMouseLeave: _klHelpClose,
    onClick: function (ev) {
      ev.preventDefault(); ev.stopPropagation();
      if (_klHelpEl) { _klHelpClose(); }
      else { _klHelpOpen(ev.currentTarget, props.settingKey, String(props.text || "")); }
    },
    children: "?" }, "hd");
}

function settingsField(props) {
  const key = props.key;
  const sItem = props.schema;
  const val = props.value;
  const draft = props.draft;              // undefined when untouched
  const src = props.source;
  const error = props.error;              // current validation issue text
  const dirty = draft !== undefined;

  const kind = sItem ? sItem.type : "str";
  const secret = sItem ? sItem.secret : false;
  const help = sItem ? (sItem.help || "") : "";
  const isPath = !!(sItem && sItem.path);
  const isSeat = !!(sItem && sItem.model_seat);
  const cur = dirty ? draft : (val == null ? "" : val);

  const helpDot = jsx(HelpDot, { text: help, settingKey: key });

  let control;
  if (kind === "bool") {
    control = jsx("input", {
      type: "checkbox", checked: !!cur,
      onChange: function (ev) { props.onEdit(key, ev.target.checked); },
    }, "cb");
  } else if (key === "notifications.backend") {
    control = jsx("select", {
      style: S.setSelect, value: String(cur || "none"),
      onChange: function (ev) { props.onEdit(key, ev.target.value); },
      children: ["none", "discord"].map(function (o) {
        return jsx("option", { value: o, children: o }, o);
      }),
    }, "sel");
  } else if (isSeat) {
    // Model seat: the REAL Hermes model catalog menu (same searchable,
    // provider-grouped picker the composer uses). Selecting writes
    // "provider:model" into the setting.
    control = jsxs("div", { style: { display: "flex", gap: 6, alignItems: "center" }, children: [
      jsx("input", {
        type: "text", style: S.setInput,
        value: String(cur || ""), placeholder: "provider:model \u2014 or pick from the menu",
        onChange: function (ev) { props.onEdit(key, ev.target.value); },
      }, "seat"),
      jsx(Button, {
        onClick: function () { props.onPickModel && props.onPickModel(key); },
        children: "Browse models\u2026",
      }, "mb"),
    ] }, "seatw");
  } else if (secret) {
    const set = isSecretVal(val);
    control = jsxs("div", { style: { display: "flex", gap: 6, alignItems: "center" }, children: [
        jsx("input", {
          type: "password", style: S.setInput, autoComplete: "off",
          placeholder: set
            ? "\u2022\u2022\u2022\u2022\u2022 already stored (type to replace)"
            : "paste token \u2014 saved to settings.local.yaml only",
          value: dirty ? String(draft) : "",
          onChange: function (ev) { props.onEdit(key, ev.target.value); },
        }, "sec"),
        set && !dirty
          ? jsx("span", { style: S.setSecretSet, children: String(val) }, "ok")
          : null,
      ].filter(Boolean) }, "secw");
  } else if (isPath) {
    // Filesystem setting: text input + Browse (native picker; directory
    // mode for dir-typed paths).
    const isDir = sItem.path === "dir";
    control = jsxs("div", { style: { display: "flex", gap: 6, alignItems: "center" }, children: [
      jsx("input", {
        type: "text", style: S.setInput, value: String(cur || ""),
        placeholder: isDir ? "C:\\path\\to\\folder" : "C:\\path\\to\\file",
        onChange: function (ev) { props.onEdit(key, ev.target.value); },
      }, "pv"),
      jsx(Button, {
        onClick: function () {
          const inp = document.createElement("input");
          inp.type = "file";
          if (isDir) { inp.webkitdirectory = true; }
          inp.onchange = function () {
            if (inp.files && inp.files.length) {
              // dir pickers return a child file; take the first path segment
              const rel = inp.files[0].webkitRelativePath || inp.files[0].name;
              const top = isDir ? rel.split("/")[0] : rel;
              const guessed = String(cur || ".").replace(/[\\/][^\\/]*$/, "");
              props.onEdit(key, (guessed ? guessed + "/" : "") + top);
            }
          };
          inp.click();
        },
        children: isDir ? "Browse\u2026" : "Pick file\u2026",
      }, "pb"),
    ] }, "pw");
  } else {
    control = jsx("input", {
      type: "text", style: S.setInput, value: String(cur == null ? "" : cur),
      onChange: function (ev) { props.onEdit(key, ev.target.value); },
    }, "t");
  }

  return jsxs("div", { style: S.setRow, children: [
    jsxs("span", { style: S.setKey, children: [
      labelFor(key),
      helpDot,
    ] }, "k"),
    jsx("span", { style: { display: "flex", flexDirection: "column", minWidth: 0, flex: 1 }, children: [
      control,
      error ? jsx("span", { style: { color: "#c0392b", fontSize: 10.5, marginTop: 2 }, children: error }, "e") : null,
      src ? jsx("span", { style: S.setSrc, children: src }, "s") : null,
    ] }, "v"),
  ] });
}

// Rotation sliders (rot:*) edit loop.json, not settings.yaml, so they carry no
// schema item of their own — map them to their settings twin purely to borrow
// the layman help text for their "?" bubble (behaviour otherwise unchanged).
const ROT_HELP_ALIAS = {
  "rot:angles_per_visit": "runtime.angles_per_visit",
  "rot:sweep_minutes":    "runtime.sweep_minutes",
  "rot:max_rounds":       "runtime.max_rounds",
  "rot:max_hours":        "runtime.max_hours",
  "rot:implementer":      "models.implementer",
  "rot:reviewer":         "models.reviewer",
};

function settingsSection(props) {
  const sec = props.section;
  const doc = props.doc;
  const rows = sec.keys.map(function (key) {
    let sItem = schemaFor(props.schema, key);
    if (!sItem && ROT_HELP_ALIAS[key]) {
      const twin = schemaFor(props.schema, ROT_HELP_ALIAS[key]);
      sItem = { type: "str", secret: false, help: (twin && twin.help) || "" };
    }
    return settingsField({
      key: key, schema: sItem || { type: "str", secret: false },
      onPickModel: props.onPickModel,
      value: currentValue(doc, key),
      draft: props.drafts[key],
      source: props.sources && (key.indexOf("rot:") === 0
        ? "loop.json" : props.sources[key]),
      error: props.errorFor(key),
      onEdit: props.onEdit, onSaveSecret: props.onSaveSecret,
      onClearSecret: props.onClearSecret, saving: props.saving,
    });
  });
  return jsx("div", { style: S.setSection, children: jsxs("div", { children: [
    jsxs("div", { style: S.setSecHead, children: [
      jsx("span", { style: S.setSecTitle, children: sec.title }, "t"),
      jsx("span", { style: S.setSecNote, children: sec.note }, "n"),
    ] }, "h"),
    rows,
    // The one promise the card must make explicit where GitHub is concerned:
    sec.id === "github" && !(currentValue(doc, "github.enabled"))
      ? jsx("div", { style: S.setNote, children:
          "Local-only mode is fully supported: every round still gets its "
          + "kp/<project>/rNN tags and the Checkpoints panel's rollback works "
          + "exactly the same — tags are local git objects; GitHub only adds "
          + "off-machine backup." } , "note")
      : null,
  ] }, "sec") }, sec.id);
}

function settingsModal(props) {
  const [doc, setDoc] = useState(props.doc || null);
  const [loadErr, setLoadErr] = useState(props.loadError || "");
  const [drafts, setDrafts] = useState({});
  const [issues, setIssues] = useState((props.doc && props.doc.issues) || []);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [baseDraft, setBaseDraft] = useState(SERVER_BASE);
  const [pickKey, setPickKey] = useState("");   // seat being chosen, "" = closed

  const load = useCallback(async function (force) {
    setLoadErr("");
    const r = await httpJson("GET", "/settings.json", null, 20000);
    if (!r.ok) { setLoadErr(r.error); return; }
    setDoc(r.value);
    setIssues((r.value && r.value.issues) || []);
  }, []);

  useEffect(function () { load(false); }, [load]);

  const onEdit = function (key, v) {
    setDrafts(function (d0) { const n = Object.assign({}, d0); n[key] = v; return n; });
    setMsg("");
  };

  const issueFor = function (key) {
    for (const i of issues) if (i.level === "error" && i.key === key)
      return i.message + (i.fix ? " — " + i.fix : "");
    return null;
  };

  const save = async function () {
    setSaving(true); setMsg("");
    const settingsVals = {};
    const rotVals = {};
    Object.keys(drafts).forEach(function (key) {
      if (key.indexOf("rot:") === 0) rotVals[key.slice(4)] = drafts[key];
      else if (!isSecretKey(key)) settingsVals[key] = drafts[key];
    });
    try {
      if (Object.keys(settingsVals).length) {
        const r = await httpJson("PUT", "/settings.json",
                                 { values: settingsVals }, 30000);
        setIssues((r.value && r.value.issues) || []);
        if (!r.ok) { setMsg(r.error || "save rejected"); return; }
      }
      if (Object.keys(rotVals).length) {
        const r = await httpJson("POST", "/rotation.json", rotVals, 30000);
        if (!r.ok) {
          setMsg(r.error || "rotation rejected");
          return;
        }
      }
      setDrafts({});
      await load(true);
      setMsg("Saved \u2713  (settings go to settings.local.yaml; secrets never "
             + "leave this machine's gitignored file)");
      props.onChanged();
    } finally { setSaving(false); }
  };

  const isSecretKey = function (key) {
    const f = schemaFor((doc && doc.schema) || [], key);
    return !!(f && f.secret);
  };

  const saveSecret = async function (key) {
    const v = drafts[key];
    if (v == null || !String(v).trim()) { setMsg("type the token first"); return; }
    setSaving(true); setMsg("");
    const r = await httpJson("POST", "/settings/secret",
                             { key: key, value: String(v) }, 30000);
    setSaving(false);
    if (!r.ok) { setMsg(r.error || "secret save failed"); return; }
    const n = Object.assign({}, drafts); delete n[key]; setDrafts(n);
    if (r.value && r.value.values) setDoc(Object.assign({}, doc, { values: r.value.values }));
    setMsg("Token saved to settings.local.yaml \u2713 (never shown again, never committed)");
    props.onChanged();
  };

  const clearSecret = async function (key) {
    setSaving(true); setMsg("");
    const r = await httpJson("POST", "/settings/secret/unset", { key: key }, 30000);
    setSaving(false);
    if (!r.ok) { setMsg(r.error || "clear failed"); return; }
    if (r.value && r.value.values) setDoc(Object.assign({}, doc, { values: r.value.values }));
    setMsg("Token cleared \u2713");
    props.onChanged();
  };


  function seatParts(seat) {
    const s = String(seat || "");
    const i = s.indexOf(":");
    return i < 0 ? { provider: "", model: s } : { provider: s.slice(0, i), model: s.slice(i + 1) };
  }
  const onPickModel = function (key) { setPickKey(key); };

  // The controller that commits a catalog pick into the seat setting. The
  // menu is Hermes' own — we only decide what a selection MEANS here.
  const seatController = (function () {
    const parts = seatParts(drafts[pickKey] !== undefined ? drafts[pickKey]
                             : (doc && doc.values && doc.values[pickKey]));
    return {
      current: { provider: parts.provider, model: parts.model,
                 label: parts.model },
      presetFor: function () { return {}; },
      applyPreset: function () {},
      select: async function (model, provider) {
        const seat = provider + ":" + model;
        onEdit(pickKey, seat);
        setPickKey("");
        return true;
      },
    };
  })();

  const body = loadErr
    ? jsx("div", { style: S.err, children: loadErr }, "le")
    : (!doc
        ? jsx("div", { style: S.kvEmpty, children: "loading settings\u2026" }, "ld")
        : jsxs("div", { children: [
            SETTING_SECTIONS.map(function (sec) {
              return settingsSection({
                section: sec, doc: doc, schema: doc.schema || [],
                sources: doc.sources || {}, drafts: drafts,
                errorFor: issueFor, onEdit: onEdit, onPickModel: onPickModel,
                onSaveSecret: saveSecret, onClearSecret: clearSecret,
                saving: saving,
              });
            }),
            // Server location (this machine only) — honest escape hatch when
            // the loop's server runs on a non-default port.
            jsxs("div", { style: S.setSection, children: [
              jsxs("div", { style: S.setSecHead, children: [
                jsx("span", { style: S.setSecTitle, children: "Loop server" }, "t"),
                jsx("span", { style: S.setSecNote, children:
                  "where THIS widget talks to the loop (browser-local, not saved to the loop)" }, "n"),
              ] }, "h"),
              jsxs("div", { style: S.setRow, children: [
                jsxs("span", { style: S.setKey, children: [
                  "server base URL",
                  jsx(HelpDot, { text: "The web address this dashboard uses to talk to the loop on THIS computer (default http://127.0.0.1:8765). Only change it if the loop's server runs on another port. Stored in the browser only \u2014 never sent to the loop. Optional.", settingKey: "widget server base URL" }, "hd"),
                ] }, "k"),
                jsxs("div", { style: { display: "flex", gap: 6, alignItems: "center" }, children: [
                  jsx("input", { type: "text", style: S.setInput,
                    defaultValue: SERVER_BASE,
                    onChange: function (ev) { setBaseDraft(ev.target.value); } }, "u"),
                  jsx(Button, { onClick: function () {
                    resetServerBase(baseDraft); load(true);
                  }, children: "Apply" }, "a"),
                ] }, "c"),
                jsx("span", { style: S.setSrc, children: "browser" }, "s"),
              ] }, "row"),
            ] }, "server"),
          ] })
        );

  return jsxs("div", { style: S.modalBack, onClick: function (ev) {
      if (ev.target === ev.currentTarget) props.onClose();
    }, children: [
    pickKey ? jsxs("div", { style: {
        position: "fixed", top: 60, left: "50%", transform: "translateX(-50%)",
        zIndex: 60, background: "#ffffff", color: "#111827",
        border: "1px solid #d1d5db", borderRadius: 10, padding: 8,
        maxHeight: "70vh", overflowY: "auto", width: "min(560px, 92%)",
      }, children: [
        jsxs("div", { style: { display: "flex", alignItems: "center", gap: 8,
                               padding: "2px 6px 8px" }, children: [
          jsx("span", { style: { fontWeight: 650, fontSize: 12.5 },
            children: "Pick a model for " + pickKey }, "t"),
          jsx(Button, { onClick: function () { setPickKey(""); },
                        children: "Close" }, "c"),
        ] }, "h"),
        (ModelCatalogMenu && DropdownMenu && DropdownMenuContent)
          ? jsx(DropdownMenu, { open: true, children: [
              DropdownMenuTrigger
                ? jsx(DropdownMenuTrigger, { asChild: true, children:
                    jsx("span", { style: { display: "none" } }, "tr") }, "trg")
                : null,
              jsx(DropdownMenuContent, { align: "start",
                  className: "w-72 p-0",
                  style: { border: "1px solid #d1d5db", background: "#ffffff" },
                  children: jsx(ModelCatalogMenu, { controller: seatController }, "mcm") },
                "ctn"),
            ].filter(Boolean) }, "dd")
          : jsx("div", { style: S.err,
              children: "Model menu unavailable in this Hermes build \u2014 type provider:model manually." }, "nm"),
      ] }, "mp") : null,
    jsxs("div", { style: Object.assign({}, S.modalCard,
                                        { flexDirection: "column" }), children: [
      jsxs("div", { style: S.panelHead, children: [
        jsx("h3", { style: S.panelTitle, children: "Loop settings" }, "t"),
        jsx("span", { style: S.panelNote,
          children: "writes settings.local.yaml \u00b7 secrets stay masked" }, "n"),
        jsx("span", { style: { marginLeft: "auto", display: "flex", gap: 8 } }, "sp"),
        jsx(Button, { onClick: function () { load(true); }, disabled: saving,
                      children: "Reload" }, "r"),
        jsx(Button, { onClick: save,
                      disabled: saving || !doc || !Object.keys(drafts).length,
                      children: saving ? "Saving\u2026"
                        : ("Save" + (Object.keys(drafts).length
                            ? " " + Object.keys(drafts).length + " change(s)" : "")) }, "sv"),
        jsx(Button, { onClick: props.onClose, children: "Close" }, "c"),
      ] }, "h"),
      jsx("div", { style: Object.assign({}, S.panelBody, S.modalScroll), children: jsxs("div", { children: [
        body,
        issues.filter(function (i) { return i.level === "warn"; }).map(function (i, j) {
          return jsx("div", { style: Object.assign({}, S.setIssue, S.setIssueWarn),
            children: "WARN [" + i.key + "] " + i.message + (i.fix ? " — " + i.fix : "") },
            "w" + j);
        }),
        msg ? jsx("div", { style: S.setSaved, children: msg }, "m") : null,
      ] }) }, "b"),
    ] }, "card"),
  ] }, "settings-modal");
}

function KarpathyLoop(props) {
  const ctx = props.ctx;
  const host2 = ctx.host || host;
  // Route resolution is ASYNC (it consults host2.profileRoutes()), so it lands
  // in state. Until it resolves, route is null and the hooks below stay idle --
  // that is deliberate: passing a half-built route is what threw
  // "reading 'trim'" and blanked every panel.
  const [route, setRoute] = useState(null);
  const [routeErr, setRouteErr] = useState("");
  const [tick, setTick] = useState(0);
  // LIVE HEARTBEAT. Without this the page only re-rendered on a button click,
  // which is why the chain of thought looked like a frozen static list: the data
  // was live, the refresh was not. The TTL cache + INFLIGHT guard keep this
  // cheap (activity re-runs at most every 2.5s, checkpoints every 30s).
  useEffect(function () {
    const id = setInterval(function () {
      setTick(function (t) { return t + 1; });
    }, 2500);
    return function () { clearInterval(id); };
  }, []);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [showPicker, setShowPicker] = useState(false);
  // Two-click remove: first click arms the row, second click confirms. A single
  // accidental click must never rewrite the rotation.
  const [armRemove, setArmRemove] = useState("");

  useEffect(() => {
    currentRoute(host2)
      .then(function (r) {
        setRoute(r);
        setRouteErr("");
      })
      .catch(function (e) {
        setRouteErr(String((e && e.message) || e));
      });
  }, [host2, tick]);

  const status = useLoopStatus(host2, route, tick);
  const act0 = useActivity(host2, route, tick);
  const curWork = useCurrentWork(host2, route, Math.floor(tick / 8)); // slower cadence
  const disc = useDiscovery(host2, route, tick);
  const ckpts = useCheckpoints(host2, route, tick);
  const qst = useQuestions(host2, route, tick);
  // Briefs are per-repo and one-time; fetched once, cached 10 min, never polled.
  const briefs = useRepoBriefs(host2, route, (act0.data && act0.data.rows
    ? act0.data.rows.map(function (r) { return r.name; }) : []));

  const running = !!(status.json && status.json.running);
  const projects = (status.json && status.json.projects) || [];
  // LIVENESS (2026-09-28): `running` alone is INTENT, not reality. The backend
  // computes the honest verdict and ships it as status.liveness -- without this
  // the dashboard reported a healthy loop while wedged, which is the whole reason
  // the detector exists. An independent verifier caught that the widget was still
  // ignoring these fields; this reads them.
  const live0 = (status.json && status.json.liveness) || null;
  const wedged = !!(live0 && live0.wedged);
  const staleOnPaused = !!(live0 && live0.heartbeat_stale_on_paused);
  const hbPidAlive = live0 ? live0.heartbeat_pid_alive : null;
  const hbAge = live0 ? live0.heartbeat_age_min : null;
  const lastRound = live0 ? live0.last_round_age_min : null;

  // The in-flight worker, and its chain of thought, from the activity feed.
  // NOTE: `.projects` here is a list of NAMES (strings), while `.rows` carries
  // the per-project detail -- the two are not interchangeable.
  const cot = (act0.data && act0.data.cot) || {};
  // Which angle this round is working + the prompt it was handed.
  const angle0 = cot.angle || {};
  const actRows = (act0.data && act0.data.rows) || [];
  // Join the polled rows (which carry `see`) with the once-fetched briefs.
  // Either side may be missing and the panel renders honestly either way: a row
  // with no brief shows "no description yet", never a blank space that reads as
  // a broken feature.
  const reposRow = actRows.map(function (r) {
    return {
      name: r.name,
      running: r.running,
      rounds: r.rounds,
      see: r.see || null,
      brief: (briefs.data && briefs.data[r.name]) || null,
    };
  });
  if (!cot.live) {
    const live = actRows.filter(function (r) { return r.running; })[0];
    if (live) {
      cot.live = { project: live.name, task: live.active_task, elapsed: live.elapsed };
    }
  }

    const [showAngles, setShowAngles] = useState(false);
  const anglesData = useAngles(host2, route, tick, showAngles);
  const [showSettings, setShowSettings] = useState(false);

  const act = useCallback(async (label, args, cb) => {
    setBusy(label); setMsg("");
    let out = "";
    try {
      const r = route || (await currentRoute(host2));
      out = await runPy(host2, r, args, 600000);
      const first = out ? out.split("\n").slice(0, 2).join(" · ") : (label + " ok");
      setMsg(first);
      setTick(function (t) { return t + 1; });
    } catch (e) {
      // Hand the failure text to cb too (it may want to retry) -- the Discovery button
      // relies on this to detect the bridge's 30s timeout and re-issue the scan.
      out = label + " failed: " + String(e.message || e);
      setMsg(String(out).slice(0, 220));
    } finally {
      setBusy("");
    }
    // Some callers need the raw output (e.g. to hand live state to the picker).
    if (typeof cb === "function") { try { cb(out); } catch (e) {} }
    return out;
  }, [host2, route, tick]);

  // The embedded picker posts its config here.
  useEffect(() => {
    const onMsg = function (ev) {
      let payload = ev.data;
      if (typeof payload === "string") {
        try { payload = JSON.parse(payload); } catch (e) { return; }
      }
      if (!payload || !payload.action) return;
      const sel = payload.projects || [];
      const apv = payload.angles_per_visit;
      const impl = payload.implementer;
      const rev = payload.reviewer;

      // Reply channel: the picker re-syncs from live state, because the data
      // baked into selector_data.json is a BUILD-TIME snapshot and goes stale.
      const reply = function (msg) {
        try {
          const w = ev.source || (ev.target && ev.target.defaultView) || window.parent;
          if (w && w.postMessage) w.postMessage(JSON.stringify(msg), "*");
        } catch (e) {}
      };

      if (payload.action === "load") {
        // "what is actually on disk right now?"
        act("Read state", "loopctl.py status --json", function (out) {
          // The output is human text FOLLOWED by a JSON block, so slice from the
          // first "{" to the last "}" rather than parsing the whole thing.
          let live = null;
          try {
            const a = String(out || "").indexOf("{");
            const b = String(out || "").lastIndexOf("}");
            if (a >= 0 && b > a) live = JSON.parse(String(out).slice(a, b + 1));
          } catch (e) {}
          reply({ live: live, action: "live" });
        });
        return;
      }
      if (payload.action === "save") {
        const parts = ["loopctl.py config"];
        parts.push("--projects " + JSON.stringify(sel.join(",")));
        if (apv) parts.push("--angles-per-visit " + apv);
        if (impl) parts.push("--implementer " + JSON.stringify(impl));
        if (rev) parts.push("--reviewer " + JSON.stringify(rev));
        // TWO writes, because the loop reads TWO files:
        //   loop.json    -> the on/off switch, seats, cadence
        //   improve.yaml -> WHICH projects rotate (name/path/board/loops/enabled)
        // Writing only the first is the bug where picks silently vanished.
        // `&&` so a manifest failure surfaces instead of reporting success.
        const sync = 'manifest_sync.py sync --projects ' + JSON.stringify(sel.join(","));
        act("Save rotation", parts.join(" ") + " && " + sync, function (out) {
          // Tell the picker to re-read, so it shows what actually landed.
          reply({ action: "saved", ok: true, out: String(out || "").slice(0, 400) });
        });
      } else if (payload.action === "start") {
        act("Start", "loopctl.py start");
      } else if (payload.action === "pause") {
        act("Pause", 'loopctl.py pause --reason "paused from app"');
      } else if (payload.action === "discover") {
        act("Discovery", "karpathy_cron.py discover");
      }
    };
    window.addEventListener("message", onMsg);
    return function () { window.removeEventListener("message", onMsg); };
  }, [act]);

  // The embedded ANGLE LIBRARY posts {source:"karpathy-angles", action:"save-angle"|"reset-angle",
  // edit:{id,...}} here. Replies {source:"karpathy-angles", id, ok, changed|error} so the page
  // can flip its per-row status line; the reply rides JSON.stringify like the picker's does.
  useEffect(() => {
    const onAng = function (ev) {
      let payload = ev.data;
      if (typeof payload === "string") {
        try { payload = JSON.parse(payload); } catch (e) { return; }
      }
      if (!payload || payload.source !== "karpathy-angles") return;
      if (payload.action !== "save-angle" && payload.action !== "reset-angle") return;
      const reply = function (msg) {
        try {
          const w2 = ev.source || (ev.target && ev.target.defaultView) || window.parent;
          if (w2 && w2.postMessage) w2.postMessage(JSON.stringify(msg), "*");
        } catch (e) {}
      };
      const edit = payload.edit || {};
      const reset = payload.action === "reset-angle";
      const body = reset ? { id: edit.id, lens: "", look_for: [], evidence: "", avoid_when: "" }
                         : edit;
      const b64 = btoa(unescape(encodeURIComponent(JSON.stringify({ edits: [body] }))));
      // base64 --edits-b64: cmd.exe eats %-pairs, so URL-encoding is not shell-safe here.
      act("Save angle", "angles.py save --edits-b64 " + b64, function (out) {
        let ok = false, changed = null, err = null;
        try {
          const r = JSON.parse(String(out || "{}").slice(String(out || "").indexOf("{"),
            String(out || "").lastIndexOf("}") + 1));
          ok = !!r.saved || r.saved === 0; changed = (r.changed || []).join(", ") || "no changes";
        } catch (e) { err = "unparseable save result"; }
        reply({ source: "karpathy-angles", id: edit.id, ok: ok, changed: changed, error: err });
      });
    };
    window.addEventListener("message", onAng);
    return function () { window.removeEventListener("message", onAng); };
  }, [act]);

  // "armed" = the loop flag is on; "working" = a worker is actually alive and
  // writing steps. These are DIFFERENT states and conflating them is what let
  // the badge show RUNNING while nothing ran for hours (T&T's card died at its
  // turn cap, so no log was being written and the CoT panel was correctly
  // empty -- yet the badge still said RUNNING).
  // F-M (2026-10-02): cot.steps is the LAST round's transcript and persists
  // in the session DB -- on a paused loop with any past round it made
  // `working` true forever, so the badge showed "FINISHING" indefinitely
  // instead of PAUSED. A transcript only counts as "working" when it is
  // FRESH (log_age_s < 900s, the same recency the CoT card already uses).
  const working = actRows.some(function (r) { return r.running; })
    || !!(cot.steps && cot.steps.length
          && (typeof cot.log_age_s !== "number" || cot.log_age_s < 900));

  // Report the ACTUAL reason instead of a blanket "unavailable" -- an opaque
  // badge is what made this look like a dead widget while it was really a
  // route/permission problem.
  // Pause is DRAIN semantics (the operator, 2026-09-24: "we should let it finish always"): no NEW
  // rounds start, but a round already in flight runs to its commit/checkpoint. While that
  // happens the loop is paused AND working -- show FINISHING, not a bare PAUSED, so the
  // badge never contradicts visible activity.
  const finishing = !running && working;
  const statusLine = routeErr
    ? "no profile route"
    : (status.error
        ? "status unavailable — " + String(status.error).slice(0, 90)
        : (status.loading
            ? "loading…"
            : (wedged ? "WEDGED — flag says running but nothing is"
               : (finishing ? "FINISHING — no new rounds after this one"
                  : (!running ? "PAUSED" : (working ? "RUNNING" : "ARMED — idle"))))));

  // A wedged loop must NEVER read as RUNNING: that was the original bug report
  // ("shows running but all the threads look idle"). The wedge verdict outranks
  // every other state, and badgeErr makes it unmissable.
  const badgeStyle = (routeErr || status.error) ? S.badgeErr
    : (wedged ? S.badgeErr
       : (finishing ? S.badgeWarn : (!running ? S.badgeOff : (working ? S.badgeOn : S.badgeIdle))));

  return jsxs("div", { style: S.wrap, children: [
    /* ── header ─────────────────────────────────────────────────────── */
    jsxs("div", { style: S.hrow, children: [
      jsx("h2", { style: S.h2, children: "Karpathy Loop" }, "h"),
      jsx("span", { style: badgeStyle, children: statusLine }, "b"),
    ] }),
    jsx("div", { style: S.sub, children:
      running && !working
        ? "Loop is armed but no worker is running. If a project shows a stalled card, its turn ended early — the next pass rotates to the next project."
        : "Pick projects, models and review angles \u2014 the loop rotates through them and checkpoints every accepted round." }, "s"),

    /* ── WEDGE banner: flag says running, reality says otherwise ────── */
    // Backend computes this (loopctl._liveness). Shown ABOVE everything else
    // because a wedge is the one state the operator must not miss: the loop
    // reports RUNNING while no round lands in ~2x the round timeout.
    wedged ? jsxs("div", { key: "wb", style: { marginTop: 12, padding: "10px 13px",
                                   borderRadius: 9,
                                   background: "rgba(192,57,43,0.12)",
                                   border: "1px solid #7a2c2c", fontSize: 12.5 },
      children: [
        jsx("div", { style: { fontWeight: 700, color: "#c0392b" },
                     children: "WEDGED \u2014 the loop says RUNNING but nothing is working." }, "wt"),
        jsx("div", { style: { marginTop: 4 }, children: (live0 && live0.wedge_why) || "no round has landed" }, "ww"),
        jsx("div", { style: { marginTop: 4, color: "var(--muted-foreground)" }, children:
          "last round " + (lastRound === null ? "unknown" : Math.round(lastRound) + " min ago")
          + (hbPidAlive === false ? " \u00b7 the runner process is GONE" : "")
          + ".  Fix: Stop, then Start." }, "wf"),
      ] }) : null,

    /* ── paused-but-stale-heartbeat note (honest, not alarming) ─────── */
    (!running && staleOnPaused) ? jsx("div", { key: "sp", style: { marginTop: 12,
        padding: "8px 12px", borderRadius: 9, background: "var(--card)",
        border: "1px solid var(--border)", fontSize: 12,
        color: "var(--muted-foreground)" },
      children: "Heartbeat on disk is left over from a previous run "
        + (hbPidAlive === false ? "(that process is gone). " : "")
        + "No round is in flight \u2014 this is normal after a pause." }) : null,

    /* ── question banner (red dot) ──────────────────────────────────── */
    (qst.open && qst.open.open) ? jsx(questionBanner, { q: qst.open.open, onGoto: function () { setTick(function (t) { return t + 1; }); } }, "qb") : null,

    /* ── controls ───────────────────────────────────────────────────── */
    jsxs("div", { style: S.btnRow, children: [
      jsx(Button, { onClick: function () { act("Start", "loopctl.py start"); },
                    disabled: !!busy || running,
                    children: busy === "Start" ? "Starting\u2026" : "Start loop" }, "1"),
      jsx(Button, { onClick: function () { act("Pause", 'loopctl.py pause --reason "paused from app"'); },
                    disabled: !!busy || !running,
                    children: busy === "Pause" ? "Pausing\u2026" : "Pause" }, "2"),
      jsx(Button, { onClick: function () {
                      // Discovery rebuilds the 225-project selector (git facts per repo). Usually
                      // ~3s with nothing new, ~11s on a full rebuild -- a cold disk once tripped
                      // the bridge's hard 30s shell.exec ceiling.
                      act("Discovery", "karpathy_cron.py discover", function (out) {
                        if (/timed out|timeout/i.test(String(out || ""))) {
                          act("Discovery (retry)", "karpathy_cron.py discover");
                        }
                      });
                    },
                    disabled: !!busy,
                    children: busy === "Discovery" ? "Scanning\u2026"
                      : (busy === "Discovery (retry)" ? "Scanning (retry)\u2026" : "Run discovery") }, "3"),
      jsx(Button, { onClick: function () { setShowPicker(function (v) { return !v; }); },
                    children: showPicker ? "Hide picker" : "Open project picker" }, "4"),
      jsx(Button, { onClick: function () { setShowSettings(true); },
                    children: "Settings\u2026" }, "6"),
      jsx(Button, { onClick: function () { setTick(function (t) { return t + 1; }); },
                    disabled: !!busy, children: "Refresh" }, "5"),
    ] }),

    msg ? jsx("div", { style: S.msg, children: msg }) : null,

    /* ── 1. PROJECTS (the "where is it running" answer) ─────────────── */
    jsx("div", { style: { height: 16 } }),
    jsx(Panel, {
      title: "Projects in rotation",
      note: actRows.length
        ? (actRows.filter(function (r) { return r.running; }).length
            + " running \u00b7 " + actRows.length + " queued")
        : null,
      children: (actRows.length
        ? jsx("div", { style: { overflowX: "auto" }, children: jsx("table", {
            style: S.tbl, children: [
              jsx("thead", { children: jsx("tr", { children: [
                jsx("th", { style: S.thDot, children: "" }, "0"),
                jsx("th", { style: S.th, children: "Project" }, "1"),
                jsx("th", { style: S.th, children: "State" }, "2"),
                jsx("th", { style: S.thR, children: "Rounds" }, "3"),
                jsx("th", { style: S.th, children: "Card" }, "4"),
                jsx("th", { style: S.th, children: "Thread" }, "6"),
                jsx("th", { style: S.thR, children: "Elapsed" }, "5"),
                jsx("th", { style: S.th, children: "See it" }, "8"),
                jsx("th", { style: S.th, children: "" }, "7"),
              ] }) }),
              jsx("tbody", { children: actRows.map(function (p, i) {
                const active = !!p.running;
                // A card whose worker died mid-round (turn cap / superseded)
                // shows as STALLED, not as a vague "idle" -- that ambiguity is
                // what left a dead round looking like a working one for hours.
                const stalled = !active && p.active_status === "blocked";
                const tone = active ? "live"
                  : (stalled ? "err" : (p.never_rotated ? "wait" : "idle"));
                const label = active ? "running"
                  : (stalled ? "stalled" : (p.never_rotated ? "waiting" : "idle"));
                // Every cell carries its own key; React warns per-child on
                // arrays, and a missing key on one cell is enough to warn.
                return jsx("tr", {
                  style: active ? S.trLive : S.tr,
                  children: [
                    jsx("td", { style: S.tdDot, children: jsx("span", {
                      style: active ? S.dotLive : S.dotIdle,
                      children: active ? "\u25B6" : "\u00b7" }, "d") }, i + "d"),
                    jsx("td", { style: active ? S.tdNameLive : S.tdName,
                                children: jsxs("div", { style: { display: "grid", gap: 1 },
                                  children: [
                                    jsx("span", { children: p.name }, "n"),
                                    /* SURFACE: which sub-category of this repo the
                                       current round is on. Rendered under the name
                                       rather than in its own column -- it scopes the
                                       name, it is not a peer of it, and a new column
                                       would push the table past the panel width on
                                       the narrow dock. Absent for single-surface
                                       repos, which is most of them. */
                                    p.surface
                                      ? jsx("span", {
                                          style: S.ckAngle,
                                          title: p.campaign
                                            ? ("campaign " + p.campaign + ": "
                                               + (p.campaign_n - p.campaign_open) + " of "
                                               + p.campaign_n + " surfaces done")
                                            : "working this surface of the repo",
                                          children: p.surface
                                            + (p.surface_pos ? " \u00b7 " + p.surface_pos : "")
                                            + (p.campaign
                                                ? "  \u2934 " + (p.campaign_n - p.campaign_open)
                                                  + "/" + p.campaign_n
                                                : ""),
                                        }, "sf")
                                      : null,
                                  ] }) }, i + "n"),
                    jsx("td", { style: S.td, children: jsx(Chip, {
                      tone: tone, children: label }, "c") }, i + "s"),
                    jsx("td", { style: S.tdR, children: String(p.rounds || 0) }, i + "r"),
                    jsx("td", { style: S.tdMono, children: p.active_task || "\u2014" }, i + "t"),
                    jsx("td", { style: S.tdMono, title: p.thread_title || "",
                      children: p.thread_id
                        ? jsx("span", {
                            style: { cursor: "pointer", textDecoration: "underline dotted" },
                            onClick: function () {
                              try { host2.openSession(p.thread_id, { profile: route && route.profile }); }
                              catch (e) { setMsg("Could not open the thread: " + String(e.message || e)); }
                            },
                            children: "open \u2197 r" + (p.thread_rounds || 0),
                          }, i + "thl")
                        : "\u2014" }, i + "th"),
                    jsx("td", { style: S.tdR, children: p.elapsed || "\u2014" }, i + "e"),
                    /* See it -- the clickable way to LOOK at this project.
                       The URL is opened by the user, never executed by the
                       widget: nothing here runs a shell. When a repo declares
                       no viewable surface we say so with a dash rather than
                       offering a dead link. */
                    jsx("td", { style: S.td, children: (function () {
                      const see = p.see;
                      if (!see || !see.kind) {
                        return jsx("span", { style: S.ckMeta, title:
                          "this project declares no see: block in improve.yaml" ,
                          children: "\u2014" }, "sc");
                      }
                      const href = safeUrl(see.url);
                      if (href) {
                        return jsx("a", {
                          href: href, target: "_blank", rel: "noreferrer",
                          style: { color: "var(--accent)", cursor: "pointer",
                                   textDecoration: "underline dotted", fontSize: 12 },
                          title: (see.note || "") + (see.start_cmd
                            ? "\n\nstart (copy, never auto-run): " + see.start_cmd : ""),
                          children: (see.label || "open") + " \u2197",
                        }, "sl");
                      }
                      if (see.url && !href) {
                        return jsx("span", { style: S.ckMeta,
                          title: "refused (only http/https is linkable): " + see.url,
                          children: "not linkable" }, "sl");
                      }
                      // A local path or a bare declaration: show the path and
                      // keep the start command as a tooltip on the label.
                      return jsx("span", {
                        style: Object.assign({}, S.ckMeta, { color: "var(--foreground)" }),
                        title: see.start_cmd ? "start: " + see.start_cmd : (see.note || ""),
                        children: see.path || see.label || see.kind,
                      }, "sp");
                    })() }, i + "see"),
                    jsx("td", { style: S.td, children: jsx(Button, {
                      // Two-click remove from rotation, right in the table --
                      // no picker needed (the operator, 2026-09-26: "add an easy way to
                      // do this so I don't have to go in the project picker").
                      onClick: function () {
                        if (armRemove !== p.name) { setArmRemove(p.name); return; }
                        setArmRemove("");
                        const keep = actRows
                          .filter(function (r2) { return r2.name !== p.name; })
                          .map(function (r2) { return r2.name; });
                        if (!keep.length) { setMsg("Refusing: cannot remove the last project in rotation."); return; }
                        act("Remove " + p.name,
                          "loopctl.py config --projects " + JSON.stringify(keep.join(",")),
                          function () { setTick(function (t) { return t + 1; }); });
                      },
                      disabled: !!busy,
                      children: armRemove === p.name ? "Click again to confirm" : "Remove",
                    }, i + "rm") }, i + "rmtd"),
                  ],
                }, i);
              }) }),
            ],
          }) })
        : jsx("div", { style: S.empty, children:
            "Nothing yet \u2014 open the picker and tick some repos." })),
    }),

    act0.error ? jsx("div", { style: S.err, children: String(act0.error) }) : null,

    /* ── 1b. REPOS — what each project IS, and how to look at it ────── */
    reposRow.length ? jsx("div", { style: { height: 16 } }) : null,
    reposRow.length ? reposPanel({ repos: reposRow, briefsLoading: briefs.loading,
                                   briefsError: briefs.error }) : null,

    /* ── 2. CHAIN OF THOUGHT (live reasoning + tool steps) ──────────── */
    jsx("div", { style: { height: 16 } }),
    cotCard({ cot: cot, error: null, host: host2, profile: route && route.profile,
      onOpenError: function (m) { setMsg("Could not open the thread: " + m); },
      onRefresh: function () { setTick(function (t) { return t + 1; }); },
      busy: !!busy }),

    /* ── 3. SETTINGS + DISCOVERY (side by side) ─────────────────────── */
    jsx("div", { style: { height: 16 } }),
    jsxs("div", { style: S.grid, children: [
      jsx(Panel, { title: "Loop settings",
                   right: jsxs(Fragment, { children: [
                     jsx(Button, { onClick: function () { setShowSettings(true); },
                       children: "Edit settings\u2026" }, "e"),
                     jsx(Button, { onClick: function () { setShowAngles(true); },
                       children: "Angles & prompts\u2026" }, "a"),
                   ] }),
                   children: settingsRows(status, act0) }, "a"),
      jsx(Panel, { title: "Discovery",
                   children: discoRows(disc) }, "b"),
    ] }),

    /* ── 3b. ANGLE LIBRARY (settings modal) ─────────────────────────── */
    showAngles ? jsx(anglesModal, { regen: anglesData.regen,
      onClose: function () { setShowAngles(false); anglesData.refresh(); } }) : null,

    /* ── 3c. THE SETTINGS CARD (configure the loop from this page) ──── */
    showSettings ? jsx(settingsModal, {
      onClose: function () { setShowSettings(false); },
      onChanged: function () { setTick(function (t) { return t + 1; }); },
    }) : null,

    /* ── 4. PICKER ──────────────────────────────────────────────────── */
    showPicker ? jsxs("div", { style: { marginTop: 16 }, children: [
      jsx(Panel, { title: "Project picker",
        note: "tick repos, choose models and angles, then Save rotation",
        children: (cachedMeta()
          ? jsx("iframe", {
              src: "file:///" + String(cachedMeta().root).replace(/\\/g, "/") + "/selector.html",
              style: S.iframe,
              title: "Karpathy Loop project picker",
            })
          : jsx("div", { style: S.empty, children:
              "The picker page lives in the loop folder, which the widget learns "
              + "from the loop server — and the server has not answered yet. "
              + "Start it:  python scripts/status_server.py" })),
      }),
    ] }) : null,

    /* ── 4b. WORKING ON RIGHT NOW, in plain words (F-V) ─────────────── */
    (curWork.data && curWork.data.running && curWork.data.plain)
      ? jsx(Panel, {
          title: "Working on right now",
          note: (curWork.data.project || "") + " \u00b7 round " +
                (curWork.data.round || "?") + " \u00b7 " +
                (curWork.data.angle || ""),
          children: jsx("div", { style: {
            fontSize: 13, lineHeight: 1.5, color: "var(--foreground)",
            paddingTop: 2,
          }, children: curWork.data.plain }, "p"),
        }, "cw")
      : null,

    /* ── 5. CHECKPOINTS (the rollback net) ──────────────────────────── */
    jsx(Panel, {
      title: "Current angle",
      note: "what this round is iterating on, and the exact prompt it was given",
      right: (angle0 && angle0.id)
        ? jsx(Chip, { tone: "live", children: "round " + (angle0.round || "?") }, "r")
        : null,
      children: (angle0 && angle0.id)
        ? jsxs("div", { children: [
            jsxs("div", { style: S.angRow, children: [
              jsx("span", { style: S.angId, children: angle0.id }, "i"),
              angle0.family
                ? jsx("span", { style: S.angFam, children: angle0.family }, "f") : null,
              angle0.lens
                ? jsx("span", { style: S.angLens, children: angle0.lens }, "l") : null,
            ] }),
            (angle0.angle_history && angle0.angle_history.length)
              ? jsx("div", { style: { marginTop: 7, display: "flex", gap: 6,
                                      flexWrap: "wrap", alignItems: "center" }, children: [
                  jsx("span", { style: S.ckMeta, children: "recent:" }, "rb"),
                  angle0.angle_history.map(function (h, i) {
                    return jsx("span", { key: "h" + i, style: S.ckAngle,
                      title: "round " + (h.round || "?") + (h.rc === 0 ? " (ok)" : " (rc " + h.rc + ")"),
                      children: (h.id || "?") + " r" + (h.round || "?") }, "h" + i);
                  }),
                ] })
              : null,
            angle0.prompt
              ? jsxs("div", { children: [
                  jsx("div", { style: { marginTop: 9, fontSize: 10.5,
                                        color: "var(--muted-foreground)" },
                    children: "prompt sent to the agent \u2014 "
                              + (angle0.prompt_lines || 0) + " lines, showing the first "
                              + angle0.prompt.length + " chars" }, "pl"),
                  jsx("div", { style: S.angPrompt, children: angle0.prompt }, "p"),
                ] })
              : null,
          ] })
        : jsx("div", { style: S.empty, children:
            "No round in flight. The angle is chosen the moment a round starts." }),
    }),

    jsxs("div", { style: { marginTop: 16 }, children: [
      jsx(Panel, {
        title: "Checkpoints",
        note: "every round is tagged and pushed \u2014 roll any one back",
        right: jsx(Button, { onClick: function () { setTick(function (t) { return t + 1; }); },
                             disabled: !!busy, children: "Refresh" }),
        children: jsxs("div", { children: [
          jsx("div", { style: S.sub, children:
            "Tags: kp/<project>/r<NN>-start (before) and kp/<project>/r<NN> (after). "
            + "Rollback restores that exact state \u2014 nothing after it is lost, it stays in git." }, "s"),
          jsx("div", { style: { height: 10 } }, "g"),
          (ckpts.loading
            ? jsx("div", { style: S.empty, children: "loading checkpoints\u2026" }, "l")
            : (ckpts.error
                ? jsx("div", { style: S.err, children: String(ckpts.error) }, "e")
                : (ckpts.rows.length
                    ? jsx("div", { style: { display: "grid", gap: 6 },
                                   children: ckpts.rows.map(function (r, i) {
                        return jsxs("div", { style: { display: "grid", gap: 2,
                                                       padding: "4px 0",
                                                       borderTop: i ? "1px solid var(--border)" : "none" },
                                              title: r.summary || "" , children: [
                          jsxs("div", { style: S.ckRow, children: [
                            jsx("span", { style: S.ckTag, children: r.tag }, "g"),
                            jsx("span", { style: S.ckSha,
                              children: String(r.sha || "").slice(0, 7) }, "s"),
                            r.angle ? jsx("span", { style: S.ckAngle, children: r.angle }, "a") : null,
                            jsx("span", { style: S.ckMeta,
                              children: String(r.created || "").slice(0, 16).replace("T", " ") }, "m"),
                          ] }, "r"),
                          /* F-S: plain-language line FIRST (the operator: "explain
                             like I'm in fifth grade"); the technical line
                             follows smaller/dimmer for the engineer read. */
                          /* F-S2: FULL plain text, never clamped. */
                          r.plain ? jsx("div", { style: {
                            fontSize: 11.5, color: "var(--foreground)",
                            lineHeight: 1.4, paddingLeft: 2,
                          }, children: r.plain }, "pl") : null,
                          r.summary ? jsx("div", { style: S.ckMeta, children: r.summary }, "s") : null,
                        ] }, i);
                      }) }, "r")
                    : jsx("div", { style: S.empty, children:
                        "No checkpoints yet. Press Start \u2014 the loop lays one down "
                        + "before it touches any code." })))),
        ] }),
      }),
    ] }),
  ] });
}

// ---------------------------------------------------------------- export
var plugin_default = {
  id: ID,
  name: "Karpathy Loop",
  defaultEnabled: true,
  register(ctx) {
    ctx.register({
      id: "page",
      area: ROUTES_AREA,
      data: { path: "/karpathy" },
      render: function () { return jsx(KarpathyLoop, { ctx: ctx }); },
    });
    ctx.register({
      id: "navigation",
      area: SIDEBAR_NAV_AREA,
      data: { path: "/karpathy", label: "Karpathy Loop", codicon: "sync" },
    });
    ctx.register({
      id: "open",
      area: PALETTE_AREA,
      data: {
        id: "karpathy-loop.open",
        label: "Open Karpathy Loop",
        keywords: ["karpathy", "improve", "loop", "codebase", "rotate", "checkpoint"],
        run: function () { host.navigate("/karpathy"); },
      },
    });
  },
};

export default plugin_default;
export { anglesSettingsPanel, anglesModal, angleEditor, KarpathyLoop,
         settingsModal, settingsSection, settingsField, SETTING_SECTIONS,
         schemaFor, currentValue, isSecretVal, secretKeyIn, labelFor,
         httpJson, ensureMeta, cachedMeta, resetServerBase };

