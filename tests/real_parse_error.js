// Get node's ACTUAL error with a line number on the real file.
// Copying to .mjs makes node parse it as a module (the file uses ESM imports).
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const file = process.argv[2];
const src = fs.readFileSync(file, "utf8");
const tmp = path.join(os.tmpdir(), "plugin_as_module.mjs");
fs.writeFileSync(tmp, src, "utf8");

let out = "";
try {
  execFileSync(process.execPath, ["--check", tmp], { stdio: "pipe" });
  console.log("PARSE OK - no syntax error");
} catch (e) {
  out = (e.stderr ? e.stderr.toString() : "") + (e.stdout ? e.stdout.toString() : "");
  console.log("PARSE FAILED. Raw stderr:");
  console.log(out);
}

// Also try it the way the app likely loads it: as CommonJS (imports become
// syntax errors only if the host doesn't support ESM -- so report that case).
const tmp2 = path.join(os.tmpdir(), "plugin_as_cjs.js");
fs.writeFileSync(tmp2, src, "utf8");
try {
  execFileSync(process.execPath, ["--check", tmp2], { stdio: "pipe" });
  console.log("\n[cjs] PARSE OK");
} catch (e) {
  console.log("\n[cjs] PARSE FAILED:");
  console.log((e.stderr ? e.stderr.toString() : "").split("\n").slice(0, 14).join("\n"));
}