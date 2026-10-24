#!/usr/bin/env python3
"""
seat_preflight.py -- W1/W2: lane preflight, failure classification, circuit breaker.

THE REGRESSION THIS EXISTS TO PREVENT
-------------------------------------
Oct 15, 2026: rounds 174-180 burned into a dead lane. Every child call failed at
the transport layer, but nothing in the loop could tell a DEAD PROVIDER from a
MODEL REFUSAL -- so each round looked like "the model declined this angle" and
the loop happily rotated on, spending 7 rounds' worth of wall-clock on an
endpoint that was not answering at all.

Two different facts, two different responses:
  * PROVIDER FAILURE -> transport/timeout/empty. Nothing was generated. Retrying
    the same angle is pointless; the SEAT is broken. Count it, and after 3 in a
    row on one seat, HALT (do not keep burning rounds).
  * MODEL REFUSAL   -> the model answered, in words, that it will not do this.
    That is a legitimate round outcome (the angle is not applicable). The seat
    is fine. Never halt for it.

MEASURED FACTS THIS MODULE IS BUILT ON (probed live 2026-10-23)
--------------------------------------------------------------
1. An ALIAS-HONORING lane answers ANY model name. `POST /v1/chat/completions`
   with model="qwen3.8-flash-next" against 127.0.0.1:18888 returns
   model="DeepSeek-V4.1-Flash-1017". So the seat LABEL proves nothing about what
   generated -- only the response `model` field does. `probe()` therefore records
   BOTH `requested` and `served`, and `family_identity()` keys on `served`.
2. Three seat slugs in config.yaml point at the SAME endpoint
   (192.168.1.49:8081): glm53-flash-4x-spark-tp4, evo-x2, d754-mia-flashnext.
   Two seats on one endpoint are ONE family no matter what they are named.
3. A reasoning model can return `content: ""` with `finish_reason: "length"`
   when max_tokens is small -- the budget was spent on reasoning_tokens. That is
   NOT a provider failure and NOT a refusal; it is an inconclusive probe. The
   probe uses enough max_tokens that a healthy lane always emits real content,
   and treats empty-content-with-no-error as inconclusive rather than as death.

DESIGN RULE: a probe never mutates anything. It is read-only and cheap (~1 call).
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

HERMES_HOME = Path(r"C:\Users\gene\AppData\Local\hermes")

# Classification verdicts. These are the ONLY values classify() returns.
PROVIDER_FAILURE = "provider-failure"
MODEL_REFUSAL = "model-refusal"
OK = "ok"
INCONCLUSIVE = "inconclusive"

# A probe asks for a fixed token so the answer is checkable without trusting the
# model's goodwill. 256 is comfortably above the reasoning budget we measured
# (9 reasoning tokens for a one-word answer) and still finishes in ~1-2s.
PROBE_MAX_TOKENS = 256
PROBE_PROMPT = "Reply with exactly this word and nothing else: PROBE"
PROBE_EXPECT = "PROBE"
DEFAULT_TIMEOUT_S = 45          # first call after idle measured ~32s on this fleet
PROVIDER_FAILURE_HALT = 3       # PRD W1: 3 consecutive -> halt dispatch


# ---------------------------------------------------------------------------
# provider resolution: seat string -> endpoint
# ---------------------------------------------------------------------------
def _load_config(config_path: Path | None = None) -> dict:
    import yaml
    p = config_path or (HERMES_HOME / "config.yaml")
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def parse_seat(seat: str):
    """`custom:<slug>:<model>` -> (slug, model); `provider:model` -> (provider, model).

    Mirrors karpathy_runner._seat_provider so the two can never disagree about
    what a seat means. Returns (None, None) for a malformed seat.
    """
    parts = [p for p in str(seat or "").split(":") if p]
    if not parts:
        return (None, None)
    if parts[0].lower() == "custom":
        if len(parts) < 3:
            return (None, None)
        # a model name may itself contain ':' -- everything after the slug is it
        return (parts[1], ":".join(parts[2:]))
    if len(parts) < 2:
        return (None, None)
    return (parts[0], ":".join(parts[1:]))


def resolve_endpoint(seat: str, config: dict | None = None) -> dict:
    """Turn a seat into {slug, model, base_url, api_key, error}.

    An unconfigured slug is a CONFIG error, not a provider failure -- it must be
    reported as such so nobody "fixes" it by retrying.
    """
    cfg = config if config is not None else _load_config()
    slug, model = parse_seat(seat)
    out = {"seat": seat, "slug": slug, "model": model,
           "base_url": None, "api_key": "", "error": None}
    if slug is None:
        out["error"] = "seat %r is not in provider:model form" % (seat,)
        return out
    provs = cfg.get("providers") or {}
    spec = provs.get(slug)
    if not isinstance(spec, dict):
        out["error"] = ("provider %r is not configured in config.yaml "
                        "(config error -- not a lane failure)" % slug)
        return out
    base = (spec.get("api") or "").rstrip("/")
    if not base:
        out["error"] = "provider %r has no api/base_url" % slug
        return out
    out["base_url"] = base
    out["api_key"] = spec.get("api_key") or ""
    out["transport"] = spec.get("transport") or "chat_completions"
    return out


# ---------------------------------------------------------------------------
# the probe
# ---------------------------------------------------------------------------
def probe(seat: str, config: dict | None = None,
          timeout: float = DEFAULT_TIMEOUT_S,
          max_tokens: int = PROBE_MAX_TOKENS) -> dict:
    """One minimal round-trip to a seat's endpoint. Never raises.

    Returns:
      seat, requested (the model name we ASKED for), served (the model name the
      endpoint REPORTED -- the only honest identity), verdict, http_status,
      latency_s, detail, and (for refusals) refusal_text.

    The verdict is classified from the RESPONSE, not from our expectations:
      * no HTTP response / non-200 / JSON that does not parse -> provider-failure
      * 200 with an error object inside                     -> provider-failure
      * 200 with content that is not the expected token,
        and looks like a decline                            -> model-refusal
      * 200 with empty content and finish_reason=length     -> inconclusive
      * 200 with any other content                          -> ok
    """
    res = {"seat": seat, "requested": None, "served": None, "verdict": None,
           "http_status": None, "latency_s": None, "detail": "", "refusal_text": "",
           "at": time.time()}
    ep = resolve_endpoint(seat, config)
    res["requested"] = ep.get("model")
    if ep.get("error"):
        # Config error is not a provider failure -- it is an operator error and
        # must halt the same way, but with a message that says which.
        res["verdict"] = PROVIDER_FAILURE
        res["detail"] = "config: " + ep["error"]
        res["config_error"] = True
        return res

    url = ep["base_url"] + "/chat/completions"
    body = json.dumps({
        "model": ep["model"],
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "stream": False,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if ep.get("api_key"):
        headers["Authorization"] = "Bearer " + ep["api_key"]

    t0 = time.time()
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res["http_status"] = r.status
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        res["http_status"] = e.code
        res["latency_s"] = round(time.time() - t0, 3)
        try:
            res["detail"] = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            res["detail"] = "HTTP %s" % e.code
        res["verdict"] = PROVIDER_FAILURE
        return res
    except Exception as e:
        res["latency_s"] = round(time.time() - t0, 3)
        res["detail"] = "%s: %s" % (type(e).__name__, e)
        res["verdict"] = PROVIDER_FAILURE
        return res

    res["latency_s"] = round(time.time() - t0, 3)
    try:
        doc = json.loads(raw)
    except Exception:
        res["verdict"] = PROVIDER_FAILURE
        res["detail"] = "response is not JSON: %r" % raw[:200]
        return res

    if isinstance(doc, dict) and doc.get("error"):
        res["verdict"] = PROVIDER_FAILURE
        res["detail"] = "endpoint returned an error object: %s" % json.dumps(doc["error"])[:300]
        return res

    # THE HONEST IDENTITY: what the endpoint says it served, never what we asked.
    res["served"] = doc.get("model") if isinstance(doc, dict) else None

    try:
        choice = doc["choices"][0]
        msg = choice.get("message") or {}
        content = (msg.get("content") or "").strip()
        finish = choice.get("finish_reason")
    except Exception:
        res["verdict"] = PROVIDER_FAILURE
        res["detail"] = "response has no choices/message: %r" % raw[:200]
        return res

    if not content:
        # Measured trap (2026-10-23): a reasoning model with a small token budget
        # spends it all on reasoning_tokens and returns content="" with
        # finish_reason="length". That is INCONCLUSIVE -- the lane is answering.
        if finish == "length":
            res["verdict"] = INCONCLUSIVE
            res["detail"] = ("empty content with finish_reason=length -- token "
                             "budget spent on reasoning, lane is answering")
        else:
            res["verdict"] = PROVIDER_FAILURE
            res["detail"] = "200 with empty content (finish_reason=%s)" % finish
        return res

    if PROBE_EXPECT.lower() in content.lower():
        res["verdict"] = OK
        res["detail"] = "answered %r" % content[:80]
        return res

    # The lane answered in words, but not what we asked for. Decide refusal vs ok:
    # a REFUSAL is an explicit decline. Anything else (e.g. it echoed the prompt,
    # or answered a different word) still proves the lane GENERATES, so it is ok.
    res["refusal_text"] = content[:400]
    if _looks_like_refusal(content):
        res["verdict"] = MODEL_REFUSAL
        res["detail"] = "explicit decline: %r" % content[:160]
    else:
        res["verdict"] = OK
        res["detail"] = "answered (unexpected text, lane healthy): %r" % content[:120]
    return res


_REFUSAL_MARKERS = (
    "i can't", "i cannot", "i can not", "i won't", "i will not", "i'm unable",
    "i am unable", "i'm not able", "i am not able", "as an ai",
    "i must decline", "i refuse", "cannot comply", "can't comply",
    "cannot assist", "can't assist", "cannot help with", "i'm sorry, but",
    "i apologize, but", "not able to help", "unable to comply",
    "i'm not going to", "i am not going to",
)


def _looks_like_refusal(text: str) -> bool:
    """Content-level refusal detection. Deliberately conservative: a false
    'refusal' only costs a marked round; a false 'ok' hides a broken lane."""
    t = (text or "").lower()
    if not t:
        return False
    return any(m in t for m in _REFUSAL_MARKERS)


def classify(text: str) -> str:
    """Classify a CHILD ROUND's output the same way probe() classifies a probe.

    This is the function W1's dispatch gate uses after a round returns, so a
    round that produced nothing (empty stdout, a timeout marker, a canned
    "request not processed") counts as a PROVIDER FAILURE and increments the
    breaker -- while a round whose child said "this angle does not apply" counts
    as a legitimate refusal and does NOT.
    """
    t = (text or "").strip()
    if not t:
        return PROVIDER_FAILURE
    low = t.lower()
    # The canned strings our own code writes on a transport death.
    for m in ("request not processed", "timed out after", "timeout after",
              "connection refused", "connectionreset", "no route to host",
              "temporary failure in name resolution", "502 bad gateway",
              "503 service unavailable", "504 gateway timeout",
              "read timed out", "max retries exceeded"):
        if m in low:
            return PROVIDER_FAILURE
    return MODEL_REFUSAL


# ---------------------------------------------------------------------------
# family identity (W2)
# ---------------------------------------------------------------------------
def family_identity(probe_result: dict) -> str:
    """The family key for a probed seat.

    Aliasing lanes make the seat LABEL meaningless, so the key is built from the
    SERVED model name -- what actually generated. The endpoint is deliberately
    NOT part of the key: two ports of the same host serving the same model are
    the same family, and folding host:port in made `18888` vs `18889` read as
    "different" for one model (caught in a live check-pair, 2026-10-23). The
    endpoint is reported separately by `endpoint_key()` so a caller can tell
    "same model, two boxes" from "same model, same box" without the family test
    ever depending on it.

    If nothing was served (a failed probe) the family is unknown -- callers must
    treat unknown as "cannot establish separation", never as "different".
    """
    served = probe_result.get("served")
    if not served:
        return None
    return served.strip().lower()


def endpoint_key(probe_result: dict) -> str:
    """host:port of the seat's endpoint, or '' when unknown."""
    ep = resolve_endpoint(probe_result.get("seat") or "")
    return _hostport(ep.get("base_url") or "")


def _same_host(a: dict, b: dict) -> bool:
    ha, hb = endpoint_key(a), endpoint_key(b)
    if not ha or not hb:
        return False
    return ha.split(":")[0] == hb.split(":")[0]



def _hostport(base_url: str) -> str:
    m = re.match(r"^https?://([^/]+)", base_url or "")
    return (m.group(1) if m else "").lower()


def families_differ(a: dict, b: dict) -> dict:
    """Are two probed seats genuinely different families?

    Returns {differ, reason, a_family, b_family, same_host, endpoints}.
    `differ` is only True when BOTH families are established AND differ. Unknown
    -> False with a reason naming what is missing (W2: cannot establish
    separation means review-invalid, not that it passed).
    """
    fa = family_identity(a)
    fb = family_identity(b)
    out = {"a_family": fa, "b_family": fb, "differ": False, "reason": "",
           "same_host": _same_host(a, b),
           "endpoints": [endpoint_key(a), endpoint_key(b)]}
    if not fa:
        out["reason"] = "implementer family unknown (%s)" % (a.get("detail") or a.get("verdict"))
        return out
    if not fb:
        out["reason"] = "reviewer family unknown (%s)" % (b.get("detail") or b.get("verdict"))
        return out
    if fa == fb:
        out["reason"] = ("same family: both seats served %s -- an alias-honoring "
                         "lane or two slugs on one endpoint" % fa)
        return out
    out["differ"] = True
    out["reason"] = "%s vs %s" % (fa, fb)
    return out


# ---------------------------------------------------------------------------
# circuit breaker state (W1)
# ---------------------------------------------------------------------------
def breaker_path(root: Path) -> Path:
    return Path(root) / "state" / "lane_breaker.json"


def load_breaker(root: Path) -> dict:
    p = breaker_path(root)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"seats": {}}


def save_breaker(root: Path, data: dict) -> None:
    p = breaker_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)


def record(root: Path, role: str, verdict: str, seat: str,
           detail: str = "", halt_after: int = PROVIDER_FAILURE_HALT) -> dict:
    """Update the consecutive-provider-failure counter for one seat role.

    Only PROVIDER_FAILURE increments. OK and MODEL_REFUSAL reset the streak --
    a lane that answered, even to refuse, is alive. Returns the updated seat
    record including `tripped` (True the moment the streak reaches the limit).
    """
    data = load_breaker(root)
    seats = data.setdefault("seats", {})
    rec = seats.setdefault(role, {"seat": seat, "streak": 0, "tripped": False,
                                  "history": []})
    # A seat CHANGE starts a fresh streak: the old failures belong to the old lane.
    if rec.get("seat") != seat:
        rec.update({"seat": seat, "streak": 0, "tripped": False, "history": []})
    if verdict == PROVIDER_FAILURE:
        rec["streak"] = int(rec.get("streak", 0)) + 1
        rec["last_failure"] = {"detail": detail, "at": time.time()}
    else:
        rec["streak"] = 0
        rec["tripped"] = False
    rec["last_verdict"] = verdict
    rec["at"] = time.time()
    hist = rec.setdefault("history", [])
    hist.append({"verdict": verdict, "at": time.time(), "detail": (detail or "")[:200]})
    rec["history"] = hist[-20:]
    rec["tripped"] = bool(rec["streak"] >= halt_after)
    save_breaker(root, data)
    return rec


def tripped(root: Path) -> dict | None:
    """Which seat (if any) is currently over the limit? None = dispatch allowed."""
    data = load_breaker(root)
    for role, rec in (data.get("seats") or {}).items():
        if rec.get("tripped"):
            return {"role": role, **rec}
    return None


def reset(root: Path, role: str | None = None) -> None:
    """Clear the breaker. Called on an explicit human restart, never automatically
    -- auto-clearing is how a dead lane gets silently re-tried forever."""
    data = load_breaker(root)
    if role is None:
        data["seats"] = {}
    else:
        (data.get("seats") or {}).pop(role, None)
    save_breaker(root, data)


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------
def _cmd_probe(argv) -> int:
    seat = argv[0] if argv else ""
    r = probe(seat)
    print(json.dumps(r, indent=2))
    return 0 if r["verdict"] == OK else 1


def _cmd_check_pair(argv) -> int:
    """Probe both seats and report whether their families differ (W2 dry-run)."""
    impl = argv[0] if len(argv) > 0 else ""
    rev = argv[1] if len(argv) > 1 else ""
    a = probe(impl)
    b = probe(rev)
    fam = families_differ(a, b)
    print(json.dumps({"implementer": a, "reviewer": b, "separation": fam}, indent=2))
    return 0 if fam["differ"] else 1


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if args and args[0] == "probe":
        raise SystemExit(_cmd_probe(args[1:]))
    if args and args[0] == "check-pair":
        raise SystemExit(_cmd_check_pair(args[1:]))
    print("usage: seat_preflight.py probe <seat> | check-pair <impl-seat> <rev-seat>")
    raise SystemExit(2)
