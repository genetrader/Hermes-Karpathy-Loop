#!/usr/bin/env python3
"""F-Y (2026-10-05): Karpathy widget status server.

The desktop plugin's shell.exec polls route through the profile agent's
shell queue. That queue is shared with the human's agent sessions and every
spawn pays a 13-70s MCP-connect storm (obsidian ssh cancel + OAuth retries
in gateway-stdio.log). Under load the widget's polls timed out and the page
showed 'status unavailable' or spun on loading forever.

This server bypasses all of it: it runs the SAME producers (loopctl status,
activity, checkpoints, current-work) and serves their JSON over localhost
HTTP. The plugin fetch()es directly. Zero agent involvement.

Run under the loop's watcher so it stays alive (or sceduled separately).
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CACHE_TTL = {
    "/status.json": 5,
    "/activity.json": 5,
    "/checkpoints.json": 30,
    "/current.json": 30,
}
_cache = {}


def _build(path):
    if path == "/status.json":
        import loopctl
        cfg = loopctl.load()
        return {"running": bool(cfg.get("running")),
                "projects": cfg.get("projects") or [],
                "liveness": loopctl._liveness(cfg)}
    if path == "/activity.json":
        import activity
        return json.loads(activity.compact_payload(activity.activities(max_steps=18)))
    if path == "/checkpoints.json":
        import subprocess, os
        py = sys.executable
        out = subprocess.run([py, str(ROOT / "checkpoint.py"), "all"],
                             cwd=str(ROOT), capture_output=True, text=True,
                             timeout=180).stdout
        return json.loads(out.strip())
    if path == "/current.json":
        import subprocess
        py = sys.executable
        out = subprocess.run([py, str(ROOT / "checkpoint.py"), "current"],
                             cwd=str(ROOT), capture_output=True, text=True,
                             timeout=180).stdout
        return json.loads(out.strip())
    return None


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path not in CACHE_TTL:
            self.send_response(404)
            self.end_headers()
            return
        now = time.time()
        hit = _cache.get(path)
        if hit and now - hit[0] < CACHE_TTL[path]:
            body, code, t0 = hit[1], 200, hit[0]
        else:
            try:
                body = json.dumps(_build(path)).encode("utf-8")
                _cache[path] = (now, body)
                code = 200
            except Exception as e:
                body = json.dumps({"error": str(e)}).encode("utf-8")
                code = 500
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print("karpathy status server on 127.0.0.1:%d" % port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
