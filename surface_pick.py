#!/usr/bin/env python3
"""
surface_pick.py -- round-robin SURFACE picker (the frontier INSIDE one project).

A surface is a sub-category of a repo (server / android / chrome-extension /
android-assistant ...). The project still advances once per round -- the rotation
sort on `last_nudge` (karpathy_runner.py:475) is untouched -- but WHERE inside the
project this round looks is decided here (design: plans/2026-09-28_surfaces.md
section 6.1).

Rules (Gene, 2026-09-28 -- locked design):
  * ROUND-ROBIN, not least-visited. Surfaces are not symmetric (android-assistant
    has far more work than chrome-extension); least-visited lets a high-churn
    surface starve the others. Round-robin gives every surface a guaranteed turn.
  * SINGLE-SURFACE NO-OP. Fewer than 2 surfaces -> pick() returns None and the
    caller behaves exactly as before surfaces existed. This is a hard
    requirement: every project without a surfaces declaration must be
    byte-for-byte unchanged.
  * RECONCILE, DON'T RESET. The manifest is authoritative for the ORDER, but the
    state's visit counts are real history. When surfaces are added/removed
    between rounds we keep `visits`, drop removed slugs, and append new slugs at
    the END so every surface still gets a turn. Re-adding one surface must not
    silently restart the whole rotation.
  * NEVER RAISE INTO THE RUNNER. A corrupt or missing state file degrades to a
    warning + first surface. Bookkeeping must never break a round -- the
    convention karpathy_runner.py:278 already states for prompt bookkeeping.

State shape (state/surfaces/<name>.json):
    {"order": [slug, ...], "last": slug, "visits": {slug: n}}

Atomic writes (tmp + os.replace) throughout -- the 2026-09-28 review (finding
B5, plans/REVIEW-TRIAGE-2026-09-28.md) showed a torn JSON save here wiped a
whole registry; that mistake is not repeated.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state" / "surfaces"


# --------------------------------------------------------------------------
# state -- per project, atomic, never raising on read
# --------------------------------------------------------------------------
def _state_path(name: str) -> Path:
    """One file per project. Project names are slugs by construction
    (improve.yaml `name:` keys); anything path-shaped is flattened so a hostile
    name cannot escape the state dir."""
    safe = str(name or "unknown").strip().lower().replace(" ", "-")
    safe = "".join(c for c in safe if c not in '\\/:*?"<>|') or "unknown"
    return STATE_DIR / (safe + ".json")


def state_for(name: str) -> dict:
    """Read a project's surface state, or the empty seed. Corrupt file -> seed,
    with the caller's reconcile step rebuilding `order` from the manifest.

    Load swallows the parse error ON PURPOSE here (unlike review finding B5,
    which flagged a swallowing load NEXT TO a non-atomic save -- the deadly
    combination). Corruption is safe precisely because every save is atomic
    below: a torn file cannot be produced by us, and losing the rotation cursor
    is a warning, not a lost registry.
    """
    p = _state_path(name)
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception as e:
        # Visible in the runner log, not fatal (convention: karpathy_runner.py:278)
        print("surface_pick: %s state unreadable (%s) -- rebuilding from manifest"
              % (name, e))
        return {}


def save_state(name: str, st: dict) -> None:
    """Atomic write. Raise-free by contract? No: `pick()` wraps its calls, and a
    REAL write failure (disk full, ACL) should be loud in tests -- so this
    raises, and the callers own the degrade-to-warning policy."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = _state_path(name)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, indent=2), encoding="utf-8")
    os.replace(tmp, p)      # atomic swap so a reader never sees a half file (B5)


def record(name: str, slug: str, order: list[str] | None = None) -> dict:
    """Persist that `slug` was just worked on. Returns the state written.

    `order` (the manifest's current list) is stored beside the cursor so the
    next pick() can RECONCILE against it instead of guessing. Accepting it here
    keeps the writer the single atomic choke point.
    """
    st = state_for(name)
    if order:
        st["order"] = [str(s) for s in order]
    st["last"] = slug
    visits = st.setdefault("visits", {})
    if not isinstance(visits, dict):
        visits = st["visits"] = {}
    visits[slug] = int(visits.get(slug, 0)) + 1
    save_state(name, st)
    return st


# --------------------------------------------------------------------------
# reconcile -- manifest order wins, history survives
# --------------------------------------------------------------------------
def reconcile(st: dict, order: list[str]) -> dict:
    """Align stored state with the manifest's current surface list.

    The manifest is authoritative for ORDER (design section 3), but resetting
    the rotation because a slug changed would (a) lose `visits` history and
    (b) restart the cycle on a one-surface rename. So:

    T2-14 HONEST LIMIT (2026-09-30): surfaces are identified by SLUG. A
    RENAME is indistinguishable from remove+add: the old slug's visits are
    dropped and the new slug waits its turn at the END of the order. No
    alias map exists -- do not claim rename continuity this module cannot
    deliver.
      * dropped from manifest  -> removed from order AND visits (its slug can
        never be picked again -- if it returns, reconcile re-appends it fresh)
      * new in manifest        -> appended at the END of order (waits its turn;
        the rotation in progress is not silently restarted)
      * visits for live slugs  -> kept verbatim
    """
    order = [str(s) for s in order]
    visits = st.get("visits") if isinstance(st.get("visits"), dict) else {}
    old_order = [str(s) for s in (st.get("order") or [])]

    new_order: list[str] = []
    for s in order:
        if s and s not in new_order:
            new_order.append(s)

    kept_visits = {s: int(visits.get(s, 0)) for s in new_order if s in visits}

    out = dict(st)
    out["order"] = new_order
    out["visits"] = kept_visits

    # `last` must still be a member, or the cursor math in pick() breaks.
    last = st.get("last")
    if last not in new_order:
        # Was the removed one the last worked? Then the rotation resumes at the
        # START of the surviving order -- deterministic, and logged by pick().
        out["last"] = ""
    # New arrivals appended at the END so they wait their turn: the rotation in
    # progress is not silently restarted just because one surface was added.
    appended = [s for s in new_order if s not in old_order]
    if appended and old_order:
        rotated = [s for s in new_order if s in old_order] + appended
        out["order"] = rotated
        if out.get("last") and out["last"] not in rotated:
            out["last"] = ""
    return out


# --------------------------------------------------------------------------
# the pick
# --------------------------------------------------------------------------
def pick(name: str, proj: dict | None = None,
          prefer: list[str] | None = None) -> dict | None:
    """One step of the project's surface rotation.

    `prefer` (review fix C, 2026-10-01): the open campaign's OUTSTANDING
    surfaces. When given and non-empty, the rotation rotates among the
    outstanding surfaces only -- a held campaign round must not be
    assigned an already-terminal surface (the checklist frontier is the
    whole point of the campaign). None keeps the legacy behavior.

    Returns
        None                    -- fewer than 2 surfaces (single-surface no-op)
        {"id": slug,
         "index": i,
         "total": n}            -- the surface this round should work on

    The returned dict is a DECISION; record() (inside pick) persists it
    immediately, the same way the runner stamps current_angle at round START
    rather than at finish (karpathy_runner.py:294). On any state problem:
    warn + first surface, never raise (a bookkeeping failure must never break
    a round -- the convention at karpathy_runner.py:278).
    """
    try:
        if proj is None:
            return None
        # Local import: improver owns manifest parsing; importing it lazily
        # keeps surface_pick importable standalone (tests monkeypatch paths).
        import improver as _I
        order = _I.surfaces_for(proj)
        if len(order) < 2:
            return None

        st = reconcile(state_for(name), order)
        # Reconcile then WRITE BACK even before choosing: if the manifest just
        # dropped a surface, its stale visits entry must leave the state NOW,
        # not linger until some later save. (A removed slug's count otherwise
        # survives forever because record() only touches the picked slug.)
        save_state(name, st)

        last = st.get("last") or ""
        _prefer = [s for s in (prefer or []) if s in st["order"]]
        if _prefer:
            # rotate among the campaign's outstanding surfaces only
            if last in _prefer:
                nxt = _prefer[(_prefer.index(last) + 1) % len(_prefer)]
            else:
                # first outstanding surface AFTER `last` in rotation order
                nxt = _prefer[0]
                if last in st["order"]:
                    _idx = st["order"].index(last)
                    for _k in range(1, len(st["order"]) + 1):
                        _cand = st["order"][(_idx + _k) % len(st["order"])]
                        if _cand in _prefer:
                            nxt = _cand
                            break
        elif last in st["order"]:
            nxt = st["order"][(st["order"].index(last) + 1) % len(st["order"])]
        else:
            # First pick ever, or the last-worked surface was just removed:
            # start at the head. Warn on the removal case so the log explains
            # why the rotation seemingly restarted -- it did not, the cursor
            # was orphaned by reconciliation.
            if last:
                print("surface_pick: %s last surface %r left the manifest -- "
                      "resuming at %r" % (name, last, st["order"][0]))
            nxt = st["order"][0]

        # Persist the DECISION immediately, not after the round: the panel will
        # show "working on <surface> now" the same way current_angle does
        # (karpathy_runner.py:294 -- chosen-at-start, not chosen-at-finish).
        record(name, nxt, order=st["order"])
        return {"id": nxt, "index": st["order"].index(nxt),
                "total": len(st["order"])}
    except Exception as e:
        # Absolute last resort: try a stateless first-surface answer before
        # giving up entirely, so even a broken state dir cannot stall a round.
        try:
            import improver as _I2
            order = _I2.surfaces_for(proj or {})
            if len(order) >= 2:
                print("surface_pick: %s bookkeeping failed (%s) -- falling back "
                      "to first surface" % (name, e))
                # T2-13 (2026-09-30): the fallback returned a DECISION but
                # never booked it -- state stayed unwritten, so the NEXT pick
                # resolved the same first surface again and the rest starved.
                # Book best-effort; a booking failure must not break the
                # round (same convention as the happy path).
                try:
                    record(name, order[0], order=order)
                except Exception as _e2:
                    print("surface_pick: %s fallback booking failed (%s)"
                          % (name, _e2))
                return {"id": order[0], "index": 0, "total": len(order)}
        except Exception:
            pass
        print("surface_pick: %s pick failed: %s" % (name, e))
        return None


# --------------------------------------------------------------------------
# cli (parity with angle_pick.py's `pull` -- one-line manual probes)
# --------------------------------------------------------------------------
def main() -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser(description="round-robin surface picker")
    ap.add_argument("cmd", choices=["pick", "state"])
    ap.add_argument("--project", required=True)
    ap.add_argument("--path", help="repo path (to read improve.yaml); omit to "
                                   "probe state only")
    a = ap.parse_args()
    if a.cmd == "state":
        print(json.dumps(state_for(a.project), indent=2))
        return 0
    proj = None
    if a.path:
        proj = {"name": a.project, "path": a.path,
                "see": _read_manifest_surfaces(a.project)}
    res = pick(a.project, proj)
    print(json.dumps(res, indent=2))
    return 0


def _read_manifest_surfaces(project: str) -> dict:
    """Pull just this project's `see:` block out of improve.yaml (tiny probe)."""
    try:
        import improver as _I
        for p in _I.enabled(_I.load_manifest()):
            if p.get("name") == project:
                return p.get("see") or {}
    except Exception:
        pass
    return {}


if __name__ == "__main__":
    raise SystemExit(main())
