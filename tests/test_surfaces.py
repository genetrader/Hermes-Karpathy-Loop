"""Unit tests for S1: surfaces_for() + surface_pick round-robin.

Covers the S1 gate from plans/2026-09-28_surfaces-IMPLEMENTATION.md:
round-robin sequence, wrap-around, single-surface no-op, manifest
reconciliation (added AND removed), corrupt-state degradation, and the
two manifest shapes parsing to identical slugs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"C:\CODING\project-improver")))

import improver as I
import surface_pick as sp


# --------------------------------------------------------------------------
# helpers -- every test runs against a throwaway state dir
# --------------------------------------------------------------------------
PROJ4 = {"name": "p", "see": {"surfaces": "alpha, beta, gamma, delta"}}
ORDER4 = ["alpha", "beta", "gamma", "delta"]


def _isolate(tmp_path, monkeypatch):
    """Point surface_pick's state dir at a throwaway dir."""
    monkeypatch.setattr(sp, "STATE_DIR", tmp_path / "surfaces")


def _drive(name, proj, n):
    """Call pick() n times, returning the id sequence."""
    return [sp.pick(name, proj)["id"] for _ in range(n)]


# --------------------------------------------------------------------------
# (a) fairness: 4 surfaces over 8 picks is two clean cycles
# --------------------------------------------------------------------------
def test_round_robin_is_fair_over_two_cycles(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seq = _drive("p", PROJ4, 8)
    assert seq == ORDER4 + ORDER4, seq
    st = sp.state_for("p")
    assert st["visits"] == {"alpha": 2, "beta": 2, "gamma": 2, "delta": 2}


# --------------------------------------------------------------------------
# (b) wrap-around: the element after the last is the first
# --------------------------------------------------------------------------
def test_wrap_around(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    # land on the LAST surface, then confirm the next pick wraps to the head
    seq = _drive("p", PROJ4, 4)
    assert seq[-1] == "delta"
    assert sp.pick("p", PROJ4)["id"] == "alpha"


# --------------------------------------------------------------------------
# (c) single-surface (and zero-surface) projects are a NO-OP
# --------------------------------------------------------------------------
def test_single_surface_returns_none(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    one = {"name": "solo", "see": {"surfaces": "server"}}
    assert sp.pick("solo", one) is None
    none_at_all = {"name": "bare"}
    assert sp.pick("bare", none_at_all) is None
    # and a no-op leaves NO state file behind -- zero footprint
    assert not (tmp_path / "surfaces" / "solo.json").exists()


# --------------------------------------------------------------------------
# (d) a surface ADDED mid-rotation keeps visits and waits its turn
# --------------------------------------------------------------------------
def test_added_surface_keeps_visits_and_appends(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    # two rounds done: alpha, beta -- rotation is mid-cycle, next is gamma
    _drive("p", PROJ4, 2)
    # manifest grows a fifth surface
    proj5 = {"name": "p", "see": {"surfaces": "alpha, beta, gamma, delta, epsilon"}}
    # The cycle continues WHERE IT WAS: gamma, delta, then the newcomer
    # epsilon at the END, then wraps to alpha. No silent restart, prior
    # visits intact.
    seq = [sp.pick("p", proj5)["id"] for _ in range(4)]
    assert seq == ["gamma", "delta", "epsilon", "alpha"], seq
    st = sp.state_for("p")
    assert st["visits"]["alpha"] == 2      # history preserved through reconcile
    assert st["visits"]["beta"] == 1


# --------------------------------------------------------------------------
# (e) a surface REMOVED mid-rotation never returns
# --------------------------------------------------------------------------
def test_removed_surface_never_returns(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _drive("p", PROJ4, 8)                  # two full cycles
    proj3 = {"name": "p", "see": {"surfaces": "alpha, beta, gamma"}}
    seq = _drive("p", proj3, 9)            # 9 picks = 3 cycles of the survivors
    assert "delta" not in seq, seq
    assert seq == ["alpha", "beta", "gamma"] * 3
    # `delta` was removed from the manifest: its slug leaves `order` and can
    # never be picked again. Its OLD visit count is dropped with it -- the
    # state describes the CURRENT rotation, not a graveyard.
    st = sp.state_for("p")
    assert "delta" not in st["order"]
    assert "delta" not in st["visits"]
    assert st["visits"] == {"alpha": 5, "beta": 5, "gamma": 5}


# --------------------------------------------------------------------------
# (f) corrupt / missing state degrades to warning + first surface, no raise
# --------------------------------------------------------------------------
def test_corrupt_state_degrades_not_raises(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    d = tmp_path / "surfaces"
    d.mkdir()
    (d / "p.json").write_text("{not json at all", encoding="utf-8")
    # First pick after corruption: rotation rebuilds from the manifest head.
    # Must not raise; must return a valid surface.
    res = sp.pick("p", PROJ4)
    assert res is not None and res["id"] in ORDER4
    # And the very next pick continues the rotation from that choice.
    assert sp.pick("p", PROJ4)["id"] == "beta"


def test_missing_state_starts_at_head(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert sp.pick("p", PROJ4)["id"] == "alpha"


# --------------------------------------------------------------------------
# (g) BOTH manifest shapes yield identical slugs
# --------------------------------------------------------------------------
def test_both_manifest_shapes_parse_identically():
    flat = {"see": {"surfaces": "server, android, chrome-extension"}}
    objs = {"see": {"surfaces": [
        {"name": "server", "kind": "server", "where": "server/"},
        {"name": "android", "kind": "mobile", "where": "android/"},
        {"name": "chrome-extension", "kind": "extension", "where": "chrome-extension/"},
    ]}}
    assert I.surfaces_for(flat) == I.surfaces_for(objs) == \
        ["server", "android", "chrome-extension"]


def test_surfaces_for_normalizes_and_dedupes():
    p = {"see": {"surfaces": "Server, ANDROID,  server , chrome extension"}}
    assert I.surfaces_for(p) == ["server", "android", "chrome-extension"]


def test_surfaces_for_absent_or_malformed_is_empty():
    assert I.surfaces_for({}) == []
    assert I.surfaces_for({"name": "x"}) == []
    assert I.surfaces_for({"see": {}}) == []
    assert I.surfaces_for({"see": None}) == []
    assert I.surfaces_for({"see": 5}) == []          # scalar `see`
    assert I.surfaces_for({"see": {"surfaces": 42}}) == []
    # object entries without a name degrade to "absent", not a crash
    assert I.surfaces_for({"see": {"surfaces": [{"kind": "mobile"}]}}) == []


# --------------------------------------------------------------------------
# atomicity: the save path is tmp + os.replace (review finding B5's lesson)
# --------------------------------------------------------------------------
def test_save_is_atomic(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    sp.record("p", "alpha", order=ORDER4)
    p = tmp_path / "surfaces" / "p.json"
    assert p.exists() and not p.with_suffix(".json.tmp").exists()
    import json
    st = json.loads(p.read_text(encoding="utf-8"))
    assert st["last"] == "alpha" and st["visits"] == {"alpha": 1}


# --------------------------------------------------------------------------
# record() / state_for() contract
# --------------------------------------------------------------------------
def test_record_accumulates_visits(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    sp.record("p", "alpha", order=ORDER4)
    sp.record("p", "beta", order=ORDER4)
    sp.record("p", "alpha", order=ORDER4)
    st = sp.state_for("p")
    assert st["visits"] == {"alpha": 2, "beta": 1}
    assert st["last"] == "alpha"
    assert st["order"] == ORDER4


def test_state_for_missing_file_is_empty(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert sp.state_for("never-seen") == {}
