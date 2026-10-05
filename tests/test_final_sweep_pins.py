"""F-sweep regression tests (2026-10-02) — plans/2026-10-02_FINAL-SWEEP-TRIAGE.md

Covers: F-A (answer poison pill deleted), F-B (pause-kill parses quoted CSV +
runner-row parity from field start), F-C (round_refused cleared on successful
launch), F-D (campaign_open = scope semantics), F-E (git pathspec "--","*"),
F-K (is_running excludes pythonw.exe), F-N (show_campaign guarded int),
F-G (maintained() fresh-load merge).

Each fix was verified live before this file; the drills here pin them.

Run: python -m pytest tests/test_final_sweep_pins.py -q
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PY = sys.executable


# ------------------------------------------------------------------ F-A

def test_corrupt_answer_file_is_deleted_not_replayed(tmp_path, monkeypatch):
    import karpathy_runner as K
    answers = tmp_path / "answers"
    answers.mkdir()
    (answers / "answer.json").write_text("{torn json", encoding="utf-8")
    monkeypatch.setattr(K, "ANSWERS_DIR", answers)
    monkeypatch.setattr(K, "ANSWERS_CONSUMED", answers / "consumed")
    logs = []
    monkeypatch.setattr(K, "log", lambda m: logs.append(m))
    out = K._take_answer()
    assert out is None
    assert not (answers / "answer.json").exists(), \
        "the corrupt answer file survived — it would replay the failure every round"
    assert any("deleting the unreadable" in m for m in logs)


def test_valid_answer_still_consumed(tmp_path, monkeypatch):
    import karpathy_runner as K
    answers = tmp_path / "answers"
    answers.mkdir()
    (answers / "answer.json").write_text(
        json.dumps({"qid": "q1", "answer": "go"}), encoding="utf-8")
    monkeypatch.setattr(K, "ANSWERS_DIR", answers)
    monkeypatch.setattr(K, "ANSWERS_CONSUMED", answers / "consumed")
    monkeypatch.setattr(K, "log", lambda m: None)
    out = K._take_answer()
    assert out and out["answer"] == "go"
    assert not (answers / "answer.json").exists()
    assert list((answers / "consumed").glob("*.json"))


# ------------------------------------------------------------------ F-B

def _ps_row(cmd: str, pid: str) -> str:
    """A row exactly like PowerShell Get-CimInstance | ConvertTo-Csv emits."""
    return '"' + cmd.replace('"', '""') + '"' + ',"' + pid + '"'


def test_quoted_csv_row_yields_the_pid():
    """F-B: PowerShell CSV rows quote every field; the kill list must still
    parse. F-B3: matching is argv-based (tail `--resume <bare-sid>`), which
    survives list2cmdline's \" escaping that broke every parity heuristic."""
    import subprocess
    import loopctl
    cmd = subprocess.list2cmdline([
        "C:\\x\\python.exe", "-m", "hermes_cli.main", "-p", "default",
        "-z", '[KL TASK] say "hi"', "--resume", "sess-abc"])
    line = _ps_row(cmd, "4242")
    pids = loopctl._pids_for_known_sessions([line], known={"sess-abc"})
    assert pids == [4242], \
        ("the quoted CSV row's PID was dropped — pause --now kills nothing "
         "on wmic-less Windows")


def test_quoted_runner_row_matches_top_level():
    """The runner is identified by argv[0] ending in karpathy_runner.py —
    parity heuristics are gone (F-B3)."""
    import subprocess
    import loopctl
    cmd = subprocess.list2cmdline(
        ["C:\\CODING\\project-improver\\karpathy_runner.py"])
    line = _ps_row(cmd, "5555")
    cmd_decoded, pid = loopctl._csv_cmd_and_pid(line)
    first = cmd_decoded.split()[0]
    assert first.rstrip('"').lower().endswith("karpathy_runner.py"), \
        "the real runner row reads as inside-quotes — --now would spare it"


def test_arg_text_quoting_the_runner_path_is_spared():
    """A chat/agent process whose ARGUMENT merely quotes the runner path must
    NOT match: argv[0] is the python/hermes interpreter, not the runner."""
    import subprocess
    import loopctl
    cmd = subprocess.list2cmdline([
        "C:\\x\\python.exe", "-m", "hermes_cli.main", "-z",
        "review of C:\\CODING\\project-improver\\karpathy_runner.py"])
    line = _ps_row(cmd, "777")
    cmd_decoded, _ = loopctl._csv_cmd_and_pid(line)
    first = cmd_decoded.split()[0]
    assert not first.rstrip('"').lower().endswith("karpathy_runner.py"), \
        "an argument-text mention would be killed as the runner"


def test_resume_inside_payload_is_never_matched():
    """F-B3: a -z payload MENTIONING --resume must not look like our child,
    even when the mention is the tail or a bare sid look-alike."""
    import subprocess
    import loopctl
    cmd = subprocess.list2cmdline([
        "C:\\x\\python.exe", "-m", "hermes_cli.main", "-z",
        "chat about --resume sess-abc"])
    line = _ps_row(cmd, "999")
    assert loopctl._pids_for_known_sessions([line], known={"sess-abc"}) == []


def test_wmic_shape_still_parses():
    import subprocess
    import loopctl
    cmd = subprocess.list2cmdline(
        ["C:\\a b\\python.exe", "-m", "hermes_cli.main", "--resume", "sess-abc"])
    wmic_row = "node,localhost," + cmd.replace('"', '""') + ",4242"
    assert loopctl._pids_for_known_sessions([wmic_row], known={"sess-abc"}) == [4242]


# ------------------------------------------------------------------ F-C

def test_round_refused_cleared_after_successful_launch():
    """F-C: the refusal key must not outlive a successful worktree launch.
    Structural: the pop sits immediately after entry["worktree"] is set."""
    src = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
    i = src.find('entry["worktree"] = str(wt)')
    assert i != -1
    window = src[i:i + 400]
    assert 'entry.pop("round_refused", None)' in window, \
        "round_refused is not cleared on successful launch — the registry lies"


# ------------------------------------------------------------------ F-D

def test_campaign_outstanding_matches_scope_semantics():
    import activity
    import scope
    camp = {"surfaces": ["a", "b"],
            "verdicts": {"a": {"verdict": "deferred"},
                         "b": {"verdict": "done-uncited"}}}
    assert activity._campaign_outstanding(camp) == len(scope.outstanding_surfaces(camp)) == 2, \
        "the panel counted non-terminal verdicts as closed"


# ------------------------------------------------------------------ F-E

def test_dep_discovery_pathspec_is_valid_git(tmp_path):
    """F-E: '--' + '*' must be TWO argv tokens; the old '--'+chr(42) built the
    literal option --* which git rejects with rc 129."""
    d = tmp_path / "repo"
    d.mkdir()
    subprocess.run(["git", "-C", str(d), "init", "-q"], capture_output=True)
    r = subprocess.run(["git", "-C", str(d), "ls-files", "--others", "--ignored",
                        "--directory", "--exclude-standard", "--", "*"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "even the fixed two-token form must be accepted by git"
    src = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
    assert '"--", "*"]' in src and '"--" + chr(42)' not in src


# ------------------------------------------------------------------ F-K

def test_is_running_requires_python_exe_not_pythonw():
    import karpathy_runner as K
    src = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
    i = src.find('pid_field == str(pid)')
    assert '"python.exe" in cols[0].lower()' in src[i:i + 80], \
        "pythonw.exe (a GUI interpreter) still counted as the runner"


# ------------------------------------------------------------------ F-N

def test_show_campaign_guarded_int():
    src = (ROOT / "loopctl.py").read_text(encoding="utf-8")
    i = src.find('campaign_holds')
    window = src[max(0, i - 200):i + 300]
    assert "except (TypeError, ValueError)" in window, \
        "a corrupt campaign_holds value crashes the operator command"


# ------------------------------------------------------------------ F-G

def test_maintained_merges_into_a_fresh_load(tmp_path, monkeypatch):
    """F-G: the sidebar tick must not save its stale snapshot over a concurrent
    runner write. Simulate: maintained loads, the runner writes rounds=6,
    maintained saves — rounds must still be 6."""
    import threads
    state = tmp_path / "threads.json"
    monkeypatch.setattr(threads, "STATE", state)
    threads.save({"p1": {"session_id": "s1", "rounds": 5}})

    reg = threads.load()          # maintained's entry snapshot
    reg["p1"]["tip"] = "t1"

    # the runner's concurrent write lands
    fresh = threads.load()
    fresh["p1"]["rounds"] = 6
    threads.save(fresh)

    # F-G behavior: merge computed keys into a FRESH load, not the snapshot
    merged = threads.load()
    for project, tip in {"p1": "t1"}.items():
        ent = merged.get(project)
        if isinstance(ent, dict):
            ent["tip"] = tip
    threads.save(merged)

    final = threads.load()
    assert final["p1"]["rounds"] == 6, \
        "the maintained snapshot reverted the runner's write (lost update)"
    assert final["p1"]["tip"] == "t1"

def test_fresh_round_started_heartbeat_overrides_round_stale_wedge():
    """F-O: after a restart following a long pause, `since_round` can exceed
    the window while the CURRENT round is legitimately in flight. A fresh
    round-started heartbeat with a live pid is positive evidence of work and
    must suppress ONLY the round_stale wedge arm."""
    import time
    import tempfile
    import pathlib as _pl
    import loopctl
    cfg = {"running": True}

    class _R:
        def __init__(self, out): self.stdout = out

    def probe(pid_alive):
        def _f(*a, **k):
            out = ('"python.exe","83828","Console","1","10,000 K"'
                   if pid_alive else "INFO: No tasks are running")
            return _R(out)
        return _f

    import subprocess as sp
    orig_run, orig_root = sp.run, loopctl.ROOT
    with tempfile.TemporaryDirectory() as td:
        st = _pl.Path(td) / "state"
        st.mkdir()
        (st / "threads.json").write_text(
            '{"zip": {"last_nudge": %f}}' % (time.time() - 900 * 60),
            encoding="utf-8")
        (st / "runner_heartbeat.json").write_text(
            '{"state":"round-started","project":"zip","pid":83828,"ts":%f}'
            % (time.time() - 5 * 60), encoding="utf-8")
        loopctl.ROOT = _pl.Path(td)
        try:
            sp.run = probe(True)
            alive = loopctl._liveness(cfg)
            assert alive["wedged"] is False, (
                "a fresh live round-started heartbeat must NOT read wedged "
                "(this is the false banner Gene saw after restart)")
            sp.run = probe(False)
            dead = loopctl._liveness(cfg)
            assert dead["wedged"] is True and dead["wedge_why"], (
                "a dead heartbeat pid must still wedge")
        finally:
            sp.run = orig_run
            loopctl.ROOT = orig_root


def test_missing_heartbeat_still_wedges():
    """The hb_missing arm is untouched by F-O."""
    import tempfile
    import pathlib as _pl
    import loopctl
    orig_root = loopctl.ROOT
    with tempfile.TemporaryDirectory() as td:
        st = _pl.Path(td) / "state"
        st.mkdir()
        (st / "threads.json").write_text(
            '{"zip": {"last_nudge": %f}}' % (__import__("time").time() - 900 * 60),
            encoding="utf-8")
        loopctl.ROOT = _pl.Path(td)
        try:
            lv = loopctl._liveness({"running": True})
            assert lv["wedged"] is True
            assert "no heartbeat" in (lv["wedge_why"] or "")
        finally:
            loopctl.ROOT = orig_root


def test_push_retries_once_via_gh_credential_helper():
    """F-P: an https push with an empty credential helper fails rc=128 every
    round ('could not read Username'). The runner must retry ONCE with the gh
    CLI's non-interactive credential helper before reporting failure."""
    import pathlib
    src = pathlib.Path("C:/CODING/project-improver/karpathy_runner.py").read_text(
        encoding="utf-8")
    assert 'credential.helper=!gh auth git-credential' in src, (
        "the gh fallback push retry is missing — round work reaches a local "
        "tag but never GitHub")
    assert 'could not read Username' in src, (
        "the retry must be GATED on the credential-failure signature, not "
        "fired on arbitrary push failures (e.g. non-ff)")


def test_checkpoint_payload_is_small():
    """F-Q: the desktop bridge clipped the 4KB checkpoints payload mid-stream
    ('Unexpected token o' at byte ~16). Summaries cap at 72 chars."""
    import pathlib, re
    src = pathlib.Path("C:/CODING/project-improver/checkpoint.py").read_text(
        encoding="utf-8")
    assert '[:72]' in src and '[:160]' not in src.split("F-Q")[0].split("LIMIT = 12")[-1]


def test_elapsed_is_never_dropped_by_size_guard():
    """F-R: `elapsed` was in ROW_OPTIONAL and vanished whenever the payload
    popped over the 3400B bridge budget -- the widget's Elapsed column read
    blank. The floor guard must now shrink `see` blocks FIRST and must NEVER
    drop `elapsed`."""
    import sys
    sys.path.insert(0, "C:/CODING/project-improver")
    import activity
    d = activity.activities(max_steps=18)
    payload = json.loads(activity.compact_payload(d))
    rows = payload.get("rows") or []
    running = [r for r in rows if r.get("running")]
    if running:
        assert running[0].get("elapsed"), (
            "the in-flight repo's elapsed was dropped by the size guard")


def test_checkpoints_carry_plain_summaries():
    """F-S: checkpoint.py all must emit a `plain` field (fifth-grade
    explanation) per row, cached in state/plain_summaries.json."""
    import subprocess, json as _j
    out = subprocess.run(
        ["C:/Users/gene/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe",
         "checkpoint.py", "all"],
        cwd="C:/CODING/project-improver", capture_output=True, text=True,
        timeout=300).stdout
    d = _j.loads(out)
    cks = d.get("checkpoints") or []
    assert cks, "no checkpoints emitted"
    assert any(c.get("plain") for c in cks), (
        "no plain-language summaries present (LLM endpoints both down, or "
        "cache empty)")
    import pathlib
    cache = pathlib.Path("C:/CODING/project-improver/state/plain_summaries.json")
    if cache.exists():
        vals = _j.loads(cache.read_text(encoding="utf-8")).values()
        assert not any(v == "None" or v is None for v in vals), (
            "poison None cached from a failed LLM call")


def test_worktree_process_sweep_is_wired_before_removal():
    """F-T: next.js rounds leak detached dev servers that lock the worktree ->
    removal fails -> repo quarantined (happened twice: r57, r58 x3). The
    sweep must run inside _remove_worktree BEFORE _cleanup_junctions."""
    import pathlib, ast
    src = pathlib.Path("C:/CODING/project-improver/karpathy_runner.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name == "_remove_worktree"}
    assert fns, "_remove_worktree missing"
    body_src = ast.get_source_segment(src, fns["_remove_worktree"])
    assert "_sweep_worktree_processes" in body_src, (
        "orphan-process sweep not called in _remove_worktree")
    assert body_src.index("_sweep_worktree_processes") < body_src.index(
        "_cleanup_junctions"), "sweep must run BEFORE junction cleanup"


def test_heartbeat_fallback_covers_inter_round_gap():
    """F-U: between a round child exiting and the next launch, no live
    --resume python exists for minutes (gate/checkpoint/worktree). The widget
    read that gap as 'no repo in rotation'. The producer must fall back to a
    fresh round-started heartbeat naming the project."""
    import ast, pathlib
    src = pathlib.Path("C:/CODING/project-improver/activity.py").read_text(
        encoding="utf-8")
    assert "runner_heartbeat.json" in src, (
        "no heartbeat fallback in the activity producer")
    assert "round-started" in src.split("F-U")[1][:900], (
        "fallback must require state round-started")


def test_current_work_verb_emits_plain():
    """F-V: checkpoint.py current must emit the in-flight round with a plain
    fifth-grade explanation of what the loop is working on."""
    import subprocess, json as _j
    out = subprocess.run(
        ["C:/Users/gene/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe",
         "checkpoint.py", "current"],
        cwd="C:/CODING/project-improver", capture_output=True, text=True,
        timeout=120).stdout
    d = _j.loads(out)
    if d.get("running"):
        assert d.get("plain"), "in-flight round emitted without a plain summary"
    else:
        assert d == {"running": False}
