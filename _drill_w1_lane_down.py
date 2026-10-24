"""W1 ACCEPTANCE DRILL 1: kill a lane mid-run -> the loop halts within 3 rounds.

Drives the REAL `_lane_gate()` against a REAL loop state, with the implementer
seat pointed at a port nothing is listening on (a genuinely dead lane), and
checks that after 3 dispatches the gate refuses and the loop state reads
paused:lane-down.

Run: python _drill_w1_lane_down.py
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import seat_preflight as pf
import karpathy_runner as kr
import loopctl


def main():
    # Work on a throwaway state dir so the live loop state is never touched.
    tmp = Path(tempfile.mkdtemp(prefix="kl-drill-"))
    shutil.copytree(ROOT / "state", tmp / "state",
                    ignore=shutil.ignore_patterns("worktrees"))
    print("drill state:", tmp)

    # Point the runner's ROOT at the throwaway dir.
    kr.ROOT = tmp
    kr.LOG = tmp / "logs" / "drill.log"
    kr.LOG.parent.mkdir(parents=True, exist_ok=True)
    pf.breaker_path(tmp).unlink(missing_ok=True)

    dead = "custom:drill-dead-lane:nosuch-model"
    alive = "custom:deepseek1017-mia-tp2:DeepSeek-V4.1-Flash-1017"

    # A config where the "dead" slug points at a closed port.
    cfg = {
        "providers": {
            "drill-dead-lane": {"api": "http://127.0.0.1:1/v1", "api_key": ""},
            "deepseek1017-mia-tp2": {"api": "http://127.0.0.1:18888/v1", "api_key": ""},
        }
    }
    pf._load_config = lambda p=None: cfg

    lc = {"implementer": dead, "reviewer": alive}

    print("\n--- dispatching rounds against a dead implementer lane ---")
    for attempt in range(1, 6):
        ok, pfl = kr._lane_gate(lc)
        impl_v = pfl["implementer"]["verdict"]
        streak = pf.load_breaker(tmp)["seats"]["implementer"]["streak"]
        print("dispatch %d: allowed=%-5s impl_verdict=%-16s streak=%d  %s"
              % (attempt, ok, impl_v, streak,
                 ("HALTED: " + pfl["reason"]) if not ok else ""))
        if not ok:
            break

    st = loopctl.load() if False else json.loads((tmp / "state" / "loop.json").read_text())
    print("\nloop.json running=%s paused_reason=%s" % (st.get("running"), st.get("paused_reason")))
    print("detail: %s" % st.get("paused_detail"))

    tripped = pf.tripped(tmp)
    print("\nbreaker tripped seat: %s" % (tripped.get("role") if tripped else None))

    assert not ok, "FAIL: the gate never halted"
    assert attempt == 3, "FAIL: halted on dispatch %d, expected the 3rd (the 3rd failure trips it)" % attempt
    assert st.get("running") is False, "FAIL: loop still marked running"
    assert st.get("paused_reason") == "lane-down", "FAIL: paused_reason=%r" % st.get("paused_reason")
    assert tripped and tripped["role"] == "implementer", "FAIL: breaker not tripped"
    print("\nPASS: lane-down drill halted the loop after 3 consecutive provider failures.")


if __name__ == "__main__":
    main()
