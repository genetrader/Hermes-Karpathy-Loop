"""W5 regression: the runner must enforce max_rounds / max_hours ITSELF.

Found 2026-10-25: a 10-round supervised run dispatched 11 rounds and then kept
looping, because the caps were only checked by loopctl's CLI (status/pause) and
never by the runner's own dispatch loop. A cap that only the operator's command
line respects is decoration -- exactly the class of lie this PRD exists to kill.

These tests assert the guard exists in the runner source and behaves correctly,
without needing to spin a real runner.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "karpathy_runner.py"


def _loop_source() -> str:
    src = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            fn = node
            break
    assert fn is not None, "main() not found in karpathy_runner.py"
    return ast.get_source_segment(src, fn) or ""


def test_runner_reads_max_rounds_in_its_own_loop():
    loop = _loop_source()
    assert 'lc.get("max_rounds")' in loop, (
        "the runner never reads max_rounds -- the cap would be enforced only by "
        "loopctl, which is not running when the loop is"
    )


def test_runner_reads_max_hours_in_its_own_loop():
    loop = _loop_source()
    assert 'lc.get("max_hours")' in loop, "the runner never reads max_hours"


def test_runner_persists_a_pause_when_a_cap_is_reached():
    loop = _loop_source()
    # Reaching a cap must PAUSE (persist running=False + a reason), not merely
    # stop this process -- otherwise the next nudge resurrects the loop.
    assert 'paused_reason"] = "max_rounds reached' in loop
    assert 'paused_reason"] = (' in loop, "max_hours pause must record a reason"
    assert 'lc["running"] = False' in loop


def test_runner_cap_check_precedes_dispatch():
    """The cap must be checked BEFORE a project is picked and a child launched."""
    loop = _loop_source()
    cap_at = loop.index('lc.get("max_rounds")')
    dispatch_at = loop.index("pick_next_project")
    assert cap_at < dispatch_at, (
        "the max_rounds check must come before pick_next_project, or a capped "
        "run still launches one more round"
    )


def test_runner_cap_check_survives_a_bad_started_at():
    """A malformed started_at must not crash the loop -- skip the hours check."""
    loop = _loop_source()
    assert "max_hours check skipped" in loop
    assert "except (ValueError, TypeError)" in loop
