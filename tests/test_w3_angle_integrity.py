"""W3 regression tests -- angle picker integrity.

Pins three drifts the audit found:
  1. the global ledger froze after day one (no writer except a prompt instruction)
  2. gameplay angles were dispatched to non-game projects
  3. one angle could be depth-cycled past 2 consecutive variants
"""
import json

import pytest

import angle_pick as ap


# ---------------------------------------------------------------------------
# scoping
# ---------------------------------------------------------------------------
def test_gameplay_is_scoped_to_game_projects():
    assert ap.family_allowed("gameplay", "tombs-and-tyrants") is True
    assert ap.family_allowed("gameplay", "secondbrain") is False
    assert ap.family_allowed("gameplay", "zip-code-ranker") is False
    assert ap.family_allowed("gameplay", "ai-consultancy-website") is False


def test_unscoped_families_reach_every_project():
    for fam in ("correctness", "robustness", "performance", "interface",
                "simplification", "comprehension", "meta"):
        for proj in ("secondbrain", "zip-code-ranker", "tombs-and-tyrants"):
            assert ap.family_allowed(fam, proj) is True, (fam, proj)


def test_an_unknown_family_is_not_silently_blocked():
    assert ap.family_allowed("brand-new-family", "secondbrain") is True


def test_the_picker_never_returns_a_gameplay_angle_for_a_server():
    """The live drift: inert-npc reached secondbrain 4x. Must be impossible."""
    facts = {"file_count": 40, "has_tests": True}
    for seed in range(40):
        got = ap.pick("secondbrain", facts=facts, seed=seed)
        assert got["family"] != "gameplay", (seed, got["angle"])


def test_the_picker_still_returns_gameplay_for_a_game(tmp_path, monkeypatch):
    """Scoping must not over-correct: tombs-and-tyrants must still see gameplay.

    Uses a CLEAN ledger: the live one has already retired several gameplay
    angles (untelegraphed-punishment, dead-end-action), which would make this
    test pass or fail for the wrong reason.
    """
    monkeypatch.setattr(ap, "HISTORY", tmp_path / "angle_history.json")
    monkeypatch.setattr(ap, "load_angles", lambda path=None, merged=True: {
        "angles": [
            {"id": "inert-npc", "family": "gameplay", "lens": "g"},
            {"id": "reward-pacing", "family": "gameplay", "lens": "g"},
            {"id": "lying-test", "family": "correctness", "lens": "c"},
        ],
        "picker": {"cooldown_rounds": 0, "retire_after_failures": 0,
                   "family_rotation": True},
    })
    # Seed the ledger so correctness has already had its turn: family rotation
    # then has an unambiguous "least used" family and the test is deterministic.
    ap.save_history({"rounds": [
        {"angle": "lying-test", "project": "tombs-and-tyrants",
         "family": "correctness"},
    ], "angles": {}, "notes": []})
    facts = {"file_count": 40, "has_tests": True}
    fams = {ap.pick("tombs-and-tyrants", facts=facts, seed=s)["family"]
            for s in range(60)}
    assert "gameplay" in fams, fams


# ---------------------------------------------------------------------------
# consecutive-variant cap
# ---------------------------------------------------------------------------
def _hist(rows):
    return {"rounds": rows, "angles": {}, "notes": []}


def test_consecutive_variants_counts_only_this_project():
    h = _hist([
        {"angle": "a", "project": "p1", "family": "correctness"},
        {"angle": "a", "project": "p2", "family": "correctness"},
        {"angle": "a", "project": "p1", "family": "correctness"},
    ])
    assert ap.consecutive_variants(h, "p1", "a") == 1, \
        "p2's row must not extend p1's streak"


def test_consecutive_variants_stops_at_a_different_angle():
    h = _hist([
        {"angle": "a", "project": "p", "family": "x"},
        {"angle": "a", "project": "p", "family": "x"},
        {"angle": "b", "project": "p", "family": "x"},
    ])
    assert ap.consecutive_variants(h, "p", "a") == 0


def test_consecutive_variants_counts_a_run():
    h = _hist([{"angle": "a", "project": "p", "family": "x"}] * 2)
    assert ap.consecutive_variants(h, "p", "a") == 2


def test_the_cap_is_two():
    assert ap.MAX_CONSECUTIVE_VARIANTS == 2


def test_picker_skips_an_angle_at_the_cap(monkeypatch, tmp_path):
    """After 2 consecutive rounds of one angle, the picker must move on."""
    # Pin the ledger so only one angle is a candidate besides the capped one.
    monkeypatch.setattr(ap, "HISTORY", tmp_path / "angle_history.json")
    h = _hist([
        {"angle": "lying-test", "project": "p", "family": "correctness"},
        {"angle": "lying-test", "project": "p", "family": "correctness"},
    ])
    h["angles"] = {"lying-test": {"used": 2, "accepted": 0, "failed": 2,
                                  "streak_fail": 2, "retired": False}}
    ap.save_history(h)

    monkeypatch.setattr(ap, "load_angles", lambda path=None, merged=True: {
        "angles": [
            {"id": "lying-test", "family": "correctness", "lens": "x"},
            {"id": "garbage-input", "family": "robustness", "lens": "y"},
        ],
        "picker": {"cooldown_rounds": 0, "retire_after_failures": 0,
                   "family_rotation": False},
    })
    got = ap.pick("p", facts={}, seed=1)
    assert got["angle"] != "lying-test", got


# ---------------------------------------------------------------------------
# record_round: the ledger writer the runner calls
# ---------------------------------------------------------------------------
def test_record_round_appends_and_updates_stats(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "HISTORY", tmp_path / "angle_history.json")
    st = ap.record_round("p", "lying-test", "correctness", "accepted", round_no=5)
    assert st["used"] == 1 and st["accepted"] == 1 and st["streak_fail"] == 0
    h = ap.load_history()
    assert len(h["rounds"]) == 1
    assert h["rounds"][0]["project"] == "p"
    assert h["rounds"][0]["round"] == 5


def test_record_round_retires_after_the_configured_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "HISTORY", tmp_path / "angle_history.json")
    monkeypatch.setattr(ap, "load_angles", lambda path=None, merged=True: {
        "angles": [], "picker": {"retire_after_failures": 4}})
    for _ in range(3):
        st = ap.record_round("p", "a", "correctness", "failed")
        assert st["retired"] is False
    st = ap.record_round("p", "a", "correctness", "failed")
    assert st["retired"] is True
    assert st["streak_fail"] == 4


def test_a_success_clears_the_failure_streak(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "HISTORY", tmp_path / "angle_history.json")
    monkeypatch.setattr(ap, "load_angles", lambda path=None, merged=True: {
        "angles": [], "picker": {"retire_after_failures": 4}})
    for _ in range(3):
        ap.record_round("p", "a", "correctness", "failed")
    st = ap.record_round("p", "a", "correctness", "accepted")
    assert st["streak_fail"] == 0
    assert st["retired"] is False


def test_a_success_re_qualifies_a_retired_angle(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "HISTORY", tmp_path / "angle_history.json")
    monkeypatch.setattr(ap, "load_angles", lambda path=None, merged=True: {
        "angles": [], "picker": {"retire_after_failures": 1}})
    ap.record_round("p", "a", "correctness", "failed")
    st = ap.record_round("p", "a", "correctness", "accepted")
    assert st["retired"] is False


def test_record_round_is_atomic_and_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "HISTORY", tmp_path / "angle_history.json")
    monkeypatch.setattr(ap, "load_angles", lambda path=None, merged=True: {
        "angles": [], "picker": {"retire_after_failures": 0}})
    for i in range(510):
        ap.record_round("p", "a", "correctness", "accepted", round_no=i)
    h = ap.load_history()
    assert len(h["rounds"]) == 500, "ledger must stay bounded"
    assert not list(tmp_path.glob("angle_history.json.tmp")), "temp left behind"


def test_save_history_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(ap, "HISTORY", tmp_path / "angle_history.json")
    ap.save_history({"rounds": [{"angle": "x"}], "angles": {}, "notes": []})
    assert not list(tmp_path.glob("*.tmp"))
    assert json.loads((tmp_path / "angle_history.json").read_text())["rounds"]


# ---------------------------------------------------------------------------
# cooldown/rotation now see real history
# ---------------------------------------------------------------------------
def test_recent_families_ignores_other_projects():
    h = _hist([
        {"angle": "a", "project": "p1", "family": "correctness"},
        {"angle": "b", "project": "p2", "family": "gameplay"},
        {"angle": "c", "project": "p1", "family": "robustness"},
    ])
    assert ap.recent_families(h, "p1", 5) == ["robustness", "correctness"]


def test_recent_families_respects_the_limit():
    h = _hist([{"angle": str(i), "project": "p", "family": "f%d" % i}
               for i in range(10)])
    assert len(ap.recent_families(h, "p", 3)) == 3


def test_the_prompt_layer_cannot_smuggle_an_out_of_scope_angle(tmp_path, monkeypatch):
    """The deeper drift: a prompt existed for every angle, and the prompt layer
    picked by prompt-availability -- overriding pool, scope, cooldown and the cap.
    This is why inert-npc reached secondbrain despite APPLIES WHEN saying it
    should not."""
    monkeypatch.setattr(ap, "HISTORY", tmp_path / "angle_history.json")
    monkeypatch.setattr(ap, "load_angles", lambda path=None, merged=True: {
        "angles": [
            {"id": "inert-npc", "family": "gameplay", "lens": "g"},
            {"id": "lying-test", "family": "correctness", "lens": "c"},
        ],
        "picker": {"cooldown_rounds": 0, "retire_after_failures": 0,
                   "family_rotation": False},
    })
    for seed in range(25):
        got = ap.pick("secondbrain", facts={}, seed=seed)
        assert got["family"] != "gameplay", (seed, got["angle"])
