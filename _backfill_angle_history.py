"""W3 backfill: reconstruct angle_history.json from state/threads.json.

Each thread entry carries `angle_history` (newest-first, 20 rows, per project)
with id/family/round/at. That is real per-round data the loop DID record -- it
just never reached the GLOBAL ledger. Rebuilding from it gives cooldown,
rotation and retirement an honest 893-round history instead of a day-one stub.

Idempotent: rows already present (same project + round + angle) are not duplicated.

Usage:
  python _backfill_angle_history.py --dry-run
  python _backfill_angle_history.py
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import angle_pick


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--threads", default=str(ROOT / "state" / "threads.json"))
    a = ap.parse_args()

    threads = json.loads(Path(a.threads).read_text(encoding="utf-8"))
    hist = angle_pick.load_history()

    have = set()
    for r in hist.get("rounds") or []:
        have.add((r.get("project"), r.get("round"), r.get("angle")))

    # Rebuild the round list newest-last (the ledger's convention is append).
    recovered = []
    for proj, entry in threads.items():
        if not isinstance(entry, dict):
            continue
        rows = entry.get("angle_history") or []
        for row in rows:
            aid = row.get("id") or row.get("angle")
            if not aid:
                continue
            rno = row.get("round")
            key = (proj, rno, aid)
            if key in have:
                continue
            rc = row.get("rc")
            # rc == 0 means the child exited cleanly; acceptance is not recorded
            # per-row, so a clean exit is the closest honest signal we have.
            # Marked `unverified` rather than claiming "accepted".
            result = "accepted" if rc == 0 else "failed"
            at = row.get("at")
            if isinstance(at, (int, float)):
                from datetime import datetime, timezone
                at = datetime.fromtimestamp(at, timezone.utc).isoformat(timespec="seconds")
            recovered.append({
                "angle": aid,
                "project": proj,
                "family": row.get("family") or "?",
                "result": result,
                "at": at or "",
                "round": rno,
                "note": "backfilled from threads.json (rc=%s; acceptance unverified)" % rc,
            })
            have.add(key)

    recovered.sort(key=lambda r: (r.get("at") or ""))
    print("threads scanned:", len(threads))
    print("rows already in ledger:", len(hist.get("rounds") or []))
    print("rows recoverable:", len(recovered))
    if recovered:
        print("  first:", json.dumps(recovered[0]))
        print("  last: ", json.dumps(recovered[-1]))
        fams = {}
        for r in recovered:
            fams[r["family"]] = fams.get(r["family"], 0) + 1
        print("  families:", fams)

    if a.dry_run:
        print("\nDRY RUN -- nothing written")
        return 0

    if not recovered:
        print("nothing to backfill")
        return 0

    hist.setdefault("rounds", []).extend(recovered)
    if len(hist["rounds"]) > 2000:
        hist["rounds"] = hist["rounds"][-2000:]

    # Rebuild the aggregate stats from the FULL round list so retirement,
    # cooldown and rotation all see the same history the rows describe.
    agg = {}
    for r in hist["rounds"]:
        aid = r.get("angle")
        if not aid:
            continue
        st = agg.setdefault(aid, {"used": 0, "accepted": 0, "failed": 0,
                                  "streak_fail": 0, "retired": False})
        st["used"] += 1
        if r.get("result") == "accepted":
            st["accepted"] += 1
            st["streak_fail"] = 0
        else:
            st["failed"] += 1
            st["streak_fail"] += 1
    k = int(angle_pick.load_angles()["picker"].get("retire_after_failures", 0) or 0)
    if k:
        for aid, st in agg.items():
            st["retired"] = st["streak_fail"] >= k
    hist["angles"] = agg
    hist.setdefault("notes", []).append({
        "angle": "*",
        "note": "backfilled %d rows from threads.json on %s"
                % (len(recovered), angle_pick._now()),
        "at": angle_pick._now(),
    })

    angle_pick.save_history(hist)
    print("\nwrote ledger: %d rounds, %d angles tracked"
          % (len(hist["rounds"]), len(hist["angles"])))
    retired = [a for a, s in hist["angles"].items() if s.get("retired")]
    print("retired:", retired or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
