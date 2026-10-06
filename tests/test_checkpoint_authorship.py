#!/usr/bin/env python
"""F1-6 checkpoint authorship tests (written 2026-09-30).

The runner is the ONLY author of checkpoint tags. Driving the real
karpathy_runner.checkpoint() against a real temp git repo with a bare origin:

  case 1  a pre-existing tag at a DIFFERENT sha (the stray/child tag from
          T2-1: 88 of 112 logged checkpoints were rc=128) -> checkpoint()
          FAILS loudly, the tag is NOT moved, and NOTHING is pushed.
  case 2  a tag already at the current HEAD (a previous publish attempt whose
          push failed) -> published via push-only; NO second `git tag` (which
          would exit rc=128 and pollute the log).
  case 3  no pre-existing tag -> tag rc=0 + push rc=0 -> published.
  case 4  repo with no remote -> tag rc=0, push skipped, still published
          (the no-remote gap stays visible in the detail string).
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import karpathy_runner as kr  # noqa: E402
import settings as S_mod      # noqa: E402


def _enable_github(monkeypatch):
    """These cases exercise the PUSH path against a temp BARE origin. GitHub
    is off by default (safe shipped default), so flip the settings accessors
    the checkpoint reads -- monkeypatch restores them per test."""
    monkeypatch.setattr(kr._S, "github_enabled", lambda: True)
    monkeypatch.setattr(S_mod, "github_enabled", lambda: True)
    monkeypatch.setattr(kr._S, "push_enabled", lambda loop_cfg=None: True)
    monkeypatch.setattr(S_mod, "push_enabled", lambda loop_cfg=None: True)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True)


def _make_repo(tmp: tempfile.TemporaryDirectory):
    repo = pathlib.Path(tmp.name) / "repo"
    bare = pathlib.Path(tmp.name) / "bare.git"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "init", "-q", "--bare", str(bare))
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("1", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "c1")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "origin", "HEAD")
    return repo, bare


def test_foreign_tag_fails_loudly_and_never_pushes(monkeypatch):
    _enable_github(monkeypatch)
    with tempfile.TemporaryDirectory() as d:
        t = tempfile.TemporaryDirectory(dir=d)
        repo, bare = _make_repo(t)
        shaA = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _git(repo, "tag", "kp/proj/r01", shaA)          # tag NOT at HEAD
        (repo / "a.txt").write_text("2", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "c2")

        out = kr.checkpoint("proj", str(repo), 1)
        assert out.startswith("failed:"), out
        assert "refusing to move" in out
        # nothing reached the remote
        assert not _git(repo, "ls-remote", "origin",
                        "refs/tags/kp/proj/r01").stdout.strip()
        # and the foreign tag was not moved
        assert _git(repo, "rev-parse", "refs/tags/kp/proj/r01").stdout.strip() == shaA


def test_tag_already_at_head_publishes_push_only(monkeypatch):
    _enable_github(monkeypatch)
    with tempfile.TemporaryDirectory() as d:
        t = tempfile.TemporaryDirectory(dir=d)
        repo, bare = _make_repo(t)
        _git(repo, "tag", "kp/proj/r01",
             _git(repo, "rev-parse", "HEAD").stdout.strip())  # tag AT HEAD

        out = kr.checkpoint("proj", str(repo), 1)
        assert out.startswith("published:"), out
        assert "tag already at this sha" in out
        assert "tag rc=" not in out, "must not run a second git tag (rc=128 noise)"
        pushed = _git(repo, "ls-remote", "origin",
                      "refs/tags/kp/proj/r01").stdout.strip().split()[0]
        assert pushed == _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_clean_publish_tags_and_pushes(monkeypatch):
    _enable_github(monkeypatch)
    with tempfile.TemporaryDirectory() as d:
        t = tempfile.TemporaryDirectory(dir=d)
        repo, bare = _make_repo(t)
        out = kr.checkpoint("proj", str(repo), 1)
        assert out.startswith("published:"), out
        assert "tag rc=0" in out and "push rc=0" in out
        remote = _git(repo, "ls-remote", "origin",
                      "refs/tags/kp/proj/r01").stdout.strip().split()[0]
        assert remote == _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_no_remote_still_tags_and_reports_gap(monkeypatch):
    _enable_github(monkeypatch)
    with tempfile.TemporaryDirectory() as d:
        repo = pathlib.Path(d) / "repo2"
        repo.mkdir()
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "a.txt").write_text("x", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "c1")
        out = kr.checkpoint("proj", str(repo), 7)
        assert out.startswith("published:"), out
        assert "push skipped (no git remote)" in out
