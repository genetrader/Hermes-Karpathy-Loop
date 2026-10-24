# Why the widget kept breaking, and the check that actually catches it

**2026-09-28.** The plugin failed to load with *"missing ) after argument list"*
**three times**. Each time my own verification said "PARSE OK" first. That gap is
the real bug; the syntax error is just its symptom.

---

## Root cause 1 — I checked the wrong file type

`node --check file.js` treats the file as **CommonJS**, where `import ... from`
is a syntax error that node reports *and continues past*. The desktop host loads
`plugin.js` as an **ES module**. So the two do not agree, and the failing
construct was reported only under ESM parsing.

**The reliable check — copy to `.mjs` and check that:**

```bash
cp review/plugin.js "$TMPDIR/probe.mjs"
node --check "$TMPDIR/probe.mjs"
```

`tests/real_parse_error.js` does this and prints node's real error **with a line
number**. Use it, not bare `node --check` on the `.js`.

Bonus: `tests/find_syntax_error.js` binary-searches the first failing prefix if
the reported line is not obviously the cause.

## Root cause 2 — the actual defect, three times running

```js
// ✗ WHAT I KEPT WRITING — children as a 3rd ARGUMENT
jsx("div", { style: S.x }, "key", children: [ ... ])

// ✓ CORRECT — children INSIDE props; jsxs for arrays; key inside props or 3rd arg
jsxs("div", { key: "k", style: S.x, children: [ ... ] })
```

React's automatic runtime signature is `jsx(type, props, key)`. There is **no
fourth parameter**. `children` is a normal prop. The third positional slot is
`key` — which is why `jsx(tag, {props}, "wb", children: [...])` is a hard
syntax error: the argument list never closes.

`jsxs` (plural) is for an array of children; `jsx` (singular) for one child.
Mixing them is not a syntax error but is sloppy — `jsxs` with a non-array still
works, so prefer `jsxs` whenever you pass an array.

## Root cause 3 — my grep pattern only matched one shape

I scanned with `jsx("[a-z]*", {[^}]*} }, children` which misses any call whose
props object contains a **nested** `{}` (i.e. most of them, since `style: {...}`).
So the scan reported "0 bad call sites" on a file that had them.

**The scan must be structural, not regex.** `node --check` as `.mjs` is the
authority; the regex is a supplement. An independent verifier's balanced-brace
scanner (155 calls, 0 violations) is the right shape — write that as a
maintained test rather than re-inventing a regex each time.

---

## THE PROTOCOL (follow every time, no exceptions)

1. `node --check` on a **`.mjs` copy** → must print nothing and exit 0.
2. `diff` the edited file against its frozen baseline → must show ONLY the
   intended lines.
3. Install with `cp`, then re-run step 1 on the **installed** path.
4. `diff -q` dev vs installed → must be identical; record the MD5.
5. Reload plugins in the app.

**Never** report a widget fix as verified on the strength of `node --check` on
the `.js` file alone. That is the check that lied three times.

---

## Symptom → cause table

| Symptom | Cause |
|---|---|
| "missing ) after argument list" | `children:` outside the props object, or a 4th argument |
| Plugin loads but panel says "loading" forever | field-name mismatch between producer and consumer |
| `node --check` clean but plugin fails | you checked the `.js` as CJS, not the `.mjs` as ESM |
| Panel renders unstyled/blank section | `S.<key>` that does not exist in the style object |
| Data present in backend but not on screen | widget never reads the field (the liveness gap) |