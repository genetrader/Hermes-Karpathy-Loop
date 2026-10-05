"""F0-1 behavioral test: an unsafe starting state must NEVER reach the child.

Covers Saul's minimum-gate item 1: "dirty or unknown start cannot reach Popen."

Three scenarios, each against a REAL temp git repo, asserting (a) no child was
launched and (b) pre-existing content is untouched:

  1. dirty tracked file
  2. untracked file present
  3. not a git repository at all (_head_full_sha -> None)

The child launch is intercepted by monkeypatching subprocess.Popen -- if the
runner ever reaches the launch path the test fails loudly.

Run: python -m pytest tests/test_unsafe_start_refused.py -q
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


@pytest.fixture(autouse=True)
def _isolate_registry(tmp_path, monkeypatch):
    """Review-fix (2026-10-01): this file executes the REAL run_round, and
    run_round persists the entry via threads.load()+save() (runner :1071-73).
    Without a redirect the REAL registry got a 't' test entry, the real
    prompt_cycles.json consumed a real angle prompt, and runner.log filled
    with test noise. Redirect every threads path to tmp."""
    import threads
    monkeypatch.setattr(threads, "STATE", tmp_path / "threads.json")
    import karpathy_runner as _K
    if hasattr(_K, "threads"):
        monkeypatch.setattr(_K.threads, "STATE", tmp_path / "threads.json")
    yield


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
    (d / "a.txt").write_text("original\n", encoding="utf-8")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "first")
    return d


class _NoLaunch:
    """Stand-in for the ROUND-DRIVE subprocess.Popen (karpathy_runner.py:508).

    IMPORTANT: we cannot blanket-patch subprocess.Popen, because the session
    bootstrap (threads.resolve -> hermes -z INIT) also goes through
    subprocess.run -> Popen, BEFORE the refusal block. The round drive is the
    call whose args contain `--resume`; the INIT call does not.
    """

    def __init__(self, orig):
        self.called = False
        self._orig = orig

    def __call__(self, *a, **kw):
        cmd = a[0] if a else kw.get("args", [])
        if isinstance(cmd, list) and "--resume" in cmd:
            self.called = True
            raise AssertionError("CHILD LAUNCHED on an unsafe start -- refusal failed")
        return self._orig(*a, **kw)


def _run_round_until_refusal(monkeypatch, repo, proj):
    """Call run_round with the round-drive Popen trapped. Returns (rc, launcher, entry)."""
    launcher = _NoLaunch(K.subprocess.Popen)
    monkeypatch.setattr(K.subprocess, "Popen", launcher)
    entry = {"rounds": 0}
    proj = dict(proj)
    proj["path"] = str(repo)
    rc = K.run_round(proj, entry)
    return rc, launcher, entry


def _proj(name="t"):
    return {"name": name, "path": "", "gate": None, "see": {}}


# --------------------------------------------------------------- scenarios

def test_dirty_tracked_file_refuses_and_preserves(monkeypatch, repo):
    (repo / "a.txt").write_text("HUMAN WORK IN PROGRESS\n", encoding="utf-8")
    rc, launcher, entry = _run_round_until_refusal(monkeypatch, repo, _proj())
    assert rc == 1
    assert not launcher.called, "the child was launched despite a dirty tree"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "HUMAN WORK IN PROGRESS\n", \
        "pre-existing modification was altered"
    assert entry.get("round_refused"), "the refusal reason was not recorded"


def test_untracked_file_refuses_and_preserves(monkeypatch, repo):
    (repo / "human-notes.md").write_text("do not delete\n", encoding="utf-8")
    rc, launcher, entry = _run_round_until_refusal(monkeypatch, repo, _proj())
    assert rc == 1
    assert not launcher.called, "the child was launched despite an untracked file"
    assert (repo / "human-notes.md").exists(), "pre-existing untracked file destroyed"
    assert entry.get("round_refused")


def test_no_head_refuses(monkeypatch, tmp_path):
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    rc, launcher, entry = _run_round_until_refusal(monkeypatch, plain, _proj())
    assert rc == 1
    assert not launcher.called, "the child was launched with no HEAD anchor"
    assert entry.get("round_refused")


def test_clean_repo_is_NOT_refused(monkeypatch, repo):
    """Control: a CLEAN tree must NOT hit the refusal path -- it should proceed
    all the way to the round drive. We prove it by trapping the round-drive
    Popen with a recording (not refusing) stub and asserting it WAS reached,
    and that no round_refused marker was set."""
    reached = []

    class _FakeProc:
        def __init__(self, *a, **kw):
            reached.append(list(a[0]) if a and isinstance(a[0], list) else [])
            self.returncode = 1
        def poll(self):
            return 1          # child "exits" immediately
        def communicate(self, *a, **kw):
            return ("", "")
        def kill(self):
            pass

    orig = K.subprocess.Popen

    def trap(*a, **kw):
        cmd = a[0] if a else kw.get("args", [])
        if isinstance(cmd, list) and "--resume" in cmd:
            return _FakeProc(*a, **kw)   # record the round drive
        return orig(*a, **kw)            # let the INIT bootstrap pass through

    monkeypatch.setattr(K.subprocess, "Popen", trap)
    entry = {"rounds": 0}
    proj = _proj()
    proj["path"] = str(repo)
    rc = K.run_round(proj, entry)
    assert reached, "a CLEAN tree never reached the round drive -- refusal is over-broad"
    assert not entry.get("round_refused"), \
        "a CLEAN tree was refused -- refusal is over-broad"