"""The sidebar must show the model the loop ACTUALLY runs on.

The round child is spawned with `--model <implementer seat>` every round;
the thread's sessions.model column is a creation-era artifact (the
2026-10-01 DeepSeek-display confusion: threads created in the DeepSeek era
kept showing deepseek-v4.1-flash while the work ran GLM-5.3-Flash-FP8).
threads.maintained() re-stamps the tip's model from loop.json each
watchdog tick -- config-driven, never hard-coded.
"""
import json
import sqlite3

import pytest

import threads


def _mk_world(tmp_path, monkeypatch, seat_model="GLM-5.3-Flash-FP8"):
    db = tmp_path / "state.db"
    c = sqlite3.connect(str(db))
    c.execute("create table sessions (id text primary key, model text,"
              " source text, title text, display_name text, hidden integer,"
              " last_activity_at real, parent_session_id text)")
    c.execute("insert into sessions values ('s1', 'deepseek-v4.1-flash',"
              " 'cron', 't', 't', 0, 0, null)")
    c.commit()
    c.close()
    monkeypatch.setattr(threads, "STATE_DB", db)
    monkeypatch.setattr(threads, "STATE", tmp_path / "threads.json")
    (tmp_path / "threads.json").write_text(
        json.dumps({"p1": {"session_id": "s1", "rounds": 0}}),
        encoding="utf-8")
    # loop.json: the SETTING the display must follow
    st = tmp_path / "state"
    st.mkdir(exist_ok=True)
    (st / "loop.json").write_text(
        json.dumps({"implementer": "custom:some-provider:%s" % seat_model}),
        encoding="utf-8")
    monkeypatch.setattr(threads, "ROOT", tmp_path)
    return db


def test_maintained_stamps_tip_model_from_seat(tmp_path, monkeypatch):
    db = _mk_world(tmp_path, monkeypatch)
    threads.maintained()
    c = sqlite3.connect(str(db))
    model = c.execute("select model from sessions where id='s1'").fetchone()[0]
    c.close()
    assert model == "GLM-5.3-Flash-FP8", \
        "the sidebar must show the model the loop actually runs on"


def test_maintained_model_stamp_follows_config_change(tmp_path, monkeypatch):
    db = _mk_world(tmp_path, monkeypatch)
    threads.maintained()
    # the operator changes the seat via loopctl config -> loop.json changes:
    st = tmp_path / "state"
    cfg = json.loads((st / "loop.json").read_text(encoding="utf-8"))
    cfg["implementer"] = "custom:other-provider:SomeNextModel"
    (st / "loop.json").write_text(json.dumps(cfg), encoding="utf-8")
    threads.maintained()
    c = sqlite3.connect(str(db))
    model = c.execute("select model from sessions where id='s1'").fetchone()[0]
    c.close()
    assert model == "SomeNextModel", \
        "the display must follow the configured seat, not a hard-coded model"


def test_maintained_no_seat_leaves_model_alone(tmp_path, monkeypatch):
    db = _mk_world(tmp_path, monkeypatch)
    st = tmp_path / "state"
    (st / "loop.json").write_text(json.dumps({"implementer": ""}),
                                  encoding="utf-8")
    threads.maintained()
    c = sqlite3.connect(str(db))
    model = c.execute("select model from sessions where id='s1'").fetchone()[0]
    c.close()
    assert model == "deepseek-v4.1-flash", \
        "no configured seat -> no stamping (never guess)"


def test_maintained_first_tick_changed_branch_survives(tmp_path, monkeypatch):
    """The T3-7 'changed' branch used to crash on a split-string SQL call
    (execute(sql, stray_string, params) -> TypeError), which aborted the
    whole heal pass and silently dropped the model stamp. This pins the
    fixed call: a first tick with changed=True must complete AND stamp."""
    db = _mk_world(tmp_path, monkeypatch)
    out = threads.maintained()
    assert out == {"p1": "s1"}, "the heal pass must not abort mid-loop"
    c = sqlite3.connect(str(db))
    model, src, hid = c.execute(
        "select model, source, hidden from sessions where id='s1'").fetchone()
    c.close()
    assert model == "GLM-5.3-Flash-FP8"
    assert src == "cron" and hid == 0
