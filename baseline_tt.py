"""S2 baseline for the engine-refusal objective.

Runs the 7-file gate from improve.yaml and records the real result.
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\CODING\project-improver")
rept = Path(r"D:\ZCODE\TOMBS AND TYRANTS")
exe = rept / ".venv" / "Scripts" / "python.exe"

OUT = ROOT / "state" / "plan" / "tombs-and-tyrants"
OUT.mkdir(parents=True, exist_ok=True)

GATE = [
    "tests/test_a_degree_of_comparison_is_not_a_fixture.py",
    "tests/test_a_suggestion_offers_only_what_exists.py",
    "tests/test_refused_claims_are_recorded.py",
    "tests/test_the_refusal_is_not_a_scene.py",
    "tests/test_a_role_suggestion_does_not_travel.py",
    "tests/test_a_suggestion_does_not_outlive_its_scene.py",
    "tests/test_suggestions_every_turn.py",
]

print("exe exists:", exe.exists())

cmd = [str(exe), "-m", "pytest", "-q", *GATE]
t0 = time.time()
r = subprocess.run(cmd, cwd=str(rept), capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=900)
wall = round(time.time() - t0, 2)

out = (r.stdout or "") + (r.stderr or "")
lines = [ln for ln in out.strip().splitlines() if ln.strip()]
summary = lines[-1] if lines else ""
fails = sorted({ln.replace("FAILED ", "").split("::")[0].strip()
                for ln in lines if ln.startswith("FAILED")})

rec = {
    "project": "tombs-and-tyrants",
    "stage": "S2 baseline",
    "objective": "engine accepts natural-language intent; no premature refusal",
    "recorded_at": datetime.now().isoformat(timespec="seconds"),
    "command": ".venv/Scripts/python.exe -m pytest -q " + " ".join(GATE),
    "cwd": str(rept),
    "exit_code": r.returncode,
    "wall_seconds": wall,
    "summary": summary,
    "failures": fails,
    "fixed_this_session": {
        "commit": "00cfbde",
        "what": "degree-of-comparison words were existence-checked as fixtures",
        "verbatim": "Henk, what work can you give me? I'm ready to handle your toughest jobs.",
        "missing_before": ["toughest"],
        "missing_after": [],
    },
}

p = OUT / "S2-baseline.json"
p.write_text(json.dumps(rec, indent=2), encoding="utf-8")

print("exit_code :", r.returncode)
print("wall      :", wall, "s")
print("summary   :", summary)
print("failures  :", fails or "none")
print("wrote     :", p)
sys.exit(0 if r.returncode == 0 else 1)