"""Trace why pick() returns no prompt_id from a fresh cycle."""
import sys, tempfile, traceback
from pathlib import Path
sys.path.insert(0, r"C:/CODING/project-improver")
import angle_prompts as PN
import angle_pick as AP

tmp = Path(tempfile.mkdtemp(prefix="kldbg2_")) / "prompt_cycles.json"
PN.CYCLE_STATE = tmp
PROJ = "dbg"

print("1. _prompt_layer accessible?", hasattr(AP, "_prompt_layer"))
try:
    print("   ->", AP._prompt_layer())
except Exception:
    traceback.print_exc()

table = PN.load_prompts()
print("2. prompt table angles:", len(table))

ordered = [a["id"] for a in AP.load_angles()["angles"]]
print("3. ordered angle ids:", len(ordered))
try:
    p = AP._next_unused_prompt(PROJ, ordered)
    print("4. _next_unused_prompt ->", (p or {}).get("id") if p else None)
except Exception:
    traceback.print_exc()

print("5. calling pick() ...")
try:
    a = AP.pick(PROJ)
    print("   keys:", sorted(a.keys()))
    print("   angle:", a.get("angle"), "| prompt_id:", a.get("prompt_id"))
except Exception:
    traceback.print_exc()