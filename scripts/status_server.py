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

SETTINGS API (2026-10-06): the widget's in-page settings card reads/writes
through this server so the loop is configurable from the Karpathy Loop page
itself -- see plans/SETTINGS-PANEL-PLAN.md. Contract:

    GET  /meta.json             {root, python, status_port} -- what the widget
                                needs to run shell fallbacks on THIS machine
                                (no author paths in widget code).
    GET  /settings.json         {schema, values(redacted), sources, issues,
                                 rotation, meta}
    PUT  /settings.json         {values:{dotted:raw}} -> validate+write
                                settings.local.yaml ONLY; 400 + issues on error
    POST /settings/secret       {key,value} -> settings.local.yaml ONLY;
                                the value is NEVER echoed back
    POST /settings/secret/unset {key}       -> remove from settings.local.yaml
    POST /rotation.json         {angles_per_visit|sweep_minutes|max_rounds|
                                 max_hours|implementer|reviewer} -> loop.json

    GET  /status.json /activity.json /checkpoints.json /current.json (unchanged)

Secrets rule: token VALUES never appear in any response -- the card shows
"<set via ...>" from settings.redacted_view(). Writes land only in the
gitignored settings.local.yaml; the tracked settings.yaml is never touched.

Write hardening (a localhost server is still reachable from a browser via DNS
rebinding): the Host header must name localhost/127.*, any browser Origin
must be local/file/app-scheme (external http(s) origins are refused), bodies
are capped at 256 KB, and CORS echoes only the local origin.

Run under the loop's watcher so it stays alive (or scheduled separately).
"""
import json
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MAX_BODY = 256 * 1024
_ALLOWED_HOSTS = re.compile(
    r"^(localhost|127(\.\d+){0,3}\.\d+|\[?::1\]?)(:\d+)?$", re.I)
# A browser Origin we accept: localhost, file/app custom schemes, or null.
_ALLOWED_ORIGIN = re.compile(
    r"^(null|file://.*|chrome-extension://.*|about:blank|"
    r"https?://(localhost|127(\.\d+){0,3}\.\d+)(:\d+)?)$", re.I)

CACHE_TTL = {
    "/status.json": 5,
    "/activity.json": 5,
    "/checkpoints.json": 30,
    "/current.json": 30,
    "/settings.json": 0,   # the card must see fresh truth right after a save
    "/meta.json": 0,
}
_cache = {}


def _meta():
    import settings as _S
    return {"root": str(ROOT), "python": sys.executable,
            "status_port": _S.status_port()}


def _settings_payload():
    import settings as _S
    import loopctl
    cfg = loopctl.load()
    return {
        "schema": _S.schema_view(),
        "values": _S.redacted_view(),
        "sources": dict(_S._SOURCES),
        "issues": [{"level": i.level, "key": i.key,
                    "message": i.message, "fix": i.fix} for i in _S.issues()],
        "rotation": {k: cfg.get(k) for k in
                     ("angles_per_visit", "sweep_minutes", "max_rounds",
                      "max_hours", "implementer", "reviewer",
                      "pushed_to_github", "projects")},
        "meta": _meta(),
    }


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
    if path == "/settings.json":
        return _settings_payload()
    if path == "/meta.json":
        return _meta()
    return None


class H(BaseHTTPRequestHandler):
    # ------------------------------------------------------------ helpers
    def _forbidden(self, why):
        body = json.dumps({"error": "forbidden", "detail": why}).encode("utf-8")
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        origin = self.headers.get("Origin") or ""
        # Echo only a local origin; never a blanket '*' for credentialed reads.
        if origin and _ALLOWED_ORIGIN.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        elif not origin:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                         "GET, PUT, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _guard(self):
        """DNS-rebinding / cross-site wall. Returns True when allowed."""
        host = self.headers.get("Host") or ""
        if not _ALLOWED_HOSTS.match(host):
            self._forbidden("this server only answers on localhost "
                            "(Host header was %r)" % host)
            return False
        origin = self.headers.get("Origin") or ""
        if origin and not _ALLOWED_ORIGIN.match(origin):
            self._forbidden("origin %r may not talk to the loop settings "
                            "server" % origin)
            return False
        return True

    def _read_body(self):
        """Return (obj, error_string). Caps the body, requires a JSON object."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None, "bad Content-Length"
        if n <= 0:
            return None, "empty body"
        if n > MAX_BODY:
            return None, "body too large (max %d bytes)" % MAX_BODY
        raw = self.rfile.read(n)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as e:
            return None, "body is not JSON: %s" % e
        if not isinstance(obj, dict):
            return None, "body must be a JSON object"
        return obj, None

    # ------------------------------------------------------------ verbs
    def do_OPTIONS(self):
        if not self._guard():
            return
        self.send_response(204)
        origin = self.headers.get("Origin") or ""
        if origin and _ALLOWED_ORIGIN.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path not in CACHE_TTL:
            self.send_response(404)
            self.end_headers()
            return
        if not self._guard():
            return
        now = time.time()
        hit = _cache.get(path)
        if hit and CACHE_TTL[path] and now - hit[0] < CACHE_TTL[path]:
            self._json(200, json.loads(hit[1]))
            return
        try:
            obj = _build(path)
            if obj is None:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps(obj).encode("utf-8")
            _cache[path] = (now, body)
            self._json(200, obj)
        except Exception as e:
            self._json(500, {"error": str(e)})

    def do_PUT(self):
        path = self.path.split("?")[0]
        if not self._guard():
            return
        if path != "/settings.json":
            self._json(404, {"error": "unknown PUT path"})
            return
        obj, err = self._read_body()
        if err:
            self._json(400, {"error": err})
            return
        import settings as _S
        values = obj.get("values")
        if not isinstance(values, dict) or not values:
            self._json(400, {"error": "expected {values:{key:value}}",
                            "issues": []})
            return
        issues, redacted = _S.apply_ui_updates(values)
        errs = [i for i in issues if i.level == "error"]
        _cache.pop("/settings.json", None)
        _cache.pop("/status.json", None)
        if errs:
            self._json(400, {
                "saved": False,
                "issues": [{"level": i.level, "key": i.key,
                            "message": i.message, "fix": i.fix}
                           for i in issues],
                "values": redacted,
                "sources": dict(_S._SOURCES)})
            return
        self._json(200, {
            "saved": True,
            "issues": [{"level": i.level, "key": i.key,
                        "message": i.message, "fix": i.fix}
                       for i in issues],
            "values": redacted,
            "sources": dict(_S._SOURCES)})

    def do_POST(self):
        path = self.path.split("?")[0]
        if not self._guard():
            return
        obj, err = self._read_body()
        if err:
            self._json(400, {"error": err})
            return
        try:
            if path == "/settings/secret":
                self._post_secret(obj, unset=False)
            elif path == "/settings/secret/unset":
                self._post_secret(obj, unset=True)
            elif path == "/rotation.json":
                self._post_rotation(obj)
            else:
                self._json(404, {"error": "unknown POST path"})
        except ValueError as e:
            # bad key / empty value: a client mistake, with the reason.
            self._json(400, {"error": str(e)})
        except Exception as e:
            self._json(500, {"error": str(e)})

    # ------------------------------------------------------------ write ops
    def _post_secret(self, obj, unset):
        """Secrets: settings.local.yaml ONLY, value never echoed -- not back
        to the card, not into a server-side cache the GET path could serve."""
        import settings as _S
        key = str(obj.get("key") or "").strip()
        if key not in _S.SECRET_LEAVES:
            self._json(400, {"error": "key %r is not a secret setting "
                            "(known: %s)" % (key, ", ".join(sorted(_S.SECRET_LEAVES)))})
            return
        if unset:
            _S.clear_secret(key)
            _cache.pop("/settings.json", None)
            self._json(200, {"key": key, "cleared": True,
                             "values": _S.redacted_view()})
            return
        value = obj.get("value")
        if not isinstance(value, str) or not value.strip():
            self._json(400, {"error": "value must be a non-empty string "
                            "(use the unset endpoint to clear)"})
            return
        _S.put_secret(key, value)
        _cache.pop("/settings.json", None)
        # Deliberately NOT returning the value: confirmation only.
        self._json(200, {"key": key, "set": True, "source": "local",
                         "values": _S.redacted_view()})

    def _post_rotation(self, obj):
        """Rotation edits (angles/visit, cadence, caps, seats) write loop.json
        through loopctl's own atomic save -- the SAME file `loopctl config`
        writes, so the CLI and the card can never disagree."""
        import loopctl
        cfg = loopctl.load()
        changed = []
        for field, cast in (("angles_per_visit", int), ("sweep_minutes", int),
                            ("max_rounds", int), ("max_hours", int)):
            if field in obj:
                try:
                    v = cast(obj[field])
                except (TypeError, ValueError):
                    self._json(400, {"error": "%s must be a whole number" % field})
                    return
                if v < 0 or (field == "angles_per_visit" and v < 1):
                    self._json(400, {"error": "%s must be >= %d"
                                     % (field, 1 if field == "angles_per_visit" else 0)})
                    return
                cfg[field] = v
                changed.append(field)
        for seat in ("implementer", "reviewer"):
            if seat in obj:
                v = str(obj[seat] or "").strip()
                if v and not re.match(r"^[A-Za-z0-9._-]+(:[A-Za-z0-9._-]+){1,2}$", v):
                    self._json(400, {"error": "%s %r is not provider:model "
                                     "or custom:slug:model" % (seat, v)})
                    return
                cfg[seat] = v
                changed.append(seat)
        if cfg.get("implementer") and cfg.get("implementer") == cfg.get("reviewer"):
            self._json(400, {"error": "implementer and reviewer are the SAME "
                            "model -- a model cannot review its own work"})
            return
        loopctl.save(cfg)
        loopctl._sync_rotation(cfg)
        _cache.pop("/status.json", None)
        self._json(200, {"saved": True, "changed": changed,
                         "rotation": {k: cfg.get(k) for k in
                                      ("angles_per_visit", "sweep_minutes",
                                       "max_rounds", "max_hours",
                                       "implementer", "reviewer")}})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    import settings as _S
    port = int(sys.argv[1]) if len(sys.argv) > 1 else _S.status_port()
    print("karpathy status server on 127.0.0.1:%d" % port, flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
