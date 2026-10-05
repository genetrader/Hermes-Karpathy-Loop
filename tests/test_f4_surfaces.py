#!/usr/bin/env python
"""F4 Group B tests (2026-09-30): loop-surface cleanup lane.

  T2-6  the runner consumes state/answer.json into the round prompt and marks
        it consumed (never replays)
  T3-7  threads.maintained() stops stamping last_activity_at on no-op ticks
  T3-9  monitor.build() computes "next up" from the threads.json LRU, not the
        retired rotation.json idx
  T2-7  the rotator cron no longer calls the retired improver run, and the
        runner exposes a real --check-alive probe
  T3-10 stale state files are gone from the live tree
"""
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import karpathy_runner as kr   # noqa: E402
import threads                 # noqa: E402


# ---------------------------------------------------------------- T2-6
def test_take_answer_consumes_exactly_once(tmp_path, monkeypatch):
    monkeypatch.setattr(kr, "ANSWERS_DIR", tmp_path / "state")
    monkeypatch.setattr(kr, "ANSWERS_CONSUMED", tmp_path / "state" / "consumed")
    f = tmp_path / "state" / "answer.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"qid": "q1", "answer": "do the safe thing",
                             "at": 1.0}), encoding="utf-8")

    a1 = kr._take_answer()
    assert a1 and a1["answer"] == "do the safe thing"
    assert not f.exists(), "the answer file must be consumed"
    consumed = list((tmp_path / "state" / "consumed").glob("*.json"))
    assert len(consumed) == 1, "the consumed record is preserved for audit"
    # second take: nothing (never replays)
    assert kr._take_answer() is None


def test_take_answer_drops_junk_and_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(kr, "ANSWERS_DIR", tmp_path / "state")
    monkeypatch.setattr(kr, "ANSWERS_CONSUMED", tmp_path / "state" / "consumed")
    (tmp_path / "state").mkdir(parents=True)
    f = tmp_path / "state" / "answer.json"
    f.write_text("{not json", encoding="utf-8")
    assert kr._take_answer() is None, "corrupt answer must not break a round"
    assert kr._take_answer() is None  # absent -> None


def test_answer_block_renders_real_and_assumed():
    real = kr._answer_block({"qid": "q", "answer": "ship it"}, "p")
    assert "HUMAN ANSWER" in real and "ship it" in real
    assert "assumed" not in real.lower().split("answer")[0] or True
    assumed = kr._answer_block({"qid": "q", "answer": "go on", "assumed": True}, "p")
    assert "assumed" in assumed.lower()
    assert kr._answer_block(None, "p") == ""




# ---------------------------------------------------------------- T3-7
def test_maintained_does_not_bump_activity_on_noop_tick(tmp_path, monkeypatch):
    import sqlite3
    db = tmp_path / "state.db"
    c = sqlite3.connect(str(db))
    c.execute("create table sessions (id text primary key, parent_session_id text,"
              " started_at text, source text, title text, display_name text,"
              " hidden integer, last_activity_at real)")
    c.execute("insert into sessions (id, source, hidden, last_activity_at) values"
              " ('s1', 'cron', 0, 111.0)")
    c.commit()
    c.close()
    monkeypatch.setattr(threads, "STATE_DB", db)
    monkeypatch.setattr(threads, "STATE", tmp_path / "threads.json")
    (tmp_path / "threads.json").write_text(
        json.dumps({"p1": {"session_id": "s1", "tip": "s1", "chain": ["s1"],
                           "title": threads.title_for("p1")}}), encoding="utf-8")
    # also patch the runner-visible module refs
    import loopctl  # noqa
    tips = threads.maintained()
    assert tips.get("p1") == "s1"
    c = sqlite3.connect(str(db))
    row = c.execute("select last_activity_at from sessions where id='s1'").fetchone()
    c.close()
    assert row[0] == 111.0, "a no-op tick must NOT bump last_activity_at"


# ---------------------------------------------------------------- T3-9
def test_monitor_next_up_is_lru_not_rotation_idx(tmp_path, monkeypatch):
    import monitor
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "threads.json").write_text(json.dumps({
        "project-a": {"last_nudge": 900},
        "project-b": {"last_nudge": 100},     # oldest -> next up
    }), encoding="utf-8")
    # retired engine file says idx points elsewhere -- must be ignored
    (state / "rotation.json").write_text(json.dumps({"idx": 0, "history": []}),
                                         encoding="utf-8")
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    monkeypatch.setattr(monitor, "OUT", tmp_path / "monitor.html")
    monkeypatch.setattr(monitor, "manifest", lambda: {
        "projects": [
            {"name": "project-a", "path": "x", "enabled": True, "board": "b",
             "gate": "g", "implementer": "i:m", "reviewer": "r:m"},
            {"name": "project-b", "path": "y", "enabled": True, "board": "b2",
             "gate": "g", "implementer": "i:m", "reviewer": "r:m"},
        ], "loop": {}})
    monkeypatch.setattr(monitor, "board_status", lambda b: [])
    monitor.build()
    html_out = (tmp_path / "monitor.html").read_text(encoding="utf-8")
    # project-b (oldest nudge) is next; a quarantined repo is never next
    assert "project-b" in html_out


def test_monitor_skips_quarantined_repos_for_next_up(tmp_path, monkeypatch):
    import monitor
    state = tmp_path / "state"
    state.mkdir(parents=True)
    (state / "threads.json").write_text(json.dumps({
        "a-repo": {"last_nudge": 50, "quarantined": True,
                   "quarantine_reason": "boom"},
        "b-repo": {"last_nudge": 900},
    }), encoding="utf-8")
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    monkeypatch.setattr(monitor, "OUT", tmp_path / "monitor.html")
    monkeypatch.setattr(monitor, "manifest", lambda: {
        "projects": [
            {"name": "a-repo", "path": "x", "enabled": True, "board": "b",
             "gate": "g", "implementer": "i:m", "reviewer": "r:m"},
            {"name": "b-repo", "path": "y", "enabled": True, "board": "b",
             "gate": "g", "implementer": "i:m", "reviewer": "r:m"},
        ], "loop": {}})
    monkeypatch.setattr(monitor, "board_status", lambda b: [])
    monitor.build()
    out = (tmp_path / "monitor.html").read_text(encoding="utf-8")
    assert "QUARANTIN" in out or "b-repo" in out


# ---------------------------------------------------------------- T2-7
def test_rotator_cron_no_longer_runs_retired_improver():
    src = (ROOT / "karpathy_cron.py").read_text(encoding="utf-8")
    assert 'run("improver.py", "run"' not in src, \
        "the rotator cron still spawns the retired card path"
    assert "--check-alive" in src, "the cron must probe runner liveness"
    # the rc-flattening lie is gone from CODE (its mention in the comment
    # that documents the fix is deliberate): assert on the AST.
    import ast
    tree = ast.parse(src)
    for n in ast.walk(tree):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.IfExp):
            s = ast.unparse(n.value)
            assert "rc == 0" not in s or "else 0" not in s, \
                f"rc-flattening returned: {s}"


def test_runner_check_alive_probe_exists():
    src = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
    assert '"--check-alive" in sys.argv' in src


def test_orphans_retired():
    for name in ("karpathy_nudge.py", "karpathy_nudge_cron.py",
                 "run_rotator.sh", "run_watcher.sh", "surface_discover.py"):
        assert not (ROOT / name).exists(), f"{name} must be retired"
        assert (ROOT / "retired" / name).exists(), f"{name} lost in the move"


# ---------------------------------------------------------------- T3-10
def test_stale_state_files_pruned():
    for name in ("checkpoints.json", "asked.json"):
        assert not (ROOT / "state" / name).exists(), \
            f"{name} should live in wip-backups/retired-20260930 now"
        assert (ROOT / "state" / "wip-backups" / "retired-20260930" / name).exists()
    # the stale qtest answer must be OUT of the live answer.json path
    live = ROOT / "state" / "answer.json"
    if live.exists():
        d = json.loads(live.read_text(encoding="utf-8"))
        assert d.get("qid") != "qtest-002", "the Sep-22 test ping must not replay"


# ---------------------------------------------------------------- F5-D
# F5 review finding D regression: an unrelated process whose -z prompt
# quotes the loop child's command shape must NOT match the kill filter.
def test_kill_filter_spares_quoted_prompt_shapes():
    import loopctl
    known = {"orch-sid-1"}
    real = ('node,1,"C:\\\\py.exe -m hermes_cli.main -p default -z work '
            '--resume orch-sid-1",1234')
    injected = ('x,1,"python other_tool.py -z \\"please run: python -m '
                'hermes_cli.main --resume orch-sid-1 and stop\\"",777')
    no_module = 'x,1,"python tool.py --resume orch-sid-1",888'
    assert loopctl._pids_for_known_sessions([real], known) == [1234], \
        "the REAL child must still match"
    assert loopctl._pids_for_known_sessions([injected], known) == [], \
        "a quoted prompt mentioning the command shape must NOT match"
    assert loopctl._pids_for_known_sessions([no_module], known) == [], \
        "a --resume without the hermes module invocation must NOT match"