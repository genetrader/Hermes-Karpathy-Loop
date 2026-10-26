"""W6: checkpoint.tag_only must resolve to the MODULE, not the function.

2026-10-26: karpathy_runner.py defines a module-level function named
`checkpoint` (the end-of-round publisher). That function shadows the
`checkpoint` MODULE, so the merge-gate call `checkpoint.tag_only(...)`
resolved against the function and raised:

    AttributeError: 'function' object has no attribute 'tag_only'

Every round that reached the merge gate logged "tag failed" and stopped at
branch-only. The branch was preserved correctly, so no work was lost, but the
round tag -- the human's findability handle -- was never created.

A source-grep test would have passed here, because the call site LOOKS right.
These tests bind the real names.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "karpathy_runner.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("kr_shadow_under_test", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kr_shadow_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_module_alias_is_importable_and_exposes_tag_only():
    """The module must be reachable under a name the function cannot shadow."""
    mod = _load_runner()
    assert hasattr(mod, "checkpoint_mod"), (
        "karpathy_runner must import the checkpoint MODULE (aliased) -- a bare "
        "`import checkpoint` is shadowed by the def checkpoint() below it"
    )
    assert callable(mod.checkpoint_mod.tag_only), (
        "checkpoint_mod.tag_only must be the module's function, not an attribute "
        "of the runner's checkpoint() function"
    )


def test_checkpoint_function_still_exists():
    """The publisher function must survive -- we only renamed the module ref."""
    mod = _load_runner()
    assert callable(mod.checkpoint)
    assert not hasattr(mod.checkpoint, "tag_only"), (
        "sanity: the runner's checkpoint() function has no tag_only attribute, "
        "which is exactly why the un-aliased call failed"
    )


def test_call_site_uses_the_module_alias():
    src = RUNNER.read_text(encoding="utf-8")
    assert "checkpoint_mod.tag_only(" in src, (
        "the merge-gate call must go through checkpoint_mod"
    )
    assert "checkpoint.tag_only(" not in src.replace("checkpoint_mod.tag_only(", ""), (
        "a bare checkpoint.tag_only() call remains and will hit the shadowed function"
    )