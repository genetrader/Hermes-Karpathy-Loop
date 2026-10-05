"""Wiring tests: does the RUNNER actually contain rejected work, and does the
SELECTOR actually honour a quarantine?

F3 UPDATE (2026-09-30): containment is now WORKTREE DELETION. The child
never touches the canonical checkout; the round's worktree (branch
kl/<repo>/rNN) is deleted under `not _round_ok`, and _reset_canonical()
stays as the post-merge gate-disagreement restore + reject-path safety
net. _rollback_to() was deleted with the mechanism it served -- keeping a
dead containment helper just to pass its own test is drift, not safety.

WHY THIS FILE IS AST-BASED (read before editing):

The first version used substring matching on the runner's source --
e.g. `assert "not _round_ok" in SRC[i-900:i]`. That is not a check: it went
looking for the rollback call, landed on the *helper definition* instead (the
file's first textual occurrence of `_rollback_to(`), and reported the guard
missing from an unrelated 900-char window. Two of its own tests failed against
healthy code for exactly that reason.

Worse, the sibling file `test_rejected_work_is_contained.py` stayed 9/9 GREEN
with containment disabled -- it exercised only the helper functions and never the
runner's decision. A test that cannot fail on a real defect is a decoration.

So these tests parse the runner with `ast` and assert on the syntax tree: the
call is located structurally, and its ENCLOSING `if` tests are inspected. Each
test below has been shown to fail under defect reintroduction.

Run: python -m pytest tests/test_containment_is_wired.py -q
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = (ROOT / "karpathy_runner.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


# ------------------------------------------------------------------ helpers

def _names(node):
    """Every identifier, attribute name, AND string constant under `node`.

    Strings matter: `entry["pre_round_head"]` carries its key as a string
    CONSTANT, not a Name, so a names-only walk silently misses it. The first
    version of this helper did exactly that and produced a false failure.
    """
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.add(n.value)
    return out


def _strings(node):
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _defs(name):
    return [n for n in ast.walk(TREE)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]


def _calls_to(func_name, within=None):
    """Every Call node whose callee resolves to `func_name`.

    `within` limits the search to one function's subtree, which is what keeps
    this from confusing a definition of the helper with a call to it.
    """
    scope = within if within is not None else TREE
    found = []
    for n in ast.walk(scope):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name) and f.id == func_name:
                found.append(n)
            elif isinstance(f, ast.Attribute) and f.attr == func_name:
                found.append(n)
    return found


def _enclosing_test_text(call_node):
    """The SOURCE TEXT of every `if` test lexically enclosing the call.

    Names alone cannot distinguish `if not _round_ok:` from
    `if _round_ok:` -- the negation lives in the text, not in the name
    set. (Found by a defect drill, 2026-09-30: swapping the reject guard
    to another variable left the union-of-names check green.)"""
    parents = {}
    for node in ast.walk(TREE):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    parts = []
    anc = parents.get(call_node)
    while anc is not None and not isinstance(anc, ast.Module):
        if isinstance(anc, ast.If):
            try:
                parts.append(ast.unparse(anc.test))
            except Exception:
                parts.append("")
        anc = parents.get(anc)
    return " ; ".join(parts)


def _enclosing_test_names(call_node):
    """Identifiers used in the `if` tests that lexically enclose `call_node`.

    Uses a parent map rather than a hand-rolled NodeVisitor: the earlier
    hand-rolled version missed the guard because the call sits inside an
    `Assign` that sits inside two nested `If`s, and the visitor did not descend
    through the Assign's value field. Walking parents upward is simpler and
    cannot silently lose a level.
    """
    parents = {}
    for node in ast.walk(TREE):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    out = set()
    anc = parents.get(call_node)
    while anc is not None and not isinstance(anc, ast.Module):
        if isinstance(anc, ast.If):
            out |= _names(anc.test)
        anc = parents.get(anc)
    return out


def _round_fn():
    """The function that runs one round. A rename should be a loud failure, not
    a silent skip."""
    for cand in ("run_one_round", "run_round", "one_round"):
        d = _defs(cand)
        if d:
            return d[0]
    pytest.fail("could not locate the per-round function in karpathy_runner.py -- "
                "update _round_fn() rather than deleting these tests")


# ---------------------------------------------------- containment is CALLED

def test_rollback_call_exists_inside_the_round():
    """Structural: a CALL, not a definition. The old substring test could not
    tell those apart, which is why it failed against healthy code.
    F3: the containment call is _remove_worktree (worktree deletion)."""
    fn = _round_fn()
    assert _calls_to("_remove_worktree", within=fn), \
        "_remove_worktree is never CALLED inside the round -- rejected work is not contained"


def test_rollback_call_is_guarded_by_rejection():
    """THE wiring test. Removing the `not _round_ok` guard must break this.

    F3 has TWO _remove_worktree call sites with DIFFERENT legal guards:
      * the reject path       -> must sit under a `not ... _round_ok` test
      * the post-merge clean  -> must sit under an `_round_ok` test
    and every _reset_canonical call must sit under an `_round_ok` test.
    Each call site is checked INDIVIDUALLY: a union over sites once hid a
    neutered reject path behind a green cleanup path (defect drill,
    2026-09-30)."""
    import re as _re
    calls = _calls_to("_remove_worktree")
    assert calls, "_remove_worktree is never called"
    # Classification by the NEGATED TERM, not the bare word "not": the
    # post-merge branch legitimately contains `is not True` in its guard,
    # which a naive "not in text" misreads as a reject guard.
    def _is_reject(call):
        return bool(_re.search(r"\bnot\s+_round_ok\b",
                               _enclosing_test_text(call)))
    reject_calls = [n for n in calls if _is_reject(n)]
    accept_calls = [n for n in calls if not _is_reject(n)]
    assert reject_calls, "the reject path never deletes the worktree"
    assert accept_calls, "the accepted round never cleans its worktree"
    for call in reject_calls:
        names = _enclosing_test_names(call)
        assert "_round_ok" in names, \
            ("the reject-path worktree deletion is not guarded by a test on "
             "_round_ok -- rejected work would not be contained. Guards: %s"
             % _enclosing_test_text(call))
    for call in accept_calls:
        txt = _enclosing_test_text(call)
        names = _enclosing_test_names(call)
        assert "_round_ok" in names and not _re.search(r"\bnot\s+_round_ok\b", txt), \
            ("the accept-path worktree cleanup is not guarded by a POSITIVE "
             "_round_ok test. Guards: %s" % txt)
    for call in _calls_to("_reset_canonical"):
        names = _enclosing_test_names(call)
        assert "_round_ok" in names, \
            ("the canonical restore is not guarded by a test on _round_ok -- "
             "a rejected round could reset canonical. Guards: %s"
             % _enclosing_test_text(call))


def test_rollback_call_is_anchored_to_the_preround_head():
    """The canonical restore must be anchored to the HEAD captured before
    the round (the worktree deletion itself needs no anchor: it deletes the
    whole worktree, not a delta -- but the SAFETY NET that may reset
    canonical does)."""
    guard_names = set()
    for call in _calls_to("_reset_canonical"):
        guard_names |= _enclosing_test_names(call)
    assert "pre_round_head" in guard_names, \
        "the canonical restore is not anchored to the HEAD captured before the round"


def test_quarantine_is_reachable_and_carries_a_reason():
    assert "quarantined" in SRC, "nothing ever sets a quarantine flag"
    assert "quarantine_reason" in SRC, \
        "a quarantine with no reason cannot be acted on by a human"
    assert "RESET UNVERIFIED" in SRC, \
        "no branch distinguishes an unverified canonical restore from a clean one"


def test_rollback_helper_really_verifies_its_own_work():
    """The helper must check HEAD and cleanliness, not report success blindly.
    F3: the canonical-restore helper carries the same duty."""
    d = _defs("_reset_canonical")
    assert d, "_reset_canonical is gone"
    used = _names(d[0])
    assert "_head_full_sha" in used, "the helper does not confirm HEAD reached the anchor"
    assert "_is_dirty" in used, "the helper does not confirm the tree was actually cleaned"


def test_dirty_state_is_captured_before_the_child_runs():
    """Both anchors must be captured BEFORE the child subprocess is launched.

    NOTE: an earlier version of this test searched for the bare word "child" and
    matched the word inside a docstring, so it compared against the wrong offset
    and failed against healthy code. Anchor on the actual launch call instead.
    """
    fn = _round_fn()
    body = ast.get_source_segment(SRC, fn) or ""
    assert "pre_round_head" in body, "no pre-round HEAD captured in the round"
    assert "pre_round_dirty" in body, "no pre-round dirty capture in the round"

    launch = None
    for cand in ("subprocess.Popen(", "subprocess.run(", "_launch("):
        if cand in body:
            launch = body.index(cand)
            break
    assert launch is not None, \
        "could not find the child launch in run_round -- update this anchor"
    assert body.index("pre_round_head") < launch, \
        "the HEAD anchor is captured AFTER the child launches -- too late to roll back"
    assert body.index("pre_round_dirty") < launch, \
        "the dirty anchor is captured AFTER the child launches -- too late to roll back"


# ------------------------------------------------- the quarantine is HONOURED

def test_selector_builds_a_runnable_set_that_excludes_quarantine():
    """THE decisive guard test. Deleting the filter must break this.

    The filter lives in the MAIN LOOP, not in run_round (run_round ends before
    it), so this searches the whole module -- that is correct, and the assertion
    pins the structure: a list comprehension over `projs` whose test negates the
    `quarantined` lookup.
    """
    assert "_runnable" in SRC, \
        "the selector has no runnable set -- nothing filters quarantined repos"
    assert "_runnable = list(projs)" not in SRC, \
        "the quarantine filter was replaced by a pass-through"

    for comp in [n for n in ast.walk(TREE) if isinstance(n, ast.ListComp)]:
        src = ast.unparse(comp)
        if "quarantined" not in src:
            continue
        # must iterate the candidate set (g.iter is the iterable, not g)
        if not any(isinstance(g.iter, ast.Name) and g.iter.id == "projs"
                   for g in comp.generators):
            continue
        for g in comp.generators:
            for c in (g.ifs or []):
                csrc = ast.unparse(c)
                if "quarantined" in csrc and csrc.strip().startswith("not "):
                    return
    pytest.fail("no list comprehension over projs NEGATES the quarantine flag -- "
                "a quarantined repo could be selected")


def test_selector_refuses_to_run_when_everything_is_quarantined():
    assert "every enabled project is QUARANTINED" in SRC, \
        "an all-quarantined fleet has no back-off path -- it would spin"
    i = SRC.index("every enabled project is QUARANTINED")
    window = SRC[max(0, i - 800):i + 800]
    assert "time.sleep(900)" in window, "the all-quarantined branch does not back off"
    assert "continue" in window, "the all-quarantined branch does not skip the round"


def test_rotation_sorts_the_runnable_set_not_the_full_set():
    """B2 (2026-09-30): the sort lives in pick_next_project(); main() must
    call it on the RUNNABLE set. Sorting the full `projs` list would let a
    quarantined repo back in through the sort."""
    assert "projs = pick_next_project(_runnable, reg)" in SRC, \
        "main() does not sort the runnable set through pick_next_project"
    # The function's own `return sorted(projs, key=_key)` is fine -- it sorts
    # exactly the list main() hands it. The dangerous shape is an INLINE sort
    # of the full manifest list (`projs = sorted(projs`) anywhere in main().
    assert "projs = sorted(projs" not in SRC, \
        "an inline sort still covers quarantined repos"
    assert SRC.count("sorted(projs") == 1, \
        "the only sorted(projs must be inside pick_next_project"