import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import threads


def test_title_is_stable_and_prefixed():
    # Reference threads.PREFIX rather than a literal: the marker has changed
    # once (bare "KL :: " -> a coloured square, Sep 26) and hardcoding it made
    # these two tests red for a change that was working as designed. The
    # invariant worth pinning is "the title carries the CURRENT prefix", not
    # "the prefix is these exact bytes".
    assert threads.title_for("project-a") == threads.PREFIX + "project-a"


def test_title_for_is_idempotent():
    t = threads.title_for("project-c")
    assert threads.title_for("project-c") == t


def test_title_for_strips_existing_prefix():
    # A title that already carries the CURRENT marker must not get a second one.
    already = threads.PREFIX + "project-b"
    assert threads.title_for(already) == already


def test_title_for_strips_legacy_prefix():
    # The pre-Sep-26 marker must be healed forward, not stacked on top of.
    assert threads.title_for("KL :: project-b") == threads.PREFIX + "project-b"


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(threads, "STATE", tmp_path / "threads.json")
    threads.save({"project-a": {"session_id": "abc123", "rounds": 3}})
    got = threads.load()
    assert got["project-a"]["session_id"] == "abc123"
    assert got["project-a"]["rounds"] == 3


def test_load_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(threads, "STATE", tmp_path / "nope.json")
    assert threads.load() == {}


def test_load_corrupt_file_returns_empty(tmp_path, monkeypatch):
    p = tmp_path / "threads.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(threads, "STATE", p)
    assert threads.load() == {}
