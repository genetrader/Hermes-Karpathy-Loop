"""see_it.py — Layer 3 (See-It) for the Karpathy Loop.

Read-only "is it viewable right now?" probes for projects that declare a
`see:` block in improve.yaml. NO command execution, ever: the `start:` command
declared in a see block is displayed by the widget with a copy button, and is
NEVER run by this module — there is deliberately no code path here that could.

Probe forms (parsed from the `probe:` string):
    tcp:PORT       open a TCP socket to 127.0.0.1:PORT (~2s timeout), close.
                   Never sends any data.
    http:URL       HTTP GET with ~3s timeout. Any status < 500 counts as up.
                   401/403 are reported distinctly as "auth" — the service IS
                   running, it just wants credentials.
    file:PATH      existence test only.
    none / missing not applicable — NOT a failure.

Usage:
    python see_it.py --check          probe every enabled project with a see block
    python see_it.py --one <name>     probe a single project
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "improve.yaml"

TCP_TIMEOUT_S = 2.0
HTTP_TIMEOUT_S = 3.0

# statuses: running | not-running | auth | partial | na | unknown


# ------------------------------------------------------------------ manifest

def load_manifest() -> dict:
    """Load improve.yaml the same way improver.py does."""
    import yaml
    if not MANIFEST.exists():
        return {}
    return yaml.safe_load(MANIFEST.read_text(encoding="utf-8")) or {}


def _enabled(manifest: dict) -> list[dict]:
    return [p for p in (manifest.get("projects") or []) if p.get("enabled")]


# --------------------------------------------------------------------- probe

def _host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").strip()
    return host or "127.0.0.1"


def probe(see: dict) -> dict:
    """Run the declared read-only probe. Returns {ok, detail, checked_at}.

    ok: True/False for a real verdict, None for "not applicable"
        (probe `none`, missing, or unparseable form). Missing/unknown never
        reports running.
    """
    checked_at = time.time()
    spec = str((see or {}).get("probe") or "").strip()
    if not spec or spec.lower() in ("none", "n/a", "na"):
        return {"ok": None, "detail": "no probe declared", "checked_at": checked_at}

    if spec.startswith("tcp:"):
        port_s = spec[4:].strip()
        try:
            port = int(port_s)
        except ValueError:
            return {"ok": None, "detail": f"bad tcp port: {port_s!r}", "checked_at": checked_at}
        url = str((see or {}).get("url") or "")
        host = _host_of(url)
        try:
            with socket.create_connection((host, port), timeout=TCP_TIMEOUT_S):
                pass  # open then immediately close; never send data
            return {"ok": True, "detail": f"tcp {host}:{port} accepted", "checked_at": checked_at}
        except OSError as e:
            return {"ok": False, "detail": f"tcp {host}:{port} refused/unreachable: {e}", "checked_at": checked_at}

    # A REAL URL must be tested BEFORE the marker form: "http://127.0.0.1:8823/x"
    # also starts with "http:", so the marker branch would strip the scheme and
    # leave "//127.0.0.1:8823/x" -- a malformed URL that never probes. (This bit
    # project-b's declaration during the first live run.)
    if spec.startswith(("http://", "https://")):
        return _http_probe(spec, checked_at)

    if spec.startswith("http:"):
        url = spec[5:].strip()
        if not url.startswith(("http://", "https://")):
            url = "http://" + url.lstrip("/")
        return _http_probe(url, checked_at)

    if spec.startswith("file:"):
        path = spec[5:].strip()
        p = Path(path)
        try:
            exists = p.exists()
        except OSError as e:
            return {"ok": None, "detail": f"file probe error: {e}", "checked_at": checked_at}
        return {"ok": exists, "detail": path, "checked_at": checked_at}

    # Plain URL without a scheme prefix — probe it as http.
    if spec.startswith(("http://", "https://")):
        return _http_probe(spec, checked_at)

    return {"ok": None, "detail": f"unrecognized probe form: {spec!r}", "checked_at": checked_at}


def _http_probe(url: str, checked_at: float) -> dict:
    """HTTP GET, ~3s hard timeout. Status < 500 is up; 401/403 => auth."""
    if not url.startswith(("http://", "https://")):
        return {"ok": None, "detail": f"bad http url: {url!r}", "checked_at": checked_at}
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "karpathy-loop-see-it/1.0 (read-only up-check)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code  # got a response — service is up; classify below
        try:
            e.close()
        except Exception:
            pass
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        reason = getattr(e, "reason", None) or e
        return {"ok": False, "detail": f"http {url} unreachable: {reason}", "checked_at": checked_at}
    except Exception as e:  # never hang, never crash the caller
        return {"ok": False, "detail": f"http {url} error: {e}", "checked_at": checked_at}

    if code in (401, 403):
        return {"ok": False, "detail": f"auth (HTTP {code}) — running, wants credentials", "checked_at": checked_at}
    if code < 500:
        return {"ok": True, "detail": f"HTTP {code}", "checked_at": checked_at}
    return {"ok": False, "detail": f"HTTP {code} (server error)", "checked_at": checked_at}


# ------------------------------------------------------------------- check_all

def check_all(manifest: dict) -> dict:
    """{name: probe result} for every enabled project declaring a see block."""
    out: dict = {}
    for proj in _enabled(manifest):
        see = proj.get("see")
        if not isinstance(see, dict) or not see:
            continue
        out[str(proj.get("name") or "?")] = probe(see)
    return out


# -------------------------------------------------------------------- describe

_KIND_LABEL = {
    "server": "server",
    "static-html": "static page",
    "built-site": "built site",
    "none": "none",
}


def describe(see: dict) -> dict:
    """Widget-facing shape for one see block.

    {kind, label, url, path, start_cmd, note, status} where status is one of
    running | not-running | auth | partial | na | unknown. Never a bare bool.
    start_cmd is surfaced for display/copy ONLY — this module never runs it.
    """
    see = see or {}
    kind = str(see.get("kind") or "none").strip().lower()

    url = str(see.get("url") or "").strip()
    path = str(see.get("path") or "").strip()
    start_cmd = str(see.get("start") or "").strip()
    note = str(see.get("note") or "").strip()
    label = str(see.get("label") or "").strip() or _KIND_LABEL.get(kind, kind)

    r = probe(see)
    ok, detail = r.get("ok"), r.get("detail", "")

    if ok is None:
        status = "na" if kind == "none" or not detail else "unknown"
        # an unparseable probe or a probe error is unknown, not running
        if kind != "none" and detail.startswith("unrecognized"):
            status = "unknown"
    elif ok is True:
        status = "running"
    else:
        status = "auth" if detail.startswith("auth") else "not-running"

    return {
        "kind": kind,
        "label": label,
        "url": url,
        "path": path,
        "start_cmd": start_cmd,
        "note": note,
        "status": status,
    }


# ----------------------------------------------------------------------- main

def _fmt_ts(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="See-It: read-only up-probes for the Karpathy Loop")
    ap.add_argument("--check", action="store_true", help="probe every enabled project with a see block")
    ap.add_argument("--one", metavar="NAME", help="probe a single project by name")
    ap.add_argument("--manifest", metavar="PATH", default=None,
                    help="alternate manifest (defaults to improve.yaml next to this file)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)

    global MANIFEST
    if args.manifest:
        MANIFEST = Path(args.manifest)

    manifest = load_manifest()
    projects = {str(p.get("name") or "?"): p for p in _enabled(manifest)}

    if args.one:
        name = args.one
        proj = projects.get(name)
        if proj is None:
            print(json.dumps({"error": f"no enabled project named {name!r}"}))
            return 1
        see = proj.get("see") or {}
        result = {"name": name, "probe": probe(see), "describe": describe(see)}
        print(json.dumps(result, indent=2) if args.json else json.dumps(result))
        return 0

    if not args.check:
        ap.print_help()
        return 0

    results = check_all(manifest)
    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    if not results:
        print("no enabled projects declare a see: block")
        return 0

    # small table
    rows = [(n, r.get("ok"), r.get("detail", ""), _fmt_ts(r.get("checked_at", 0.0)))
            for n, r in results.items()]
    w = max(len(n) for n, *_ in rows) + 2
    for name, ok, detail, ts in rows:
        verdict = {True: "UP", False: "DOWN", None: "n/a"}[ok]
        print(f"{name:<{w}} {verdict:<6} {ts}  {detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
