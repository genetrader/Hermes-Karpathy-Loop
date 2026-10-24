import sys
from pathlib import Path
sys.path.insert(0, str(Path(r"C:\CODING\project-improver")))

import pytest
import threads


def test_title_is_stable_and_prefixed():
    # Reference threads.PREFIX rather than a literal: the marker has changed
    # once (bare "KL :: " -> a coloured square, Sep 26) and hardcoding it made
    # these two tests red for a change that was working as designed. The
    # invariant worth pinning is "the title carries the CURRENT prefix", not
    # "the prefix is these exact bytes".
    assert threads.title_for("tombs-and-tyrants") == threads.PREFIX + "tombs-and-tyrants"


def test_title_for_is_idempotent():
    t = threads.title_for("zip-code-ranker")
    assert threads.title_for("zip-code-ranker") == t


def test_title_for_strips_existing_prefix():
    # A title that already carries the CURRENT marker must not get a second one.
    already = threads.PREFIX + "secondbrain"
    assert threads.title_for(already) == already


def test_title_for_strips_legacy_prefix():
    # The pre-Sep-26 marker must be healed forward, not stacked on top of.
    assert threads.title_for("KL :: secondbrain") == threads.PREFIX + "secondbrain"


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(threads, "STATE", tmp_path / "threads.json")
    threads.save({"tombs-and-tyrants": {"session_id": "abc123", "rounds": 3}})
    got = threads.load()
    assert got["tombs-and-tyrants"]["session_id"] == "abc123"
    assert got["tombs-and-tyrants"]["rounds"] == 3


def test_load_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(threads, "STATE", tmp_path / "nope.json")
    assert threads.load() == {}


def test_load_corrupt_file_fails_closed(tmp_path, monkeypatch):
    """W4 (2026-10-23): this test used to assert `load() == {}`.

    That was the DEFECT, not the contract: quarantines live in this registry, so
    returning an empty dict silently un-quarantined every repo and forgot all
    round history while the loop carried on. Corrupt state now raises
    RegistryCorrupt so the caller pauses and surfaces it.
    """
    p = tmp_path / "threads.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(threads, "STATE", p)
    with pytest.raises(threads.RegistryCorrupt):
        threads.load()
    assert len(list(tmp_path.glob("threads.json.corrupt-*"))) == 1, \
        "the corrupt file must still be salvaged as evidence"
