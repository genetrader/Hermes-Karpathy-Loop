// Locate the REAL syntax error: node --check reports "missing ) after argument
// list" without a line number, which is useless in a 1,500-line file. Parse
// progressively-longer PREFIXES of the file; the first prefix that fails ends on
// (or just after) the offending line. The file has ESM imports, so it is checked
// as a module (--input-type=module via a temp .mjs copy).
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const file = process.argv[2];
const src = fs.readFileSync(file, "utf8");
const lines = src.split("\n");
console.log("file:", file, "| lines:", lines.length);

const tmp = path.join(os.tmpdir(), "prefix_probe.mjs");

function parses(text) {
  fs.writeFileSync(tmp, text, "utf8");
  try {
    execFileSync(process.execPath, ["--check", tmp], { stdio: "pipe" });
    return true;
  } catch (e) {
    return false;
  }
}

// Whole file first.
console.log("whole file parses:", parses(src));

// Binary search for the first failing prefix. A prefix can fail for reasons that
// only the tail fixes (unclosed brace), so we look for the LAST prefix that
// parses and report the next line as the suspect region.
let lo = 1, hi = lines.length, lastOk = 0;
while (lo <= hi) {
  const mid = Math.floor((lo + hi) / 2);
  if (parses(lines.slice(0, mid).join("\n"))) { lastOk = mid; lo = mid + 1; }
  else { hi = mid - 1; }
}
console.log("last prefix that parses cleanly: line", lastOk);
const from = Math.max(0, lastOk - 6);
console.log("\n--- lines " + (from + 1) + ".." + Math.min(lines.length, lastOk + 22) + " ---");
for (let i = from; i < Math.min(lines.length, lastOk + 22); i++) {
  console.log(String(i + 1).padStart(5) + "| " + lines[i]);
}