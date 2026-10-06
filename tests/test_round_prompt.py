import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import round_prompt


def test_includes_angle_and_gate():
    p = round_prompt.build("project-a",
                           angle={"id": "unsafe-none", "text": "Find the optional treated as guaranteed."},
                           gate="pytest -q", round_no=4, prior={"rounds": 3})
    assert "unsafe-none" in p
    assert "pytest -q" in p
    assert "round 4" in p.lower()
    # F1-6 (2026-09-30): the child must NOT tag -- the runner is the only
    # checkpoint author. The prompt now forbids child tags and names the tag
    # the RUNNER will take instead of instructing the child to create it.
    assert "Tag the accepted round" not in p
    assert "Do NOT create git tags" in p
    assert "kp/project-a/r04" in p


def test_reset_frame_beats_thread_memory():
    p = round_prompt.build("p", angle={"id": "x", "text": "y"}, gate="g", round_no=1, prior={})
    assert "authoritative" in p.lower()
    assert "the code wins" in p.lower()


def test_carried_context_only_when_prior_rounds():
    p0 = round_prompt.build("p", angle={"id": "x", "text": "y"}, gate="g", round_no=1, prior={})
    assert "CARRIED CONTEXT" not in p0
    p3 = round_prompt.build("p", angle={"id": "x", "text": "y"}, gate="g", round_no=4, prior={"rounds": 3})
    assert "CARRIED CONTEXT" in p3 and "3 prior round" in p3


def test_missing_angle_fields_do_not_crash():
    p = round_prompt.build("p", angle={}, gate="g", round_no=1, prior={})
    assert "unspecified" in p


def test_ask_clause_present():
    p = round_prompt.build("p", angle={"id": "x", "text": "y"}, gate="g", round_no=1, prior={})
    assert "ASK the human" in p
