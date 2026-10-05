#!/usr/bin/env python
"""F4 Group A tests (2026-09-30): scope/surface cleanup lane.

  T3-4  scope.parse counts dropped CROSS_CUTTING lines (cross_dropped)
  T3-5  commit(accepted=False) records a rejection ONLY when the proposal
        carried a claim (campaign or closure)
  T2-11 a blocked campaign UNBLOCKS when an outstanding surface went terminal
        this round, and a terminal verdict does not count as an attempt
  T3-13 next_campaign shim is GONE (import must fail)
  T2-13 surface_pick fallback books its choice (state written even on the
        fallback path)
"""
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scope            # noqa: E402
import surface_pick     # noqa: E402


# ---------------------------------------------------------------- T3-4
def test_parse_counts_dropped_cross_cutting_lines():
    text = ("SURFACE-DONE: server :: done -- r1\n"
            "CROSS_CUTTING: server,android :: the fix\n"
            "CROSS_CUTTING: server,web :: also this\n")
    out = scope.parse(text)
    assert out["cross_dropped"] == 1
    # only the FIRST cross line is parsed
    assert out["cross_cutting"]["surfaces"] == ["server", "android"]
    # and a clean message reports zero drops
    assert scope.parse("SURFACE-DONE: server :: done -- r2")["cross_dropped"] == 0
    assert scope.parse("")["cross_dropped"] == 0


def test_runner_logs_cross_dropped():
    src = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
    assert "cross_dropped" in src, "the runner never surfaces dropped lines"
    assert "DROPPED" in src


# ---------------------------------------------------------------- T3-5
def _camp_entry():
    markers = scope.parse("CROSS_CUTTING: server,android :: x")
    entry = {"rounds": 5}
    prop = scope.propose(entry, markers, "sb")
    assert prop["campaign"]
    return entry, prop


def test_rejection_recorded_only_when_proposal_carries_a_claim():
    entry, prop = _camp_entry()
    n = scope.commit(entry, prop, accepted=False)
    assert n is None, "campaign state stays untouched on a rejected round"
    assert len(entry["campaign_rejections"]) == 1, \
        "a claimed proposal on a failed round is audited as a rejection"
    # a NO-OP proposal on a failed round is NOT a rejection record
    entry2 = {"rounds": 6}
    noop = scope.propose(entry2, scope.parse(""), "sb")
    assert noop["campaign"] is None and not noop["closed"]
    before = len(entry2.get("campaign_rejections") or [])
    scope.commit(entry2, noop, accepted=False)
    assert len(entry2.get("campaign_rejections") or []) == before, \
        "a claimless proposal must not be recorded as a rejection"


# ---------------------------------------------------------------- T2-11
def _blocked_entry():
    """A campaign blocked by the attempt cap on every outstanding surface."""
    entry = {"rounds": 5}
    markers = scope.parse("CROSS_CUTTING: server,android :: x\n"
                          "SURFACE-DONE: server :: failed -- no good\n"
                          "SURFACE-DONE: android :: failed -- no good")
    for rnd in range(5, 9):
        entry["rounds"] = rnd
        prop = scope.propose(entry, markers, "sb")
        entry["campaign"] = scope.commit(entry, prop, accepted=True)
    camp = entry["campaign"]
    assert camp["state"] == "blocked", camp
    return entry, camp


def test_blocked_campaign_unblocks_when_outstanding_surface_goes_terminal():
    entry, camp = _blocked_entry()
    assert camp["state"] == "blocked"
    # next round: android goes DONE (a real, reasoned terminal verdict)
    entry["rounds"] = 9
    markers = scope.parse("SURFACE-DONE: android :: done -- actually fixed")
    prop = scope.propose(entry, markers, "sb")
    assert prop.get("unblocked") is False or prop["campaign"], prop
    # the campaign must NOT stay blocked: an outstanding surface went terminal
    assert not prop.get("blocked"), prop
    assert prop["campaign"]["state"] == "open", prop
    # and android's attempts were reset, not incremented (terminal = progress)
    assert prop["campaign"]["attempts"].get("android", 0) == 0


def test_terminal_verdict_does_not_count_as_an_attempt():
    entry = {"rounds": 5}
    markers = scope.parse("CROSS_CUTTING: server,android :: x\n"
                          "SURFACE-DONE: server :: done -- shipped")
    prop = scope.propose(entry, markers, "sb")
    camp = scope.commit(entry, prop, accepted=True)
    assert camp["attempts"].get("server", 0) == 0, \
        "a terminal verdict is progress, not a failed attempt"


# ---------------------------------------------------------------- T3-13
def test_next_campaign_shim_is_gone():
    assert not hasattr(scope, "next_campaign"), \
        "the unsafe single-call shim must be deleted"
    src = (ROOT / "scope.py").read_text(encoding="utf-8")
    assert "def next_campaign" not in src
    # and no live module CALLS it (docstring/comment mentions are history,
    # not calls -- assert on the AST, not the raw text)
    import ast
    for f in ("karpathy_runner.py", "improver.py", "monitor.py", "activity.py"):
        tree = ast.parse((ROOT / f).read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and ((isinstance(n.func, ast.Name) and n.func.id == "next_campaign")
                      or (isinstance(n.func, ast.Attribute) and n.func.attr == "next_campaign"))]
        assert not calls, f"{f} still calls the shim"


# ---------------------------------------------------------------- T2-13
def test_surface_pick_fallback_books_its_choice(tmp_path, monkeypatch):
    import improver as imp_mod
    state_dir = tmp_path / "surfaces"
    monkeypatch.setattr(surface_pick, "STATE_DIR", state_dir)
    # manifest offers two surfaces; make the normal path FAIL after reconcile
    # (record() will explode on a poisoned save), so the fallback runs and
    # must STILL book the choice.
    monkeypatch.setattr(imp_mod, "surfaces_for", lambda proj: ["a", "b"])
    real_save = surface_pick.save_state
    calls = {"n": 0}

    def flaky_save(name, st):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full (simulated)")
        return real_save(name, st)

    monkeypatch.setattr(surface_pick, "save_state", flaky_save)
    got = surface_pick.pick("projx", {"name": "projx", "path": "x"})
    assert got and got["id"] == "a", got
    # the fallback booking wrote state despite the first save failing
    st = surface_pick.state_for("projx")
    assert st.get("last") == "a", st
    assert st.get("visits", {}).get("a") == 1, st


# ---------------------------------------------------------------- T2-14
def test_surface_pick_docstring_honest_about_renames():
    doc = surface_pick.reconcile.__doc__ or ""
    assert "RENAME" in doc and "alias map" in doc
