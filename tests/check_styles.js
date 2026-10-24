// Gate check 5: every `S.foo` / `T.foo` referenced in the file must be DECLARED
// in the style objects. An undeclared one is not a parse error and not a load
// error -- it silently renders as `undefined` (a missing border, an unstyled
// cell) or throws only when that branch renders. Cheap to check, real to catch.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

function keysOf(varName) {
  const marker = "const " + varName + " = {";
  const i = src.indexOf(marker);
  if (i < 0) return null;
  // walk balanced braces from the opening "{"
  let j = i + marker.length - 1, depth = 0, end = -1;
  for (; j < src.length; j++) {
    const c = src[j];
    if (c === "{") depth++;
    else if (c === "}") { depth--; if (depth === 0) { end = j; break; } }
    else if (c === '"' || c === "'" || c === "`") {
      const q = c; j++;
      while (j < src.length && src[j] !== q) { if (src[j] === "\\") j++; j++; }
    }
  }
  const body = src.slice(i, end);
  const keys = new Set();
  for (const m of body.matchAll(/^\s*([A-Za-z_$][\w$]*)\s*:/gm)) keys.add(m[1]);
  return keys;
}

const S = keysOf("S"), T = keysOf("T");
let bad = [];
for (const [name, keys] of [["S", S], ["T", T]]) {
  if (!keys) { console.log("FAIL could not locate `const " + name + " = {`"); process.exit(1); }
  const used = new Set();
  for (const m of src.matchAll(new RegExp("\\b" + name + "\\.([A-Za-z_$][\\w$]*)", "g"))) used.add(m[1]);
  for (const u of used) if (!keys.has(u)) bad.push(name + "." + u);
}
if (bad.length) {
  console.log("FAIL undeclared style refs: " + bad.join(", "));
  process.exit(1);
}
console.log("OK S(" + S.size + ") T(" + T.size + ") all refs declared");