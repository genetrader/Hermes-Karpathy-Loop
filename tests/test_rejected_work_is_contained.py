"""SAUL'S DECISIVE TEST for blocker #6: rejected-work contamination.

The scenario that must not be possible:

  round 1  creates a commit + an untracked file, and is REJECTED
  round 2  is ACCEPTED and publishes
  =>      round 2's published range must contain NEITHER round 1's commit
          NOR round 1's untracked file

Without containment, round 2 inherits round 1's residue and publishes it under
its own checkpoint, so the published history contains work that never passed.

F3 UPDATE (2026-09-30): the canonical reset helper is _reset_canonical (the
child-work rollback _rollback_to was deleted with the mechanism it served --
the child now works in a disposable worktree and containment there is
DELETION, proven behaviorally through the REAL run_round in
tests/test_round_orchestration.py scenarios 1-3, which supersede the
helper-level contamination simulation below). The helper-level tests here
pin the LAST-LINE-OF-DEFENSE helper that still exists.

These tests drive REAL git in a throwaway repo.

Run: python -m pytest tests/test_rejected_work_is_contained.py -q
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import karpathy_runner as K  # noqa: E402


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd)] + list(args),
                          capture_output=True, text=True, timeout=60,
                          errors="replace")


@pytest.fixture()
def repo(tmp_path):
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


def _commit(d, name, msg):
    (d / name).write_text("body\n", encoding="utf-8")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", msg)
    return _head(d)


# ------------------------------------------------------------- the helpers

def test_is_dirty_detects_untracked_files(repo):
    assert K._is_dirty(str(repo)) is False
    (repo / "junk.txt").write_text("x\n", encoding="utf-8")
    assert K._is_dirty(str(repo)) is True


def test_is_dirty_detects_modified_files(repo):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    assert K._is_dirty(str(repo)) is True


def test_is_dirty_returns_none_for_a_non_repo(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert K._is_dirty(str(d)) is None


# ------------------------------------------------- the containment operation

def test_reset_removes_a_commit_and_untracked_file(repo):
    anchor = _head(repo)
    _commit(repo, "rejected.txt", "rejected work")
    (repo / "leftover.txt").write_text("x\n", encoding="utf-8")
    assert _head(repo) != anchor
    assert K._is_dirty(str(repo)) is True

    status = K._reset_canonical(str(repo), anchor)

    assert status.startswith("canonical reset"), status
    assert _head(repo) == anchor, "the rejected commit was not removed"
    assert K._is_dirty(str(repo)) is False, "untracked residue survived"
    assert not (repo / "rejected.txt").exists()
    assert not (repo / "leftover.txt").exists()


def test_reset_is_verified_not_assumed(repo):
    """An unverified restore is not a restore: it must return a status string
    that distinguishes success from failure."""
    anchor = _head(repo)
    ok = K._reset_canonical(str(repo), anchor)
    assert ok.startswith("canonical reset")


def test_reset_without_an_anchor_refuses(repo):
    assert K._reset_canonical(str(repo), "").startswith("RESET SKIPPED")
    assert K._reset_canonical("", "abc").startswith("RESET SKIPPED")


def test_reset_to_a_bogus_sha_fails_verification(repo):
    """A restore we cannot verify must not claim success."""
    status = K._reset_canonical(str(repo), "0" * 40)
    assert status.startswith("RESET FAILED") or status.startswith("RESET UNVERIFIED"), \
        "a restore that did not reach the anchor claimed success: %s" % status


# ------------------------------------------- the decisive contamination test
#
# F3: the decisive version of this scenario now runs the REAL run_round
# against a REAL worktree child (tests/test_round_orchestration.py::
# test_accepted_round2_range_excludes_round1). What is pinned HERE is the
# git-mechanics control: a reset-to-anchor restore leaves nothing behind.

def test_after_reset_the_range_is_clean(repo):
    """Git mechanics: after a reset-to-anchor restore, a later commit's
    publishable range contains none of the rejected work."""
    base = _head(repo)

    round1_commit = _commit(repo, "r1.txt", "round 1 work")
    (repo / "r1_untracked.txt").write_text("r1\n", encoding="utf-8")

    status = K._reset_canonical(str(repo), base)
    assert status.startswith("canonical reset"), status

    round2_commit = _commit(repo, "r2.txt", "round 2 work")

    published = _git(repo, "rev-list", "--objects", "%s..%s" % (base, round2_commit)).stdout

    assert round1_commit not in published, \
        "round 1's REJECTED commit is inside round 2's published range"
    assert "r1_untracked.txt" not in published, \
        "round 1's untracked file is inside round 2's published range"
    assert "r1.txt" not in published, "round 1's file survived into the published range"
    assert "r2.txt" in published, "round 2's own work is missing -- test is vacuous"


def test_without_containment_the_contamination_really_happens(repo):
    """The control. Proves the test above is not vacuous: with NO rollback, the
    rejected work DOES land in the later published range."""
    base = _head(repo)
    round1_commit = _commit(repo, "r1.txt", "round 1 work")
    (repo / "r1_untracked.txt").write_text("r1\n", encoding="utf-8")
    # NOTE: deliberately no rollback here
    round2_commit = _commit(repo, "r2.txt", "round 2 work")

    published = _git(repo, "rev-list", "--objects", "%s..%s" % (base, round2_commit)).stdout
    assert round1_commit in published, \
        "the control is vacuous: without containment the rejected commit should " \
        "have contaminated the range"
    assert "r1.txt" in published