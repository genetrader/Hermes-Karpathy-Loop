"""Why does pick() return None mid-cycle? Watch state each step."""
import sys, tempfile, json
from pathlib import Path

sys.path.insert(0, r"C:/CODING/project-improver")
import angle_pick as AP

PN = AP._prompt_layer()
tmp = Path(tempfile.mkdtemp(prefix="klroll_")) / "prompt_cycles.json"
PN.CYCLE_STATE = tmp
PROJ = "rolltest"

for i in range(40):
    a = AP.pick(PROJ, facts={})
    pid = a.get("prompt_id")
    if pid:
        PN.mark_used(PROJ, pid, "issued")
    rec = PN.cycle_for(PROJ)
    if i < 3 or not pid:
        print("i=%2d pid=%-30s cycle=%s used=%d" %
              (i + 1, pid, rec.get("cycle"), len(rec.get("used", []))))
    if not pid:
        print("   -> pick() returned None at step", i + 1)
        print("   state:", json.dumps(rec)[:400])
        # what does the raw lookup say?
        ordered = [a2["id"] for a2 in AP.load_angles()["angles"]]
        print("   _next_unused_prompt ->", PN and AP._next_unused_prompt(PROJ, ordered))
        break