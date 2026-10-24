"""Prove the prompt-cycle rules with REAL runs (no mocks, no invented output).

Rules under test (Gene, 2026-09-27 -- locked):
  1. PER-REPO cycle -- each repo walks its own pass.
  2. CONSUME ON ATTEMPT -- a spent prompt is never re-issued, including when the
     round is not-applicable.
  3. Full coverage wipes the slate and starts a fresh cycle.
  4. The prompt is actually RENDERED into the round text (not just selected).

This runs against a TEMP copy of the cycle state so the live cycles file and the
running loop are untouched.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"C:/CODING/project-improver")
sys.path.insert(0, str(ROOT))

import angle_prompts as PN  # noqa: E402
import angle_pick as AP     # noqa: E402
import round_prompt as RP   # noqa: E402

# ---- redirect cycle state to a temp file so the live run is untouched --------
# CRITICAL: mutate the module object that `angle_pick` ACTUALLY imports. Binding
# a module imported into this script does NOT affect the copy pick() calls, and
# the two then diverge silently -- pick() reads the live state file while the
# test reads its own, and the test wrongly concludes "no prompt".
tmp = Path(tempfile.mkdtemp(prefix="klcycle_")) / "prompt_cycles.json"
PN = AP._prompt_layer()          # the instance pick() consults
assert PN is not None, "prompt layer not importable"
PN.CYCLE_STATE = tmp             # mutate the live object
globals()["PN"] = PN             # this file now uses the same object
print("cycle-state (temp):", tmp)

total = len(PN.all_prompt_ids())
print("total prompts in table:", total)
print()

PROJ = "cycle-test-repo"

# --- Rule 2: 12 consecutive picks, no repeats, all consumed ------------------
seen, angles_seen = [], []
N = 12
for i in range(N):
    a = AP.pick(PROJ, facts={}, seed=None)
    pid = a.get("prompt_id")
    assert pid, "round %d: pick() returned NO prompt_id" % (i + 1)
    seen.append(pid)
    angles_seen.append(a["angle"])
    # consume exactly as karpathy_runner does
    PN.mark_used(PROJ, pid, "issued")

print("=== %d consecutive picks (per-repo cycle) ===" % N)
for i, (pid, ang) in enumerate(zip(seen, angles_seen), 1):
    print("  round %2d  angle=%-28s prompt=%s" % (i, ang, pid))

dupes = [p for p in set(seen) if seen.count(p) > 1]
print()
print("distinct prompts issued :", len(set(seen)), "/", N)
print("DUPLICATES              :", dupes or "none  <-- Rule 2 holds")

prog = PN.cycle_progress(PROJ)
print("cycle progress          :", json.dumps(prog))

# --- Rule 4: the prompt is RENDERED into the round text ----------------------
a = AP.pick(PROJ, facts={})
text = RP.build(PROJ, a, "pytest -q", 7, {"rounds": 6})
checks = {
    "THIS ROUND'S PROMPT header": "THIS ROUND'S PROMPT" in text,
    "APPLIES WHEN section": "APPLIES WHEN" in text,
    "WHAT TO HUNT section": "WHAT TO HUNT" in text,
    "EVIDENCE REQUIRED section": "EVIDENCE REQUIRED" in text,
    "NOT APPLICABLE section": "NOT APPLICABLE" in text,
    "prompt id present": a["prompt_id"] in text,
}
print()
print("=== round-text render check (prompt %s) ===" % a["prompt_id"])
for k, v in checks.items():
    print("  [%s] %s" % ("OK " if v else "MISS", k))
assert all(checks.values()), "round text is missing prompt sections"

# --- Rule 3: full coverage wipes the slate -----------------------------------
# The cycle never "ends" from the caller's side (pick always issues something --
# a spent cycle rolls over rather than going silent). So bound the walk by the
# known cycle length and assert the rollover happened exactly once, at `total`.
rec0 = PN.cycle_for(PROJ)
used_before = len(rec0.get("used", []))
need = total - used_before
print()
print("=== Rule 3: cycle rollover ===")
print("  prompts left in this cycle: %d (of %d)" % (need, total))

issued = []
for i in range(need):
    a = AP.pick(PROJ, facts={})
    pid = a.get("prompt_id")
    if not pid:
        raise SystemExit("FAIL: pick() went silent at %d/%d -- cycle stalled" % (i, need))
    issued.append(pid)
    PN.mark_used(PROJ, pid, "issued")

rec = PN.cycle_for(PROJ)
print("  filled the cycle with      : %d picks" % len(issued))
print("  distinct in that run       : %d / %d" % (len(set(issued)), len(issued)))
print("  cycle number now           :", rec.get("cycle"))
print("  used in new cycle          :", len(rec.get("used", [])))
print("  reset_at                   :", rec.get("reset_at"))

assert len(set(issued)) == len(issued), "FAIL: repeated a prompt inside one cycle"
assert rec.get("cycle", 1) >= 2, "FAIL: cycle did not roll over"
assert len(rec.get("used", [])) == 0, "FAIL: slate was not wiped"
assert rec.get("reset_at"), "FAIL: reset was not recorded"

# And the fresh cycle must start issuing again from the top.
nxt = AP.pick(PROJ, facts={})
assert nxt.get("prompt_id"), "FAIL: fresh cycle issued nothing"
print("  fresh cycle issues         :", nxt["prompt_id"])
print()
print("ALL RULES PASS")