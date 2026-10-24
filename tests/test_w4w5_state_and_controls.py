"""W4 + W5 regression tests -- state hardening and honest controls.

Pins the audit's open wedges:
  * a corrupt registry silently un-quarantining everything (fail closed instead)
  * read-modify-write races losing rounds (single-writer lock + update())
  * rounds_done drifting from the per-project counters (reconcile)
  * `pause --now` matching zero runner processes (the matcher bug, reproduced)
  * status calling a stale heartbeat "in flight" (STALLED instead)
  * an unattended loop running forever (nonzero max_rounds/max_hours defaults)
  * gateless projects auto-rejecting forever (config error -> pause)
"""
import json
import os
import subprocess
import time

import pytest

import loopctl
import threads


# ---------------------------------------------------------------------------
# W4: corrupt registry must FAIL CLOSED
# ---------------------------------------------------------------------------
def test_corrupt_registry_raises_instead_of_returning_empty(tmp_path, monkeypatch):
    """The defect: load() moved the file aside and returned {} -- which forgot
    every quarantine and all round history while the loop carried on."""
    bad = tmp_path / "threads.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(threads, "STATE", bad)

    with pytest.raises(threads.RegistryCorrupt) as ei:
        threads.load()
    assert "EMPTY registry" in str(ei.value)
    # evidence preserved, not destroyed
    salvaged = list(tmp_path.glob("threads.json.corrupt-*"))
    assert len(salvaged) == 1
    assert "this is not json" in salvaged[0].read_text(encoding="utf-8")


def test_strict_load_degrades_for_read_only_tools(tmp_path, monkeypatch):
    """A status panel must not crash on corrupt state; it just shows nothing."""
    bad = tmp_path / "threads.json"
    bad.write_text("nope", encoding="utf-8")
    monkeypatch.setattr(threads, "STATE", bad)
    assert threads.load(strict=True) == {}


def test_missing_registry_is_still_an_empty_start(tmp_path, monkeypatch):
    """Absent != corrupt: a first run must not raise."""
    monkeypatch.setattr(threads, "STATE", tmp_path / "threads.json")
    assert threads.load() == {}


# ---------------------------------------------------------------------------
# W4: single-writer lock
# ---------------------------------------------------------------------------
def test_lock_is_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(threads, "LOCK", tmp_path / "threads.lock")
    assert threads.acquire_lock(timeout=1) is True
    # a second acquisition in the SAME process is blocked (pid is alive)
    assert threads.acquire_lock(timeout=0.4) is False
    threads.release_lock()


def test_a_dead_holder_lock_is_broken(tmp_path, monkeypatch):
    """A killed runner must not wedge the loop forever."""
    lock = tmp_path / "threads.lock"
    monkeypatch.setattr(threads, "LOCK", lock)
    # pid 999999 does not exist
    lock.write_text(json.dumps({"pid": 999999, "at": time.time()}), encoding="utf-8")
    assert threads.acquire_lock(timeout=1) is True
    threads.release_lock()


def test_an_ancient_lock_is_broken(tmp_path, monkeypatch):
    lock = tmp_path / "threads.lock"
    monkeypatch.setattr(threads, "LOCK", lock)
    lock.write_text(json.dumps({"pid": os.getpid(),
                                "at": time.time() - threads.LOCK_STALE_SEC - 60}),
                    encoding="utf-8")
    assert threads.acquire_lock(timeout=1) is True
    threads.release_lock()


def test_release_never_steals_another_processes_lock(tmp_path, monkeypatch):
    lock = tmp_path / "threads.lock"
    monkeypatch.setattr(threads, "LOCK", lock)
    lock.write_text(json.dumps({"pid": 999999, "at": time.time()}), encoding="utf-8")
    threads.release_lock()
    assert lock.exists(), "must not release a lock it does not own"


# ---------------------------------------------------------------------------
# W4: update() is one atomic read-modify-write
# ---------------------------------------------------------------------------
def test_update_does_not_lose_a_concurrent_round(tmp_path, monkeypatch):
    monkeypatch.setattr(threads, "STATE", tmp_path / "threads.json")
    monkeypatch.setattr(threads, "LOCK", tmp_path / "threads.lock")

    threads.update(lambda reg: reg.__setitem__("a", {"rounds": 1}))
    threads.update(lambda reg: reg.__setitem__("b", {"rounds": 2}))

    reg = threads.load()
    assert set(reg) == {"a", "b"}, "the second write discarded the first"
    assert reg["a"]["rounds"] == 1 and reg["b"]["rounds"] == 2


def test_save_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(threads, "STATE", tmp_path / "threads.json")
    monkeypatch.setattr(threads, "LOCK", tmp_path / "threads.lock")
    threads.save({"x": 1})
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# W4: round counter reconciliation
# ---------------------------------------------------------------------------
def test_reconcile_makes_global_the_sum_of_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(loopctl, "STATE", tmp_path)
    monkeypatch.setattr(loopctl, "LOOP", tmp_path / "loop.json")
    monkeypatch.setattr(loopctl, "LOCK", tmp_path / "loop.json.lock")
    (tmp_path / "threads.json").write_text(json.dumps({
        "p1": {"rounds": 464}, "p2": {"rounds": 141},
        "p3": {"rounds": 108}, "p4": {"rounds": 180},
    }), encoding="utf-8")

    cfg = dict(loopctl.DEFAULTS)
    cfg["rounds_done"] = 236          # the drifted figure the audit found
    out = loopctl.reconcile_rounds(cfg)
    assert out["rounds_done"] == 893
    assert out["rounds_reconciled_from"] == 236


def test_reconcile_is_a_no_op_when_already_correct(tmp_path, monkeypatch):
    monkeypatch.setattr(loopctl, "STATE", tmp_path)
    monkeypatch.setattr(loopctl, "LOOP", tmp_path / "loop.json")
    monkeypatch.setattr(loopctl, "LOCK", tmp_path / "loop.json.lock")
    (tmp_path / "threads.json").write_text(json.dumps({"p": {"rounds": 7}}),
                                           encoding="utf-8")
    cfg = dict(loopctl.DEFAULTS)
    cfg["rounds_done"] = 7
    out = loopctl.reconcile_rounds(cfg)
    assert out["rounds_done"] == 7
    assert "rounds_reconciled_from" not in out


def test_reconcile_survives_a_corrupt_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(loopctl, "STATE", tmp_path)
    monkeypatch.setattr(loopctl, "LOOP", tmp_path / "loop.json")
    monkeypatch.setattr(loopctl, "LOCK", tmp_path / "loop.json.lock")
    (tmp_path / "threads.json").write_text("garbage", encoding="utf-8")
    cfg = dict(loopctl.DEFAULTS)
    cfg["rounds_done"] = 5
    out = loopctl.reconcile_rounds(cfg)
    assert out["rounds_done"] == 5, "must not zero the counter on unreadable state"


# ---------------------------------------------------------------------------
# W5: sane defaults
# ---------------------------------------------------------------------------
def test_max_rounds_and_max_hours_default_nonzero():
    """An unattended loop must not run forever by default."""
    assert loopctl.DEFAULTS["max_rounds"] > 0
    assert loopctl.DEFAULTS["max_hours"] > 0


def test_proposed_defaults():
    assert loopctl.DEFAULTS["max_rounds"] == 25
    assert loopctl.DEFAULTS["max_hours"] == 6


# ---------------------------------------------------------------------------
# W5: the runner matcher (the `pause --now` bug, reproduced then fixed)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    r"C:\venv\python.exe C:\CODING\project-improver\karpathy_runner.py",
    r"C:\venv\python.exe C:\CODING\project-improver\karpathy_runner.py --run",
    r"C:\venv\python.exe C:/CODING/project-improver/karpathy_runner.py",
    r'"C:\venv\python.exe" C:\CODING\project-improver\karpathy_runner.py',
    r"C:\venv\python.exe C:\CODING\project-improver\karpathy_runner.py -z payload",
])
def test_runner_command_shapes_are_matched(cmd):
    """All of these are the runner; the old matcher missed 3 of 5."""
    assert _match(cmd) is True


@pytest.mark.parametrize("cmd", [
    r"C:\venv\python.exe C:\CODING\project-improver\loopctl.py status",
    r'C:\venv\python.exe C:\CODING\project-improver\improver.py -z "karpathy_runner.py"',
    r"C:\venv\python.exe C:\CODING\project-improver\karpathy_runner.py.bak",
])
def test_non_runner_commands_are_not_matched(cmd):
    assert _match(cmd) is False


def _match(cmd2):
    """Mirror of loopctl._runner_at_top_level's token rule.

    Kept in sync with the implementation: a token counts only when it is a PATH
    whose basename is karpathy_runner.py. A bare filename payload is not an
    invocation.
    """
    import csv
    fields = next(csv.reader([cmd2], skipinitialspace=True))
    if not fields:
        return False
    for tok in fields[0].replace('"', " ").split():
        parts = tok.strip().rstrip(",").replace("/", "\\").split("\\")
        if len(parts) >= 2 and parts[-1].lower() == "karpathy_runner.py":
            return True
    return False


# ---------------------------------------------------------------------------
# W5: status honesty -- a stale heartbeat is STALLED, never in-flight
# ---------------------------------------------------------------------------
def test_stale_heartbeat_verdict_is_stalled(tmp_path, monkeypatch):
    monkeypatch.setattr(loopctl, "ROOT", tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "runner_heartbeat.json").write_text(json.dumps({
        "state": "round-started", "pid": 999999, "project": "p",
        "ts": time.time() - 10 * 3600,
    }), encoding="utf-8")
    (tmp_path / "state" / "threads.json").write_text("{}", encoding="utf-8")

    cfg = dict(loopctl.DEFAULTS)
    cfg["running"] = True
    live = loopctl._liveness(cfg)
    assert live["wedged"] is True
    assert live["verdict"].startswith("STALLED")
    assert live["in_flight"] is False


def test_fresh_heartbeat_with_a_live_pid_is_in_flight(tmp_path, monkeypatch):
    monkeypatch.setattr(loopctl, "ROOT", tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "runner_heartbeat.json").write_text(json.dumps({
        "state": "round-started", "pid": os.getpid(), "project": "p",
        "ts": time.time(),
    }), encoding="utf-8")
    (tmp_path / "state" / "threads.json").write_text("{}", encoding="utf-8")

    cfg = dict(loopctl.DEFAULTS)
    cfg["running"] = True
    live = loopctl._liveness(cfg)
    assert live["in_flight"] is True
    assert live["verdict"] == "RUNNING"


def test_paused_loop_verdict_is_paused(tmp_path, monkeypatch):
    monkeypatch.setattr(loopctl, "ROOT", tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "threads.json").write_text("{}", encoding="utf-8")
    cfg = dict(loopctl.DEFAULTS)
    cfg["running"] = False
    assert loopctl._liveness(cfg)["verdict"] == "PAUSED"


# ---------------------------------------------------------------------------
# W4/W5: gateless project is a config error, not an infinite reject loop
# ---------------------------------------------------------------------------
def test_gateless_project_is_refused():
    import karpathy_runner as kr
    with pytest.raises(kr.GatelessProject):
        kr._require_gate({"name": "hyperframes", "gate": ""})
    with pytest.raises(kr.GatelessProject):
        kr._require_gate({"name": "x"})            # absent gate
    with pytest.raises(kr.GatelessProject):
        kr._require_gate({"name": "y", "gate": "   "})   # whitespace


def test_a_gated_project_passes():
    import karpathy_runner as kr
    kr._require_gate({"name": "p", "gate": "npm test"})   # no raise


def test_config_error_pauses_the_loop(tmp_path, monkeypatch):
    import karpathy_runner as kr
    monkeypatch.setattr(loopctl, "STATE", tmp_path)
    monkeypatch.setattr(loopctl, "LOOP", tmp_path / "loop.json")
    monkeypatch.setattr(loopctl, "LOCK", tmp_path / "loop.json.lock")
    monkeypatch.setattr(kr, "loopctl", loopctl)
    monkeypatch.setattr(kr.dn, "stuck", lambda *a, **k: None)

    cfg = dict(loopctl.DEFAULTS)
    cfg["running"] = True
    loopctl.save(cfg)

    kr._pause_config_error("project 'hyperframes' has no gate command")
    after = loopctl.load()
    assert after["running"] is False
    assert "no gate" in after["paused_reason"]


def test_config_error_is_surfaced_only_once(tmp_path, monkeypatch):
    import karpathy_runner as kr
    monkeypatch.setattr(loopctl, "STATE", tmp_path)
    monkeypatch.setattr(loopctl, "LOOP", tmp_path / "loop.json")
    monkeypatch.setattr(loopctl, "LOCK", tmp_path / "loop.json.lock")
    monkeypatch.setattr(kr, "loopctl", loopctl)

    calls = []
    monkeypatch.setattr(kr.dn, "stuck", lambda *a, **k: calls.append(a))

    cfg = dict(loopctl.DEFAULTS)
    cfg["running"] = True
    loopctl.save(cfg)

    kr._pause_config_error("same reason")
    kr._pause_config_error("same reason")
    assert len(calls) == 1, "a repeating config error must not spam the operator"
