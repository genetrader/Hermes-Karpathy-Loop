// Gate check 2: replicate the plugin LOADER's bare-specifier scanner.
// The loader regexes the WHOLE file with no comment stripping, so prose like
//   ...from "word"...
// in a comment is parsed as an import and the plugin is REJECTED at load time.
// esbuild bundling and node --check both pass on a plugin the loader rejects.
// Only this check implements the rule.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const RE = /(from\s*|import\s*\(\s*|import\s+)(["'])([^"']+)\2/g;
const OK = new Set(["react", "react/jsx-runtime", "@hermes/plugin-sdk"]);
const bad = [];
for (const m of src.matchAll(RE)) {
  const spec = m[3];
  if (OK.has(spec)) continue;
  if (/^[./]/.test(spec)) continue;          // relative
  if (/^[a-z][a-z0-9+.-]*:/i.test(spec)) continue; // protocol/URL
  bad.push(spec);
}
if (bad.length) {
  console.log("FAIL unsupported specifiers: " + bad.join(", "));
  process.exit(1);
}
console.log("OK " + (src.match(RE) || []).length + " import specifiers, all allowed");