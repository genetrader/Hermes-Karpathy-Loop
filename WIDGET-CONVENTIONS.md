# Karpathy Loop Widget — VERIFIED code conventions

Captured 2026-09-28 by direct read of `review/plugin.js` (1496 lines, MD5 547b9062f393ef890f58d34e26cd2703).
Cite THESE; do not guess at conventions.

## Imports (lines 18-27)

```js
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
```

`Button` IS available from the SDK. `jsx`/`jsxs` come from `react/jsx-runtime`.

## THE RULE THAT BROKE THE FILE

`children` goes INSIDE the props object. `jsxs` (plural) is for an array of
children; `jsx` (singular) is for one child.

```js
// CORRECT — array of children
jsxs("div", { style: S.grid, children: [
  jsx("span", { children: "a" }, "k1"),
  jsx("span", { children: "b" }, "k2"),
] })

// CORRECT — single child
jsx("div", { style: S.sub, children: "hello" })

// SYNTAX ERROR — "missing ) after argument list"
jsx("div", { style: S.grid }, children: [ ... ])
```

Third positional arg of `jsx`/`jsxs` is the `key`, NOT children.

## Local components (only three)

| Component | Line | Props |
|---|---|---|
| `Panel(props)` | 815 | `title` (str), `note` (str, optional), `right` (node, optional), `children` |
| `Chip(props)` | 827 | `tone`: `"live"` \| `"wait"` \| `"err"` \| undefined; `children` |
| `KarpathyLoop(props)` | 1002 | the page root |

`Panel` renders: `<section style={S.panel}>` → `<header style={S.panelHead}>` with
`h3` title + optional note + optional right → `<div style={S.panelBody}>`.

## Style tokens

- `S` — const object at line 546. Keys used: `wrap, hrow, h2, badgeOn, badgeOff,
  badgeWarn, badgeIdle, badgeErr, sub, btnRow, msg, grid, label, ...`
- `T` — theme colors at line 532 (e.g. `T.live`, `T.wait`, `T.err`, `T.muted`, `T.idleBd`).
- CSS vars available: `var(--foreground)`, `var(--muted-foreground)`, `var(--card)`,
  `var(--border)`, `var(--accent)`.

## Data access

- `host.requestProfile(route, "shell.exec", { command })` — the bridge call used
  to run a backend script. Returns an object; read `r.value` (falling back to `r.output`).
- `useValue(...)` from the SDK for reactive values.
- Bridge clips at roughly **4,000 B**; the producer guards to **3400 B**.

## Backend field contract (what the widget may read)

Poll payload: `{ rows: [...], cot: {...} }`

- rows: `name, running, rounds, active_task, never_rotated, elapsed, last_rc,
  last_error, see`
- cot: `steps` (NOT `items` — the widget accepts either, but `steps` is what ships),
  `steps_total`, `log_age_s`, `angle`, `running`, `task`

## Verification protocol after ANY edit

1. `node --check review/plugin.js` must pass (rc=0).
2. Confirm no `jsx(`/`jsxs(` call has `children:` outside its props object.
3. Copy to the installed path and confirm `diff -q` is identical.
4. Only then reload plugins.