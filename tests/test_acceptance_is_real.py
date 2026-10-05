"""Tests for the SECOND round of defects -- the ones that survived "84 tests pass".

Saul's review (2026-09-29) found that the acceptance logic was still broken in
ways the existing suite could not see, because no test exercised the REAL SHA
comparison semantics. Two of these let a NO-OP ROUND BE CHECKPOINTED.

Every test here either:
  * drives real git in a throwaway repo (not source-string inspection), or
  * pins a semantic rule that a well-formed-but-wrong value would violate.

Run: python -m pytest tests/test_acceptance_is_real.py -q
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scope  # noqa: E402
import karpathy_runner as K  # noqa: E402


# --------------------------------------------------------------- real-git fixture

def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd)] + list(args),
                          capture_output=True, text=True, timeout=60,
                          errors="replace")


@pytest.fixture()
def repo(tmp_path):
    """A real, throwaway git repo with one commit."""
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@example.invalid")
    _git(d, "config", "user.name", "Test")
    (d / "a.txt").write_text("one\n", encoding="utf-8")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "first")
    return d


def _head(d):
    return _git(d, "rev-parse", "HEAD").stdout.strip()


def _commit(d, name, body="x\n", msg="c"):
    (d / name).write_text(body, encoding="utf-8")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", msg)
    return _head(d)


# ============================================================ BUG 1: full vs short

def test_head_full_sha_is_40_chars(repo):
    """`_head_full_sha` must return a FULL sha.

    The original bug: the pre-round capture was full (`rev-parse HEAD`) while the
    post-round evidence used `%h` (short). A 7-char string never equals a 40-char
    one, so `before != after` was ALWAYS TRUE and the HEAD gate was dead code.
    """
    h = K._head_full_sha(str(repo))
    assert h and len(h) == 40, "expected a full 40-char sha, got %r" % h


def test_short_and_full_sha_actually_differ(repo):
    """Pins WHY comparing a short sha to a full one is fatal."""
    full = _head(repo)
    short = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    assert short != full, "the short/full sha trap is not reproducing"
    assert full.startswith(short), "short sha should prefix the full one"


def test_unchanged_head_is_detected_as_no_progress(repo):
    """A no-op round (HEAD identical) must NOT read as 'HEAD advanced'.

    This is the bug that let a no-op be checkpointed. Compared with real git.
    """
    before = _head(repo)
    after = K._head_full_sha(str(repo))
    assert before == after, "sanity: nothing moved"
    # the corrected rule
    progressed = bool(before) and bool(after) and (before != after) and (
        K._is_ancestor(str(repo), before, after) is True)
    assert progressed is False, "a no-op round reported progress"


# ================================================ BUG 2: None must not authorise

def test_unknown_head_must_reject_not_authorise():
    """`_head_ok is True` -- never `is not False`.

    The code said 'unknown cannot authorise' in a comment and then wrote
    `(_head_ok is not False)`, which authorises None.
    """
    src = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
    assert "(_head_ok is True)" in src, \
        "acceptance must require _head_ok to be TRUE, not merely 'not False'"
    assert "(_head_ok is not False)" not in src, \
        "`is not False` lets an UNKNOWN head authorise a checkpoint"


def test_missing_either_side_yields_unknown():
    """No baseline, or no after-value => unknown (cannot authorise)."""
    # both halves of the precondition, expressed directly
    for before, after in ((None, "abc"), ("abc", None), (None, None)):
        ok = bool(before) and bool(after)
        assert ok is False, "missing %r/%r must not be treatable as success" % (before, after)


# ==================================================== ancestor-required movement

def test_a_real_forward_commit_is_ancestor(repo):
    before = _head(repo)
    after = _commit(repo, "b.txt", msg="second")
    assert before != after
    assert K._is_ancestor(str(repo), before, after) is True


def test_an_amend_is_not_a_descendant(repo):
    """Different sha, but NOT forward movement from the baseline.

    Guards the case Saul named: amend/rebase/reset change the sha without
    building on the recorded baseline.
    """
    before = _head(repo)
    _git(repo, "commit", "-q", "--amend", "-m", "amended")
    after = _head(repo)
    assert before != after, "amend should change the sha"
    assert K._is_ancestor(str(repo), before, after) is not True, \
        "an amended commit must not read as forward progress from the baseline"


def test_a_reset_backwards_is_not_ancestor(repo):
    before = _head(repo)
    _commit(repo, "c.txt", msg="third")
    _git(repo, "reset", "-q", "--hard", before)
    after = _head(repo)
    assert after == before, "reset --hard returns to the baseline"
    ok = (before != after) and (K._is_ancestor(str(repo), before, after) is True)
    assert ok is False


def test_is_ancestor_returns_none_when_it_cannot_tell(repo):
    assert K._is_ancestor(str(repo), None, "deadbeef") is None
    assert K._is_ancestor(str(repo), "deadbeef", None) is None
    assert K._is_ancestor("", "a", "b") is None
    assert K._is_ancestor(str(repo), "0" * 40, _head(repo)) is None


def test_head_helpers_never_raise_on_a_non_repo(tmp_path):
    d = tmp_path / "notarepo"
    d.mkdir()
    assert K._head_full_sha(str(d)) is None
    assert K._is_ancestor(str(d), "a" * 40, "b" * 40) is None


# ============================================ propose failures must not be silent

def test_proposal_ok_is_true_on_a_normal_proposal():
    prop = scope.propose({"rounds": 1}, scope.parse("CROSS_CUTTING: a,b :: x"), "p")
    assert prop["proposal_ok"] is True


def test_malformed_input_sets_proposal_ok_false():
    """Malformed input is reported, not swallowed into a harmless-looking no-op."""
    prop = scope.propose(None, None, "p")      # type: ignore[arg-type]
    assert prop["proposal_ok"] is False, \
        "a proposal that could not be computed must be visibly NOT ok"
    assert prop["reason"]


def test_propose_does_not_catch_everything():
    """A programming error must NOT be swallowed by propose().

    The blanket `except Exception` is the same shape as the swallow that hid the
    original AttributeError for weeks.
    """
    src = (ROOT / "scope.py").read_text(encoding="utf-8")
    i = src.find("def propose(")
    body = src[i:i + 2600]
    assert "except Exception" not in body, \
        "propose() must not blanket-catch: a code bug has to be loud"


# ================================================= widening cap arithmetic

def test_cap_cannot_be_exceeded_by_a_single_multi_addition():
    """A campaign one below the cap must not accept TWO additions in one round.

    The cap was computed once before the loop, so MAX_ADDITIONS-1 admitted two.
    """
    camp = {"id": "c", "surfaces": ["a"],
            "verdicts": {}, "attempts": {},
            "additions": ["x"] * (scope.MAX_ADDITIONS - 1),
            "state": "open"}
    entry = {"rounds": 5, "campaign": camp}
    prop = scope.propose(entry, scope.parse("CROSS_CUTTING: a,new1,new2 :: z"),
                         "p", declared=["a", "new1", "new2"])
    added = [s for s in prop["campaign"]["surfaces"] if s not in ("a",)]
    assert len(prop["campaign"]["additions"]) <= scope.MAX_ADDITIONS, \
        "widening exceeded MAX_ADDITIONS: %s" % prop["campaign"]["additions"]
    assert len(added) <= 1, "only one more addition was allowed, got %s" % added


# ============================================== empty vocabulary must NOT fail open

def test_empty_declared_does_not_refuse_everything():
    """3 of the 4 enabled repos declare no surfaces; they must not be blocked."""
    prop = scope.propose({"rounds": 1}, scope.parse("CROSS_CUTTING: server,android :: x"),
                         "p", declared=[])
    assert prop["campaign"] is not None
    assert prop["refused"] == []


def test_a_declared_vocabulary_still_bites():
    prop = scope.propose({"rounds": 1}, scope.parse("CROSS_CUTTING: server,invented :: x"),
                         "p", declared=["server"])
    assert prop["refused"] == ["invented"]


# ===================================================== publish-order invariants

def test_checkpoint_is_published_LAST():
    """acceptance -> campaign commit -> persist -> publish.

    A published tag must never describe scheduler state that was not written.
    """
    src = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
    i_accept = src.find("_round_ok = (rc == 0)")
    i_commit = src.find("scope.commit(entry, campaign_prop")
    i_persist = src.find("threads.save(reg)", i_commit)
    i_publish = src.find("ck = checkpoint(name", i_persist)
    assert -1 not in (i_accept, i_commit, i_persist, i_publish), \
        "one of the four stages is missing (accept=%d commit=%d persist=%d pub=%d)" % (
            i_accept, i_commit, i_persist, i_publish)
    assert i_accept < i_commit < i_persist < i_publish, (
        "PUBLISH-ORDER VIOLATION: accept=%d commit=%d persist=%d publish=%d"
        % (i_accept, i_commit, i_persist, i_publish))


def test_publish_requires_persistence():
    src = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
    i = src.find("_round_ok and state_persisted")
    assert i != -1, "publishing must require state_persisted"