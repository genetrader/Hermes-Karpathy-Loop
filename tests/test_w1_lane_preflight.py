"""W1/W2 regression tests -- lane preflight, failure classification, circuit breaker.

The regression being pinned (Oct 15, 2026): rounds 174-180 burned into a dead
lane because the loop could not tell a DEAD PROVIDER from a MODEL REFUSAL.

Every test here is offline: `probe()` is driven against a local stub server or a
stubbed urlopen, so the suite never touches a fleet lane.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import seat_preflight as sp


# ---------------------------------------------------------------------------
# a stub OpenAI-compatible endpoint
# ---------------------------------------------------------------------------
class _Stub(BaseHTTPRequestHandler):
    behavior = "ok"
    seen = []

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        _Stub.seen.append(body)
        b = _Stub.behavior
        if b == "http500":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b'{"error": "boom"}')
            return
        if b == "notjson":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>gateway</html>")
            return
        if b == "errorobj":
            payload = {"error": {"message": "request not processed"}}
        elif b == "alias":
            # THE ALIAS TRAP: asked for one model, serves another.
            payload = {"model": "deepseek-v4.1-flash-1017",
                       "choices": [{"message": {"content": "PROBE"},
                                    "finish_reason": "stop"}]}
        elif b == "refusal":
            payload = {"model": body.get("model"),
                       "choices": [{"message": {
                           "content": "I cannot help with that request."},
                           "finish_reason": "stop"}]}
        elif b == "reasoning_empty":
            # measured 2026-10-23: budget spent on reasoning -> content=""
            payload = {"model": body.get("model"),
                       "choices": [{"message": {"content": "",
                                                "reasoning_content": "thinking"},
                                    "finish_reason": "length"}]}
        elif b == "empty":
            payload = {"model": body.get("model"),
                       "choices": [{"message": {"content": ""},
                                    "finish_reason": "stop"}]}
        else:  # "ok"
            payload = {"model": body.get("model"),
                       "choices": [{"message": {"content": "PROBE"},
                                    "finish_reason": "stop"}]}
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture
def stub(monkeypatch):
    """Serve a stub endpoint and point a config at it."""
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    cfg = {"providers": {
        "stub": {"api": "http://127.0.0.1:%d/v1" % port, "api_key": ""},
        "stub2": {"api": "http://127.0.0.1:%d/v1" % port, "api_key": ""},
        "dead": {"api": "http://127.0.0.1:1/v1", "api_key": ""},
    }}
    monkeypatch.setattr(sp, "_load_config", lambda p=None: cfg)
    yield {"port": port, "cfg": cfg, "srv": srv}
    srv.shutdown()


def _seat(model, slug="stub"):
    return "custom:%s:%s" % (slug, model)


# ---------------------------------------------------------------------------
# W1: classification
# ---------------------------------------------------------------------------
def test_a_healthy_lane_is_ok(stub):
    _Stub.behavior = "ok"
    r = sp.probe(_seat("m1"), timeout=10)
    assert r["verdict"] == sp.OK
    assert r["served"] == "m1"
    assert r["requested"] == "m1"


def test_transport_refusal_is_a_provider_failure_not_a_refusal(stub):
    """The Oct 15 shape: nothing answers. Must be provider-failure."""
    r = sp.probe(_seat("m1", slug="dead"), timeout=5)
    assert r["verdict"] == sp.PROVIDER_FAILURE
    assert r["served"] is None
    assert "refused" in r["detail"].lower() or "urlopen" in r["detail"].lower()


def test_http_500_is_a_provider_failure(stub):
    _Stub.behavior = "http500"
    r = sp.probe(_seat("m1"), timeout=10)
    assert r["verdict"] == sp.PROVIDER_FAILURE
    assert r["http_status"] == 500


def test_non_json_response_is_a_provider_failure(stub):
    _Stub.behavior = "notjson"
    r = sp.probe(_seat("m1"), timeout=10)
    assert r["verdict"] == sp.PROVIDER_FAILURE


def test_error_object_in_200_is_a_provider_failure(stub):
    """'request not processed' rides a 200. It must NOT read as a model refusal."""
    _Stub.behavior = "errorobj"
    r = sp.probe(_seat("m1"), timeout=10)
    assert r["verdict"] == sp.PROVIDER_FAILURE


def test_empty_content_with_length_is_inconclusive_not_dead(stub):
    """Measured trap: a reasoning model spends a small budget on reasoning and
    returns content='' finish_reason='length'. The lane IS answering."""
    _Stub.behavior = "reasoning_empty"
    r = sp.probe(_seat("m1"), timeout=10)
    assert r["verdict"] == sp.INCONCLUSIVE


def test_empty_content_with_stop_is_a_provider_failure(stub):
    _Stub.behavior = "empty"
    r = sp.probe(_seat("m1"), timeout=10)
    assert r["verdict"] == sp.PROVIDER_FAILURE


def test_an_explicit_decline_is_a_model_refusal(stub):
    _Stub.behavior = "refusal"
    r = sp.probe(_seat("m1"), timeout=10)
    assert r["verdict"] == sp.MODEL_REFUSAL
    assert r["refusal_text"]


def test_unconfigured_slug_is_a_config_error(stub):
    r = sp.probe("custom:nosuch:model", timeout=10)
    assert r["verdict"] == sp.PROVIDER_FAILURE
    assert r.get("config_error") is True
    assert "not configured" in r["detail"]


def test_malformed_seat_is_refused(stub):
    r = sp.probe("bare-model-name", timeout=10)
    assert r["verdict"] == sp.PROVIDER_FAILURE
    assert "provider:model" in r["detail"]


# ---------------------------------------------------------------------------
# W1: classify() on CHILD output (the post-round gate)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "",
    "   \n\t ",
    "request not processed",
    "TIMEOUT after 4200s -- killed",
    "Connection refused by peer",
    "502 Bad Gateway",
    "Read timed out",
])
def test_dead_child_output_is_a_provider_failure(text):
    assert sp.classify(text) == sp.PROVIDER_FAILURE


@pytest.mark.parametrize("text", [
    "This angle does not apply: the repo has no dead code.",
    "SURFACE-DONE: none -- nothing to change",
    "I cannot find a lying test in this repo.",
])
def test_a_working_child_that_declines_is_a_model_refusal(text):
    assert sp.classify(text) == sp.MODEL_REFUSAL


# ---------------------------------------------------------------------------
# W1: circuit breaker
# ---------------------------------------------------------------------------
def test_breaker_trips_after_three_consecutive_provider_failures(tmp_path):
    seat = _seat("m1")
    assert sp.tripped(tmp_path) is None
    for i in range(2):
        rec = sp.record(tmp_path, "implementer", sp.PROVIDER_FAILURE, seat, "dead")
        assert rec["tripped"] is False
    rec = sp.record(tmp_path, "implementer", sp.PROVIDER_FAILURE, seat, "dead")
    assert rec["tripped"] is True
    assert rec["streak"] == 3
    hit = sp.tripped(tmp_path)
    assert hit and hit["role"] == "implementer"


def test_a_success_resets_the_streak(tmp_path):
    seat = _seat("m1")
    sp.record(tmp_path, "implementer", sp.PROVIDER_FAILURE, seat)
    sp.record(tmp_path, "implementer", sp.PROVIDER_FAILURE, seat)
    sp.record(tmp_path, "implementer", sp.OK, seat)
    assert sp.tripped(tmp_path) is None
    sp.record(tmp_path, "implementer", sp.PROVIDER_FAILURE, seat)
    sp.record(tmp_path, "implementer", sp.PROVIDER_FAILURE, seat)
    assert sp.tripped(tmp_path) is None, "two in a row after a reset must not trip"


def test_a_model_refusal_does_not_trip_the_breaker(tmp_path):
    """THE CORE REGRESSION: refusals are legitimate rounds. They must never halt."""
    seat = _seat("m1")
    for _ in range(10):
        sp.record(tmp_path, "implementer", sp.MODEL_REFUSAL, seat)
    assert sp.tripped(tmp_path) is None


def test_changing_the_seat_starts_a_fresh_streak(tmp_path):
    sp.record(tmp_path, "implementer", sp.PROVIDER_FAILURE, _seat("old"))
    sp.record(tmp_path, "implementer", sp.PROVIDER_FAILURE, _seat("old"))
    rec = sp.record(tmp_path, "implementer", sp.PROVIDER_FAILURE, _seat("new"))
    assert rec["streak"] == 1 and rec["tripped"] is False


def test_breaker_persists_across_loads(tmp_path):
    seat = _seat("m1")
    for _ in range(3):
        sp.record(tmp_path, "reviewer", sp.PROVIDER_FAILURE, seat)
    raw = json.loads(sp.breaker_path(tmp_path).read_text())
    assert raw["seats"]["reviewer"]["tripped"] is True


def test_reset_clears_the_breaker(tmp_path):
    seat = _seat("m1")
    for _ in range(3):
        sp.record(tmp_path, "implementer", sp.PROVIDER_FAILURE, seat)
    sp.reset(tmp_path)
    assert sp.tripped(tmp_path) is None


# ---------------------------------------------------------------------------
# W2: cross-family review
# ---------------------------------------------------------------------------
def test_an_alias_honoring_lane_is_one_family(stub):
    """Both seats point at an endpoint that serves a FIXED model name regardless
    of what was requested -> same family, separation impossible."""
    _Stub.behavior = "alias"
    a = sp.probe(_seat("GLM-5.3-Flash-EXL3"), timeout=10)
    b = sp.probe(_seat("qwen3.8-flash-next", slug="stub2"), timeout=10)
    assert a["requested"] != b["requested"]      # labels differ
    assert a["served"] == b["served"]            # reality does not
    fam = sp.families_differ(a, b)
    assert fam["differ"] is False
    assert "same family" in fam["reason"]


def test_two_genuinely_different_models_are_different_families(stub):
    _Stub.behavior = "ok"     # echoes the requested model back as served
    a = sp.probe(_seat("model-a"), timeout=10)
    b = sp.probe(_seat("model-b", slug="stub2"), timeout=10)
    fam = sp.families_differ(a, b)
    assert fam["differ"] is True


def test_one_dead_seat_cannot_establish_separation(stub):
    a = sp.probe(_seat("m1"), timeout=10)
    b = sp.probe(_seat("m1", slug="dead"), timeout=5)
    fam = sp.families_differ(a, b)
    assert fam["differ"] is False
    assert "unknown" in fam["reason"]


def test_the_same_model_on_two_ports_is_still_one_family(stub):
    """Caught live 2026-10-23: 18888 vs 18889 both served DeepSeek and an
    endpoint-keyed family wrongly reported 'different'."""
    _Stub.behavior = "alias"
    a = sp.probe(_seat("GLM"), timeout=10)
    b = sp.probe(_seat("qwen", slug="stub2"), timeout=10)
    fam = sp.families_differ(a, b)
    assert fam["differ"] is False


def test_family_identity_uses_served_not_requested(stub):
    _Stub.behavior = "alias"
    r = sp.probe(_seat("GLM-5.3-Flash-EXL3"), timeout=10)
    assert sp.family_identity(r) == "deepseek-v4.1-flash-1017"
    assert sp.family_identity(r) != r["requested"].lower()


def test_probe_never_reports_a_verdict_for_a_failed_parse(stub):
    """A response we cannot read must never be counted as ok."""
    _Stub.behavior = "notjson"
    r = sp.probe(_seat("m1"), timeout=10)
    assert r["verdict"] != sp.OK


# ---------------------------------------------------------------------------
# W1: the pause must land in the RUNNER's root, not loopctl's import-time root
# ---------------------------------------------------------------------------
def test_pause_lane_down_writes_to_the_runner_root(tmp_path, monkeypatch):
    """Caught in a live drill 2026-10-23: _pause_lane_down called
    loopctl.save(), which binds STATE/LOOP from ITS OWN file path -- so a
    redirected ROOT (drill/test) paused the LIVE loop instead. A control must
    act on the root it was given."""
    import json as _json
    import karpathy_runner as kr

    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "loop.json").write_text(
        _json.dumps({"running": True, "projects": ["p1"], "rounds_done": 7}))
    monkeypatch.setattr(kr, "ROOT", tmp_path)
    monkeypatch.setattr(kr, "log", lambda *a, **k: None)
    monkeypatch.setattr(kr.dn, "stuck", lambda *a, **k: None)

    kr._pause_lane_down("implementer lane dead")

    doc = _json.loads((tmp_path / "state" / "loop.json").read_text())
    assert doc["running"] is False
    assert doc["paused_reason"] == "lane-down"
    assert doc["paused_detail"] == "implementer lane dead"
    assert doc["rounds_done"] == 7, "the pause must not clobber unrelated state"


def test_pause_lane_down_is_atomic(tmp_path, monkeypatch):
    """No torn loop.json may ever exist: write temp then os.replace."""
    import json as _json
    import karpathy_runner as kr

    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "loop.json").write_text(_json.dumps({"running": True}))
    monkeypatch.setattr(kr, "ROOT", tmp_path)
    monkeypatch.setattr(kr, "log", lambda *a, **k: None)
    monkeypatch.setattr(kr.dn, "stuck", lambda *a, **k: None)

    kr._pause_lane_down("x")
    leftovers = list((tmp_path / "state").glob("loop.json.tmp"))
    assert leftovers == [], "temp file left behind: %r" % leftovers


def test_lane_gate_halts_after_three_dead_probes(tmp_path, monkeypatch):
    """End-to-end gate: a dead implementer seat halts dispatch on the 3rd probe."""
    import json as _json
    import karpathy_runner as kr

    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "loop.json").write_text(_json.dumps({"running": True}))
    monkeypatch.setattr(kr, "ROOT", tmp_path)
    monkeypatch.setattr(kr, "log", lambda *a, **k: None)
    monkeypatch.setattr(kr.dn, "stuck", lambda *a, **k: None)

    cfg = {"providers": {
        "dead": {"api": "http://127.0.0.1:1/v1", "api_key": ""},
    }}
    monkeypatch.setattr(sp, "_load_config", lambda p=None: cfg)
    lc = {"implementer": "custom:dead:m", "reviewer": "custom:dead:m"}

    results = [kr._lane_gate(lc)[0] for _ in range(3)]
    assert results == [True, True, False], results
    doc = _json.loads((tmp_path / "state" / "loop.json").read_text())
    assert doc["paused_reason"] == "lane-down"


def test_lane_gate_does_not_halt_on_a_healthy_pair(tmp_path, monkeypatch, stub):
    """A healthy pair must never be halted, even repeatedly."""
    import json as _json
    import karpathy_runner as kr

    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "loop.json").write_text(_json.dumps({"running": True}))
    monkeypatch.setattr(kr, "ROOT", tmp_path)
    monkeypatch.setattr(kr, "log", lambda *a, **k: None)

    _Stub.behavior = "ok"
    lc = {"implementer": _seat("model-a"), "reviewer": _seat("model-b", slug="stub2")}
    for _ in range(5):
        ok, pfl = kr._lane_gate(lc)
        assert ok is True
    assert pfl["separation"]["differ"] is True
    doc = _json.loads((tmp_path / "state" / "loop.json").read_text())
    assert doc.get("paused_reason") is None


def test_lane_gate_dispatches_but_flags_same_family(tmp_path, monkeypatch, stub):
    """W2: one alias-honoring family must NOT halt -- it flags review-invalid."""
    import json as _json
    import karpathy_runner as kr

    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "loop.json").write_text(_json.dumps({"running": True}))
    monkeypatch.setattr(kr, "ROOT", tmp_path)
    monkeypatch.setattr(kr, "log", lambda *a, **k: None)

    _Stub.behavior = "alias"
    lc = {"implementer": _seat("GLM"), "reviewer": _seat("qwen", slug="stub2")}
    ok, pfl = kr._lane_gate(lc)
    assert ok is True, "same-family must dispatch, not halt"
    assert pfl["separation"]["differ"] is False
    doc = _json.loads((tmp_path / "state" / "loop.json").read_text())
    assert doc.get("paused_reason") is None
