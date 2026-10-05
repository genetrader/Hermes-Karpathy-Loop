#!/usr/bin/env python
"""S4 tests: the scope markers a round emits, and the campaign state machine.

Why this exists, in the user's words (2026-09-28): "different surfaces probably
need a sub-categorization that can act as part of the repo, and can still count
as a single turn, obviously, but different parts can get cycled through and
looked at."  And, on cross-cutting changes: "they need to be done at that time,
so that things stay parallel with each other."

The design that came out of that is in scope.py's docstring: a campaign is N
consecutive rounds sharing one checklist, NOT one giant round. The reasons are
measured, not aesthetic (ROUND_TIMEOUT=4200 s vs a 250-300 min multi-surface
round; `last_nudge` advances on timeout, so a killed round still rotates).

These tests cover the two things that can silently break:
  1. scope.parse() -- the markers are PARSED, so an approximate answer is the
     same as no answer. Malformed input must degrade, never raise.
  2. scope.commit(entry, scope.propose(), accepted=True) -- the checklist must open, advance, and CLOSE, and
     must not reset itself when a round re-declares the same campaign.

Run: python -m pytest tests/test_scope.py -q
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scope


def _v(verdict):
    """Read the verdict word out of either stored shape.

    `verdicts[slug]` is a bare string in legacy entries and a dict
    {"verdict", "reason"} in current ones. Tests should compare the WORD, not
    the container, so a deliberate enrichment of the shape is not read as a
    behaviour change.
    """
    if isinstance(verdict, dict):
        return verdict.get("verdict")
    return verdict  # noqa: E402


# --------------------------------------------------------------------------
# parse(): the happy path -- exactly the format round_prompt asks for
# --------------------------------------------------------------------------

def test_parses_done_verdict_with_reason():
    out = scope.parse("SURFACE-DONE: server :: done -- added the field end to end")
    assert out["surfaces"]["server"]["verdict"] == "done"
    assert out["surfaces"]["server"]["reason"] == "added the field end to end"


def test_parses_all_three_verdicts():
    text = ("SURFACE-DONE: server :: done -- shipped\n"
            "SURFACE-DONE: android :: deferred -- needs a build\n"
            "SURFACE-DONE: chrome-extension :: not-applicable -- no UI change\n")
    s = scope.parse(text)["surfaces"]
    assert s["server"]["verdict"] == "done"
    assert s["android"]["verdict"] == "deferred"
    assert s["chrome-extension"]["verdict"] == "not-applicable"


def test_underscore_verdict_normalises():
    """A child writing not_applicable must not create a second verdict word."""
    s = scope.parse("SURFACE-DONE: android :: not_applicable -- n/a")["surfaces"]
    assert s["android"]["verdict"] == "not-applicable"


def test_parses_cross_cutting():
    """

    The marker exists so surfaces can be kept PARALLEL. It is only meaningful
    across more than one surface.
    """
    out = scope.parse("CROSS_CUTTING: server,android :: clients need the new field")
    assert out["cross_cutting"]["surfaces"] == ["server", "android"]
    assert "new field" in out["cross_cutting"]["what"]


def test_single_surface_cross_cutting_is_not_cross_cutting():
    """One surface is just this round -- opening a campaign for it is noise."""
    assert scope.parse("CROSS_CUTTING: server :: local change")["cross_cutting"] is None


def test_cross_cutting_dedupes_and_lowercases():
    out = scope.parse("CROSS_CUTTING: Server, android , server :: x")
    assert out["cross_cutting"]["surfaces"] == ["server", "android"]


# --------------------------------------------------------------------------
# parse(): hostile / degraded input. A round must never FAIL because of a
# marker. Every one of these must return a usable dict.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "", None, "no markers at all",
    "SURFACE-DONE:",
    "SURFACE-DONE: server",
    "SURFACE-DONE: server ::",
    "SURFACE-DONE :: done",
    "CROSS_CUTTING:",
    "CROSS_CUTTING: server ::",
    "surface-done: server :: done",          # wrong case -> not matched
    "  SURFACE-DONE: server :: done",        # leading space -> matched (MULTILINE)
])
def test_malformed_never_raises(text):
    out = scope.parse(text)
    assert isinstance(out, dict)
    assert "surfaces" in out and "cross_cutting" in out


def test_unknown_verdict_is_kept_visible_not_swallowed():
    """An unrecognised verdict must surface, not silently count as done."""
    s = scope.parse("SURFACE-DONE: android :: mostly-fine")["surfaces"]["android"]
    assert s["verdict"] == "mostly-fine" or s["verdict"] not in ("done",)
    assert "mostly-fine" in (s["verdict"] + s["reason"])


def test_marker_found_anywhere_in_a_long_message():
    """

    The runner parses the FULL child output (6148 B measured) before slicing to
    the last 2000 B, so a marker in the middle of a long report must still be
    found. This test pins that the regex is not anchored to the end.
    """
    body = "x" * 5000 + "\nSURFACE-DONE: server :: done -- mid-report\n" + "y" * 5000
    out = scope.parse(body)
    assert out["surfaces"]["server"]["verdict"] == "done"


# --------------------------------------------------------------------------
# propose+commit: open -> advance -> close
# --------------------------------------------------------------------------

def test_campaign_opens_on_cross_cutting():
    entry = {"rounds": 5}
    scope_ = scope.parse("CROSS_CUTTING: server,android :: add the field")
    camp = scope.commit(entry, scope.propose(entry, scope_, "project-b"), accepted=True)
    assert camp is not None
    assert camp["surfaces"] == ["server", "android"]
    assert camp["verdicts"] == {}


def test_campaign_advances_and_closes():
    entry = {"rounds": 5}

    # round 6: declare, work server
    s = scope.parse("CROSS_CUTTING: server,android :: add the field\n"
                    "SURFACE-DONE: server :: done -- server side shipped")
    scope.commit(entry, scope.propose(entry, s, "project-b"), accepted=True)
    entry["rounds"] = 6
    # Verdicts are stored RICH now ({"verdict", "reason"}) so a reviewer can see
    # WHY, not just that. Compare the verdict field, not the whole container.
    assert _v(entry["campaign"]["verdicts"]["server"]) == "done"

    # round 7: work android -> checklist full -> CLOSED (returns None)
    s = scope.parse("SURFACE-DONE: android :: done -- client updated")
    entry["rounds"] = 7
    after = scope.commit(entry, scope.propose(entry, s, "project-b"), accepted=True)
    assert after is None, "a full checklist must CLOSE the campaign"
    closed = entry["campaigns_closed"]
    assert len(closed) == 1
    assert closed[0]["surfaces"] == ["server", "android"]
    assert closed[0]["closed_round"] == 7


def test_redeclaring_the_same_campaign_does_not_reset_verdicts():
    """

    The child may re-emit CROSS_CUTTING every round (it is asked to). If that
    reset the checklist, a campaign would never close and the loop would spin.
    """
    entry = {"rounds": 6}
    s = scope.parse("CROSS_CUTTING: server,android :: x\nSURFACE-DONE: server :: done")
    scope.commit(entry, scope.propose(entry, s, "project-b"), accepted=True)
    assert _v(entry["campaign"]["verdicts"]["server"]) == "done"

    entry["rounds"] = 7
    s2 = scope.parse("CROSS_CUTTING: server,android :: x\nSURFACE-DONE: android :: done")
    camp = scope.commit(entry, scope.propose(entry, s2, "project-b"), accepted=True)
    assert camp is None, "second surface completing must close it"


def test_a_silent_round_leaves_the_campaign_exactly_as_it_was():
    entry = {"rounds": 6}
    s = scope.parse("CROSS_CUTTING: server,android :: x")
    scope.commit(entry, scope.propose(entry, s, "project-b"), accepted=True)
    before = dict(entry["campaign"])
    entry["rounds"] = 7
    after = scope.commit(entry, scope.propose(entry, scope.parse("nothing here"), "project-b"), accepted=True)
    assert after == before, "a round with no markers must not disturb the campaign"


def test_cross_cutting_surfaces_union_in():
    """A later round naming a NEW surface must widen the checklist, not drop it."""
    entry = {"rounds": 6}
    s = scope.parse("CROSS_CUTTING: server,android :: x")
    scope.commit(entry, scope.propose(entry, s, "project-b"), accepted=True)
    entry["rounds"] = 7
    s2 = scope.parse("CROSS_CUTTING: server,chrome-extension :: x\n"
                     "SURFACE-DONE: server :: done")
    camp = scope.commit(entry, scope.propose(entry, s2, "project-b"), accepted=True)
    assert "chrome-extension" in camp["surfaces"]
    assert _v(camp["verdicts"]["server"]) == "done"


def test_untracked_surface_verdict_is_ignored_not_added():
    """A verdict for a surface the campaign never declared must not extend it."""
    entry = {"rounds": 6}
    s = scope.parse("CROSS_CUTTING: server,android :: x")
    scope.commit(entry, scope.propose(entry, s, "project-b"), accepted=True)
    entry["rounds"] = 7
    s2 = scope.parse("SURFACE-DONE: web-app :: done -- surprise")
    camp = scope.commit(entry, scope.propose(entry, s2, "project-b"), accepted=True)
    assert camp["surfaces"] == ["server", "android"]
    assert "web-app" not in camp["verdicts"]


def test_no_markers_no_campaign():
    assert scope.commit({"rounds": 1}, scope.propose({"rounds": 1}, scope.parse(""), "project-b"), accepted=True) is None


def test_summary_line_is_readable():
    camp = {"id": "project-b-x3", "surfaces": ["a", "b", "c"],
            "verdicts": {"a": "done"}}
    line = scope.summary_line(camp)
    assert "1/3" in line and "b, c" in line
    assert scope.summary_line(None) == "no campaign"