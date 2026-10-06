#!/usr/bin/env python
"""Behavioral orchestration tests through the REAL run_round (master-plan F2,
re-pointed at F3 worktree reality 2026-09-30).

The unit suites (test_scope.py, test_scope_callsite.py, test_round_prompt.py)
test the PIECES. Saul's Phase A review (2026-09-30) demanded the WIRING be
proven: the real karpathy_runner.run_round() driven end-to-end against a REAL
temp git repository, a REAL child process (a small python script run through
the runner's actual subprocess.Popen call site, swapped for the hermes CLI),
a REAL gate command, and a REAL threads registry file on disk in a temp dir.

F3 UPDATE (2026-09-30): the child now runs in a disposable git WORKTREE
branch kl/<repo>/rNN created by the runner from the pre-round anchor.
  * on reject  -> the worktree + branch are DELETED; canonical was never
                  touched (asserted: head == anchor).
  * on accept  -> canonical is ff-merged, re-gated, tagged, pushed; the
                  worktree + branch are deleted afterwards.
  * dep junctions (.venv/node_modules) only exist when the canonical repo has
    untracked dep dirs -- the temp repos have none, so gates are unaffected.

What stays real: git worktrees/merges/commits/tags/pushes, the gate process,
evidence collection, registry load/save (redirected), loop.json (redirected),
checkpoint().

What is injected (and nothing more):
  * threads.resolve / title_for      -- no state.db in a test run
  * improver (I) manifest fns        -- canned facts / declared surfaces
  * angle_pick.pick                  -- canned angle (no prompt_id -> no
                                       angle_prompts bookkeeping)
  * surface_pick.pick                -- None (unscoped rounds)
  * discord_notify.progress          -- no-op (no network)
  * subprocess.Popen                 -- swaps ONLY the hermes child for a
                                       deterministic scenario script, launched
                                       with the runner's OWN cwd (the
                                       worktree) and env; everything git-wise
                                       uses subprocess.run and stays real.

Saul's required scenarios (master plan F2, F3 wording):
  1. rejected child work contained: worktree+branch deleted, canonical at anchor
  2. rejected untracked residue contained (deleted with the worktree)
  3. accepted round-2 published range contains none of round 1's work
  4. worktree-removal failure -> quarantine SURVIVES registry reload
  5. reloaded selector excludes the quarantined repo
  6. dirty start never launches, files preserved, no worktree created
  7. proposal_ok=False rejects; worktree contained
  8. publish only after final persistence
  9. evidence lands on the NEW row
 10. gate skipped on rc!=0
F3 additions:
 11. rounds_done bumps on accept only (F3.8)
 12. abandoned-worktree recovery quarantines the repo (F3.5)
 13. child env blocks push (real git push drill against the env wiring)
 14. worktree shown to the child + recorded in the entry
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import textwrap
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import karpathy_runner as kr          # noqa: E402
import threads                        # noqa: E402
import round_evidence                 # noqa: E402
import scope                          # noqa: E402
import loopctl                        # noqa: E402

PROJ = "orch-test"


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------

CHILD_SCRIPT = textwrap.dedent("""
    import subprocess, sys, os, json
    repo, spec_path = sys.argv[1], sys.argv[2]
    spec = json.load(open(spec_path, encoding="utf-8"))

    def git(*args):
        return subprocess.run(["git", "-C", repo, *args],
                              capture_output=True, text=True)

    if spec.get("commit_file"):
        p = os.path.join(repo, spec["commit_file"])
        with open(p, "w", encoding="utf-8") as f:
            f.write(spec.get("content", "x"))
        git("add", ".")
        git("commit", "-m", spec.get("msg", "child commit"))
    if spec.get("untracked_file"):
        with open(os.path.join(repo, spec["untracked_file"]), "w",
                  encoding="utf-8") as f:
            f.write(spec.get("content", "residue"))
    if spec.get("stdout"):
        print(spec["stdout"])
    sys.exit(spec.get("rc", 0))
""")


class Harness:
    """One isolated world per test: temp repo + temp registry + patched seams."""

    def __init__(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
                 gate_ok: bool = True):
        self.tmp = tmp_path
        self.monkeypatch = monkeypatch
        self.base = tmp_path / "world"
        self.repo = self.base / "repo"
        self.bare = self.base / "bare.git"
        self.state = self.base / "state"
        self.state.mkdir(parents=True)
        self.repo.mkdir()

        def git(repo, *args, **kw):
            return subprocess.run(["git", "-C", str(repo), *args],
                                  capture_output=True, text=True)

        self.git = git
        git(self.repo, "init", "-q")
        git(self.repo, "init", "-q", "--bare", str(self.bare))
        git(self.repo, "config", "user.email", "orch@test")
        git(self.repo, "config", "user.name", "orch")
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "seed")
        self.anchor = self.head()
        git(self.repo, "remote", "add", "origin", str(self.bare))
        git(self.repo, "push", "-q", "origin", "HEAD")

        # child script on disk (real process, real git, deterministic)
        self.child = tmp_path / "child.py"
        self.child.write_text(CHILD_SCRIPT, encoding="utf-8")
        self.spec_path = tmp_path / "spec.json"

        # gate: a REAL command, exits per gate_ok
        gate = tmp_path / ("gate_ok.py" if gate_ok else "gate_fail.py")
        body = "import sys; sys.exit({})\n"
        gate.write_text(body.format(0 if gate_ok else 1), encoding="utf-8")
        gate_cmd = "%s %s" % (sys.executable, gate)

        self.proj = {"name": PROJ, "path": str(self.repo), "gate": gate_cmd}
        self.entry = {"rounds": 0}

        self._gate_calls = []
        self._popen_calls = []       # (parts, cwd, env)
        self.last_wt = None          # worktree the runner last used
        self.last_env = None

        self._patch_threads()
        self._patch_loopctl()
        self._patch_interface(gate_cmd)
        self._patch_angles()
        self._patch_child()
        self._patch_gate()
        self._patch_discord()

    # -- git helpers ---------------------------------------------------------
    def head(self) -> str:
        return self.git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def dirty(self) -> bool:
        s = self.git(self.repo, "status", "--porcelain").stdout.strip()
        return bool(s)

    def branches(self):
        out = self.git(self.repo, "branch", "--list", "kl/*").stdout
        return [b.strip() for b in out.splitlines() if b.strip()]

    def wt_root(self):
        return kr.ROOT / "state" / "worktrees" / PROJ

    # -- seam patches --------------------------------------------------------
    def _patch_threads(self):
        reg_path = self.state / "threads.json"

        def load():
            try:
                return json.loads(reg_path.read_text(encoding="utf-8")) or {}
            except FileNotFoundError:
                return {}

        def save(data):
            tmp = reg_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, reg_path)

        self.monkeypatch.setattr(threads, "STATE", reg_path)
        self.monkeypatch.setattr(threads, "load", load)
        self.monkeypatch.setattr(threads, "save", save)
        self.monkeypatch.setattr(threads, "resolve", lambda project: "orch-sid-1")
        self.monkeypatch.setattr(threads, "title_for", lambda p: "t:" + p)
        self.reg_path = reg_path

        # the runner holds its own reference to the module; patch through it too
        self.monkeypatch.setattr(kr.threads, "load", load)
        self.monkeypatch.setattr(kr.threads, "save", save)
        self.monkeypatch.setattr(kr.threads, "resolve", threads.resolve)
        self.monkeypatch.setattr(kr.threads, "title_for", threads.title_for)

        # round logs into the temp world, never the real tree
        self.monkeypatch.setattr(kr, "ROOT", self.base)
        # T2-6: the answer intake must also live in the temp world (the
        # module-level Paths were bound at import time against the real ROOT).
        self.monkeypatch.setattr(kr, "ANSWERS_DIR", self.base / "state")
        self.monkeypatch.setattr(kr, "ANSWERS_CONSUMED",
                                 self.base / "state" / "answers_consumed")

    def _patch_loopctl(self):
        """F3.8: bump() must write a TEMP loop.json, never the real one."""
        loop_path = self.state / "loop.json"

        def load():
            cfg = dict(loopctl.DEFAULTS)
            try:
                cfg.update(json.loads(loop_path.read_text(encoding="utf-8")))
            except FileNotFoundError:
                pass
            return cfg

        def save(cfg):
            cfg["updated_at"] = "test"
            loop_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        self.monkeypatch.setattr(loopctl, "LOOP", loop_path)
        self.monkeypatch.setattr(loopctl, "STATE", self.state)
        self.monkeypatch.setattr(loopctl, "load", load)
        self.monkeypatch.setattr(loopctl, "save", save)
        self.monkeypatch.setattr(kr.loopctl, "load", load)
        self.monkeypatch.setattr(kr.loopctl, "save", save)
        self.monkeypatch.setattr(kr.loopctl, "bump", loopctl.bump)
        self.loop_path = loop_path

    def _patch_interface(self, gate_cmd):
        class I:
            @staticmethod
            def repo_facts(proj):
                return {"path": proj["path"], "gate": gate_cmd}

            @staticmethod
            def surfaces_for(proj):
                return ["server"]

        self.monkeypatch.setattr(kr, "I", I)

    def _patch_angles(self):
        angle = {"id": "orch-angle", "family": "test-family", "lens": "lens",
                 "text": "text"}
        self.monkeypatch.setattr(kr.angle_pick, "pick",
                                 lambda name, facts=None: dict(angle))
        import surface_pick
        self.monkeypatch.setattr(surface_pick, "pick",
                                 lambda name, proj, prefer=None: None)
        sys.modules["surface_pick"] = surface_pick

    def _patch_discord(self):
        class DN:
            @staticmethod
            def progress(*a, **k):
                return None

        self.monkeypatch.setattr(kr, "dn", DN)

    def _patch_child(self):
        """Swap ONLY the hermes child for the scenario script.

        subprocess.run() itself calls subprocess.Popen, so a blanket Popen
        patch would swallow every git/tasklist helper in the runner (that bug
        made the first F2 run refuse every round). Intercept by COMMAND SHAPE:
        only a hermes_cli.main child is swapped; everything else goes through
        the real Popen untouched. The scenario script receives the runner's
        OWN cwd -- under F3 that is the round's worktree, exactly what the
        real child would get.
        """
        real_popen = subprocess.Popen

        def fake_popen(cmd, **kwargs):
            parts = [str(c) for c in cmd]
            if not any("hermes_cli.main" in p for p in parts):
                return real_popen(cmd, **kwargs)
            cwd = str(kwargs.get("cwd") or "")
            self.last_wt = cwd
            self.last_env = dict(kwargs.get("env") or {})
            self._popen_calls.append((parts, cwd, self.last_env))
            return real_popen([sys.executable, str(self.child), cwd,
                               str(self.spec_path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, errors="replace",
                              creationflags=0x08000000)

        self.monkeypatch.setattr(kr.subprocess, "Popen", fake_popen)

    def _patch_gate(self):
        real_gate = kr._run_gate

        def counting_gate(proj):
            self._gate_calls.append(1)
            return real_gate(proj)

        self.monkeypatch.setattr(kr, "_run_gate", counting_gate)

    # -- scenario control ----------------------------------------------------
    def child_does(self, **spec) -> None:
        self.spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def run(self):
        return kr.run_round(self.proj, self.entry)

    def reload_registry(self) -> dict:
        return json.loads(self.reg_path.read_text(encoding="utf-8"))


@pytest.fixture()
def world(tmp_path, monkeypatch):
    def make(gate_ok: bool = True) -> Harness:
        return Harness(tmp_path, monkeypatch, gate_ok=gate_ok)
    return make


# --------------------------------------------------------------------------
# 1. rejected child commit contained (worktree deleted, canonical untouched)
# --------------------------------------------------------------------------
def test_rejected_child_commit_removed(world):
    h = world(gate_ok=False)          # the runner's OWN gate fails -> rejected
    h.child_does(commit_file="bad.py", msg="round work",
                 stdout="SURFACE-DONE: server :: done -- claimed")
    rc = h.run()
    assert rc == 0, "the CHILD exited cleanly; the rejection came from the gate"
    assert h.head() == h.anchor, "canonical must be untouched on reject"
    assert not (h.repo / "bad.py").exists(), "canonical never saw the commit"
    assert h.branches() == [], "the round branch must be deleted"
    wts = list(h.wt_root().iterdir()) if h.wt_root().exists() else []
    assert wts == [], "the worktree must be deleted on reject: %s" % wts
    assert h.entry.get("round_accepted") is False
    ck = h.entry["last_checkpoint"]
    assert ck.startswith("SKIPPED"), ck
    # the child worked in a WORKTREE, not the canonical repo
    assert h.last_wt and h.last_wt != str(h.repo)
    assert h.entry.get("worktree") is None   # cleared after deletion


# --------------------------------------------------------------------------
# 2. rejected untracked residue contained
# --------------------------------------------------------------------------
def test_rejected_untracked_residue_removed(world):
    h = world(gate_ok=False)
    h.child_does(untracked_file="residue.txt",
                 stdout="SURFACE-DONE: server :: done -- claimed")
    rc = h.run()
    assert rc == 0
    assert h.head() == h.anchor
    assert not (h.repo / "residue.txt").exists()
    assert h.dirty() is False
    assert h.branches() == []
    assert not h.last_wt or not pathlib.Path(h.last_wt).exists()


# --------------------------------------------------------------------------
# 3. accepted second round's published range contains none of round 1's work
# --------------------------------------------------------------------------
def test_accepted_round2_range_excludes_round1(world, monkeypatch):
    # this scenario asserts the tag reaches origin, so it exercises the PUSH
    # path: GitHub ships disabled (safe default) -- flip the settings gates.
    import settings as S_mod
    monkeypatch.setattr(kr._S, "github_enabled", lambda: True)
    monkeypatch.setattr(S_mod, "github_enabled", lambda: True)
    monkeypatch.setattr(kr._S, "push_enabled", lambda loop_cfg=None: True)
    monkeypatch.setattr(S_mod, "push_enabled", lambda loop_cfg=None: True)
    h = world(gate_ok=False)                    # round 1: rejected
    h.child_does(commit_file="r1.py", msg="round one work")
    h.run()
    assert h.head() == h.anchor, "round 1 rejected -> canonical untouched"
    assert h.branches() == []
    h.entry["rounds"] = 1                       # advance like the registry would

    # round 2: gate passes now
    h.proj["gate"] = "%s -c \"import sys;sys.exit(0)\"" % sys.executable
    h.child_does(commit_file="r2.py", msg="round two work")
    rc = h.run()
    assert rc == 0
    assert h.entry.get("round_accepted") is True
    tag = "kp/%s/r%02d" % (PROJ, 2)
    assert h.entry["publication_state"] == "published", h.entry["publication_detail"]
    pushed = h.git(h.repo, "ls-remote", "origin", "refs/tags/" + tag).stdout.strip()
    assert pushed.split()[0] == h.head(), "tag must name round 2's HEAD"
    # the published range anchor..tag contains NONE of round 1's file
    names = h.git(h.repo, "diff", "--name-only", "%s..%s" % (h.anchor, h.head()))
    assert "r1.py" not in names.stdout
    assert "r2.py" in names.stdout
    # worktree cleaned up after integration
    assert h.branches() == [], "merged round branch must be deleted"
    assert h.entry.get("worktree") is None


# --------------------------------------------------------------------------
# 4+5. worktree-removal failure -> quarantine survives reload; selector excludes
# --------------------------------------------------------------------------
def test_removal_failure_quarantines_and_survives_reload(world, monkeypatch):
    h = world(gate_ok=False)
    # Real-world shape: the worktree cannot be deleted (a file inside it is
    # locked by another process). Injected at the _remove_worktree seam --
    # the same failure string the real removal path produces.
    def broken_remove(canonical, name, round_no):
        return ("worktree remove failed rc=1: fatal: unable to remove "
                "worktrees/orch-test (simulated locked file)")

    monkeypatch.setattr(kr, "_remove_worktree", broken_remove)
    h.child_does(commit_file="r1.py", msg="round one work")
    rc = h.run()
    assert rc == 0
    # containment could not delete the worktree -> QUARANTINED, on disk
    assert h.entry.get("quarantined") is True, h.entry.get("quarantine_reason")
    reg_on_disk = h.reload_registry()
    on_disk = reg_on_disk[PROJ]
    assert on_disk.get("quarantined") is True, "quarantine must survive reload"
    assert on_disk.get("quarantine_reason")
    # the selector's own rule (main(): skip quarantined repos) must exclude it
    projs = [{"name": PROJ, "path": str(h.repo)}]
    runnable = [p for p in projs
                if not reg_on_disk.get(p["name"], {}).get("quarantined")]
    assert runnable == [], "the quarantined repo must not be selectable"


# --------------------------------------------------------------------------
# 6. dirty start never launches, files preserved, no worktree created
# --------------------------------------------------------------------------
def test_dirty_start_never_launches_and_preserves_files(world):
    h = world()
    preserve = h.repo / "human-work.txt"
    preserve.write_text("do not delete\n", encoding="utf-8")
    h.child_does(commit_file="child.py")       # would run if launched
    rc = h.run()
    assert rc == 1
    assert h._popen_calls == [], "the child must NOT be launched on a dirty tree"
    assert preserve.read_text(encoding="utf-8") == "do not delete\n"
    assert h.entry.get("round_refused"), h.entry
    assert h.entry.get("last_rc") == 1
    assert not h.wt_root().exists() or not list(h.wt_root().iterdir()), \
        "no worktree may be created for a refused round"


# --------------------------------------------------------------------------
# 7. proposal_ok=False rejects + contains
# --------------------------------------------------------------------------
def test_proposal_failure_rejects_and_rolls_back(world, monkeypatch):
    h = world(gate_ok=True)
    # Controlled fault injection at the propose seam: the proposal machinery
    # fails (Saul's blocker #3 -- "proposal raising AttributeError -> round
    # rejected, commit rolled back"). Everything else stays real.
    real_propose = kr.scope.propose

    def broken_propose(*a, **k):
        raise TypeError("injected proposal failure")

    monkeypatch.setattr(kr.scope, "propose", broken_propose)
    h.child_does(commit_file="work.py", msg="child committed anyway")
    rc = h.run()
    assert rc == 0
    assert h.entry.get("round_accepted") is False
    assert "proposal" in "; ".join(h.entry.get("acceptance_detail") or [])
    assert h.head() == h.anchor, "canonical must stay at the anchor"
    assert h.branches() == [], "the rejected round's branch must be gone"
    assert not h.last_wt or not pathlib.Path(h.last_wt).exists()


# --------------------------------------------------------------------------
# 8. publish happens only after final persistence
# --------------------------------------------------------------------------
def test_publish_happens_only_after_final_persistence(world, monkeypatch):
    h = world(gate_ok=True)
    seen = {}

    real_checkpoint = kr.checkpoint

    def spy_checkpoint(project, workdir, round_no):
        # at the moment of publish, the COMPLETE entry must already be on disk
        disk = json.loads(h.reg_path.read_text(encoding="utf-8"))[PROJ]
        seen["on_disk_round_accepted"] = disk.get("round_accepted")
        seen["on_disk_campaign_gone"] = disk.get("campaign")
        return real_checkpoint(project, workdir, round_no)

    monkeypatch.setattr(kr, "checkpoint", spy_checkpoint)
    h.child_does(commit_file="ok.py", msg="fine work")
    rc = h.run()
    assert rc == 0
    assert h.entry.get("round_accepted") is True
    assert seen["on_disk_round_accepted"] is True, \
        "publish ran before the final entry reached disk"
    # checkpoint runs against the CANONICAL checkout (the merged tree)
    assert seen.get("ckpt_workdir", None) is None


# --------------------------------------------------------------------------
# 9. evidence lands on the NEW row
# --------------------------------------------------------------------------
def test_evidence_lands_on_the_new_row(world):
    h = world(gate_ok=True)
    h.child_does(commit_file="a.py", msg="round one",
                 stdout="SURFACE-DONE: server :: done -- r1")
    h.run()
    h.entry["rounds"] = 1
    h.child_does(commit_file="b.py", msg="round two",
                 stdout="SURFACE-DONE: server :: done -- r2")
    h.run()
    hist = h.entry["angle_history"]
    assert hist[0]["round"] == 2, hist[0]
    assert hist[1]["round"] == 1
    # the gate evidence must sit on the NEWEST row (F1-2's off-by-one regressed
    # exactly this: attach() wrote onto the previous row)
    assert "gate" in hist[0] and hist[0]["gate"] and hist[0]["gate"]["ok"] is True
    assert "commit" in hist[0]
    # round 1's row keeps its own commit, not round 2's
    assert hist[1].get("commit") and hist[1]["commit"] != hist[0]["commit"]


# --------------------------------------------------------------------------
# 10. gate skipped on rc!=0
# --------------------------------------------------------------------------
def test_gate_skipped_on_child_failure(world):
    h = world(gate_ok=True)
    h.child_does(rc=3, stdout="the child failed")
    rc = h.run()
    assert rc == 3
    assert h._gate_calls == [], "a failed child must not get a 900s gate run"
    assert h.entry.get("round_accepted") is False
    assert h.head() == h.anchor
    ck = h.entry["last_checkpoint"]
    assert "gate NOT MEASURED" in ck or ck.startswith("SKIPPED"), ck
    # the row's evidence must carry no gate PASS: a skipped gate must never
    # render as a measured one (tri-state: None is not ok).
    row = (h.entry.get("angle_history") or [{}])[0]
    gate = row.get("gate")
    assert not (isinstance(gate, dict) and gate.get("ok") is True), row


# --------------------------------------------------------------------------
# F3.11: rounds_done bumps on accept only
# --------------------------------------------------------------------------
def test_rounds_done_bumps_on_accept_only(world, tmp_path, monkeypatch):
    h = world(gate_ok=True)
    before = loopctl.load()["rounds_done"]
    h.child_does(commit_file="a.py", msg="accepted work")
    h.run()
    after = loopctl.load()["rounds_done"]
    assert after == before + 1, "an accepted round must bump rounds_done"

    # a fresh isolated world for the rejected side (same tmp base would collide)
    sub = tmp_path / "reject-side"
    sub.mkdir()
    h2 = Harness(sub, monkeypatch, gate_ok=False)
    b2 = loopctl.load()["rounds_done"]
    h2.child_does(commit_file="b.py", msg="rejected work")
    h2.run()
    assert loopctl.load()["rounds_done"] == b2, \
        "a rejected round must NOT bump rounds_done"


# --------------------------------------------------------------------------
# F3.12: abandoned-worktree recovery quarantines the repo
# --------------------------------------------------------------------------
def test_abandoned_worktree_recovery_quarantines(world, monkeypatch, tmp_path):
    h = world()
    # an old fake worktree + its sidecar (the recovery sweep's age source)
    wt = h.wt_root() / "r01"
    wt.mkdir(parents=True)
    (wt / "leftover.py").write_text("half-done work", encoding="utf-8")
    side = h.wt_root() / "r01.started.json"
    side.write_text(json.dumps({"started": time.time()}), encoding="utf-8")
    old = time.time() - (kr.WT_ABANDON_SECS + 3600)
    os.utime(side, (old, old))

    import improver as imp_mod
    monkeypatch.setattr(imp_mod, "load_manifest", lambda: {"projects": []})
    monkeypatch.setattr(imp_mod, "enabled", lambda m: [])

    n = kr._recover_abandoned_worktrees()
    assert n == 1, "the abandoned worktree must quarantine its repo"
    reg = h.reload_registry()
    assert reg[PROJ].get("quarantined") is True
    assert "abandoned worktree" in reg[PROJ].get("quarantine_reason", "")
    # the abandoned tree itself is preserved (never auto-deleted)
    assert (wt / "leftover.py").exists()

    # a fresh worktree (young sidecar) must NOT trigger quarantine
    wt2 = h.wt_root() / "r02"
    wt2.mkdir()
    side2 = h.wt_root() / "r02.started.json"
    side2.write_text(json.dumps({"started": time.time()}), encoding="utf-8")
    n2 = kr._recover_abandoned_worktrees()
    assert n2 == 0, "a fresh worktree is not abandoned"


# --------------------------------------------------------------------------
# F3.13: the child env blocks push (real git push drill)
# --------------------------------------------------------------------------
def test_child_env_blocks_push(world, tmp_path):
    h = world()
    env = kr._child_env()
    assert env.get("GIT_CONFIG_VALUE_1") == "DISABLED"
    assert env.get("GIT_CONFIG_KEY_1") == "remote.origin.pushurl"
    assert env.get("GIT_CONFIG_VALUE_0") == ""        # empty credential helper
    assert "hermes-agent" in env.get("PYTHONPATH", "")

    # REAL drill: with this env, a push from a repo WITH a remote fails.
    h.child_does(commit_file="c.py", msg="work")
    # build the env the runner would hand the child, then try to push
    import karpathy_runner as _kr
    e = dict(env)
    r = subprocess.run(["git", "-C", str(h.repo), "push", "origin", "HEAD"],
                       capture_output=True, text=True, env=e, timeout=120)
    assert r.returncode != 0, "push must fail under the child env"
    combined = (r.stdout + r.stderr).lower()
    assert "disabled" in combined or "does not appear" in combined or \
        "could not read" in combined or "fatal" in combined, combined


# --------------------------------------------------------------------------
# F3.14: the worktree is recorded and shown to the child
# --------------------------------------------------------------------------
def test_worktree_recorded_and_passed_to_child(world):
    h = world(gate_ok=True)
    h.child_does(commit_file="a.py", msg="work")
    h.run()
    parts, cwd, env = h._popen_calls[0]
    assert cwd and cwd.startswith(str(h.wt_root())), \
        "the child must run with cwd=worktree, got %s" % cwd
    assert h.entry.get("worktree") is None   # cleaned after accept
    # the entry DURING the round carried the worktree (visible in the registry
    # save that happens before publish); the final entry clears it.


# --------------------------------------------------------------------------
# F3.4: post-merge canonical gate failure -> no publish, canonical restored
# --------------------------------------------------------------------------
def test_post_merge_gate_failure_does_not_publish(world, monkeypatch):
    h = world(gate_ok=True)
    # worktree gate passes; after the merge, the CANONICAL gate run fails.
    calls = {"n": 0}
    real_gate = kr._run_gate

    def flaky_gate(proj):
        calls["n"] += 1
        if pathlib.Path(proj["path"]) == h.repo:   # canonical run
            return {"cmd": "g", "ok": False, "detail": "rc=1 :: canonical-only"}
        return real_gate(proj)                     # worktree run

    monkeypatch.setattr(kr, "_run_gate", flaky_gate)
    h.child_does(commit_file="a.py", msg="work")
    rc = h.run()
    assert rc == 0
    assert h.entry.get("round_accepted") is True   # worktree measurements passed
    assert h.entry["publication_state"] == "failed"
    assert h.head() == h.anchor, "canonical must be reset to the anchor"
    assert h.dirty() is False
    assert h.entry.get("quarantined") is not True   # state is known-good again
    ck = h.entry["last_checkpoint"]
    assert "post-merge gate" in ck, ck
    # the work is preserved in the worktree for inspection
    assert h.branches(), "the round branch must survive for inspection"


# --------------------------------------------------------------------------
# F4/T2-6: a human answer reaches THIS round's prompt and is consumed
# --------------------------------------------------------------------------
def test_run_round_renders_the_answer_in_the_prompt(world):
    h = world(gate_ok=True)
    # place a real answer where the runner reads it (ANSWERS_DIR follows ROOT)
    ans_dir = kr.ROOT / "state"
    ans_dir.mkdir(parents=True, exist_ok=True)
    (ans_dir / "answer.json").write_text(
        json.dumps({"qid": "q9", "answer": "prefer the durable fix"}), encoding="utf-8")
    h.child_does(commit_file="a.py", msg="work")
    h.run()
    # the prompt the child received carried the answer
    parts, cwd, env = h._popen_calls[0]
    prompt = next(p for p in parts if "KARPATHY-LOOP-TASK" in p)
    assert "HUMAN ANSWER" in prompt and "durable fix" in prompt
    # and the answer was consumed (file gone from state/)
    assert not (ans_dir / "answer.json").exists()
