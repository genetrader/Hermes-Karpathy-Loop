"""Round-duration telemetry (Gene, 2026-10-01).

The runner stamps entry["last_round_seconds"] and appends to
entry["round_seconds_hist"] when a round finishes; loopctl/monitor display
the per-repo average and the in-flight elapsed timer. These tests pin the
stamping, the history bound, and the display helpers.
"""
import json

import pytest

import loopctl
from test_round_orchestration import PROJ, Harness


@pytest.fixture()
def world(tmp_path, monkeypatch):
    def make(gate_ok: bool = True) -> Harness:
        return Harness(tmp_path, monkeypatch, gate_ok=gate_ok)
    return make


# --------------------------------------------------------------------------
# unit: loopctl._avg_round_secs filters garbage, averages the rest
# --------------------------------------------------------------------------
def test_avg_round_secs_basic():
    assert loopctl._avg_round_secs({"round_seconds_hist": [60, 120]}) == 90
    assert loopctl._avg_round_secs({}) is None
    assert loopctl._avg_round_secs(None) is None
    got = loopctl._avg_round_secs(
        {"round_seconds_hist": [100, "x", -5, 999999, 200]})
    assert got == 150, got


# --------------------------------------------------------------------------
# unit: _fmt_mmss formatting
# --------------------------------------------------------------------------
def test_fmt_mmss():
    assert loopctl._fmt_mmss(45) == "45s"
    assert loopctl._fmt_mmss(60) == "1m"
    assert loopctl._fmt_mmss(125) == "2m"
    assert loopctl._fmt_mmss(3600) == "1h00m"
    assert loopctl._fmt_mmss(4325) == "1h12m"
    assert loopctl._fmt_mmss(None) == "-"
    assert loopctl._fmt_mmss(-3) == "-"


# --------------------------------------------------------------------------
# end-to-end: an accepted round stamps the duration + seeds the history
# --------------------------------------------------------------------------
def test_accepted_round_stamps_duration(world):
    h = world(gate_ok=True)
    h.child_does(commit_file="t.py", msg="accepted work")
    h.run()
    e = h.reload_registry()[PROJ]
    secs = e.get("last_round_seconds")
    assert isinstance(secs, int) and secs >= 0, \
        "an accepted round must stamp last_round_seconds"
    assert e.get("round_seconds_hist") == [secs], \
        "the first accepted round seeds a 1-entry history"


# --------------------------------------------------------------------------
# end-to-end: the history is bounded (cap 30, recent-last)
# --------------------------------------------------------------------------
def test_history_bounded(world):
    h = world(gate_ok=True)
    # run_round mutates the ENTRY object it is handed (the harness's), so
    # the seed belongs on h.entry -- not the registry file.
    h.entry["round_seconds_hist"] = list(range(1000, 1030))  # 30 entries
    h.child_does(commit_file="u.py", msg="accepted work 2")
    h.run()
    e2 = h.reload_registry()[PROJ]
    hist = e2.get("round_seconds_hist") or []
    assert len(hist) == 30, "history must stay capped at 30"
    assert hist[-1] not in list(range(1000, 1030)), \
        "the newest stamp must replace the oldest entry (recent-last)"


# --------------------------------------------------------------------------
# end-to-end: a REJECTED round still stamps (its runtime is real telemetry)
# --------------------------------------------------------------------------
def test_rejected_round_stamps_duration(world):
    h = world(gate_ok=False)
    h.child_does(commit_file="v.py", msg="will be rejected")
    h.run()
    e = h.reload_registry()[PROJ]
    assert isinstance(e.get("last_round_seconds"), int), \
        "a rejected round is still a round: its runtime is real telemetry"
    assert e.get("round_seconds_hist"), "rejected rounds feed the history"


# --------------------------------------------------------------------------
# unit: _timing_rows shape (avg/n/last; idle heartbeat -> none in flight)
# --------------------------------------------------------------------------
def test_timing_rows_shape(tmp_path, monkeypatch):
    reg = {
        "alpha": {"rounds": 3, "round_seconds_hist": [10, 20],
                  "last_round_seconds": 20},
        "beta": {"rounds": 1},
    }
    import threads as threads_mod
    monkeypatch.setattr(threads_mod, "load", lambda: dict(reg))
    st = tmp_path / "state"
    st.mkdir()
    (st / "runner_heartbeat.json").write_text(
        json.dumps({"state": "idle"}), encoding="utf-8")
    monkeypatch.setattr(loopctl, "ROOT", tmp_path)
    rows = loopctl._timing_rows(now=1000.0)
    by = {r["name"]: r for r in rows}
    assert by["alpha"]["avg_secs"] == 15 and by["alpha"]["n"] == 2
    assert by["alpha"]["last_secs"] == 20
    assert by["beta"]["avg_secs"] is None and by["beta"]["n"] == 0
    assert all(r["in_flight_secs"] is None for r in rows), \
        "an idle heartbeat must not report an in-flight round"


# --------------------------------------------------------------------------
# unit: in-flight timer from heartbeat + angle-start, heartbeat's project
# only
# --------------------------------------------------------------------------
def test_timing_rows_in_flight(tmp_path, monkeypatch):
    reg = {
        "alpha": {"rounds": 3, "current_angle": {"started": 900.0}},
        "beta": {"rounds": 1, "current_angle": {"started": 900.0}},
    }
    import threads as threads_mod
    monkeypatch.setattr(threads_mod, "load", lambda: dict(reg))
    st = tmp_path / "state"
    st.mkdir()
    (st / "runner_heartbeat.json").write_text(
        json.dumps({"state": "round-started", "project": "alpha",
                    "pid": 12345}), encoding="utf-8")
    monkeypatch.setattr(loopctl, "ROOT", tmp_path)
    monkeypatch.setattr(loopctl, "_pid_alive", lambda pid: True)
    rows = loopctl._timing_rows(now=1000.0)
    by = {r["name"]: r for r in rows}
    assert by["alpha"]["in_flight_secs"] == 100, \
        "the heartbeat's project gets the elapsed timer"
    assert by["beta"]["in_flight_secs"] is None, \
        "other projects must not show an in-flight timer"


# --------------------------------------------------------------------------
# unit: a DEAD heartbeat pid must not tick the timer (stale heartbeat)
# --------------------------------------------------------------------------
def test_timing_rows_stale_heartbeat(tmp_path, monkeypatch):
    reg = {"alpha": {"rounds": 3, "current_angle": {"started": 900.0}}}
    import threads as threads_mod
    monkeypatch.setattr(threads_mod, "load", lambda: dict(reg))
    st = tmp_path / "state"
    st.mkdir()
    (st / "runner_heartbeat.json").write_text(
        json.dumps({"state": "round-started", "project": "alpha",
                    "pid": 424242}), encoding="utf-8")
    monkeypatch.setattr(loopctl, "ROOT", tmp_path)
    monkeypatch.setattr(loopctl, "_pid_alive", lambda pid: False)
    rows = loopctl._timing_rows(now=1000.0)
    assert rows[0]["in_flight_secs"] is None, \
        "a dead runner's heartbeat must not show an in-flight round"
