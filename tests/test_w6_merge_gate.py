"""W6 regression tests -- the human merge gate + W2 review gating of integration.

The Sep 22 contract: "nothing merges to main without you." The shipped runner had
drifted into an unconditional ff-merge into canonical on every accepted round.
"""
import pytest

import karpathy_runner as kr


PROJ = {"name": "p", "path": "C:/tmp/p", "gate": ""}


def test_auto_merge_defaults_to_off():
    assert kr._auto_merge_enabled({}) is False
    assert kr._auto_merge_enabled({"name": "p"}) is False


def test_auto_merge_requires_an_explicit_true():
    assert kr._auto_merge_enabled({"auto_merge": True}) is True
    assert kr._auto_merge_enabled({"auto_merge": "true"}) is True
    assert kr._auto_merge_enabled({"auto_merge": "yes"}) is True
    assert kr._auto_merge_enabled({"auto_merge": False}) is False
    assert kr._auto_merge_enabled({"auto_merge": "false"}) is False
    assert kr._auto_merge_enabled({"auto_merge": "maybe"}) is False
    assert kr._auto_merge_enabled({"auto_merge": 0}) is False


def test_auto_merge_env_is_an_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("KL_AUTO_MERGE", raising=False)
    assert kr._auto_merge_enabled({}) is False
    monkeypatch.setenv("KL_AUTO_MERGE", "1")
    assert kr._auto_merge_enabled({}) is True
    monkeypatch.setenv("KL_AUTO_MERGE", "0")
    assert kr._auto_merge_enabled({}) is False


# ---------------------------------------------------------------------------
# approval reading
# ---------------------------------------------------------------------------
def test_no_approval_means_not_approved():
    ok, detail = kr._merge_approved({}, 5)
    assert ok is False
    assert "no approval" in detail


def test_an_approval_for_this_round_is_honoured():
    ok, detail = kr._merge_approved({"approved_by": "gene", "approved_round": 5}, 5)
    assert ok is True
    assert "gene" in detail


def test_a_stale_approval_does_not_launder_a_later_round():
    """Approving round 5 says nothing about round 6."""
    ok, detail = kr._merge_approved({"approved_by": "gene", "approved_round": 5}, 6)
    assert ok is False
    assert "round 5" in detail


def test_an_approval_without_a_round_applies_to_the_current_round():
    ok, _ = kr._merge_approved({"approved_by": "gene"}, 9)
    assert ok is True


def test_an_unreadable_approval_round_fails_closed():
    ok, detail = kr._merge_approved({"approved_by": "gene", "approved_round": "x"}, 5)
    assert ok is False


# ---------------------------------------------------------------------------
# eligibility: W2 x W6
# ---------------------------------------------------------------------------
def test_a_review_invalid_round_is_never_merge_eligible():
    """THE W2/W6 INTERSECTION: auto_merge ON still cannot merge an invalid review."""
    entry = {"review_valid": False,
             "review_family": {"reason": "same family"},
             "approved_by": "gene", "approved_round": 3}
    e = kr._merge_eligibility(dict(PROJ, auto_merge=True), entry, 3)
    assert e["eligible"] is False
    assert "review-invalid" in e["reason"]


def test_missing_review_validity_fails_closed():
    """Absent review_valid must read as invalid, never as 'no objection'."""
    e = kr._merge_eligibility(PROJ, {}, 1)
    assert e["eligible"] is False


def test_a_valid_review_still_needs_approval_by_default():
    entry = {"review_valid": True}
    e = kr._merge_eligibility(PROJ, entry, 4)
    assert e["eligible"] is False
    assert "no approval" in e["reason"]


def test_a_valid_review_plus_approval_is_eligible():
    entry = {"review_valid": True, "approved_by": "gene", "approved_round": 4}
    e = kr._merge_eligibility(PROJ, entry, 4)
    assert e["eligible"] is True


def test_auto_merge_opt_in_plus_valid_review_is_eligible():
    entry = {"review_valid": True}
    e = kr._merge_eligibility(dict(PROJ, auto_merge=True), entry, 4)
    assert e["eligible"] is True
    assert "auto_merge" in e["reason"]


def test_auto_merge_does_not_bypass_approval_of_a_wrong_round():
    """auto_merge on means no approval needed -- but review validity still binds."""
    entry = {"review_valid": False}
    e = kr._merge_eligibility(dict(PROJ, auto_merge=True), entry, 4)
    assert e["eligible"] is False


# ---------------------------------------------------------------------------
# the branch+tag stop
# ---------------------------------------------------------------------------
def test_tag_only_creates_a_tag_on_a_branch(tmp_path):
    """W6: a stopped round gets a tag on its branch; canonical is untouched."""
    import checkpoint as ck
    import subprocess

    def run(*a):
        return subprocess.run(list(a), cwd=str(tmp_path), capture_output=True,
                              text=True, creationflags=0x08000000)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("one")
    run("git", "add", ".")
    run("git", "commit", "-qm", "base")
    base = run("git", "rev-parse", "HEAD").stdout.strip()

    run("git", "checkout", "-qb", "kl/p/r01")
    (tmp_path / "a.txt").write_text("two")
    run("git", "add", ".")
    run("git", "commit", "-qm", "round 1")
    tip = run("git", "rev-parse", "HEAD").stdout.strip()

    out = ck.tag_only("p", str(tmp_path), 1, ref="kl/p/r01")
    assert out.startswith("tag kp/p/r01 at "), out
    assert "unmerged" in out

    # the tag points at the BRANCH tip, not at canonical
    tagged = run("git", "rev-parse", "kp/p/r01^{commit}").stdout.strip()
    assert tagged == tip and tagged != base

    # canonical (master/main) did not move
    cur = run("git", "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    run("git", "checkout", "-q", cur.replace("kl/p/r01", "master"))
    canon = run("git", "rev-parse", "HEAD").stdout.strip()
    assert canon == base, "canonical must be untouched by tag_only"


def test_tag_only_is_idempotent(tmp_path):
    import checkpoint as ck
    import subprocess

    def run(*a):
        return subprocess.run(list(a), cwd=str(tmp_path), capture_output=True,
                              text=True, creationflags=0x08000000)

    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    (tmp_path / "a.txt").write_text("one")
    run("git", "add", ".")
    run("git", "commit", "-qm", "base")

    first = ck.tag_only("p", str(tmp_path), 1)
    assert first.startswith("tag ")
    second = ck.tag_only("p", str(tmp_path), 1)
    assert "already exists" in second


def test_tag_only_never_raises_on_a_bad_dir(tmp_path):
    import checkpoint as ck
    out = ck.tag_only("p", str(tmp_path / "nope"), 1)
    assert out.startswith("failed:")
