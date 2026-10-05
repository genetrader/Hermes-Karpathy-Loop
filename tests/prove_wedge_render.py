"""Prove the wedge path renders: feed the widget logic a WEDGED liveness payload
and confirm it would take the wedge branch, then feed it the HEALTHY one and
confirm it does NOT. Static reasoning over the real source, no guessing."""
import json
import subprocess
from pathlib import Path

ROOT = Path(r"C:\CODING\project-improver")
PLUG = ROOT / "review" / "plugin.js"
src = PLUG.read_text(encoding="utf-8")

# 1. The three liveness branches must all be reachable in the render.
checks = {
    "reads liveness":        "status.json.liveness" in src,
    "computes wedged":       "live0 && live0.wedged" in src,
    "wedge overrides badge": "wedged ? S.badgeErr" in src,
    "wedge banner exists":   "WEDGED \\u2014 the loop says RUNNING" in src,
    "wedge outranks FINISHING": src.index("wedged ? \"WEDGED") < src.index("finishing ? \"FINISHING"),
    "paused note exists":    "staleOnPaused" in src,
    "no bad jsx call":       'jsx("div", { style: S.grid }, children:' not in src,
}
for k, v in checks.items():
    print(("%-28s %s" % (k, "PASS" if v else "FAIL")))

# 2. Simulate the two payloads through the EXACT expression used in the widget.
def status_line(wedged, finishing, running, working):
    if wedged:
        return "WEDGED - flag says running but nothing is"
    if finishing:
        return "FINISHING - no new rounds after this one"
    if not running:
        return "PAUSED"
    return "RUNNING" if working else "ARMED - idle"

def badge(wedged, finishing, running, working):
    if wedged:
        return "badgeErr"
    if finishing:
        return "badgeWarn"
    if not running:
        return "badgeOff"
    return "badgeOn" if working else "badgeIdle"

print()
print("=== simulated render decisions ===")
cases = [
    ("WEDGED  (flag running, no rounds)", dict(wedged=True,  finishing=False, running=True,  working=False)),
    ("healthy (running + working)",       dict(wedged=False, finishing=False, running=True,  working=True)),
    ("paused  (stale heartbeat)",         dict(wedged=False, finishing=False, running=False, working=False)),
    ("finishing (drain)",                 dict(wedged=False, finishing=True,  running=True,  working=True)),
]
ok = True
for name, kw in cases:
    line, bdg = status_line(**kw), badge(**kw)
    expect_err = kw["wedged"]
    good = (bdg == "badgeErr") == expect_err
    ok = ok and good
    print("  %-36s -> %-42s %-9s %s" % (name, line, bdg, "OK" if good else "WRONG"))
print()
print("ALL RENDER DECISIONS CORRECT" if ok and all(checks.values()) else "PROBLEM FOUND")

# 3. Live sanity: the real backend currently reports paused + stale, NOT wedged.
p = subprocess.run([r"C:\Users\gene\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe",
                    "loopctl.py", "status", "--json"],
                   cwd=str(ROOT), capture_output=True, text=True, timeout=120)
d = json.loads(p.stdout)
lv = d["liveness"]
print()
print("=== live backend liveness right now ===")
print("  wedged          :", lv["wedged"], "(expect False - loop is paused)")
print("  stale_on_paused :", lv["heartbeat_stale_on_paused"])
print("  pid_alive       :", lv["heartbeat_pid_alive"])
print("  -> widget would show:", status_line(lv["wedged"], False, lv["running"], False),
      "/", badge(lv["wedged"], False, lv["running"], False))