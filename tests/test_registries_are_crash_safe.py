"""F0-2/F0-3 tests: the two registries are now crash-safe.

Proves three properties for threads.json and loop.json:
  1. save() is atomic -- the file on disk after save() always parses, and no
     .tmp residue is left.
  2. a torn/corrupt file never silently becomes "empty defaults": the damaged
     file is moved aside (salvaged) before any reset.
  3. a corrupt-file load followed by a save cannot destroy the evidence.

Run: python -m pytest tests/test_registries_are_crash_safe.py -q
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import threads  # noqa: E402
import loopctl  # noqa: E402


@pytest.fixture()
def tmp_state(tmp_path, monkeypatch):
    """Point both registries' real STATE/LOOP/ROTATION paths into tmp_path."""
    monkeypatch.setattr(threads, "STATE", tmp_path / "threads.json")
    monkeypatch.setattr(loopctl, "STATE", tmp_path)
    monkeypatch.setattr(loopctl, "LOOP", tmp_path / "loop.json")
    monkeypatch.setattr(loopctl, "ROTATION", tmp_path / "rotation.json")
    return tmp_path


# ------------------------------------------------------------------- threads

def test_threads_save_is_atomic_and_parses(tmp_state):
    threads.save({"p1": {"rounds": 35}})
    d = json.loads(threads.STATE.read_text(encoding="utf-8"))
    assert d["p1"]["rounds"] == 35
    assert not list(tmp_state.glob("*.tmp")), "temp file left behind"


def test_threads_corrupt_file_is_salvaged_not_silently_reset(tmp_state):
    threads.STATE.write_text('{"p1": {"rounds": 35', encoding="utf-8")  # torn JSON
    d = threads.load()
    assert d == {}                       # starts empty, as it must
    salvage = list(tmp_state.glob("threads.json.corrupt-*"))
    assert len(salvage) == 1, "the corrupt file was deleted, not salvaged"
    assert "rounds" in salvage[0].read_text(encoding="utf-8"), \
        "the salvaged copy lost the data"


def test_threads_corrupt_then_save_keeps_evidence(tmp_state):
    threads.STATE.write_text("{torn", encoding="utf-8")
    threads.load()                        # salvages the corrupt file
    threads.save({"p1": {"rounds": 1}})   # writes a fresh, valid registry
    assert json.loads(threads.STATE.read_text(encoding="utf-8"))["p1"]["rounds"] == 1
    assert len(list(tmp_state.glob("threads.json.corrupt-*"))) == 1


# ------------------------------------------------------------------ loopctl

def test_loopctl_save_is_atomic_and_parses(tmp_state):
    cfg = dict(loopctl.DEFAULTS)
    cfg["projects"] = ["a", "b"]
    loopctl.save(cfg)
    d = json.loads(loopctl.LOOP.read_text(encoding="utf-8"))
    assert d["projects"] == ["a", "b"]
    assert not list(tmp_state.glob("*.tmp"))


def test_loopctl_corrupt_file_is_salvaged_not_silently_reset(tmp_state):
    loopctl.LOOP.write_text('{"projects": ["kept-project"', encoding="utf-8")
    cfg = loopctl.load()
    assert cfg["projects"] == loopctl.DEFAULTS["projects"]  # defaults, as designed
    salvage = list(tmp_state.glob("loop.json.corrupt-*"))
    assert len(salvage) == 1, "the corrupt loop.json was deleted, not salvaged"
    assert "kept-project" in salvage[0].read_text(encoding="utf-8"), \
        "the salvaged copy lost the projects list"


def test_loopctl_sync_rotation_atomic(tmp_state):
    cfg = dict(loopctl.DEFAULTS)
    cfg["projects"] = ["x"]
    loopctl._sync_rotation(cfg)
    d = json.loads(loopctl.ROTATION.read_text(encoding="utf-8"))
    assert d["projects"] == ["x"]
    assert not list(tmp_state.glob("*.tmp"))