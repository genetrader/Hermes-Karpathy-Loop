#!/usr/bin/env python
"""F1 fold-in regression tests (written 2026-09-30, master-plan F1 pass).

Covers the lane-2 defects folded into F1, at the level the fixes landed:

  T2-9   scope.parse keeps the PARSED reason for unrecognized verdicts
         (the old ternary always overwrote it with raw rest-text).
  T2-10  the OPENING round's baseline does not consume MAX_ADDITIONS
         (capping the opening admission silently dropped declared surfaces).
  T2-12  campaign ids are unique across sequential campaigns on one repo
         (the id now carries the opening round number).
  T3-14  terminal verdicts are STICKY in parse() (a late `not-applicable`
         must not downgrade an earlier `done`), and the marker template no
         longer invites copying the verdict menu verbatim.
  T3-12  the campaign checklist renders terminal state and the clean verdict
         word (no raw dict reprs, no truthiness "done").

Each test pins the defect first by construction: the assertion names the bug
and would fail on the pre-fix code.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scope          # noqa: E402
import round_prompt   # noqa: E402


# ---------------------------------------------------------------- T2-9
def test_parse_preserves_reason_for_unrecognized_verdict():
    out = scope.parse("SURFACE-DONE: server :: mostly-fine -- actual reason here")
    s = out["surfaces"]["server"]
    assert s["verdict"] == "mostly-fine"
    # THE BUG: reason used to become 'mostly-fine -- actual reason here'
    assert s["reason"] == "actual reason here"


def test_parse_unrecognized_without_reason_keeps_reason_empty():
    out = scope.parse("SURFACE-DONE: server :: mostly-fine")
    s = out["surfaces"]["server"]
    assert s["verdict"] == "mostly-fine"
    assert s["reason"] == ""


# ---------------------------------------------------------------- T2-10
def test_opening_baseline_is_not_capped():
    markers = scope.parse("CROSS_CUTTING: a,b,c :: do the thing")
    prop = scope.propose({"rounds": 5}, markers, "repo-x", declared=["a", "b", "c"])
    camp = prop["campaign"]
    assert camp is not None
    assert sorted(camp["surfaces"]) == ["a", "b", "c"], camp["surfaces"]
    assert prop["capped"] is False
    assert camp["additions"] == [], "baseline is not additions"


def test_later_widening_still_capped():
    # NOTE on mechanics: parse() only treats a >=2-surface CROSS_CUTTING line as
    # cross-cutting, and the cap bounds the TOTAL additions a campaign may take
    # (MAX_ADDITIONS=2), not "one surface per propose call". So the drill is:
    # opening baseline [a,b] (additions=[]), widen once with [c,d] (budget 2 ->
    # both admitted, additions=[c,d]), then ANY further widening must be capped.
    entry = {"rounds": 5}
    prop1 = scope.propose(entry, scope.parse("CROSS_CUTTING: a,b :: x"),
                          "repo-y", declared=["a", "b", "c", "d"])
    assert prop1["capped"] is False
    entry["campaign"] = prop1["campaign"]
    entry["rounds"] = 6
    scope.commit(entry, prop1, accepted=True)
    prop2 = scope.propose(entry, scope.parse("CROSS_CUTTING: c,d :: wider"),
                          "repo-y", declared=["a", "b", "c", "d"])
    assert prop2["capped"] is False
    assert sorted(prop2["campaign"]["surfaces"]) == ["a", "b", "c", "d"]
    entry["campaign"] = prop2["campaign"]
    entry["rounds"] = 7
    scope.commit(entry, prop2, accepted=True)
    # Third widening names a NEW declared surface e: the budget (2 additions)
    # is spent, so e must be blocked and the campaign must stay monotone.
    prop3 = scope.propose(entry, scope.parse("CROSS_CUTTING: c,e :: wider still"),
                          "repo-y", declared=["a", "b", "c", "d", "e"])
    assert prop3["capped"] is True, "widening budget must be exhausted"
    assert "e" not in prop3["campaign"]["surfaces"], \
        "capped widening must not add surfaces"
    assert sorted(prop3["campaign"]["surfaces"]) == ["a", "b", "c", "d"], \
        "capped widening must not drop surfaces"


# ---------------------------------------------------------------- T2-12
def test_sequential_campaign_ids_are_unique():
    entry = {"rounds": 6}
    p1 = scope.propose(entry, scope.parse("CROSS_CUTTING: a,b :: first"),
                       "repo-z", declared=["a", "b"])
    id1 = p1["campaign"]["id"]
    # simulate the first campaign having closed and a second one opening later
    entry2 = {"rounds": 9}
    p2 = scope.propose(entry2, scope.parse("CROSS_CUTTING: a,b :: second"),
                       "repo-z", declared=["a", "b"])
    id2 = p2["campaign"]["id"]
    assert id1 != id2, (id1, id2)
    assert id1.endswith("-r6") and id2.endswith("-r9")


# ---------------------------------------------------------------- T3-14 (parse)
def test_late_terminal_marker_cannot_downgrade_done():
    text = ("SURFACE-DONE: server :: done -- shipped it\n"
            "SURFACE-DONE: server :: not-applicable -- actually no")
    s = scope.parse(text)["surfaces"]["server"]
    assert s["verdict"] == "done", s


def test_late_non_terminal_marker_cannot_unfinish_done():
    text = ("SURFACE-DONE: android :: done -- ok\n"
            "SURFACE-DONE: android :: deferred -- changed my mind")
    s = scope.parse(text)["surfaces"]["android"]
    assert s["verdict"] == "done", s


def test_non_terminal_still_upgrades_to_terminal():
    text = ("SURFACE-DONE: web :: deferred -- not yet\n"
            "SURFACE-DONE: web :: done -- finished later in the message")
    s = scope.parse(text)["surfaces"]["web"]
    assert s["verdict"] == "done", s


def test_template_does_not_invite_verdict_menu_copy():
    p = round_prompt.build("p", angle={"id": "a", "text": "t"}, gate="g",
                           round_no=1, prior={},
                           surface={"id": "server", "index": 0, "total": 2})
    assert "done | deferred | not-applicable" not in p
    assert "<verdict>" in p
    assert "exactly ONE word" in p


# ---------------------------------------------------------------- T3-12 (F1-7)
def _checklist(campaign):
    p = round_prompt.build("p", angle={"id": "a", "text": "t"}, gate="g",
                           round_no=7, prior={"rounds": 6}, campaign=campaign)
    return [ln for ln in p.splitlines()
            if ln.strip().startswith("[") or "surface(s)" in ln]


def test_deferred_renders_unchecked_not_done():
    camp = {"id": "c-x2-r6", "surfaces": ["server", "android"],
            "verdicts": {"server": {"verdict": "done", "reason": "shipped"},
                         "android": {"verdict": "deferred", "reason": "later"}}}
    lines = _checklist(camp)
    joined = "\n".join(lines)
    assert "[x] server -- done" in joined
    assert "[ ] android -- deferred" in joined, joined
    assert "1 of 2 surface(s) done." in joined, joined


def test_not_applicable_is_terminal_and_clean():
    camp = {"id": "c-x2-r6", "surfaces": ["server", "android"],
            "verdicts": {"server": "done",
                         "android": {"verdict": "not-applicable",
                                     "reason": "no UI"}}}
    joined = "\n".join(_checklist(camp))
    assert "[x] android -- not-applicable" in joined, joined
    assert "2 of 2 surface(s) done." in joined, joined


def test_no_raw_dict_repr_on_checklist():
    camp = {"id": "c-x2-r6", "surfaces": ["server"],
            "verdicts": {"server": {"verdict": "done", "reason": "ok"}}}
    joined = "\n".join(_checklist(camp))
    assert "{'verdict'" not in joined and "'verdict'" not in joined
