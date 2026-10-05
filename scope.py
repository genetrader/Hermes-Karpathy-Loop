#!/usr/bin/env python
"""scope.py -- read the scope markers a round emits, and advance a campaign.

Gene, 2026-09-28: surfaces can be worked one at a time, but when a change has to
land on SEVERAL surfaces "they need to be done at that time... so that things
stay parallel with each other". That is a campaign.

WHY A CAMPAIGN AND NOT ONE BIG ROUND
An adversarial review measured that a single child doing all five of a repo's
surfaces would run 250-300 minutes against ROUND_TIMEOUT=4200 s (70 min), be
killed mid-edit, and -- because the timeout path still advances `last_nudge` --
rotate onward with a half-applied edit and no wedge flagged. So a campaign is N
conscutive rounds sharing one checklist. The parallel INTENT is preserved; the
parallel blast radius is not.

WHAT THIS PARSES
The round prompt asks for exact final lines (round_prompt.SCOPE_PREFIX):

    SURFACE-DONE: server :: done -- added the field end to end
    CROSS_CUTTING: server,android :: clients need the new field

Parsing happens against the child's FULL final message, before the runner
truncates it to 2000 B (see karpathy_runner.run_round). A marker read from the
truncated tail is found almost never -- a real final message measured 6,148 B.

PROPSE / COMMIT SPLIT  (Saul, 2026-09-29 -- correctness blocker)
The first version had ONE mutating function (`next_campaign`) that the runner
called BEFORE it knew whether the round had succeeded. That let a child mark a
surface done, or open/close a campaign, on a round that then FAILED its gate:
campaign state was driven by the child's CLAIM.

So the logic is now split:
  * `propose()` -- PURE. Writes nothing. Says what the campaign WOULD become.
  * `commit()`  -- MUTATING, called only after the runner has ACCEPTED the round
                   (rc == 0 + runner-observed gate + evidence consistent).
A rejected round can no longer touch campaign state. The three facts stay
separate: child claim, runner observation, scheduler transition.

BOUNDED WIDENING  (Saul blocker #4)
A campaign may widen, but only from the repo's DECLARED surface vocabulary, at
most MAX_ADDITIONS times, and monotonically -- a surface is never silently
dropped. Once attempts are exhausted the campaign goes BLOCKED, preserving its
outstanding slugs, rather than quietly vanishing.

HONEST LIMITS -- do not overstate this
- A `done` verdict is the CHILD'S CLAIM, not a verified fact. There is one
  project-level gate command, so the runner cannot independently verify each
  surface. `commit(accepted=True)` records that the ROUND passed; it is not
  per-surface proof.
- An unparsable or absent marker is NOT an error. Most rounds will have nothing
  to say here, and a round that fails to emit the line must not fail the round.
"""
from __future__ import annotations

import re

# `SURFACE-DONE: <slug> :: <verdict> [-- reason]`
_DONE_RE = re.compile(
    r"^\s*SURFACE-DONE:\s*(?P<surface>[A-Za-z0-9._-]+)\s*::\s*(?P<rest>.+?)\s*$",
    re.MULTILINE)
# `CROSS_CUTTING: a,b,c :: what`
_CROSS_RE = re.compile(
    r"^\s*CROSS_CUTTING:\s*(?P<surfaces>[^:]+?)\s*::\s*(?P<what>.+?)\s*$",
    re.MULTILINE)
# `SCOPE: LOCAL | CROSS_CUTTING | UNCERTAIN [-- what is unclear]` (B3,
# 2026-09-30). The opening gate: a child UNSURE a change generalizes must be
# able to SAY so -- the old design left LOCAL implicit, so an uncertain child
# and a certain-local child were indistinguishable and drift could start
# silently. First classification wins within one message (sticky, like
# terminal verdicts); extras are COUNTED, never silently swallowed (T3-4).
# Review fix L1 (2026-10-01): tolerate plausible punctuation variants
# (colon note separator, hyphenated cross-cutting, missing colon) -- the
# strict shape failed OPEN (an UNCERTAIN line read as absent, letting a
# CROSS_CUTTING line open a campaign).
_SCOPE_RE = re.compile(
    r"^\s*SCOPE\s*[:=]?\s*(?P<cls>LOCAL|CROSS_CUTTING|CROSS-CUTTING|UNCERTAIN)\b"
    r"(?:(?:\s*[--:]|\s+)\s*(?P<note>.+?))?\s*$",
    re.MULTILINE | re.IGNORECASE)

VERDICT_WORDS = ("done", "deferred", "not-applicable", "not_applicable")

# WHICH VERDICTS ACTUALLY COMPLETE A SURFACE.
#
# Not every verdict is completion. A checklist whose whole purpose is to stop a
# change landing on one surface and never reaching the others MUST NOT close on
# "deferred" -- that is literally the bug it exists to prevent. (Found by an
# external review, 2026-09-29: the first version closed on any verdict text,
# including arbitrary prose.)
#
#   done              TERMINAL. The surface is finished.
#   not-applicable    TERMINAL only as an explicit, reasoned decision. It is the
#                     honest exit for "this surface genuinely does not need the
#                     change", so it must be able to close a campaign -- but it
#                     is tracked distinctly so a reviewer can audit it later.
#   deferred          NON-TERMINAL. Work moved intentionally; still outstanding.
#   failed            NON-TERMINAL, and counts an attempt.
#   unknown / prose   NON-TERMINAL. An unrecognised verdict must never be read
#                     as success just because it was well-formed.
TERMINAL_VERDICTS = ("done", "not-applicable")

# --------------------------------------------------------------- bounds (Saul)
#
# A campaign must not widen forever, and an exhausted campaign must not silently
# drop a surface. These caps make both true. They are deliberately small: the
# cost of a campaign that gives up visibly is one operator glance, while the cost
# of one that expands forever is an unattended loop that never advances.
MAX_ADDITIONS = 2               # evidence-backed surface additions per campaign
MAX_ATTEMPTS_PER_SURFACE = 3    # then that surface stops being retried
MAX_ROUNDS_PER_CAMPAIGN = 12    # absolute ceiling, whatever the surface count
# B2 (2026-09-30): an OPEN campaign holds the repo rotation so its checklist
# rounds run back-to-back. This caps how many consecutive rounds one held
# campaign may claim (belt-and-braces: the campaign ceiling already bounds
# it; a wedged-open campaign must not monopolize the loop either).
MAX_CAMPAIGN_HOLDS = MAX_ROUNDS_PER_CAMPAIGN + 3


def parse(text: str) -> dict:
    """Pull the scope markers out of a round's final message.

    Returns {"surfaces": {slug: {"verdict": str, "reason": str}},
             "cross_cutting": {"surfaces": [...], "what": str} | None,
             "cross_dropped": int}   # T3-4: extra CROSS_CUTTING lines dropped

    Never raises. Malformed lines are ignored rather than aborting a round: the
    markers are an optimisation for the loop, not a correctness requirement.
    """
    out = {"surfaces": {}, "cross_cutting": None, "cross_dropped": 0,
           "scope_class": None, "uncertain_note": "", "scope_dropped": 0}
    if not text:
        return out

    # B3: the scope classification. First line wins (sticky); later lines
    # are counted so a contradictory second answer stays visible.
    _scopes = list(_SCOPE_RE.finditer(text))
    if _scopes:
        out["scope_class"] = (_scopes[0].group("cls").upper()
                               .replace("-", "_"))
        if out["scope_class"] == "UNCERTAIN":
            out["uncertain_note"] = (
                (_scopes[0].group("note") or "").strip().lstrip("-").strip()[:300])
        out["scope_dropped"] = max(0, len(_scopes) - 1)

    for m in _DONE_RE.finditer(text):
        slug = m.group("surface").strip().lower()
        rest = m.group("rest").strip()
        verdict, reason = rest, ""
        if "--" in rest:
            head, reason = rest.split("--", 1)
            verdict = head.strip()
            reason = reason.strip()
        verdict = verdict.strip().lower().replace("_", "-")
        # Normalise anything unrecognised to the raw text so it is visible in
        # the checklist rather than silently swallowed.
        if verdict not in ("done", "deferred", "not-applicable"):
            # T2-9 (2026-09-30): the old `(rest if not reason else rest)` ternary
            # ALWAYS yielded `rest`, so a verdict the child had explained with a
            # real `-- reason` lost that reason and the audit line instead
            # carried the raw text INCLUDING the verdict word
            # (repro: 'mostly-fine -- actual reason here' -> reason became
            # 'mostly-fine -- actual reason here'). Keep the parsed reason; when
            # no `-- reason` was given, the verdict word IS the raw text and the
            # reason stays empty -- the verdict field already shows it.
            reason = reason.strip()
            verdict = verdict or "unknown"
        _prev = out["surfaces"].get(slug)
        if _prev is not None and _is_terminal(_prev.get("verdict")):
            # T3-14 (2026-09-30): a terminal verdict is STICKY within one
            # message. The old last-wins overwrite let a late `not-applicable`
            # silently downgrade an earlier `done` (and any later line erase a
            # completion). First terminal VERDICT WORD wins; a non-terminal
            # verdict still upgrades normally when a terminal one arrives
            # later.
            # Review fix A (2026-10-01): a later TERMINAL line may still
            # carry a BETTER REASON (the child self-correcting a vague
            # 'vibes' into a real citation in the same message). Merge the
            # reason instead of discarding the line -- the B4 cite gate
            # otherwise downgraded a done that DID carry a citation and cost
            # a whole extra round.
            if _is_terminal(verdict):
                _prev["reason"] = (reason or _prev.get("reason") or "")[:200]
                if len(reason) > 200:
                    _prev["reason_full"] = reason[:600]
            continue
        _entry_v = {"verdict": verdict, "reason": reason[:200]}
        if len(reason) > 200:
            # review c-5 (2026-09-30): a citation past char 200 must survive
            # for the closing gate -- keep a bounded full-reason field so the
            # gate sees what the child actually wrote.
            _entry_v["reason_full"] = reason[:600]
        out["surfaces"][slug] = _entry_v

    # T3-4 (2026-09-30): finditer + first-match meant a SECOND CROSS_CUTTING
    # line in the same message was silently ignored -- the child's "also
    # needed on other surfaces" signal vanished without a trace. Count the
    # drops so the runner can log them (the marker is advisory; a dropped
    # line must still be VISIBLE).
    crosses = list(_CROSS_RE.finditer(text))
    out["cross_dropped"] = max(0, len(crosses) - 1)
    m = crosses[0] if crosses else None
    if m:
        surfs = []
        for s in m.group("surfaces").split(","):
            s = s.strip().lower()
            if s and s not in surfs:
                surfs.append(s)
        # A single surface is not "cross-cutting" -- it is just this round.
        if len(surfs) > 1:
            out["cross_cutting"] = {"surfaces": surfs,
                                    "what": m.group("what").strip()[:300]}
    return out


# ============================================================ PROPOSE (pure)

def propose(entry: dict, markers: dict, project: str,
            declared: list | None = None,
            cite_ok=None) -> dict:
    """What the campaign WOULD become. PURE -- writes nothing.

    Never raises. `declared`, when given, is the repo's authoritative surface
    vocabulary (from the manifest); a surface outside it is REFUSED and reported
    rather than folded in, because arbitrary child markers must not be able to
    invent surfaces.

    Returns:
      {"campaign": dict|None,   # the campaign this round would leave behind
       "closed":   dict|None,   # record to append when it completes
       "blocked":  bool,        # exhausted -> move to BLOCKED, keep outstanding
       "refused":  [slug, ...], # outside the declared vocabulary
       "capped":   bool,        # widening refused (hit MAX_ADDITIONS)
       "proposal_ok": bool,     # FALSE => infrastructure failure, see below
       "reason":   str}

    ON `proposal_ok` (Saul, 2026-09-29)
    A blanket `except` that returns a harmless-looking no-op recreates the exact
    failure mode that hid the original AttributeError for weeks: the loop carries
    on as though nothing happened. A proposal that could not be computed is an
    INFRASTRUCTURE FAILURE, and the caller must refuse the round -- a no-op must
    never be indistinguishable from a legitimate "nothing to propose".
    """
    res = {"campaign": None, "closed": None, "blocked": False,
           "refused": [], "capped": False, "reason": "", "proposal_ok": True,
           "unblocked": False, "uncertain": False, "contradicted": False,
           "uncited": []}
    try:
        return _propose(entry, markers, project, declared, res, cite_ok)
    except (TypeError, ValueError, KeyError, AttributeError, IndexError) as e:
        # Malformed INPUT: a bad entry or marker shape. Expected, recoverable,
        # and reported so the caller can refuse the round rather than proceed.
        res["proposal_ok"] = False
        res["reason"] = "propose rejected malformed input: %s: %s" % (type(e).__name__, e)
        return res
    # A programming error is NOT caught here. It propagates to the runner's
    # failure boundary with a traceback, because a code bug must be loud -- that
    # is precisely what the old silent swallow got wrong.


def _propose(entry, markers, project, declared, res, cite_ok=None):
    existing = entry.get("campaign") if isinstance(entry.get("campaign"), dict) else None
    markers = markers or {}
    verdicts = markers.get("surfaces") or {}
    cross = markers.get("cross_cutting")
    # `declared` is the manifest's surface vocabulary. THREE states, and the
    # difference matters:
    #   None / []      -> the repo declares NO surfaces. There is no vocabulary to
    #                     enforce, so no filter (and, in practice,
    #                     `surface_pick` returns None and no campaign is ever
    #                     proposed for this repo at all). Treating this as
    #                     "refuse everything" would silently kill campaigns on
    #                     every repo that has not yet adopted surfaces -- 3 of the
    #                     4 enabled repos today.
    #   ["server",...] -> enforce it. A name outside it is refused.
    declared_set = set(declared) if declared else None
    # --- B3 OPENING/WIDENING GATE (2026-09-30) ---------------------------
    # The classification the child emitted GATES what the cross marker may
    # do. LOCAL contradicts a CROSS_CUTTING line (the child said the change
    # belongs only on this surface, then listed other surfaces) -- refuse to
    # open or widen, but keep folding this round's verdicts. UNCERTAIN may
    # not open a checklist at all: an unsure claim must not drive N rounds.
    # Absent classification = legacy caller shape, which opens as before.
    _cls = (markers or {}).get("scope_class")
    if _cls == "LOCAL" and cross:
        res["contradicted"] = True
        res["reason"] = ("scope gate: SCOPE: LOCAL contradicts the CROSS_CUTTING "
                         "line -- no campaign opened or widened")
        cross = None
    elif _cls == "UNCERTAIN":
        res["uncertain"] = True
        res["reason"] = ("scope gate: classification UNCERTAIN -- campaign not "
                         "opened or widened")
        cross = None


    # Copy the two mutable containers so a PURE propose can never alias the
    # caller's entry -- the split is worthless if propose mutates in place.
    #
    # EVERY surface, including the opening round's, is admitted through ONE
    # filter below. An earlier draft built the opening campaign straight from
    # cross["surfaces"] and only vocabulary-checked LATER widening -- so a first
    # round could invent any surface name and sail past the check. The declared
    # vocabulary is the only authority on what a surface may be called; it must
    # bound the campaign from its first instant, not from its second round.
    camp = None
    if existing:
        camp = dict(existing)
        camp["surfaces"] = list(existing.get("surfaces") or [])
        camp["verdicts"] = dict(existing.get("verdicts") or {})
        camp["attempts"] = dict(existing.get("attempts") or {})
        camp["additions"] = list(existing.get("additions") or [])
    elif cross:
        camp = {"id": None,                      # set once the set is final
                "surfaces": [],
                "what": cross.get("what") or "",
                "opened_round": entry.get("rounds"),
                "verdicts": {}, "attempts": {}, "additions": [],
                "state": "open"}

    if camp is None:
        return res

    # --- admit surfaces: bounded, vocabulary-checked, monotone -------------
    # One path for the opening round AND for later widening, so they cannot
    # diverge.
    if cross:
        for s in cross["surfaces"]:
            if s in camp["surfaces"]:
                continue
            if declared_set is not None and s not in declared_set:
                if s not in res["refused"]:
                    res["refused"].append(s)
                continue
            # remaining capacity, RECOMPUTED per candidate. Computing the cap once
            # before the loop let a campaign at MAX_ADDITIONS-1 admit TWO more
            # surfaces (Saul, 2026-09-29) -- the bound must hold for every
            # individual admission, not just the first.
            # T2-10 (2026-09-30): the opening round's surface list is the
            # campaign's BASELINE (cleared into additions=[] below) -- the
            # additions cap is budget for LATER widening only. Capping the
            # opening admission silently dropped declared surfaces (repro:
            # CROSS_CUTTING a,b,c with declared=[a,b,c] admitted only a,b and
            # reported capped=True). Skip the cap while the campaign is opening.
            if camp["id"] is not None and len(camp["additions"]) >= MAX_ADDITIONS:
                res["capped"] = True
                continue
            camp["surfaces"].append(s)
            camp["additions"].append(s)

    # An opening round that named ONLY undeclared surfaces leaves an empty
    # candidate campaign: that is not a campaign, it is noise. Drop it rather
    # than opening a checklist with nothing on it.
    if camp["id"] is None:
        if not camp["surfaces"]:
            res["reason"] = ("opening CROSS_CUTTING declared no usable surface"
                             + (" (refused: %s)" % ", ".join(res["refused"])
                                if res["refused"] else ""))
            return res
        camp["id"] = _slug(project, camp["surfaces"], entry.get("rounds"))
        # the opening round's surfaces are the campaign's baseline, not
        # "additions" -- the addition budget is for LATER widening.
        camp["additions"] = []

    # --- fold this round's verdicts ---------------------------------------
    made_terminal_progress = False
    for slug, v in verdicts.items():
        vmap = v if isinstance(v, dict) else {"verdict": v}
        new = (vmap.get("verdict") or "unknown")
        # --- B4 closing gate: an uncited `done` is not terminal ------
        if new == "done" and cite_ok is not None:
            try:
                _cited = bool(cite_ok(vmap.get("reason_full")
                                      or vmap.get("reason") or ""))
            except Exception:
                _cited = False          # fail closed (see default_cite_check)
            # review c-5 (2026-09-30): the gate evidence lives in DEDICATED
            # fields, not spliced into the 200-char reason -- a citation past
            # char 200 must survive in persisted state, and an uncited long
            # reason must not lose the gate note to truncation.
            vmap = dict(vmap)
            if _cited:
                vmap["cite"] = "ok"
            else:
                res["uncited"].append(slug)
                new = "done-uncited"
                vmap["cite"] = "none"
                vmap["gate_note"] = _cite_note().strip()
        old = camp["verdicts"].get(slug)
        old_v = old.get("verdict") if isinstance(old, dict) else old
        # NEVER let a non-terminal verdict ERASE an existing terminal one: a
        # later `deferred` must not un-finish a surface that already passed.
        if _is_terminal(old_v) and not _is_terminal(new):
            continue
        if slug in camp["surfaces"]:
            if _is_terminal(new) and not _is_terminal(old_v):
                # T2-11 (2026-09-30): a round that moved a surface to a
                # TERMINAL verdict is EVIDENCE-BACKED PROGRESS on that
                # surface. It must not count as another failed attempt
                # against the cap, and if the campaign was BLOCKED this is
                # exactly the "explicit reason to unblock" the block exists
                # for: an outstanding surface just went terminal.
                made_terminal_progress = True
                camp["attempts"][slug] = 0
            elif not _is_terminal(new):
                camp["attempts"][slug] = int(camp["attempts"].get(slug) or 0) + 1
            # carry the gate evidence fields (cite/gate_note/reason_full)
            # through to the stored verdict -- rebuilding the dict here dropped
            # them (review c-5 follow-up).
            _stored = {"verdict": new,
                       "reason": (vmap.get("reason") or "")[:200]}
            for _k in ("cite", "gate_note", "reason_full"):
                if _k in vmap:
                    _stored[_k] = vmap[_k]
            camp["verdicts"][slug] = _stored

    # --- completion / exhaustion ------------------------------------------
    outstanding = outstanding_surfaces(camp)
    # Review fix L3 (2026-10-01): a legacy/migrated campaign without
    # opened_round must not inherit the whole repo history -- default the
    # opening to the CURRENT round so the ceiling counts forward.
    opened = int(camp.get("opened_round") or int(entry.get("rounds") or 0))
    rounds_used = int(entry.get("rounds") or 0) - opened

    if not outstanding:
        res["closed"] = {"id": camp.get("id"), "surfaces": camp["surfaces"],
                         "verdicts": camp["verdicts"],
                         "additions": camp["additions"],
                         "closed_round": entry.get("rounds")}
        res["campaign"] = None
        res["reason"] = "complete"
        return res

    # Exhausted? A cap is a LOUD stop with the work preserved -- never a silent
    # drop, and never an automatic reopen.
    starved = [s for s in outstanding
               if int(camp["attempts"].get(s) or 0) >= MAX_ATTEMPTS_PER_SURFACE]
    ceiling = rounds_used >= MAX_ROUNDS_PER_CAMPAIGN
    if ceiling or (len(starved) == len(outstanding)):
        # T2-11: a BLOCKED campaign reopens when an outstanding surface went
        # terminal THIS round (attempts cleared above). The block exists to
        # an outstanding surface is the opposite of hopeless.
        if camp.get("state") == "blocked" and made_terminal_progress and not ceiling:
            camp["state"] = "open"
            camp.pop("blocked_round", None)
            camp.pop("blocked_because", None)
            res["unblocked"] = True
            res["reason"] = ("unblocked: an outstanding surface went terminal "
                             "this round")
        else:
            if camp.get("state") == "blocked":
                # Review fix L4 (2026-10-01): a silent round on an already-
                # BLOCKED campaign must not re-mark it -- blocked_round/
                # blocked_because record when the block HAPPENED, not the
                # latest idle round.
                res["reason"] = ("campaign already blocked: %s"
                                 % (camp.get("blocked_because") or "?"))
            else:
                camp["state"] = "blocked"
                camp["blocked_round"] = entry.get("rounds")
                camp["blocked_because"] = (
                    "every outstanding surface hit the attempt cap"
                    if len(starved) == len(outstanding)
                    else "campaign round ceiling reached")
                res["blocked"] = True
                res["reason"] = camp["blocked_because"]
    else:
        camp["state"] = "open"
    res["campaign"] = camp
    if not res["reason"]:
        bits = []
        if res["refused"]:
            bits.append("refused undeclared surface(s): %s" % ", ".join(res["refused"]))
        if res["capped"]:
            bits.append("widening capped at %d" % MAX_ADDITIONS)
        res["reason"] = "; ".join(bits)
    return res


# ============================================================ CLOSING GATE (B4)

# A `done` verdict is a CLAIM. Section "HONEST LIMITS" above says so. The
# closing gate narrows the claim until the runner can partially check it: a
# done whose reason cites NO verifiable repo path is downgraded to the
# non-terminal verdict `done-uncited`. Consequence: an uncited claim can
# never be terminal, so the verdict-map-full close check cannot fire on it --
# a campaign cannot CLOSE on an unverifiable claim. Re-emitting the verdict
# WITH a citation next round takes the normal terminal-upgrade path
# (attempts reset; T2-11 unblock interplay preserved).
#
# Only `done` is gated. `not-applicable` stays terminal ungated: it is the
# reasoned exit for "this surface does not need the change", a citation does
# not apply to it, and it is already tracked distinctly for audit.

_PATH_TOKEN_RE = re.compile(
    r"[A-Za-z0-9_.\-]+(?:[/\\][A-Za-z0-9_.\-]+)+"   # slash paths
    r"|\b[A-Za-z0-9_\-]+\.[A-Za-z0-9]{1,6}\b")       # bare file names
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__",
              ".gradle", "build", "dist", ".idea"}
_INDEX_CAP = 20000


def _repo_file_index(root):
    """Bounded one-shot relative-path index of a repo tree.

    Deterministic (sorted walk), skips dependency/build dirs, and stops at
    _INDEX_CAP entries so a huge tree cannot stall a round. Returns a set of
    lowercase relative paths plus a lowercase basename set for bare names.
    Returns the tuple (relative_paths, basenames).
    """
    import os
    files, bases = set(), set()
    root = str(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        for f in sorted(filenames):
            rp = f if rel == "." else rel + "/" + f
            files.add(rp.lower())
            bases.add(f.lower())
            if len(files) >= _INDEX_CAP:
                return files, bases
    return files, bases


def default_cite_check(root):
    """Build the B4 predicate for a repo tree: reason -> cited?

    A reason is CITED when at least one path-shaped token in it resolves to
    a real file: an EXACT relative path (slash tokens -- review c-1), or a
    bare filename that exists anywhere under the tree. This is a
    claim-NARROWING gate (the reason must mention a real repo file), NOT
    evidence verification: appending an unrelated real filename would
    pass it, and that is the accepted limit (review c-2). The predicate
    never raises: any internal error is read as NOT cited (fail-closed --
    a gate that crashes open is not a gate).
    """
    # Lazy index (review c-4): rounds that emit no done verdict never pay
    # the walk. The index builds on FIRST predicate call.
    _idx = []

    def _files():
        if not _idx:
            try:
                _idx.extend(_repo_file_index(root))
            except Exception:
                _idx.extend((set(), set()))
        return _idx[0], _idx[1]

    def _ok(reason) -> bool:
        files, bases = _files()
        if not files and not bases:
            return False          # empty index (unreadable tree) -> fail closed
        try:
            toks = _PATH_TOKEN_RE.findall(str(reason or ""))
        except Exception:
            return False
        for t in toks:
            # review fix B (2026-10-01): lstrip('./') strips a leading DOT
            # character, so '.github/workflows/ci.yml' could never exact-
            # match. Strip only a literal './' (or '../') prefix.
            tl = t.lower()
            while tl.startswith("./") or tl.startswith("../"):
                tl = tl[2:] if tl[1:2] == "/" else tl[3:]
            tl = tl.replace("\\", "/")
            if "/" in tl:
                # review c-1 (2026-09-30): a slash path must resolve EXACTLY --
                # the basename fallback let 'invented/location/README.md'
                # pass whenever any README.md existed anywhere.
                if tl in files:
                    return True
            elif tl in bases:
                return True
        return False

    return _ok


def _cite_note():
    return " [closing gate: no verifiable repo citation in the done claim]"


# ============================================================ COMMIT (mutating)

def commit(entry: dict, prop: dict, accepted: bool) -> dict | None:
    """Apply a PROPOSAL to `entry`. Returns the campaign left behind, or None.

    `accepted` is the runner's verdict on the ROUND (rc == 0, gate passed,
    evidence consistent) -- NOT the child's claim. When it is False this
    function refuses to change campaign state at all and only records the
    rejected claim for audit. That refusal is the whole point of the split.
    """
    if not isinstance(prop, dict):
        return entry.get("campaign")
    if not accepted:
        # Preserve the rejected claim separately. Campaign state is UNTOUCHED so
        # a failed round cannot open, advance, or close anything.
        # T3-5 (2026-09-30): only record a rejection when the proposal CARRIED
        # a claim (a campaign or a closure). A no-op proposal on a failed
        # round is not a rejected claim -- recording every failed round as
        # "rejection" buried the real ones.
        if not (prop.get("campaign") or prop.get("closed")):
            return entry.get("campaign")
        rej = entry.get("campaign_rejections")
        if not isinstance(rej, list):
            rej = []
        rej.append({"round": entry.get("rounds"),
                    "reason": prop.get("reason") or "round not accepted",
                    "refused": prop.get("refused") or []})
        entry["campaign_rejections"] = rej[-20:]
        return entry.get("campaign")

    camp = prop.get("campaign")
    if prop.get("closed"):
        closed = entry.get("campaigns_closed")
        if not isinstance(closed, list):
            closed = []
        closed.append(prop["closed"])
        if len(closed) > 20:
            # The [-20:] cap silently dropped closure records; a dropped
            # closure is a completed campaign nobody can audit. Say so.
            print("scope: campaigns_closed cap hit -- dropping %d record(s)"
                  % (len(closed) - 20), flush=True)
        entry["campaigns_closed"] = closed[-20:]
        entry.pop("campaign", None)
        return None
    if camp is None:
        return entry.get("campaign")
    entry["campaign"] = camp
    return camp


def outstanding_surfaces(camp) -> list:
    """Surfaces in this campaign with no TERMINAL verdict yet, in checklist order."""
    if not isinstance(camp, dict):
        return []
    verdicts = camp.get("verdicts") or {}
    return [s for s in (camp.get("surfaces") or []) if not _is_terminal(verdicts.get(s))]


def _is_terminal(verdict) -> bool:
    """Is this verdict an actual COMPLETION of its surface?

    `verdicts[slug]` is stored in two shapes by design, because the widget and
    older entries predate the richer form:
      * a bare string  ("done")                     -- legacy / summary view
      * a dict         ({"verdict": "done", ...})   -- current
    Accept both. An unrecognised verdict is NOT terminal: an odd word must never
    be read as success.
    """
    v = verdict.get("verdict") if isinstance(verdict, dict) else verdict
    if not isinstance(v, str):
        return False
    return v.strip().lower().replace("_", "-") in TERMINAL_VERDICTS


def _slug(project: str, surfaces: list, round_no) -> str:
    # T2-12 (2026-09-30): the id previously encoded only the surface count, so
    # two sequential two-surface campaigns on one repo shared the id `p-x2`
    # (reproduced by lane 2). The opening round number disambiguates them.
    if not surfaces:
        return project
    return "%s-x%d-r%d" % (project, len(surfaces), int(round_no or 0))


def summary_line(camp: dict | None) -> str:
    """One-line human summary for the log/panel."""
    if not camp:
        return "no campaign"
    n = len(camp.get("surfaces") or [])
    done = [s for s in (camp.get("surfaces") or [])
            if _is_terminal((camp.get("verdicts") or {}).get(s))]
    left = outstanding_surfaces(camp)
    state = camp.get("state") or "open"
    tail = (" (outstanding: %s)" % ", ".join(left)) if left else ""
    return "%s [%s]: %d/%d surfaces%s" % (camp.get("id"), state, len(done), n, tail)