// Gate check 3: STRUCTURAL — `jsx`/`jsxs` must never be called with more than
// three top-level arguments, and `children` must be INSIDE the props object.
//
// A regex cannot do this: any call whose props contain a nested object
// (style: {...}) defeats a naive pattern, which is how a scan reported
// "0 bad call sites" on a file that had several.
//
// THE RUNTIME CONTRACT: jsx(type, props, key). There is NO fourth parameter.
// So two independent defects are possible and BOTH must be caught:
//   (a) `children:` appearing after the top-level props object closed
//   (b) any call with MORE THAN THREE top-level arguments -- which is what
//       `jsx("div", {..}, "k", children)` is
//
// (b) matters because it is the one shape that passes BOTH the ESM parse and
// the (a)-only scan. Measured 2026-09-28 on injected defects:
//   shape A  jsx(t,{..},"k", children)   ESM parse MISSED, (a)-scan MISSED
//   shape B  jsx(t,{..},"k", jsx(..))    ESM parse MISSED, (a)-scan caught
//   shape C  jsx(t,{..},"k",{children:}) ESM parse MISSED, (a)-scan caught
// Counting arguments catches all three. That is why this counts.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

function skipString(s, i) {
  const q = s[i];
  i++;
  while (i < s.length) {
    if (s[i] === "\\") { i += 2; continue; }
    if (s[i] === q) return i + 1;
    i++;
  }
  return i;
}
function skipTemplate(s, i) {
  i++;
  while (i < s.length) {
    if (s[i] === "\\") { i += 2; continue; }
    if (s[i] === "`") return i + 1;
    if (s[i] === "$" && s[i + 1] === "{") {
      let d = 1; i += 2;
      while (i < s.length && d > 0) {
        if (s[i] === "{") d++;
        else if (s[i] === "}") d--;
        else if (s[i] === '"' || s[i] === "'") { i = skipString(s, i); continue; }
        else if (s[i] === "`") { i = skipTemplate(s, i); continue; }
        i++;
      }
      continue;
    }
    i++;
  }
  return i;
}
function skipComment(s, i) {
  if (s[i + 1] === "/") { const n = s.indexOf("\n", i); return n < 0 ? s.length : n + 1; }
  if (s[i + 1] === "*") { const n = s.indexOf("*/", i); return n < 0 ? s.length : n + 2; }
  return i;
}

const bad = [];
let calls = 0;
const re = /\bjsxs?\s*\(/g;
let m;
while ((m = re.exec(src)) !== null) {
  calls++;
  const line = src.slice(0, m.index).split("\n").length;
  let i = m.index + m[0].length - 1; // at the "("
  let pd = 0, bd = 0, args = 1, propsClosedAt = -1, childrenAfterProps = false;
  for (; i < src.length; i++) {
    const c = src[i];
    if (c === '"' || c === "'") { i = skipString(src, i) - 1; continue; }
    if (c === "`") { i = skipTemplate(src, i) - 1; continue; }
    if (c === "/" && (src[i + 1] === "/" || src[i + 1] === "*")) {
      const n = skipComment(src, i); if (n <= i) break; i = n - 1; continue;
    }
    if (c === "(" || c === "[" || c === "{") {
      if (c === "(") pd++; else bd++;
      continue;
    }
    if (c === ")" || c === "]" || c === "}") {
      if (c === ")") { pd--; if (pd === 0) break; }
      else bd--;
      if (c === "}" && bd === 0 && pd === 1 && propsClosedAt < 0) {
        propsClosedAt = i;
        let j = i + 1;
        while (j < src.length && /[ \t\r\n]/.test(src[j])) j++;
        if (src[j] === ",") {
          // look at everything after the props object, up to the close paren
          let k = j + 1, depth = 0, end = k;
          for (let q = k; q < src.length; q++) {
            const cq = src[q];
            if (cq === '"' || cq === "'") { q = skipString(src, q) - 1; continue; }
            if (cq === "(" || cq === "[" || cq === "{") depth++;
            else if (cq === ")" || cq === "]" || cq === "}") {
              if (depth === 0) { end = q; break; }
              depth--;
            } else if (cq === "," && depth === 0) {
              // a SECOND comma at top level => a 4th argument exists
              args = 4;
            }
            end = q + 1;
          }
          const tail = src.slice(j + 1, end);
          if (/\bchildren\s*:/.test(tail)) childrenAfterProps = true;
        }
      }
      continue;
    }
    if (c === "," && bd === 0 && pd === 1) args++;
  }
  if (args > 3) {
    bad.push({ line, why: "called with " + args + " arguments; jsx(type, props, key) accepts 3" });
  }
  if (childrenAfterProps) {
    bad.push({ line, why: "children appears after the top-level props object closed" });
  }
}

if (bad.length) {
  console.log("FAIL " + bad.length + " structural problem(s):");
  for (const b of bad) console.log("  line " + b.line + ": " + b.why);
  process.exit(1);
}
console.log("OK " + calls + " jsx/jsxs calls, all <=3 args, 0 children-outside-props");