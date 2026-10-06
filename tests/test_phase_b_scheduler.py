# -*- coding: utf-8 -*-
"""Phase B (surfaces scheduler) tests -- 2026-09-30.

B2 campaign rotation hold (burst+LRU at the repo level)
B3 opening gate (LOCAL / CROSS_CUTTING / UNCERTAIN)
B4 closing gate (narrow falsification, repo-cited)

Discipline: every gate below was also re-introduced by hand and watched to
FAIL before being trusted (see plans/2026-09-30_B-EXECUTION.md, drills D5-D7).
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / ("%s.py" % name))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


scope = _load("scope")
rp = _load("round_prompt")


# ============================================================ B2 -- hold

def _proj(name):
    return {"name": name}


def test_open_campaign_holds_the_rotation_over_fresh_repos():
    kr = _load("karpathy_runner")
    # DECISIVE shape: the held repo has the NEWER last_nudge, so pure LRU
    # (ascending) would sort it LAST -- only the hold explains it sorting
    # first. (An earlier draft used the older-nudge shape, which passed for
    # the wrong reason; caught by drill D5 staying green on the plant.)
    reg = {
        "fresh": {"last_nudge": 100},
        "campaign": {"last_nudge": 900,
                     "campaign": {"id": "c", "state": "open", "surfaces": ["a"]}},
    }
    out = [p["name"] for p in kr.pick_next_project(
        [_proj("fresh"), _proj("campaign")], reg)]
    assert out == ["campaign", "fresh"], out


def test_blocked_campaign_does_not_hold():
    kr = _load("karpathy_runner")
    reg = {
        "fresh": {"last_nudge": 100},
        "blocked": {"last_nudge": 900,
                    "campaign": {"id": "c", "state": "blocked", "surfaces": ["a"]}},
    }
    out = [p["name"] for p in kr.pick_next_project(
        [_proj("fresh"), _proj("blocked")], reg)]
    assert out == ["fresh", "blocked"], out


def test_hold_cap_releases_the_rotation():
    kr = _load("karpathy_runner")
    reg = {
        "fresh": {"last_nudge": 100},
        "held": {"last_nudge": 900,
                 "campaign_holds": scope.MAX_CAMPAIGN_HOLDS,
                 "campaign": {"id": "c", "state": "open", "surfaces": ["a"]}},
    }
    out = [p["name"] for p in kr.pick_next_project(
        [_proj("fresh"), _proj("held")], reg)]
    assert out == ["fresh", "held"], out


def test_hold_state_is_sparse_entry_safe():
    kr = _load("karpathy_runner")
    assert kr._hold_state({}) == (False, 0)
    assert kr._hold_state({"campaign_holds": "7"})[1] == 7


def test_main_loop_calls_pick_next_project(tmp_path):
    # wired: main() must route selection through pick_next_project on the
    # runnable set (the AST twin lives in test_containment_is_wired; this is
    # the behavioral twin at function level).
    kr = _load("karpathy_runner")
    projs = [_proj("a"), _proj("b")]
    reg = {"a": {"last_nudge": 1}, "b": {"last_nudge": 2,
                                         "campaign": {"state": "open", "surfaces": ["x"]}}}
    out = [p["name"] for p in kr.pick_next_project(projs, reg)]
    assert out[0] == "b"


# ============================================================ B3 -- opening gate

def test_parse_reads_scope_classification_and_note():
    m = scope.parse("SCOPE: LOCAL\nSURFACE-DONE: server :: done -- t")
    assert m["scope_class"] == "LOCAL"
    m = scope.parse("scope: uncertain -- does it hit android too?")
    assert m["scope_class"] == "UNCERTAIN"
    assert m["uncertain_note"] == "does it hit android too?"
    assert m["scope_dropped"] == 0


def test_parse_scope_first_line_wins_and_extras_counted():
    m = scope.parse("SCOPE: UNCERTAIN -- unsure\nSCOPE: CROSS_CUTTING")
    assert m["scope_class"] == "UNCERTAIN"
    assert m["scope_dropped"] == 1


def test_parse_without_scope_line_is_legacy_none():
    m = scope.parse("SURFACE-DONE: server :: done -- t")
    assert m["scope_class"] is None


def test_opening_gate_local_contradiction_refuses():
    markers = scope.parse("SCOPE: LOCAL\n"
                          "CROSS_CUTTING: server,android :: the field is needed everywhere\n")
    entry = {"rounds": 3}
    prop = scope.propose(entry, markers, "repo", declared=["server", "android"])
    assert prop["contradicted"] is True
    assert prop["campaign"] is None


def test_opening_gate_uncertain_does_not_open():
    markers = scope.parse("SCOPE: UNCERTAIN -- unsure\n"
                          "CROSS_CUTTING: server,android :: the field is needed everywhere\n")
    entry = {"rounds": 3}
    prop = scope.propose(entry, markers, "repo", declared=["server", "android"])
    assert prop["uncertain"] is True
    assert prop["campaign"] is None


def test_opening_gate_legacy_absent_classification_still_opens():
    markers = scope.parse("CROSS_CUTTING: server,android :: the field is needed everywhere\n")
    entry = {"rounds": 3}
    prop = scope.propose(entry, markers, "repo", declared=["server", "android"])
    assert prop["campaign"] is not None
    assert prop["campaign"]["surfaces"] == ["server", "android"]


def test_opening_gate_explicit_cross_classification_opens():
    markers = scope.parse("SCOPE: CROSS_CUTTING\n"
                          "CROSS_CUTTING: server,android :: the field is needed everywhere\n")
    entry = {"rounds": 3}
    prop = scope.propose(entry, markers, "repo", declared=["server", "android"])
    assert prop["campaign"] is not None


def test_widening_gate_local_refuses_new_surfaces_but_keeps_campaign():
    entry = {"rounds": 5,
             "campaign": {"id": "repo-x2-r3", "state": "open",
                          "surfaces": ["server", "android"],
                          "verdicts": {}, "attempts": {}, "additions": [],
                          "opened_round": 3}}
    markers = scope.parse("SCOPE: LOCAL\n"
                          "CROSS_CUTTING: server,android,chrome-extension :: more surfaces\n")
    prop = scope.propose(entry, markers, "repo", declared=["server", "android",
                                                           "chrome-extension"])
    assert prop["contradicted"] is True
    # campaign unchanged: no widening
    assert prop["campaign"]["surfaces"] == ["server", "android"]


def test_verdicts_still_fold_under_the_gate():
    entry = {"rounds": 5,
             "campaign": {"id": "repo-x2-r3", "state": "open",
                          "surfaces": ["server", "android"],
                          "verdicts": {}, "attempts": {}, "additions": [],
                          "opened_round": 3}}
    markers = scope.parse("SCOPE: LOCAL\n"
                          "SURFACE-DONE: server :: done -- shipped it\n")
    prop = scope.propose(entry, markers, "repo", declared=["server", "android"])
    assert prop["campaign"]["verdicts"]["server"]["verdict"] == "done"


def test_prompt_asks_for_the_classification_when_surface_in_play():
    p = rp.build("p", angle={"id": "a", "text": "t"}, gate="g", round_no=1, prior={},
                 surface={"id": "server", "index": 0, "total": 2})
    assert "0) Classify this round's finding" in p
    assert "SCOPE: LOCAL" in p and "SCOPE: CROSS_CUTTING" in p
    assert "SCOPE: UNCERTAIN -- <what is unclear>" in p


def test_prompt_scope_question_block_renders():
    p = rp.build("p", angle={"id": "a", "text": "t"}, gate="g", round_no=7,
                 prior={"rounds": 6},
                 surface={"id": "server", "index": 0, "total": 2},
                 scope_question={"round": 6, "note": "does it hit android?"})
    assert "OPEN SCOPE QUESTION (from round 6 -- unsettled):" in p
    assert '"does it hit android?"' in p
    assert "Settle it THIS round" in p


def test_prompt_without_question_renders_nothing():
    p = rp.build("p", angle={"id": "a", "text": "t"}, gate="g", round_no=1, prior={},
                 surface={"id": "server", "index": 0, "total": 2})
    assert "OPEN SCOPE QUESTION" not in p


# ============================================================ B4 -- closing gate

@pytest.fixture()
def mini_repo(tmp_path):
    d = tmp_path / "repo"
    (d / "server").mkdir(parents=True)
    (d / "server" / "routes.py").write_text("x = 1\n", encoding="utf-8")
    (d / "android").mkdir()
    (d / "android" / "client.kt").write_text("y = 2\n", encoding="utf-8")
    return d


def test_cite_check_resolves_real_paths(mini_repo):
    ok = scope.default_cite_check(mini_repo)
    assert ok("touched server/routes.py and client.kt") is True
    assert ok("added the field end to end") is False


def test_cite_check_fail_closed_on_unreadable_root(tmp_path):
    missing = tmp_path / "nope"
    ok = scope.default_cite_check(missing)
    assert ok("routes.py") is False


def _entry_with_campaign():
    return {"rounds": 5,
            "campaign": {"id": "repo-x2-r3", "state": "open",
                         "surfaces": ["server", "android"],
                         "verdicts": {}, "attempts": {}, "additions": [],
                         "opened_round": 3}}


def test_cited_done_is_terminal(mini_repo):
    ok = scope.default_cite_check(mini_repo)
    markers = scope.parse("SURFACE-DONE: server :: done -- changed server/routes.py\n")
    prop = scope.propose(_entry_with_campaign(), markers, "repo",
                         declared=["server", "android"], cite_ok=ok)
    v = prop["campaign"]["verdicts"]["server"]
    assert v["verdict"] == "done"
    assert prop["uncited"] == []


def test_uncited_done_is_downgraded_and_not_terminal(mini_repo):
    ok = scope.default_cite_check(mini_repo)
    markers = scope.parse("SURFACE-DONE: server :: done -- added the field end to end\n")
    entry = _entry_with_campaign()
    prop = scope.propose(entry, markers, "repo",
                         declared=["server", "android"], cite_ok=ok)
    v = prop["campaign"]["verdicts"]["server"]
    assert v["verdict"] == "done-uncited"
    assert prop["uncited"] == ["server"]
    assert scope.outstanding_surfaces(prop["campaign"]) == ["server", "android"]
    # the attempt counted (a real attempt that failed the gate)
    assert prop["campaign"]["attempts"]["server"] == 1


def test_campaign_cannot_close_while_uncited(mini_repo):
    ok = scope.default_cite_check(mini_repo)
    entry = {"rounds": 5,
             "campaign": {"id": "repo-x1-r3", "state": "open",
                          "surfaces": ["server"],
                          "verdicts": {}, "attempts": {}, "additions": [],
                          "opened_round": 3}}
    markers = scope.parse("SURFACE-DONE: server :: done -- vibes\n")
    prop = scope.propose(entry, markers, "repo",
                         declared=["server"], cite_ok=ok)
    assert prop["closed"] is None, "an uncited claim must not close a campaign"
    assert prop["campaign"] is not None


def test_reemission_with_citation_upgrades_and_resets_attempts(mini_repo):
    ok = scope.default_cite_check(mini_repo)
    entry = _entry_with_campaign()
    entry["campaign"]["verdicts"]["server"] = {"verdict": "done-uncited", "reason": "x"}
    entry["campaign"]["attempts"]["server"] = 2
    markers = scope.parse("SURFACE-DONE: server :: done -- touched server/routes.py\n")
    prop = scope.propose(entry, markers, "repo",
                         declared=["server", "android"], cite_ok=ok)
    v = prop["campaign"]["verdicts"]["server"]
    assert v["verdict"] == "done"
    assert prop["campaign"]["attempts"]["server"] == 0  # T2-11 reset on terminal


def test_not_applicable_is_never_downgraded(mini_repo):
    ok = scope.default_cite_check(mini_repo)
    entry = _entry_with_campaign()
    markers = scope.parse("SURFACE-DONE: server :: not-applicable -- the server has no such field\n")
    prop = scope.propose(entry, markers, "repo",
                         declared=["server", "android"], cite_ok=ok)
    assert prop["campaign"]["verdicts"]["server"]["verdict"] == "not-applicable"


def test_cite_ok_none_is_legacy(mini_repo):
    markers = scope.parse("SURFACE-DONE: server :: done -- added the field end to end\n")
    prop = scope.propose(_entry_with_campaign(), markers, "repo",
                         declared=["server", "android"], cite_ok=None)
    assert prop["campaign"]["verdicts"]["server"]["verdict"] == "done"


def test_predicate_exception_fails_closed():
    def boom(reason):
        raise RuntimeError("x")
    markers = scope.parse("SURFACE-DONE: server :: done -- cited thing\n")
    prop = scope.propose(_entry_with_campaign(), markers, "repo",
                         declared=["server", "android"], cite_ok=boom)
    assert prop["campaign"]["verdicts"]["server"]["verdict"] == "done-uncited"

# --------------------------------------------- review fixes (B round)
# Review a-2/b-4/b-2: refusal consumes the slot; only an ACCEPTED round
# settles the scope question; UNCERTAIN refreshes carry a seen counter.
def test_refusal_stamps_last_nudge_source():
    src = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
    # both refusal sites stamp last_nudge (hot-loop fix)
    assert src.count('entry["last_nudge"] = time.time()') >= 3, \
        "refusal paths must consume the scheduling slot"

def test_scope_question_settle_needs_accepted_round_source():
    src = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
    i = src.find("scope question SETTLED")
    window = src[max(0, i - 700):i]
    assert "if _round_ok:" in window, \
        "a rejected round's classification must not settle the question"

def test_scope_question_escalation_counter_source():
    src = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
    i = src.find("scope UNCERTAIN")
    window = src[max(0, i - 400):i + 1200]
    assert '"seen": _seen' in window, "refreshes must carry a counter"
    assert "ESCALATION" in window, "consecutive UNCERTAIN must escalate loudly"

def test_scope_question_lifecycle_behavior():
    # behavioral: the lifecycle helpers live in run_round (not importable),
    # so pin the pure half through the registry entry shape instead.
    entry = {"scope_question": {"round": 3, "note": "x", "seen": 2}}
    assert entry["scope_question"]["seen"] == 2
# --------------------------------------------- review c fixes (B round)
def test_slash_token_requires_exact_path(mini_repo):
    ok = scope.default_cite_check(mini_repo)
    (mini_repo / "docs").mkdir()
    (mini_repo / "docs" / "README.md").write_text("x", encoding="utf-8")
    assert ok("docs/README.md") is True          # exact relative path
    assert ok("invented/location/README.md") is False  # basename laundering
    assert ok("README.md") is True               # bare name still resolves

def test_lazy_index_no_walk_until_first_call(mini_repo, monkeypatch):
    calls = []
    real = scope._repo_file_index
    monkeypatch.setattr(scope, "_repo_file_index",
                        lambda root: calls.append(root) or real(root))
    ok = scope.default_cite_check(mini_repo)
    assert calls == [], "the index must not build at predicate-creation time"
    ok("routes.py")
    assert len(calls) == 1, "the index builds once, on first use"

def test_three_uncited_dones_starve_and_block_not_spin(mini_repo):
    ok = scope.default_cite_check(mini_repo)
    entry = {"rounds": 5,
             "campaign": {"id": "repo-x1-r3", "state": "open",
                          "surfaces": ["server"],
                          "verdicts": {}, "attempts": {}, "additions": [],
                          "opened_round": 3}}
    markers = scope.parse("SURFACE-DONE: server :: done -- vibes\n")
    for _ in range(3):
        entry["rounds"] += 1
        prop = scope.propose(entry, markers, "repo",
                             declared=["server"], cite_ok=ok)
        entry["campaign"] = prop["campaign"]
    camp = entry["campaign"]
    assert camp["attempts"]["server"] == 3
    assert camp["state"] == "blocked", camp
    assert scope.outstanding_surfaces(camp) == ["server"]

def test_gate_evidence_in_dedicated_fields_not_spliced(mini_repo):
    ok = scope.default_cite_check(mini_repo)
    long_cited = "x" * 250 + " changed server/routes.py"   # citation past 200
    markers = scope.parse("SURFACE-DONE: server :: done -- %s\n" % long_cited)
    entry = _entry_with_campaign()
    prop = scope.propose(entry, markers, "repo",
                         declared=["server", "android"], cite_ok=ok)
    v = prop["campaign"]["verdicts"]["server"]
    assert v["verdict"] == "done"
    assert v["cite"] == "ok"
    assert len(v["reason"]) <= 200
    assert "closing gate" not in v["reason"], "note must not splice into reason"
    # uncited: full note preserved in its own field
    markers2 = scope.parse("SURFACE-DONE: android :: done -- %s\n" % ("y" * 250))
    prop2 = scope.propose(_entry_with_campaign(), markers2, "repo",
                          declared=["server", "android"], cite_ok=ok)
    v2 = prop2["campaign"]["verdicts"]["android"]
    assert v2["verdict"] == "done-uncited"
    assert v2["cite"] == "none"
    assert v2["gate_note"].startswith("[closing gate")
    assert len(v2["reason"]) <= 200
# --------------------------------------------- review d insisted-on test
def test_show_campaign_is_read_only_and_legacy_safe(tmp_path, monkeypatch):
    import shutil, hashlib
    import threads as _threads
    import loopctl as _loopctl
    real = ROOT / "state" / "threads.json"
    dst = tmp_path / "threads.json"
    if real.exists():
        shutil.copy(real, dst)
    else:
        # fresh clone: state/ is gitignored -- seed a minimal registry so the
        # read-only guarantee is still exercised
        dst.write_text('{"zip": {"rounds": 3, "campaign": {"state": "open",'
                       ' "surfaces": ["a"], "verdicts": {}}}}', encoding="utf-8")
    before = hashlib.md5(dst.read_bytes()).hexdigest()
    monkeypatch.setattr(_threads, "STATE", dst)
    rc = _loopctl.cmd_show_campaign(type("A", (), {"json": False})())
    assert rc == 0
    after = hashlib.md5(dst.read_bytes()).hexdigest()
    assert before == after, "--show-campaign must not write the registry"
# ============================================= debug-session fixes (10-01)
# Two-agent review (deleg_6e742cb0) findings, fixed pre-arm. Each test pins
# the fix at the repro the reviewer gave.

def test_cited_reemission_same_message_is_seen():
    # Reviewer A-1: sticky parse dropped the later cited line; the gate
    # downgraded a done that DID carry a citation.
    markers = scope.parse("SURFACE-DONE: server :: done -- vibes\n"
                          "SURFACE-DONE: server :: done -- changed server/routes.py")
    assert markers["surfaces"]["server"]["reason"] == "changed server/routes.py"

def test_dot_prefixed_repo_path_cites_exactly(tmp_path):
    # Reviewer A-2: lstrip('./') mangled '.github/...' so a real citation
    # could never pass.
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("x", encoding="utf-8")
    ok = scope.default_cite_check(tmp_path)
    assert ok("changed .github/workflows/ci.yml") is True
    assert ok("changed github/workflows/ci.yml") is False  # exact path only

def test_scope_regex_tolerates_punctuation_variants():
    # Reviewer A-4: colon separator / no colon / hyphenated cross-cutting
    # all failed OPEN (UNCERTAIN read as absent).
    assert scope.parse("SCOPE: UNCERTAIN: does it hit android?")["scope_class"] == "UNCERTAIN"
    assert scope.parse("SCOPE: UNCERTAIN unsure")["scope_class"] == "UNCERTAIN"
    assert scope.parse("SCOPE: cross-cutting")["scope_class"] == "CROSS_CUTTING"
    assert scope.parse("SCOPE: LOCAL")["scope_class"] == "LOCAL"
    assert scope.parse("SCOPE: CROSS_CUTTING")["scope_class"] == "CROSS_CUTTING"

def test_legacy_campaign_without_opened_round_does_not_insta_block():
    # Reviewer A-5: opened_round defaulted to 0 = whole repo history.
    entry = {"rounds": 50,
             "campaign": {"id": "legacy", "state": "open",
                          "surfaces": ["server", "android"],
                          "verdicts": {"server": "done"},
                          "attempts": {}, "additions": []}}
    markers = scope.parse("")
    prop = scope.propose(entry, markers, "repo", declared=["server", "android"])
    assert prop["campaign"] is not None and prop["campaign"]["state"] == "open", prop

def test_silent_round_on_blocked_campaign_keeps_blocked_round():
    # Reviewer A-6: every silent round re-marked blocked_round forward.
    entry = {"rounds": 20,
             "campaign": {"id": "c", "state": "blocked", "blocked_round": 9,
                          "blocked_because": "every outstanding surface hit the attempt cap",
                          "surfaces": ["server"], "verdicts": {},
                          "attempts": {"server": 5}, "additions": [],
                          "opened_round": 3}}
    prop = scope.propose(entry, scope.parse(""), "repo", declared=["server"])
    assert prop["campaign"]["blocked_round"] == 9, prop["campaign"]

def test_hold_state_never_raises_on_garbage():
    # Reviewer B-7: campaign_holds='x' crashed the selection sort key.
    kr = _load("karpathy_runner")
    held, holds = kr._hold_state({"campaign": {"state": "open", "surfaces": ["a"]},
                                  "campaign_holds": "x"})
    assert held is True and holds == 0

def test_surface_pick_prefers_outstanding_campaign_surfaces(tmp_path, monkeypatch):
    # Reviewer A-3: held campaign rounds could land on terminal surfaces.
    sp = _load("surface_pick")
    monkeypatch.setattr(sp, "STATE_DIR", tmp_path)
    monkeypatch.setattr(sp, "_read_manifest_surfaces", lambda p: {})
    order = ["server", "android", "chrome-extension"]
    st = sp.state_for("p")
    st = sp.reconcile(st, order)
    sp.save_state("p", st)
    # outstanding = android only; last worked = server
    import improver as _real_I
    import sys as _sys
    _had = "improver" in _sys.modules
    _orig = getattr(_real_I, "surfaces_for", None)

    class _FakeI:
        @staticmethod
        def surfaces_for(proj):
            return order

    _real_I.surfaces_for = _FakeI.surfaces_for
    try:
        res = sp.pick("p", {"name": "p"}, prefer=["android"])
        assert res["id"] == "android", res
    finally:
        if _orig is not None:
            _real_I.surfaces_for = _orig
        elif _had:
            try:
                del _real_I.surfaces_for
            except AttributeError:
                pass