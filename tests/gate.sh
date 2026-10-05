#!/usr/bin/env bash
# THE GATE. Run after EVERY edit, before installing. Non-zero = do not install.
#
# Why this exists: the same 4th-argument syntax error broke the plugin three
# times and `node --check plugin.js` was SILENT on the broken file every time.
# As CommonJS the top-level imports are the first error and node reports-and-
# continues past them instead of reaching the real defect. The host loads the
# file as an ES module, so only an ESM parse is honest.
#
# Measured 2026-09-28 (same file, injected defect):
#   node --check f.js                              -> exit 0, silent   NO
#   node --check f.mjs                             -> exit 1            yes
#   node --input-type=module --check < f.js        -> exit 1            yes
set -u
F="${1:-review/plugin.js}"
# Native node needs NATIVE paths; MSYS path conversion is disabled here, so
# `node tests/x.js` resolves to C:\c\CODING\... and `/tmp/f.js` to C:\tmp\f.js.
# Normalize both the tool dir and the target file to Windows-style paths.
norm() { case "$1" in /[a-zA-Z]/*) echo "$1" | sed 's|^/\([a-zA-Z]\)/|\1:/|' ;; *) echo "$1" ;; esac; }
D="$(norm "$(cd "$(dirname "$0")" && pwd)")"
F="$(norm "$F")"
[ -f "$F" ] || { echo "GATE: no such file: $F"; exit 2; }
FAIL=0
echo "=== GATE  $F ==="

# 1. ESM parse — the authoritative syntax check.
OUT=$(node --input-type=module --check < "$F" 2>&1); RC=$?
if [ $RC -ne 0 ]; then
  echo "  [1] ESM PARSE .......... FAIL (rc=$RC)"
  echo "$OUT" | head -6 | sed 's/^/      /'
  FAIL=1
else
  echo "  [1] ESM PARSE .......... ok"
fi

# 2. Loader import-scanner rule: prose like  from "word"  in a COMMENT is
#    parsed as an import and rejects the plugin. esbuild and node both miss it.
OUT=$(node "$D/check_imports.js" "$F" 2>&1); RC=$?
echo "  [2] IMPORTS ........... $OUT"
[ $RC -ne 0 ] && FAIL=1

# 3. Structural: children must be INSIDE the props object. Balanced-brace scan
#    (a regex misses nested props like style:{...}).
OUT=$(node "$D/check_structure.js" "$F" 2>&1); RC=$?
echo "  [3] STRUCTURE ......... $OUT"
[ $RC -ne 0 ] && FAIL=1

# 4. JSX leftovers (.js is not compiled; JSX is a hard parse error).
JSX=$(grep -cE '^[[:space:]]*<[A-Za-z]|</[A-Za-z]|<>' "$F" 2>/dev/null | head -1)
JSX=${JSX:-0}
if [ "$JSX" != "0" ]; then
  echo "  [4] NO RAW JSX ......... FAIL ($JSX lines)"; FAIL=1
else
  echo "  [4] NO RAW JSX ......... ok"
fi

# 5. Every S.x / T.x referenced must be declared. An undeclared one is neither a
#    parse nor a load error -- it renders as literal `undefined`, which React
#    drops silently (a border that just vanishes, no error anywhere).
OUT=$(node "$D/check_styles.js" "$F" 2>&1); RC=$?
echo "  [5] STYLE REFS ......... $OUT"
[ $RC -ne 0 ] && FAIL=1

echo "  [6] MD5 ................ $(md5sum "$F" | cut -d' ' -f1)"

echo "=== $([ $FAIL -eq 0 ] && echo "GATE PASS" || echo "GATE FAIL - DO NOT INSTALL") ==="
exit $FAIL