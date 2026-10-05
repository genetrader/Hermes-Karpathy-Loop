"""Stamp the yellow KL marker onto the three thread tips the loop maintains.

Design notes (why this is safe):
  * `title` has a UNIQUE partial index (idx_sessions_title_unique, WHERE title
    IS NOT NULL). Only ONE row per repo may carry 'KL :: <repo>', so we touch
    the TIP named in state/threads.json -- the row `threads.maintained()` heals
    on every round -- and leave every other row byte-identical.
  * Hidden/collapsed history is deliberately NOT rewritten: it is state this
    pass did not create, and hiding rows is not required for the ask.
  * The loop is mid-round, so this is a DISPLAY-ONLY update: no session id, no
    message, no process, no loop.json field is touched.
"""
import json
import sqlite3
import sys

sys.path.insert(0, r"C:/CODING/project-improver")
import threads as T  # noqa: E402

DB = r"C:/Users/gene/AppData/Local/hermes/state.db"
REG = r"C:/CODING/project-improver/state/threads.json"

reg = json.load(open(REG, encoding="utf-8"))
tips = {}
for proj, entry in reg.items():
    if isinstance(entry, dict):
        tip = entry.get("tip") or entry.get("session_id")
        if tip:
            tips[proj] = tip

con = sqlite3.connect(DB, timeout=30)
con.execute("PRAGMA busy_timeout=20000")

# Preflight against EVERY row -- the unique index ignores `hidden`, which is
# exactly what broke the first attempt.
taken = {}
for sid, title in con.execute("select id, title from sessions where title is not null"):
    taken[title] = sid

planned = []
for proj, sid in sorted(tips.items()):
    new = T.title_for(proj)
    holder = taken.get(new)
    if holder is not None and holder != sid:
        raise SystemExit("ABORT: %r already held by %s" % (new, holder))
    cur = con.execute("select title from sessions where id=?", (sid,)).fetchone()
    old = cur[0] if cur else None
    if old != new:
        planned.append((sid, proj, old, new))

print("=== PLAN: %d row(s) ===" % len(planned))
for sid, proj, old, new in planned:
    print("  %-20s %s\n      %r -> %r" % (proj, sid, old, new))

con.execute("begin")
try:
    for sid, proj, old, new in planned:
        con.execute("update sessions set title=? where id=?", (new, sid))
    con.commit()
    print("COMMITTED")
except Exception:
    con.rollback()
    raise

# ---- VERIFY: read the mutated target back, do not trust the write ----------
print()
print("=== VERIFY (re-read from disk) ===")
for proj, sid in sorted(tips.items()):
    row = con.execute("select id, title, source, hidden from sessions where id=?",
                      (sid,)).fetchone()
    print("  %-20s %s  title=%r  source=%s hidden=%s" % (proj, row[0], row[1], row[2], row[3]))
con.close()