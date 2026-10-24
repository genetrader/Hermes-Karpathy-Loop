// ../../Users/gene/AppData/Local/hermes/desktop-plugins/karpathy-loop/plugin.js
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Fragment, jsx, jsxs } from "react/jsx-runtime";
import {
  Button,
  host,
  useValue,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  PALETTE_AREA
} from "@hermes/plugin-sdk";
var ID = "karpathy-loop";
var ROOT = "C:\\CODING\\project-improver";
var PY = "C:\\Users\\gene\\AppData\\Local\\hermes\\hermes-agent\\venv\\Scripts\\python.exe";
var SEL_HTML = ROOT + "\\selector.html";
async function currentRoute(host2) {
  const profile = host2.state.profile.get();
  const connectionId = host2.state.connectionId?.get() || "local";
  const routes = await host2.profileRoutes();
  const route = (routes || []).find(
    (r) => r.profile === profile && r.connectionId === connectionId
  ) || (routes || [])[0];
  if (!route) {
    throw new Error(
      "No connected Hermes profile. Open a connection first, then reopen this tab."
    );
  }
  return route;
}
var _clock = function() {
  return Date.now();
};
var INFLIGHT = {};
var CACHE = {};
var ACTIVITY_TTL_MS = 2500;
var CHECKPOINT_TTL_MS = 3e4;
function pollNow() {
  return _clock();
}
function cacheGet(key, ttlMs, now) {
  var e = CACHE[key];
  if (!e) return null;
  if (now - e.t > ttlMs) return null;
  return e.v;
}
function cacheSet(key, value, now) {
  CACHE[key] = { t: now, v: value };
}
function parseJsonOut(out, label) {
  const raw = String(out == null ? "" : out);
  const t = raw.trim();
  if (!t) return { ok: false, value: null, error: (label || "command") + " returned no output", raw };
  try {
    return { ok: true, value: JSON.parse(t), error: null, raw };
  } catch (e) {
    const msg = String(e && e.message || e);
    const looksTruncated = !/[}\]]\s*$/.test(t);
    return {
      ok: false,
      value: null,
      raw,
      error: (label || "command") + " did not return JSON (" + msg + ")" + (looksTruncated ? "\nThe output looks truncated (it does not end with } or ]).\nfirst 160: " + JSON.stringify(t.slice(0, 160)) + "\nlast 160:  " + JSON.stringify(t.slice(-160)) : "\nfirst 160: " + JSON.stringify(t.slice(0, 160)))
    };
  }
}
function assertOwner(host2, route) {
  const nowConn = host2.state.connectionId?.get?.() || "local";
  const nowProf = host2.state.profile?.get?.();
  if (route.connectionId !== nowConn || route.profile !== nowProf) {
    throw new Error("Route changed during command");
  }
}
async function runPy(host2, route, args, timeoutMs) {
  assertOwner(host2, route);
  const command = 'cd /d "' + ROOT.replace(/\//g, "\\") + '" && "' + PY + '" ' + args;
  const result = await host2.requestProfile(
    route,
    "shell.exec",
    { command },
    timeoutMs || 3e5
  );
  assertOwner(host2, route);
  if (!result) throw new Error("No result from shell.exec for: " + command);
  if (result.code !== 0) {
    throw new Error(
      "Command failed: " + command + "\n" + String(result.stderr || result.stdout || "").slice(0, 400)
    );
  }
  return String(result.stdout == null ? "" : result.stdout).trim();
}
function useActivity(host2, route, tick) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  useEffect(() => {
    let alive = true;
    if (!route) return () => {
      alive = false;
    };
    (async () => {
      const key = "activity";
      const now = pollNow();
      if (INFLIGHT[key]) return;
      const hit = cacheGet(key, ACTIVITY_TTL_MS, now);
      if (hit) {
        setState({ loading: false, data: hit, error: null });
        return;
      }
      INFLIGHT[key] = true;
      try {
        const out = await runPy(host2, route, "activity.py", 9e4);
        if (!alive) return;
        const r = parseJsonOut(out, "activity");
        if (r.ok) cacheSet(key, r.value, now);
        setState({ loading: false, data: r.value, error: r.ok ? null : r.error });
      } catch (e) {
        if (!alive) return;
        setState({ loading: false, data: null, error: String(e.message || e) });
      } finally {
        INFLIGHT[key] = false;
      }
    })();
    return () => {
      alive = false;
    };
  }, [host2, route && route.connectionId, route && route.profile, tick]);
  return state;
}
function useCheckpoints(host2, route, tick) {
  const [state, setState] = useState({ loading: true, rows: [], error: null });
  useEffect(() => {
    let alive = true;
    if (!route) return () => {
      alive = false;
    };
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
      try {
        const out = await runPy(host2, route, "checkpoint.py all", 9e4);
        if (!alive) return;
        const r = parseJsonOut(out, "checkpoints");
        const v = r.value;
        const rows = v ? Array.isArray(v) ? v : v.checkpoints || v.rows || [] : [];
        if (r.ok) cacheSet(key, rows, now);
        setState({ loading: false, rows, error: r.ok ? null : r.error });
      } catch (e) {
        if (!alive) return;
        setState({ loading: false, rows: [], error: String(e.message || e) });
      } finally {
        INFLIGHT[key] = false;
      }
    })();
    return () => {
      alive = false;
    };
  }, [host2, route && route.connectionId, route && route.profile, tick]);
  return state;
}
function useLoopStatus(host2, route, tick) {
  const [state, setState] = useState({ loading: true, text: "", json: null, error: null });
  useEffect(() => {
    let alive = true;
    if (!route) return () => {
      alive = false;
    };
    (async () => {
      try {
        const out = await runPy(host2, route, "loopctl.py status --json", 3e4);
        if (!alive) return;
        const r = parseJsonOut(out, "loopctl status");
        setState({ loading: false, text: out, json: r.value, error: r.ok ? null : r.error });
      } catch (e) {
        if (!alive) return;
        setState({ loading: false, text: "", json: null, error: String(e.message || e) });
      }
    })();
    return () => {
      alive = false;
    };
  }, [host2, route && route.connectionId, route && route.profile, tick]);
  return state;
}
function useDiscovery(host2, route, tick) {
  const [state, setState] = useState({ loading: true, data: null, error: null });
  useEffect(() => {
    let alive = true;
    if (!route) return () => {
      alive = false;
    };
    (async () => {
      try {
        const out = await runPy(host2, route, "discover.py report --json", 6e4);
        if (!alive) return;
        const r = parseJsonOut(out, "discovery report");
        setState({ loading: false, data: r.value, error: r.ok ? null : r.error });
      } catch (e) {
        if (!alive) return;
        setState({ loading: false, data: null, error: String(e.message || e) });
      }
    })();
    return () => {
      alive = false;
    };
  }, [host2, route && route.connectionId, route && route.profile, tick]);
  return state;
}
function shorten(s, n) {
  const t = String(s == null ? "" : s).replace(/\s+/g, " ").trim();
  if (t.length <= n) return t;
  const cut = t.slice(0, n);
  const sp = cut.lastIndexOf(" ");
  return (sp > n * 0.6 ? cut.slice(0, sp) : cut) + "\u2026";
}
function shortenModel(s) {
  const t = String(s == null ? "" : s);
  const parts = t.split(":");
  return parts.length > 1 ? parts[parts.length - 1] : t;
}
function ago(sec) {
  const s = Math.max(0, Number(sec) || 0);
  if (s < 60) return Math.round(s) + "s ago";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  return (s / 3600).toFixed(1) + "h ago";
}
function tidyPath(s) {
  const t = String(s || "").trim();
  if (!t || /\s/.test(t)) return t;
  if (!/^[A-Za-z]:[\\/]|^[\\/~.]/.test(t)) return t;
  const wt = t.match(/\.worktrees[\\/][^\\/]+[\\/](.+)$/);
  const rel = wt ? wt[1] : t;
  const parts = rel.split(/[\\/]+/).filter(Boolean);
  return parts.length > 2 ? parts.slice(-2).join("/") : parts.join("/");
}
function discoRows(disc) {
  const d = disc && disc.data || null;
  if (!d) {
    return jsx("div", {
      style: S.kvEmpty,
      children: disc && disc.loading ? "\u2026" : "no data"
    });
  }
  const s = d.stats || d;
  const rows = [
    ["runs", s.runs],
    ["last run", s.last_run ? String(s.last_run).slice(0, 16).replace("T", " ") : "\u2014"],
    ["threads tracked", s.threads_tracked],
    ["codebases seen", s.codebases_seen],
    ["new additions", s.additions],
    ["attached to a project", s.attached],
    ["standalone (workable)", s.standalone]
  ].filter(function(kv) {
    return kv[1] !== void 0 && kv[1] !== null;
  });
  return kvTable(rows, "disco");
}
function settingsRows(status, act0) {
  const j = status && status.json || {};
  const rows = [
    ["angles per visit", j.angles_per_visit],
    ["rounds done", j.rounds_done],
    ["max rounds", j.max_rounds || "unlimited"],
    ["push to GitHub", j.pushed_to_github === void 0 ? null : j.pushed_to_github ? "yes" : "no"],
    ["implementer", shortenModel(j.implementer)],
    ["reviewer", shortenModel(j.reviewer)],
    ["sweep every", j.sweep_minutes ? j.sweep_minutes / 60 + " h" : null]
  ].filter(function(kv) {
    return kv[1] !== void 0 && kv[1] !== null && kv[1] !== "";
  });
  if (!rows.length) {
    return jsx("div", {
      style: S.kvEmpty,
      children: status && status.error ? String(status.error) : "\u2026"
    });
  }
  return kvTable(rows, "set");
}
function kvTable(rows, key) {
  return jsx("div", { style: S.tblWrap, children: jsx("table", {
    style: S.tbl,
    children: jsx("tbody", { children: rows.map(function(kv, i) {
      return jsxs("tr", {
        style: i === rows.length - 1 ? S.trLast : S.tr,
        children: [
          jsx("td", { style: S.tdKey, children: kv[0] }, "k"),
          jsx("td", { style: S.tdVal, children: shorten(String(kv[1]), 64) }, "v")
        ]
      }, key + i);
    }) })
  }) });
}
function Panel(props) {
  return jsxs("section", { style: S.panel, children: [
    jsxs("header", { style: S.panelHead, children: [
      jsx("h3", { style: S.panelTitle, children: props.title }, "t"),
      props.note ? jsx("span", { style: S.panelNote, children: props.note }, "n") : null,
      props.right ? jsx("span", { style: { marginLeft: "auto" }, children: props.right }, "r") : null
    ] }),
    jsx("div", { style: S.panelBody, children: props.children })
  ] });
}
function Chip(props) {
  const tone = props.tone === "live" ? { color: "#fff", background: T.live, borderColor: T.live } : props.tone === "wait" ? {
    color: "#3a2a00",
    background: T.wait,
    borderColor: T.wait,
    fontWeight: 600
  } : props.tone === "err" ? { color: "#fff", background: T.err, borderColor: T.err } : { color: T.muted, background: "transparent", borderColor: T.idleBd };
  return jsx("span", { style: Object.assign({}, S.chip, tone), children: props.children });
}
function cotCard(props) {
  const cot = props.cot || {};
  const steps = cot.steps || [];
  const live = cot.task && (typeof cot.log_age_s !== "number" || cot.log_age_s < 900);
  const note = live ? cot.project + " \xB7 " + cot.task + (cot.steps_total ? " \xB7 " + cot.steps_total + " steps" : "") + (typeof cot.log_age_s === "number" ? " \xB7 updated " + ago(cot.log_age_s) : "") : "nothing in flight";
  let body;
  if (props.error) {
    body = jsx("div", { style: S.err, children: String(props.error) });
  } else if (!steps.length) {
    body = jsx("div", {
      style: Object.assign({}, S.empty, { padding: "18px 4px" }),
      children: live ? "Waiting for the first step\u2026" : "No round in flight. Press Start loop \u2014 steps appear here as the agent works."
    });
  } else {
    body = jsx("div", {
      style: S.cotScroll,
      children: steps.map(function(s, i) {
        return cotStep(s, i);
      })
    });
  }
  return jsx(Panel, {
    title: "Chain of thought",
    note,
    right: live ? jsx(Chip, { tone: "live", children: "live" }) : null,
    children: body
  });
}
function stepGlyph(s) {
  if (s.kind === "think") return "\u{1F9E0}";
  const t = String(s.text || "");
  if (/^sed\b|^cat\b|^head\b|^tail\b|^less\b/.test(t)) return "\u{1F4C4}";
  if (/^grep\b|^rg\b|^find\b|^ls\b/.test(t)) return "\u{1F50D}";
  if (/^git\b/.test(t)) return "\u{1F500}";
  if (/^pytest\b|^npm\b|^node\b|^python\b/.test(t)) return "\u{1F9EA}";
  if (/^patch\b|^apply_patch\b|^write_file\b/.test(t)) return "\u270F\uFE0F";
  if (/\.py\b|\.ts\b|\.js\b|\.json\b/.test(t)) return "\u{1F4C4}";
  return "\u25B8";
}
function cotStep(s, i) {
  const isThink = s.kind === "think";
  const inner = jsxs("div", {
    style: S.cotBody,
    children: [
      jsx("div", {
        style: isThink ? S.cotThinkText : S.cotStepText,
        children: shorten(
          isThink ? s.text : tidyPath(s.text),
          isThink ? 320 : 190
        )
      }, "t"),
      jsx("div", {
        style: S.cotMeta,
        children: (isThink ? "reasoning" : s.kind) + (s.duration ? " \xB7 " + s.duration : "") + (s.note ? " \xB7 " + shorten(s.note, 80) : "")
      }, "m")
    ]
  }, "b");
  return jsxs("div", {
    style: isThink ? S.cotThink : S.cotStep,
    children: [
      jsx("span", {
        style: S.cotIcon,
        children: stepGlyph(s)
      }, "i"),
      inner
    ]
  }, String(i));
}
var T = {
  radius: 12,
  radiusSm: 8,
  gap: 16,
  pad: 14,
  // Surfaces
  card: "var(--card)",
  border: "var(--border)",
  fg: "var(--foreground)",
  muted: "var(--muted-foreground)",
  accent: "var(--accent)",
  // Semantic (works on light and dark app themes)
  live: "#2f9e44",
  liveBg: "rgba(47,158,68,0.14)",
  liveBd: "rgba(47,158,68,0.45)",
  wait: "#b8860b",
  waitBg: "rgba(184,134,11,0.14)",
  waitBd: "rgba(184,134,11,0.45)",
  idle: "#6b7280",
  idleBg: "rgba(107,114,128,0.14)",
  idleBd: "rgba(107,114,128,0.35)",
  err: "#c0392b",
  errBg: "rgba(192,57,43,0.12)",
  errBd: "rgba(192,57,43,0.45)",
  mono: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
};
var S = {
  wrap: { padding: "18px 22px", fontFamily: "inherit", color: "var(--foreground)" },
  hrow: { display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" },
  h2: { margin: 0, fontSize: 20, fontWeight: 650, letterSpacing: -0.2 },
  badgeOn: {
    fontSize: 10.5,
    padding: "3px 10px",
    borderRadius: 999,
    border: "1px solid #1c4a38",
    color: "#fff",
    background: "#2f9e44",
    fontWeight: 700,
    letterSpacing: 0.5,
    textTransform: "uppercase"
  },
  badgeOff: {
    fontSize: 10.5,
    padding: "3px 10px",
    borderRadius: 999,
    border: "1px solid #6b5217",
    color: "#fff",
    background: "#b8860b",
    fontWeight: 700,
    letterSpacing: 0.5,
    textTransform: "uppercase"
  },
  // Armed but not working: distinct from both RUNNING and PAUSED so the badge
  // never claims activity that isn't happening.
  badgeIdle: {
    fontSize: 10.5,
    padding: "3px 10px",
    borderRadius: 999,
    border: "1px solid var(--border)",
    color: "var(--muted-foreground)",
    background: "color-mix(in srgb, var(--foreground) 6%, transparent)",
    fontWeight: 700,
    letterSpacing: 0.5,
    textTransform: "uppercase"
  },
  badgeErr: {
    fontSize: 10.5,
    padding: "3px 10px",
    borderRadius: 999,
    border: "1px solid #7a2c2c",
    color: "#fff",
    background: "#c0392b",
    fontWeight: 700,
    letterSpacing: 0.5,
    textTransform: "uppercase"
  },
  sub: { color: "var(--muted-foreground)", fontSize: 12.5, marginTop: 6 },
  btnRow: {
    display: "flex",
    gap: 8,
    marginTop: 16,
    flexWrap: "wrap",
    alignItems: "center",
    paddingTop: 14,
    borderTop: "1px solid var(--border)"
  },
  msg: {
    marginTop: 12,
    padding: "9px 13px",
    borderRadius: 9,
    fontSize: 12.5,
    background: "var(--card)",
    border: "1px solid var(--border)"
  },
  grid: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginTop: 18 },
  label: {
    fontSize: 11.5,
    textTransform: "uppercase",
    letterSpacing: ".06em",
    color: "var(--muted-foreground)",
    marginBottom: 7
  },
  mono: {
    padding: "5px 10px",
    marginBottom: 4,
    borderRadius: 7,
    background: "var(--card)",
    border: "1px solid var(--border)",
    fontFamily: "ui-monospace,Menlo,monospace",
    fontSize: 12
  },
  pre: {
    background: "var(--card)",
    border: "1px solid var(--border)",
    borderRadius: 9,
    padding: 11,
    fontSize: 11.5,
    overflow: "auto",
    margin: 0
  },
  iframe: {
    width: "100%",
    height: 780,
    border: "1px solid var(--border)",
    borderRadius: 11,
    background: "var(--card)"
  },
  empty: { color: "var(--muted-foreground)" },
  // Same 8px row rhythm as the main table so the two panels read as one
  // system; the value column is right-weight and tabular so numbers line up.
  tdKey: {
    padding: "8px 10px",
    color: "var(--muted-foreground)",
    fontSize: 12.5,
    whiteSpace: "nowrap",
    width: "42%",
    borderTop: "1px solid var(--border)",
    fontWeight: 450
  },
  tdVal: {
    padding: "8px 10px",
    fontSize: 12.5,
    fontVariantNumeric: "tabular-nums",
    color: "var(--foreground)",
    wordBreak: "break-word",
    borderTop: "1px solid var(--border)"
  },
  trLast: {},
  /* ── panels & chips ─────────────────────────────────────────────── */
  panel: {
    background: "var(--card)",
    border: "1px solid var(--border)",
    borderRadius: 12,
    overflow: "hidden"
  },
  panelHead: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "10px 14px",
    borderBottom: "1px solid var(--border)",
    background: "color-mix(in srgb, var(--foreground) 3%, transparent)"
  },
  panelTitle: {
    margin: 0,
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: 0.7,
    textTransform: "uppercase",
    color: "var(--foreground)"
  },
  panelNote: { fontSize: 11.5, color: "var(--muted-foreground)" },
  panelBody: { padding: 14 },
  chip: {
    display: "inline-flex",
    alignItems: "center",
    gap: 5,
    padding: "2px 9px",
    borderRadius: 999,
    fontSize: 10.5,
    fontWeight: 700,
    letterSpacing: 0.4,
    textTransform: "uppercase",
    border: "1px solid transparent"
  },
  thDot: {
    width: 30,
    padding: "8px 4px 8px 14px",
    borderBottom: "1px solid var(--border)"
  },
  ckRow: {
    display: "grid",
    gridTemplateColumns: "1fr auto auto",
    gap: 12,
    alignItems: "center",
    padding: "7px 10px",
    borderRadius: 8,
    background: "color-mix(in srgb, var(--foreground) 3%, transparent)",
    border: "1px solid var(--border)"
  },
  ckTag: {
    fontSize: 11.5,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
    fontWeight: 600,
    color: "var(--foreground)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    flex: "1 1 auto"
  },
  ckMeta: {
    fontSize: 10.5,
    color: "var(--muted-foreground)",
    whiteSpace: "nowrap",
    fontVariantNumeric: "tabular-nums",
    flex: "0 0 auto"
  },
  ckSha: {
    fontSize: 10.5,
    color: "var(--foreground)",
    whiteSpace: "nowrap",
    padding: "1px 6px",
    borderRadius: 5,
    flex: "0 0 auto",
    background: "color-mix(in srgb, var(--accent) 10%, transparent)",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
  },
  /* ── projects table ─────────────────────────────────────────────── */
  tblWrap: {
    border: "1px solid var(--border)",
    borderRadius: 10,
    overflow: "hidden",
    background: "var(--card)"
  },
  tbl: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: 12.5,
    tableLayout: "fixed"
  },
  th: {
    textAlign: "left",
    padding: "8px 10px",
    fontWeight: 600,
    fontSize: 10.5,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    color: "var(--muted-foreground)",
    borderBottom: "1px solid var(--border)",
    whiteSpace: "nowrap"
  },
  thR: {
    textAlign: "right",
    padding: "8px 10px",
    fontWeight: 600,
    fontSize: 10.5,
    letterSpacing: 0.6,
    textTransform: "uppercase",
    color: "var(--muted-foreground)",
    borderBottom: "1px solid var(--border)",
    whiteSpace: "nowrap"
  },
  thP: {
    width: 26,
    padding: "8px 4px 8px 12px",
    borderBottom: "1px solid var(--border)"
  },
  tr: { borderBottom: "1px solid var(--border)" },
  trLive: {
    borderBottom: "1px solid var(--border)",
    background: "color-mix(in srgb, var(--accent) 10%, transparent)"
  },
  td: { padding: "9px 10px", verticalAlign: "middle" },
  tdR: {
    padding: "9px 10px",
    textAlign: "right",
    verticalAlign: "middle",
    fontVariantNumeric: "tabular-nums",
    width: 72,
    whiteSpace: "nowrap",
    color: "var(--muted-foreground)"
  },
  tdREm: {
    padding: "9px 10px",
    textAlign: "right",
    verticalAlign: "middle",
    fontVariantNumeric: "tabular-nums",
    width: 72,
    whiteSpace: "nowrap",
    color: "var(--muted-foreground)",
    fontStyle: "normal"
  },
  tdName: {
    padding: "9px 10px",
    verticalAlign: "middle",
    color: "var(--muted-foreground)"
  },
  tdNameLive: {
    padding: "9px 10px",
    verticalAlign: "middle",
    color: "var(--foreground)",
    fontWeight: 600
  },
  tdMono: {
    padding: "9px 10px",
    verticalAlign: "middle",
    fontSize: 11.5,
    color: "var(--muted-foreground)",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
  },
  tdDot: { padding: "9px 4px 9px 12px", width: 26, verticalAlign: "middle" },
  pillRun: {
    display: "inline-block",
    padding: "2px 8px",
    borderRadius: 999,
    fontSize: 10.5,
    fontWeight: 700,
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: "#fff",
    background: "#2f9e44"
  },
  pillWait: {
    display: "inline-block",
    padding: "2px 8px",
    borderRadius: 999,
    fontSize: 10.5,
    fontWeight: 700,
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: "#fff",
    background: "#b8860b"
  },
  pillIdle: {
    display: "inline-block",
    padding: "2px 8px",
    borderRadius: 999,
    fontSize: 10.5,
    fontWeight: 700,
    letterSpacing: 0.4,
    textTransform: "uppercase",
    color: "var(--muted-foreground)",
    background: "var(--border)"
  },
  err: {
    marginTop: 10,
    padding: "9px 11px",
    borderRadius: 8,
    fontSize: 12,
    color: "var(--foreground)",
    background: "rgba(200,60,60,0.12)",
    border: "1px solid rgba(200,60,60,0.35)",
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
  },
  errenv: {},
  /* chain of thought */
  cotCard: {
    background: "var(--card)",
    border: "1px solid var(--border)",
    borderRadius: 12,
    overflow: "hidden"
  },
  cotHead: { display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", borderBottom: "1px solid var(--border)", background: "color-mix(in srgb, var(--foreground) 3%, transparent)" },
  cotTitle: { fontSize: 12, fontWeight: 700, letterSpacing: 0.7, textTransform: "uppercase", color: "var(--foreground)" },
  cotSub: { fontSize: 11.5, color: "var(--muted-foreground)" },
  cotScroll: {
    padding: "12px 12px 16px",
    display: "grid",
    gap: 6,
    maxHeight: 380,
    overflowY: "auto",
    scrollbarGutter: "stable"
  },
  cotStep: {
    display: "flex",
    gap: 10,
    padding: "5px 7px",
    borderRadius: 7,
    alignItems: "flex-start",
    background: "transparent"
  },
  cotThink: {
    display: "flex",
    gap: 10,
    padding: "9px 10px",
    borderRadius: 8,
    alignItems: "flex-start",
    margin: "6px 0",
    background: "color-mix(in srgb, var(--accent) 9%, transparent)",
    borderLeft: "2px solid var(--accent)"
  },
  cotIcon: {
    fontSize: 12,
    width: 17,
    textAlign: "center",
    lineHeight: "17px",
    flexShrink: 0,
    opacity: 0.9
  },
  cotBody: { minWidth: 0, flex: 1 },
  cotStepText: {
    fontSize: 12,
    lineHeight: 1.5,
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    wordBreak: "break-word",
    opacity: 0.92
  },
  cotThinkText: {
    fontSize: 12.5,
    lineHeight: 1.55,
    fontStyle: "italic",
    wordBreak: "break-word"
  },
  cotMeta: {
    fontSize: 10.5,
    color: "var(--muted-foreground)",
    marginTop: 2,
    letterSpacing: ".02em"
  },
  cotErr: {
    fontSize: 12,
    color: "var(--muted-foreground)",
    background: "var(--card)",
    border: "1px solid var(--border)",
    borderRadius: 8,
    padding: 10
  },
  /* key/value panels (replaces the raw JSON dumps) */
  kvGrid: { display: "grid", gap: 3, fontSize: 12.5 },
  kvRow: {
    display: "flex",
    gap: 10,
    padding: "3px 0",
    borderBottom: "1px solid color-mix(in srgb, var(--border) 45%, transparent)"
  },
  kvKey: { color: "var(--muted-foreground)", minWidth: 118, flexShrink: 0 },
  kvVal: {
    wordBreak: "break-word",
    textAlign: "right",
    marginLeft: "auto",
    fontVariantNumeric: "tabular-nums"
  },
  kvEmpty: { color: "var(--muted-foreground)", fontSize: 12.5 },
  /* live-activity feed */
  actBox: {
    background: "var(--card)",
    border: "1px solid var(--border)",
    borderRadius: 9,
    padding: 12
  },
  actHead: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    fontSize: 12.5,
    fontWeight: 600,
    flexWrap: "wrap"
  },
  actRow: {
    display: "flex",
    alignItems: "baseline",
    gap: 9,
    fontSize: 12.5,
    padding: "4px 0",
    flexWrap: "wrap"
  },
  actName: { color: "var(--muted-foreground)", minWidth: 150 },
  actMeta: { color: "var(--muted-foreground)", fontSize: 11.5, opacity: 0.85 },
  dotOn: { color: "#3fb950" },
  dotOff: { color: "var(--muted-foreground)" },
  dotLive: { color: "#3fb950", fontWeight: 700, width: 12, display: "inline-block" },
  dotIdle: { color: "var(--muted-foreground)", width: 12, display: "inline-block" }
};
function KarpathyLoop(props) {
  const ctx = props.ctx;
  const host2 = ctx.host || host;
  const [route, setRoute] = useState(null);
  const [routeErr, setRouteErr] = useState("");
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState("");
  const [showPicker, setShowPicker] = useState(false);
  useEffect(() => {
    let alive = true;
    currentRoute(host2).then(function(r) {
      if (!alive) return;
      setRoute(r);
      setRouteErr("");
    }).catch(function(e) {
      if (!alive) return;
      setRouteErr(String(e && e.message || e));
    });
    return function() {
      alive = false;
    };
  }, [host2, tick]);
  const status = useLoopStatus(host2, route, tick);
  const act0 = useActivity(host2, route, tick);
  const disc = useDiscovery(host2, route, tick);
  const ckpts = useCheckpoints(host2, route, tick);
  const running = !!(status.json && status.json.running);
  const projects = status.json && status.json.projects || [];
  const cot = act0.data && act0.data.cot || {};
  const actRows = act0.data && act0.data.rows || [];
  if (!cot.live) {
    const live = actRows.filter(function(r) {
      return r.running;
    })[0];
    if (live) {
      cot.live = { project: live.name, task: live.active_task, elapsed: live.elapsed };
    }
  }
  const act = useCallback(async (label, args, cb) => {
    setBusy(label);
    setMsg("");
    let out = "";
    try {
      const r = route || await currentRoute(host2);
      out = await runPy(host2, r, args, 6e5);
      const first = out ? out.split("\n").slice(0, 2).join(" \xB7 ") : label + " ok";
      setMsg(first);
      setTick(function(t) {
        return t + 1;
      });
    } catch (e) {
      setMsg(label + " failed: " + String(e.message || e).slice(0, 220));
    } finally {
      setBusy("");
    }
    if (typeof cb === "function") {
      try {
        cb(out);
      } catch (e) {
      }
    }
    return out;
  }, [host2, route, tick]);
  useEffect(() => {
    const onMsg = function(ev) {
      let payload = ev.data;
      if (typeof payload === "string") {
        try {
          payload = JSON.parse(payload);
        } catch (e) {
          return;
        }
      }
      if (!payload || !payload.action) return;
      const sel = payload.projects || [];
      const apv = payload.angles_per_visit;
      const impl = payload.implementer;
      const rev = payload.reviewer;
      const reply = function(msg2) {
        try {
          const w = ev.source || ev.target && ev.target.defaultView || window.parent;
          if (w && w.postMessage) w.postMessage(JSON.stringify(msg2), "*");
        } catch (e) {
        }
      };
      if (payload.action === "load") {
        act("Read state", "loopctl.py status --json", function(out) {
          let live = null;
          try {
            const a = String(out || "").indexOf("{");
            const b = String(out || "").lastIndexOf("}");
            if (a >= 0 && b > a) live = JSON.parse(String(out).slice(a, b + 1));
          } catch (e) {
          }
          reply({ live, action: "live" });
        });
        return;
      }
      if (payload.action === "save") {
        const parts = ["loopctl.py config"];
        parts.push("--projects " + JSON.stringify(sel.join(",")));
        if (apv) parts.push("--angles-per-visit " + apv);
        if (impl) parts.push("--implementer " + JSON.stringify(impl));
        if (rev) parts.push("--reviewer " + JSON.stringify(rev));
        const sync = "manifest_sync.py sync --projects " + JSON.stringify(sel.join(","));
        act("Save rotation", parts.join(" ") + " && " + sync, function(out) {
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
    return function() {
      window.removeEventListener("message", onMsg);
    };
  }, [act]);
  const working = actRows.some(function(r) {
    return r.running;
  }) || !!(cot.steps && cot.steps.length);
  const statusLine = routeErr ? "no profile route" : status.error ? "status unavailable \u2014 " + String(status.error).slice(0, 90) : status.loading ? "loading\u2026" : !running ? "PAUSED" : working ? "RUNNING" : "ARMED \u2014 idle";
  const badgeStyle = routeErr || status.error ? S.badgeErr : !running ? S.badgeOff : working ? S.badgeOn : S.badgeIdle;
  return jsxs("div", { style: S.wrap, children: [
    /* ── header ─────────────────────────────────────────────────────── */
    jsxs("div", { style: S.hrow, children: [
      jsx("h2", { style: S.h2, children: "Karpathy Loop" }, "h"),
      jsx("span", { style: badgeStyle, children: statusLine }, "b")
    ] }),
    jsx("div", { style: S.sub, children: running && !working ? "Loop is armed but no worker is running. If a project shows a stalled card, its turn ended early \u2014 the next pass rotates to the next project." : "Pick projects, models and review angles \u2014 the loop rotates through them and checkpoints every accepted round." }, "s"),
    /* ── controls ───────────────────────────────────────────────────── */
    jsxs("div", { style: S.btnRow, children: [
      jsx(Button, {
        onClick: function() {
          act("Start", "loopctl.py start");
        },
        disabled: !!busy || running,
        children: busy === "Start" ? "Starting\u2026" : "Start loop"
      }, "1"),
      jsx(Button, {
        onClick: function() {
          act("Pause", 'loopctl.py pause --reason "paused from app"');
        },
        disabled: !!busy || !running,
        children: busy === "Pause" ? "Pausing\u2026" : "Pause"
      }, "2"),
      jsx(Button, {
        onClick: function() {
          act("Discovery", "karpathy_cron.py discover");
        },
        disabled: !!busy,
        children: busy === "Discovery" ? "Scanning\u2026" : "Run discovery"
      }, "3"),
      jsx(Button, {
        onClick: function() {
          setShowPicker(function(v) {
            return !v;
          });
        },
        children: showPicker ? "Hide picker" : "Open project picker"
      }, "4"),
      jsx(Button, {
        onClick: function() {
          setTick(function(t) {
            return t + 1;
          });
        },
        disabled: !!busy,
        children: "Refresh"
      }, "5")
    ] }),
    msg ? jsx("div", { style: S.msg, children: msg }) : null,
    /* ── 1. PROJECTS (the "where is it running" answer) ─────────────── */
    jsx("div", { style: { height: 16 } }),
    jsx(Panel, {
      title: "Projects in rotation",
      note: actRows.length ? actRows.filter(function(r) {
        return r.running;
      }).length + " running \xB7 " + actRows.length + " queued" : null,
      children: actRows.length ? jsx("div", { style: { overflowX: "auto" }, children: jsx("table", {
        style: S.tbl,
        children: [
          jsx("thead", { children: jsx("tr", { children: [
            jsx("th", { style: S.thDot, children: "" }, "0"),
            jsx("th", { style: S.th, children: "Project" }, "1"),
            jsx("th", { style: S.th, children: "State" }, "2"),
            jsx("th", { style: S.thR, children: "Rounds" }, "3"),
            jsx("th", { style: S.th, children: "Card" }, "4"),
            jsx("th", { style: S.thR, children: "Elapsed" }, "5")
          ] }) }),
          jsx("tbody", { children: actRows.map(function(p, i) {
            const active = !!p.running;
            const stalled = !active && p.active_status === "blocked";
            const tone = active ? "live" : stalled ? "err" : p.never_rotated ? "wait" : "idle";
            const label = active ? "running" : stalled ? "stalled" : p.never_rotated ? "waiting" : "idle";
            return jsx("tr", {
              style: active ? S.trLive : S.tr,
              children: [
                jsx("td", { style: S.tdDot, children: jsx("span", {
                  style: active ? S.dotLive : S.dotIdle,
                  children: active ? "\u25B6" : "\xB7"
                }, "d") }, i + "d"),
                jsx("td", {
                  style: active ? S.tdNameLive : S.tdName,
                  children: p.name
                }, i + "n"),
                jsx("td", { style: S.td, children: jsx(Chip, {
                  tone,
                  children: label
                }, "c") }, i + "s"),
                jsx("td", { style: S.tdR, children: String(p.rounds || 0) }, i + "r"),
                jsx("td", { style: S.tdMono, children: p.active_task || "\u2014" }, i + "t"),
                jsx("td", { style: S.tdR, children: p.elapsed || "\u2014" }, i + "e")
              ]
            }, i);
          }) })
        ]
      }) }) : jsx("div", { style: S.empty, children: "Nothing yet \u2014 open the picker and tick some repos." })
    }),
    act0.error ? jsx("div", { style: S.err, children: String(act0.error) }) : null,
    /* ── 2. CHAIN OF THOUGHT (live reasoning + tool steps) ──────────── */
    jsx("div", { style: { height: 16 } }),
    cotCard({ cot, error: null, onRefresh: function() {
      setTick(function(t) {
        return t + 1;
      });
    }, busy: !!busy }),
    /* ── 3. SETTINGS + DISCOVERY (side by side) ─────────────────────── */
    jsx("div", { style: { height: 16 } }),
    jsxs("div", { style: S.grid, children: [
      jsx(Panel, {
        title: "Loop settings",
        children: settingsRows(status, act0)
      }, "a"),
      jsx(Panel, {
        title: "Discovery",
        children: discoRows(disc)
      }, "b")
    ] }),
    /* ── 4. PICKER ──────────────────────────────────────────────────── */
    showPicker ? jsxs("div", { style: { marginTop: 16 }, children: [
      jsx(Panel, {
        title: "Project picker",
        note: "tick repos, choose models and angles, then Save rotation",
        children: jsx("iframe", {
          src: "file:///" + SEL_HTML.replace(/\\/g, "/"),
          style: S.iframe,
          title: "Karpathy Loop project picker"
        })
      })
    ] }) : null,
    /* ── 5. CHECKPOINTS (the rollback net) ──────────────────────────── */
    jsxs("div", { style: { marginTop: 16 }, children: [
      jsx(Panel, {
        title: "Checkpoints",
        note: "every round is tagged and pushed \u2014 roll any one back",
        right: jsx(Button, {
          onClick: function() {
            setTick(function(t) {
              return t + 1;
            });
          },
          disabled: !!busy,
          children: "Refresh"
        }),
        children: jsxs("div", { children: [
          jsx("div", { style: S.sub, children: "Tags: kp/<project>/r<NN>-start (before) and kp/<project>/r<NN> (after). Rollback restores that exact state \u2014 nothing after it is lost, it stays in git." }, "s"),
          jsx("div", { style: { height: 10 } }, "g"),
          ckpts.loading ? jsx("div", { style: S.empty, children: "loading checkpoints\u2026" }, "l") : ckpts.error ? jsx("div", { style: S.err, children: String(ckpts.error) }, "e") : ckpts.rows.length ? jsx("div", {
            style: { display: "grid", gap: 6 },
            children: ckpts.rows.map(function(r, i) {
              return jsxs("div", { style: S.ckRow, children: [
                jsx("span", { style: S.ckTag, children: r.tag }, "g"),
                jsx("span", {
                  style: S.ckSha,
                  children: String(r.sha || "").slice(0, 7)
                }, "s"),
                jsx("span", {
                  style: S.ckMeta,
                  children: String(r.created || "").slice(0, 16).replace("T", " ")
                }, "m")
              ] }, i);
            })
          }, "r") : jsx("div", { style: S.empty, children: "No checkpoints yet. Press Start \u2014 the loop lays one down before it touches any code." })
        ] })
      })
    ] })
  ] });
}
var plugin_default = {
  id: ID,
  name: "Karpathy Loop",
  defaultEnabled: true,
  register(ctx) {
    ctx.register({
      id: "page",
      area: ROUTES_AREA,
      data: { path: "/karpathy" },
      render: function() {
        return jsx(KarpathyLoop, { ctx });
      }
    });
    ctx.register({
      id: "navigation",
      area: SIDEBAR_NAV_AREA,
      data: { path: "/karpathy", label: "Karpathy Loop", codicon: "sync" }
    });
    ctx.register({
      id: "open",
      area: PALETTE_AREA,
      data: {
        id: "karpathy-loop.open",
        label: "Open Karpathy Loop",
        keywords: ["karpathy", "improve", "loop", "codebase", "rotate", "checkpoint"],
        run: function() {
          host.navigate("/karpathy");
        }
      }
    });
  }
};
var plugin_default2 = plugin_default;
export {
  plugin_default2 as default
};
