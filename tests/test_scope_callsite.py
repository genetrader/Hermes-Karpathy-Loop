#!/usr/bin/env python
"""Regression tests for the defects an external review found on 2026-09-29.
   HONEST LABEL (F4, 2026-09-30): this file is a MIX. Some checks parse the
   runner with `ast` (structural wiring: the call exists, inside the right
   guard) -- those are SMOKE tests for wiring, not behavior; the BEHAVIOR
   for the same properties is proven through the real run_round in
   tests/test_round_orchestration.py. Keep both: wiring drift should be
   caught cheaply, behavior change loudly.

WHY THIS FILE IS SEPARATE FROM tests/test_scope.py
test_scope.py tested `scope.py` DIRECTLY, and all 27 of its tests passed while the
live runner could not advance a campaign AT ALL. That is the "a check that cannot
fail" failure: the unit under test was right and the CALLER was wrong.

So every test here does one of two things the old suite did not:
  1. it exercises the runner's ACTUAL call shape, or
  2. it pins a SEMANTIC rule that a well-formed-but-wrong value would violate.

Defects covered:
  D1  `scope` module shadowed by the parsed dict -> AttributeError swallowed by a
      broad except -> campaigns never advanced in the live runner.
  D2  the timeout path left the parsed name unbound (TWO handlers exist).
  D3  ANY verdict -- including `deferred` and arbitrary prose -- closed a campaign,
      which is exactly the half-applied drift the feature exists to prevent.
  D4  nothing could tell the loop WHICH surface a campaign still needed.

Run: python -m pytest tests/test_scope_callsite.py -q
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scope  # noqa: E402


def _v(verdict):
    """Read the verdict word out of either stored shape (str, or dict+reason)."""
    if isinstance(verdict, dict):
        return verdict.get("verdict")
    return verdict


def _src() -> str:
    return (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")


# ---------------------------------------------------------- D1: module shadowing

def test_runner_does_not_rebind_the_scope_module():
    """`scope` must not be REASSIGNED anywhere in the runner.

    Static half of D1. An AST walk catches aliases a regex would miss.
    """
    tree = ast.parse(_src())
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if getattr(t, "id", None) == "scope":
                    bad.append(node.lineno)
    assert not bad, ("`scope` (the imported MODULE) is reassigned at line(s) %s -- "
                     "that shadowing made every campaign call raise AttributeError"
                     % bad)


def test_runner_uses_the_propose_commit_split_in_the_right_ORDER():
    """The runner must PROPOSE while the child runs, and COMMIT only after it has
    ACCEPTED the round.

    This is Saul's correctness blocker #1. The old code called a single mutating
    `next_campaign()` BEFORE the gate ran, so a child could open/advance/close a
    campaign on a round that then failed. Asserting the *order* is the point: a
    future edit that moves the commit back up must fail here.
    """
    src = _src()
    i_prop = src.find("scope.propose(")
    i_accept = src.find("_round_ok = (rc == 0)")
    i_commit = src.find("scope.commit(entry, campaign_prop")
    assert i_prop != -1, "the runner must call scope.propose()"
    assert i_accept != -1, "the runner must compute _round_ok"
    assert i_commit != -1, "the runner must call scope.commit()"
    assert i_prop < i_accept < i_commit, (
        "ORDER VIOLATION: propose(%d) / accept(%d) / commit(%d). The commit must "
        "come AFTER acceptance, or a failed round can still move campaign state."
        % (i_prop, i_accept, i_commit))
    # Assert there is no CALL to the deprecated shim. A historical COMMENT
    # that documents the old bug is fine and must stay -- so match on the
    # runner's real argument names, which a comment would not contain.
    assert "scope.next_campaign(entry" not in src, \
        "the runner must not CALL the deprecated mutating next_campaign() shim"


def test_propose_is_pure_at_the_call_site():
    """The proposal block must not write campaign state.

    A `propose` that mutates would restore the old unsafe ordering invisibly.
    """
    src = _src()
    i = src.find("# ---- CAMPAIGN PROPOSAL")
    if i == -1:
        i = src.find("# ---- CAMPAIGN PROPOSAL")
    assert i != -1, "proposal block missing"
    block = src[i:i + 2600]
    assert 'entry["campaign"] =' not in block, \
        "the PROPOSAL block must not assign entry['campaign']"
    assert "entry.pop(" not in block, "the proposal block must not pop campaign"


def test_campaign_advance_reaches_the_real_module():
    """End-to-end: the runner's call shape must WORK through propose+commit."""
    entry = {"rounds": 5}
    round_scope = scope.parse(
        "CROSS_CUTTING: server,android :: add the field\n"
        "SURFACE-DONE: server :: done -- shipped")
    before = scope.summary_line(entry.get("campaign"))
    prop = scope.propose(entry, round_scope, "project-b")
    camp = scope.commit(entry, prop, accepted=True)
    assert camp is not None, "a cross-cutting round must OPEN a campaign"
    now = scope.summary_line(entry.get("campaign"))
    assert before != now and "1/2" in now
    assert _v(entry["campaign"]["verdicts"]["server"]) == "done"


# ---------------------------------------------------------------- D2: timeout

def test_round_timeout_handler_binds_round_scope():
    """A timeout must not leave the parsed name unbound.

    TWO TimeoutExpired handlers exist (a helper near :249 and the round near
    :399). Target the ROUND one: its body sets rc = 124.
    """
    s = _src()
    idxs, start = [], 0
    while True:
        i = s.find("except subprocess.TimeoutExpired", start)
        if i == -1:
            break
        idxs.append(i)
        start = i + 1
    assert len(idxs) >= 2, "expected two timeout handlers, found %d" % len(idxs)

    round_handler = None
    for i in idxs:
        body = s[i:i + 1500]
        if "rc = 124" in body:
            round_handler = body
    assert round_handler is not None, "could not locate the round's timeout handler"
    assert 'round_scope = {"surfaces": {}, "cross_cutting": None}' in round_handler, \
        "the ROUND's timeout handler must bind round_scope"


def test_a_timeout_cannot_fold_a_verdict():
    empty = {"surfaces": {}, "cross_cutting": None, "cross_dropped": 0}
    assert scope.commit({"rounds": 9}, scope.propose({"rounds": 9}, empty, "x"), accepted=True) is None


# --------------------------------------------------- D3: completion semantics

def _open_two_surface_campaign():
    entry = {"rounds": 6}
    scope.commit(entry, scope.propose(
        entry,
        scope.parse("CROSS_CUTTING: server,android :: x\nSURFACE-DONE: server :: done"),
        "sb"), accepted=True)
    entry["rounds"] = 7
    return entry


def test_deferred_does_not_close_a_campaign():
    """THE central semantic fix: `deferred` is not completion."""
    entry = _open_two_surface_campaign()
    camp = scope.commit(entry, scope.propose(entry, scope.parse("SURFACE-DONE: android :: deferred -- later"), "sb"), accepted=True)
    assert camp is not None, "deferred must NOT close the campaign"
    assert "android" in scope.outstanding_surfaces(camp)


def test_arbitrary_prose_does_not_close_a_campaign():
    entry = _open_two_surface_campaign()
    camp = scope.commit(entry, scope.propose(entry, scope.parse("SURFACE-DONE: android :: mostly-fine"), "sb"), accepted=True)
    assert camp is not None, "an unrecognised verdict must not close a campaign"


def test_failed_does_not_close_a_campaign():
    entry = _open_two_surface_campaign()
    camp = scope.commit(entry, scope.propose(entry, scope.parse("SURFACE-DONE: android :: failed -- gate red"), "sb"), accepted=True)
    assert camp is not None


def test_done_then_done_closes():
    """The happy path must still work -- a fix that breaks completion is worse."""
    entry = _open_two_surface_campaign()
    assert scope.commit(entry, scope.propose(entry, scope.parse("SURFACE-DONE: android :: done"), "sb"), accepted=True) is None
    assert _v(entry["campaigns_closed"][-1]["verdicts"]["android"]) == "done"


def test_not_applicable_closes_and_stays_auditable():
    entry = _open_two_surface_campaign()
    assert scope.commit(entry, scope.propose(entry, scope.parse("SURFACE-DONE: android :: not-applicable -- no UI"), "sb"), accepted=True) is None
    assert _v(entry["campaigns_closed"][-1]["verdicts"]["android"]) == "not-applicable"


def test_deferred_then_done_closes():
    entry = _open_two_surface_campaign()
    c = scope.commit(entry, scope.propose(entry, scope.parse("SURFACE-DONE: android :: deferred -- later"), "sb"), accepted=True)
    entry["campaign"] = c
    entry["rounds"] = 8
    assert scope.commit(entry, scope.propose(entry, scope.parse("SURFACE-DONE: android :: done -- now"), "sb"), accepted=True) is None


# ------------------------------------------- D4: campaign drives surface choice

def test_outstanding_surfaces_is_the_campaign_frontier():
    camp = {"surfaces": ["server", "android", "chrome-extension"],
            "verdicts": {"server": {"verdict": "done"},
                         "android": {"verdict": "deferred"}}}
    assert scope.outstanding_surfaces(camp) == ["android", "chrome-extension"]


def test_outstanding_accepts_legacy_bare_string_verdicts():
    camp = {"surfaces": ["a", "b"], "verdicts": {"a": "done", "b": "deferred"}}
    assert scope.outstanding_surfaces(camp) == ["b"]


def test_outstanding_on_no_campaign_is_empty():
    assert scope.outstanding_surfaces(None) == []
    assert scope.outstanding_surfaces({}) == []


def test_is_terminal_rejects_odd_input():
    assert scope._is_terminal(None) is False
    assert scope._is_terminal("") is False
    assert scope._is_terminal(7) is False
    assert scope._is_terminal("done") is True
    assert scope._is_terminal({"verdict": "done"}) is True

# ------------------------------------- D5: a failed round must not checkpoint

def test_checkpoint_is_gated_on_round_success():
    """A failed round must NOT publish a tag/GitHub checkpoint.

    External review 2026-09-29: `checkpoint()` ran unconditionally, so a
    timed-out or gate-failing round still got a kp/<repo>/rNN tag pushed to
    GitHub -- publishing a failed round as legitimate.

    The guard is now `_round_ok and state_persisted` (Saul, 2026-09-29): a tag
    must describe state that actually reached disk, so publishing moved to the
    END of the round, after the campaign commit and the registry write.
    """
    s = _src()
    i = s.find("End-of-run checkpoint")
    assert i != -1, "checkpoint block missing"
    # WINDOW (2026-09-30): the fix commentary documenting the acceptance and
    # ordering rules is deliberately long, and pushed the publish guard past
    # the old fixed windows. F3 added the integration branch (ff-merge +
    # post-merge gate), moving the reject branch further still (measured:
    # guard ~12.5k; reject-branch anchor 15,788; first SKIPPED 16,025). The
    # window is a measured margin -- every assertion keeps its strictness.
    block = s[i:i + 18500]
    assert "if _round_ok and state_persisted" in block, \
        ("publishing must require BOTH acceptance and a persisted state, got "
         "neither guard")
    # the failure branch must NOT call checkpoint -- that is the whole point.
    # F3 (2026-09-30): the accept branch now contains a NESTED if/else (the
    # ff-merge + post-merge gate), so splitting on the FIRST "else:" landed
    # inside the nested structure and lost the reject branch entirely.
    # Anchor on the reject branch itself: its opening line is unique.
    after_if = block.split("if _round_ok and state_persisted", 1)[1]
    assert "else:" in after_if, "there must be an else branch for a failed round"
    REJECT_ANCHOR = "    else:" + chr(10) + "        _pub_why = list(_why)"
    assert REJECT_ANCHOR in after_if, \
        "the reject branch anchor moved -- update REJECT_ANCHOR"
    after_else = after_if.split(REJECT_ANCHOR, 1)[1]
    assert "checkpoint(" not in after_else, \
        "the failure branch must NOT call checkpoint"
    assert "SKIPPED" in after_else, "the failure must be visible in last_checkpoint"
    # GUARD SQUINT (2026-09-30): with the widened window, `after_else` extends
    # into main(); bound the no-checkpoint check to the failure branch's own
    # tail (everything up to the SKIPPED recording) so it stays meaningful.
    fail_branch = after_else.split("SKIPPED", 1)[0]
    assert "checkpoint(" not in fail_branch, \
        "the failure branch must NOT call checkpoint"


def test_checkpoint_requires_both_rc_and_gate():
    s = _src()
    i = s.find("End-of-run checkpoint")
    block = s[i:i + 12000]
    assert "_round_ok = (rc == 0) and _gate_ok" in block, \
        "acceptance must require BOTH a clean exit and a passing gate"
    assert 'entry["round_accepted"]' in block, \
        "the accept verdict must be recorded, not just used"
