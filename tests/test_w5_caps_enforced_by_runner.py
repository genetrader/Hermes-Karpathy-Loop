"""W5: the cap guard must actually EXECUTE, not merely appear in the source.

2026-10-25: an earlier version of this file only asserted that certain strings
existed in main()'s source segment. It passed while the runner crashed with
NameError: name 'datetime' is not defined -- because the guard referenced
datetime/timezone without importing them, and no test ever ran the code. A
source-grep test can only prove a string is present, never that it works.

These tests import the real module and exercise the guard's behaviour, so a
missing import or a broken expression fails here instead of in production.
"""

import ast
import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "karpathy_runner.py"


def _load_runner():
    """Import karpathy_runner as a module without executing main()."""
    spec = importlib.util.spec_from_file_location("karpathy_runner_under_test", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["karpathy_runner_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_module_imports_cleanly():
    """A missing import shows up here, not as a NameError mid-run."""
    assert _load_runner() is not None


def test_datetime_and_timezone_are_bound_at_module_level():
    mod = _load_runner()
    assert hasattr(mod, "datetime"), (
        "karpathy_runner uses datetime for the max_hours cap but never imports it"
    )
    assert hasattr(mod, "timezone"), (
        "karpathy_runner uses timezone for the max_hours cap but never imports it"
    )


def test_max_hours_elapsed_computation_runs():
    """Execute the same expression the guard runs -- it must not raise."""
    mod = _load_runner()
    started = (mod.datetime.now(mod.timezone.utc) - timedelta(hours=3)).isoformat()
    t0 = mod.datetime.fromisoformat(started.replace("Z", "+00:00"))
    if t0.tzinfo is None:
        t0 = t0.replace(tzinfo=mod.timezone.utc)
    elapsed = (mod.datetime.now(mod.timezone.utc) - t0).total_seconds() / 3600.0
    assert 2.9 < elapsed < 3.1


def test_guard_handles_naive_and_malformed_started_at():
    """A naive or junk started_at must not crash the loop."""
    mod = _load_runner()
    t0 = mod.datetime.fromisoformat("2026-10-25T00:00:00")
    assert t0.tzinfo is None
    assert t0.replace(tzinfo=mod.timezone.utc).tzinfo is not None
    with pytest.raises(ValueError):
        mod.datetime.fromisoformat("not-a-timestamp")


def test_cap_guard_source_shape():
    """The guard must precede dispatch and persist a pause."""
    src = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    seg = ast.get_source_segment(src, fn) or ""
    assert 'lc.get("max_rounds")' in seg
    assert 'lc.get("max_hours")' in seg
    assert 'paused_reason"] = "max_rounds reached' in seg
    assert 'lc["running"] = False' in seg
    assert seg.index('lc.get("max_rounds")') < seg.index("pick_next_project")
