#!/usr/bin/env python
"""
ask_policy.py -- how long a question waits before the loop proceeds alone.

Gene's spec (2026-09-23): ping Discord every 5 minutes, up to ~25 times. If he
has not answered by then, the loop takes the BEST ASSUMPTION, records it, tells
him what it assumed, and keeps working. An unanswered question must never
deadlock the loop.
"""
from __future__ import annotations

MAX_PINGS = 25
PING_INTERVAL_S = 5 * 60


def ping_due(pings_sent: int, since_last_s: float) -> bool:
    """Time for the next reminder ping?"""
    if exhausted(pings_sent):
        return False
    return since_last_s >= PING_INTERVAL_S


def should_ping(pings_sent: int, elapsed_s: float) -> bool:
    """Is there still ping budget left at all?"""
    if exhausted(pings_sent):
        return False
    return elapsed_s < budget_seconds()


def exhausted(pings_sent: int) -> bool:
    return pings_sent >= MAX_PINGS


def budget_seconds() -> int:
    return MAX_PINGS * PING_INTERVAL_S


def _cli(argv=None) -> int:
    """status --json -> one compact object for the widget's red dot."""
    import sys, json
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "status":
        print("usage: ask_policy.py status --json")
        return 2
    qfile = ROOT / "questions" / "pending.json"
    open_q = None
    try:
        if qfile.exists():
            st = json.loads(qfile.read_text(encoding="utf-8"))
            if not st.get("answered") and not st.get("parked"):
                open_q = {"qid": st.get("qid"), "project": st.get("project"),
                          "question": st.get("question"),
                          "pings_sent": st.get("reminders", 0),
                          "max_pings": st.get("max_repeat", MAX_PINGS),
                          "assumption": st.get("assumption"),
                          "asked_at": st.get("asked_at")}
    except Exception:
        open_q = None
    print(json.dumps({"open": open_q, "max_pings": MAX_PINGS,
                      "interval_s": PING_INTERVAL_S}))
    return 0


ROOT = None
if __name__ == "__main__":
    from pathlib import Path as _P
    ROOT = _P(__file__).resolve().parent
    raise SystemExit(_cli())
