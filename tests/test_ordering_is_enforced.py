"""PROVE the ordering fix: a REJECTED round must not move campaign state.

Saul's correctness blocker #1 (2026-09-29): the runner folded campaign verdicts
BEFORE it knew whether the round had passed its gate, so a child could mark a
surface done, or open/close a campaign, on a round that then failed.

This module drives the REAL code path shape -- propose -> accept -> commit --
against the REAL scope module, for every acceptance state the runner can produce.

These are pytest tests, not a script, so `pytest tests/` actually COLLECTS them.
(A loose .py file of asserts runs ZERO tests under pytest and is silently
skipped -- the same class of "check that cannot fail" this whole repair is about.)
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scope  # noqa: E402


def _open_campaign(**over):
    camp = {"id": "c", "surfaces": ["server", "android"], "verdicts": {},
            "attempts": {}, "additions": [], "state": "open", "opened_round": 6}
    camp.update(over)
    return camp


# ------------------------------------------------- rejected: cannot OPEN

def test_rejected_round_cannot_open_a_campaign():
    entry = {"rounds": 6}
    prop = scope.propose(entry, scope.parse("CROSS_CUTTING: server,android :: field"),
                         "sb", declared=["server", "android"])
    assert prop["campaign"] is not None, "proposal should WANT to open"
    scope.commit(entry, prop, accepted=False)
    assert entry.get("campaign") is None, "a failed round opened a campaign"


def test_rejected_claim_is_still_auditable():
    entry = {"rounds": 6}
    prop = scope.propose(entry, scope.parse("CROSS_CUTTING: a,b :: field"), "sb")
    scope.commit(entry, prop, accepted=False)
    assert len(entry.get("campaign_rejections") or []) == 1


# --------------------------------------------- rejected: cannot ADVANCE

def test_rejected_round_cannot_mark_a_surface_done():
    entry = {"rounds": 7, "campaign": _open_campaign()}
    prop = scope.propose(entry, scope.parse("SURFACE-DONE: server :: done"), "sb")
    assert (prop["campaign"]["verdicts"].get("server") or {}).get("verdict") == "done", \
        "the PROPOSAL should want to mark it done"
    scope.commit(entry, prop, accepted=False)
    assert scope.outstanding_surfaces(entry["campaign"]) == ["server", "android"]


# ---------------------------------------------- rejected: cannot CLOSE

def test_rejected_round_cannot_close_a_campaign():
    entry = {"rounds": 8, "campaign": _open_campaign(surfaces=["server"])}
    prop = scope.propose(entry, scope.parse("SURFACE-DONE: server :: done"), "sb")
    assert prop["closed"] is not None, "the PROPOSAL should want to close"
    scope.commit(entry, prop, accepted=False)
    assert entry.get("campaign") is not None, "a failed round closed the campaign"
    assert not entry.get("campaigns_closed")


# ------------------------------------------- the control: accepted DOES move

def test_accepted_round_does_move_state():
    """Without this the tests above could pass on a commit() that never works."""
    entry = {"rounds": 9, "campaign": _open_campaign(surfaces=["server"])}
    prop = scope.propose(entry, scope.parse("SURFACE-DONE: server :: done"), "sb")
    scope.commit(entry, prop, accepted=True)
    assert entry.get("campaign") is None
    assert entry.get("campaigns_closed")


def test_accepted_round_advances_without_closing():
    entry = {"rounds": 9, "campaign": _open_campaign()}
    prop = scope.propose(entry, scope.parse("SURFACE-DONE: server :: done"), "sb")
    scope.commit(entry, prop, accepted=True)
    assert scope.outstanding_surfaces(entry["campaign"]) == ["android"]


# ------------------------------------------------ propose must be PURE

def test_propose_does_not_mutate_the_entry():
    entry = {"rounds": 9, "campaign": _open_campaign()}
    snapshot = repr(entry)
    scope.propose(entry, scope.parse("SURFACE-DONE: server :: done"), "sb")
    assert repr(entry) == snapshot, "propose() mutated the entry -- it must be pure"


def test_propose_does_not_alias_campaign_containers():
    """A deep-ish copy: mutating the proposal must not touch the entry's lists."""
    entry = {"rounds": 9, "campaign": _open_campaign()}
    prop = scope.propose(entry, scope.parse("SURFACE-DONE: server :: done"), "sb")
    prop["campaign"]["surfaces"].append("injected")
    assert "injected" not in entry["campaign"]["surfaces"]

# ------------------------------------- declared vocabulary: THREE states

def test_empty_declared_does_not_block_everything():
    """A repo that declares NO surfaces must not have every surface refused.

    3 of the 4 enabled repos declare no surfaces today. If an empty vocabulary
    meant "refuse every name", campaigns would be silently impossible for them --
    a fix that looks safe and quietly disables the feature for most of the fleet.
    """
    entry = {"rounds": 5}
    prop = scope.propose(entry, scope.parse("CROSS_CUTTING: server,android :: x"),
                         "sb", declared=[])
    assert prop["campaign"] is not None, "an empty vocabulary must not block"
    assert prop["refused"] == [], prop["refused"]
    assert prop["campaign"]["surfaces"] == ["server", "android"]


def test_none_declared_does_not_block_everything():
    entry = {"rounds": 5}
    prop = scope.propose(entry, scope.parse("CROSS_CUTTING: server,android :: x"), "sb")
    assert prop["campaign"] is not None
    assert prop["refused"] == []


def test_a_real_vocabulary_IS_enforced():
    """The control: when surfaces ARE declared, the filter must bite."""
    entry = {"rounds": 5}
    prop = scope.propose(entry, scope.parse("CROSS_CUTTING: server,invented :: x"),
                         "sb", declared=["server", "android"])
    assert prop["refused"] == ["invented"], prop["refused"]
    assert prop["campaign"]["surfaces"] == ["server"]
