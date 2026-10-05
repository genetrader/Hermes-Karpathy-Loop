#!/usr/bin/env python
"""
round_prompt.py -- the text handed to a project thread for ONE round.

This is a nudge, not a fresh briefing. The thread already knows the repo, the
history and the conventions; what it needs each round is the ANGLE, the current
gate, and an explicit anchor that live repo state outranks anything it
remembers.
"""
from __future__ import annotations

import scope  # F1-7/T3-12: terminal-verdict checks for the campaign checklist

TEMPLATE = """[KARPATHY-LOOP-TASK -- this is a work order, not a chat message. Execute it
with your tools now; do not reply with questions or acknowledgements.]

Karpathy Loop -- round {round_no} on **{project}**.

Before anything else: treat the repository's CURRENT state as authoritative and
your memory of it as a hint. Read before you edit. If something you remember
contradicts the code, the code wins.

THIS ROUND'S ANGLE: {angle_id}
{angle_text}
{worktree_block}{surface_block}{scope_question}{prompt_block}
GATE (must exit 0 before you call the round done):
    {gate}

REVIEW (different model family -- required before you call the round done)
- After your change passes the gate, get an ADVERSARIAL read from a different family
  than the one doing the work. Ask it to try to FALSIFY the finding, not to confirm it.
- Record the review verdict in your final summary as one line:
      REVIEW(<model>): accept | reject -- <one-sentence reason>
- A `reject` verdict means you either fix it in this round or you revert and report
  the angle as not-applicable with the reviewer's reasoning as your evidence.
{human_answer}{review_block}

RULES
- ONE finding this round. Do not batch several changes into one commit.
- Commit every passing round on the current branch.
- Do NOT create git tags: the runner takes the kp/{project}/r{round_no:02d}
  checkpoint itself after the round is accepted, so a tag you make by hand
  would collide with it.
- If after real investigation the angle genuinely does not apply, say so with
  the evidence that convinced you and stop. A clean "not applicable, here is
  why" is a valid round -- do not invent work.
- If a decision is genuinely yours to make, ASK the human in your thread and
  continue with the best-reasoned default; state the assumption explicitly.

WRAP-UP (before you run out of room)
- Stop starting new work, run the gate a final time, commit what passes
  (never tag -- the runner checkpoints), and write a short summary of what
  changed and what is next.
{scope_marker}{prior_block}"""


# F3.7 (2026-09-30): reviewer seats must be LIVE endpoints. The old primary
# (glm53-flash-2x-spark / EXL3) was hidden on 2026-09-30, so every round was
# offered a dead reviewer. The runner passes the loop.json seat via
# build(seats=); these constants are only the fallback for callers that do
# not pass seats (legacy/test calls). Primary and alt are different machines
# so one dead box cannot take out both seats.
REVIEWER_MODEL = "custom:d754-mia-flashnext:qwen3.8-flash-next"
REVIEWER_ALT_MODEL = "custom:evo-x2:qwen3.8-flash-next"

# The machine-readable scope line. It is PARSED (see karpathy_runner), so it must
# be requested explicitly -- a marker nobody asks for does not arrive. The
# precedent is unkind: the already-mandated REVIEW() line appeared in 0 of 3
# recent substantive rounds, so this one is placed FIRST in the required block
# and its exact format is spelled out with an example.
SCOPE_PREFIX = "SURFACE-DONE:"

# B3 (2026-09-30): the classification menu. Rendered as THREE separate lines
# on purpose -- a single "a | b | c" menu line was the T3-14 defect (children
# copied the menu verbatim onto the marker line).
SCOPE_LINE_LOCAL = "SCOPE: LOCAL"
SCOPE_LINE_CROSS = "SCOPE: CROSS_CUTTING"
SCOPE_LINE_UNC = "SCOPE: UNCERTAIN -- <what is unclear>"


def _scope_marker_instruction(sid: str, campaign: dict | None) -> str:
    """The required final lines that tell the runner what this round covered.

    Kept tiny and format-exact on purpose. The runner parses THIS, so an
    approximate answer is the same as no answer.
    """
    lines = ["", "REQUIRED FINAL LINES (the runner PARSES these -- exact format matters)",
             "0) Classify this round's finding (the OPENING GATE):",
                SCOPE_LINE_LOCAL + "   (the change belongs only on this surface)",
                SCOPE_LINE_CROSS + "   (it belongs on other surfaces too -- fill line 2)",
                SCOPE_LINE_UNC + "   (unsure it generalizes -- say what is unclear)",
             "   Emit exactly ONE of the three; never copy the menu onto the line.",
             "1) Name the surface you worked and its verdict:"]
    if sid:
        lines.append("       %s %s :: <verdict> -- <why>"
                     % (SCOPE_PREFIX, sid))
    else:
        lines.append("       %s <surface> :: <verdict> -- <why>"
                     % SCOPE_PREFIX)
    lines.append("   <verdict> is exactly ONE word: done, deferred, or"
                 " not-applicable.")
    lines.append("   Pick one -- never copy the three words themselves onto"
                 " the line.")
    if isinstance(campaign, dict) and campaign.get("surfaces"):
        lines += ["   Include EVERY surface you touched, one line each.",
                  "2) If this change ALREADY EXISTS or is needed on OTHER surfaces too,",
                  "   say so on its own line, comma-separated, so the loop can keep the",
                  "   surfaces parallel instead of drifting apart:"]
        if sid:
            lines.append("       CROSS_CUTTING: %s,<other> :: <what needs to change where>"
                         % sid)
        else:
            lines.append("       CROSS_CUTTING: <surface>,<surface> :: <what needs to change where>")
    else:
        lines += ["2) Only if this change also belongs on OTHER surfaces, add:",
                  "       CROSS_CUTTING: <surface>,<surface> :: <what needs to change where>",
                  "   Omit that line entirely when the change is local to this surface."]
    lines.append("")
    return "\n".join(lines)


def build(project: str, angle: dict, gate: str, round_no: int, prior: dict,
          surface: dict | None = None,
          campaign: dict | None = None,
          surface_see: dict | None = None,
          seats: dict | None = None,
          worktree: str | None = None,
          canonical: str | None = None,
          worktree_branch: str | None = None,
          human_answer: str = "",
          scope_question: dict | None = None) -> str:
    """Render one round's work order.

    `surface` scopes this round to ONE surface of the repo (a sub-category that
    can be worked on independently -- e.g. project-b's `server`, `android`,
    `chrome-extension`). Surfaces round-robin inside a project, so the project
    still counts as ONE visit; surface is a frontier, not a rotation slot.

    `campaign` is the CROSS-CUTTING case (Gene, 2026-09-28): when one change has
    to land on several surfaces, the surfaces are worked as CONSECUTIVE rounds
    sharing one checklist rather than one giant round. That is deliberate -- a
    single child doing all surfaces would run 250-300 min against a 70 min
    ROUND_TIMEOUT and be killed mid-edit with the tree dirty (measured, see
    plans/2026-09-28_surfaces.md 6.2). This prompt therefore keeps the ONE
    finding rule and asks for a per-surface verdict instead of batching.

    `seats` (F3.7) carries the live reviewer seat from loop.json; without it
    the module constants (live slugs) are used, so legacy callers see live
    seats too.

    `worktree`/`canonical`/`worktree_branch` (F3) render the isolation
    boundary: the child is told exactly which disposable working copy it is
    in and that the original checkout is off-limits. Without them the block
    is empty.
    `human_answer` (T2-6) is the pre-rendered HUMAN ANSWER block from the
    runner (empty when there is none), so an operator reply reaches the
    round that asked the question.
    `scope_question` (B3, 2026-09-30) is the OPEN SCOPE QUESTION the runner
    carries between rounds: the child classified a finding UNCERTAIN and
    the question is unsettled. Rendered as a settle-it-this-round block;
    None renders nothing (legacy callers unchanged).
    All of these are optional so every existing caller behaves exactly as
    before.
    before.
    """
    aid = ((angle or {}).get("id") or (angle or {}).get("angle")
           or (angle or {}).get("name") or "unspecified")
    atext = ((angle or {}).get("text") or (angle or {}).get("lens")
             or (angle or {}).get("evidence_required") or "")

    # ---- SURFACE SCOPE -------------------------------------------------------
    surface_block = ""
    sid = ((surface or {}).get("id") or "").strip() if isinstance(surface, dict) else ""
    if sid:
        total = (surface or {}).get("total")
        idx = (surface or {}).get("index")
        pos = ""
        if isinstance(idx, int) and isinstance(total, int):
            pos = "  (surface %d of %d in this project's rotation)" % (idx + 1, total)
        lines = ["", "THIS ROUND'S SURFACE: %s%s" % (sid, pos)]
        # How to LOOK at this surface, when the manifest describes one. Copy-only:
        # the start command is never executed by the harness.
        desc = (surface_see or {}).get(sid) if isinstance(surface_see, dict) else None
        if isinstance(desc, dict):
            if desc.get("url"):
                lines.append("    view        : %s" % desc["url"])
            if desc.get("where"):
                lines.append("    lives at    : %s" % desc["where"])
            if desc.get("note"):
                lines.append("    note        : %s" % desc["note"])
            if desc.get("start"):
                lines.append("    start (copy, never auto-run): %s" % desc["start"])
        lines += ["    Scope your finding to THIS surface. Do not fix other surfaces",
                  "    in this round -- they get their own turns."]
        lines.append("")
        surface_block = "\n".join(lines)

    # ---- OPEN SCOPE QUESTION (B3) --------------------------------------------
    # An unsettled UNCERTAIN classification from a previous round. The block
    # asks THIS round to settle it; the runner clears the entry the moment a
    # LOCAL/CROSS_CUTTING classification arrives.
    scope_question_block = ""
    if isinstance(scope_question, dict) and (scope_question.get("round") is not None
                                             or scope_question.get("note")):
        _qr = scope_question.get("round")
        _qn = (scope_question.get("note") or "").strip()
        _qlines = ["", "OPEN SCOPE QUESTION (from round %s -- unsettled):" % (_qr or "?")]
        if _qn:
            _qlines.append('    "%s"' % _qn)
        _qlines += ["    Settle it THIS round: either SCOPE: CROSS_CUTTING (then fill",
                    "    the CROSS_CUTTING line with the surfaces) or SCOPE: LOCAL.",
                    "    Do not leave it uncertain twice.", ""]
        scope_question_block = "\n".join(_qlines)

    # ---- CAMPAIGN (cross-cutting, one checklist across rounds) ---------------
    scope_marker = ""
    if isinstance(campaign, dict) and campaign.get("surfaces"):
        surfs = [str(s) for s in campaign["surfaces"]]
        verdicts = campaign.get("verdicts") or {}
        # F1-7 / T3-12 (2026-09-30): "done" here means a TERMINAL verdict
        # (done / not-applicable), not merely "the child said anything" --
        # truthiness rendered `deferred` as a checked box and counted it as
        # accounted-for, which is exactly the half-done drift the campaign
        # exists to prevent. Verdicts are rendered as their clean WORD: the
        # stored shape may be a dict {verdict, reason}, and printing that
        # raw put a python repr on the checklist.
        def _word(v):
            if isinstance(v, dict):
                v = v.get("verdict")
            return str(v).strip().lower().replace("_", "-") if v else ""
        done = [s for s in surfs if scope._is_terminal(verdicts.get(s))]
        todo = [s for s in surfs if not scope._is_terminal(verdicts.get(s))]
        lines = ["", "CROSS-CUTTING CAMPAIGN: %s" % (campaign.get("id") or "(unnamed)"),
                 "This change touches several surfaces and must stay parallel across them.",
                 "This round still does ONE finding -- on ONE surface. The campaign closes",
                 "only when every surface below carries a verdict.", ""]
        for s in surfs:
            v = verdicts.get(s)
            w = _word(v)
            lines.append("    [%s] %s%s" % ("x" if scope._is_terminal(v) else " ",
                                            s, (" -- " + w) if w else ""))
        lines += ["", "    %d of %d surface(s) done." % (len(done), len(surfs))]
        if todo:
            lines += ["    Still outstanding: %s" % ", ".join(todo),
                      "    Prefer working an outstanding surface this round."]
        lines.append("")
        scope_marker = "\n".join(lines)

    # Only ask for the scope lines when a surface is actually in play. A legacy
    # call (no surface, no campaign) must produce the SAME prompt it always did
    # -- adding a required final line to every round would change behaviour for
    # every existing caller and make the old prompts look wrong.
    if sid or (isinstance(campaign, dict) and campaign.get("surfaces")):
        scope_marker += _scope_marker_instruction(sid, campaign)

    # ---- PROMPT LAYER --------------------------------------------------------
    # `angle` must carry a concrete prompt for the round to be actionable. When
    # the picker supplies one (angle_pick.pick adds `prompt_id` + `prompt`), we
    # render it in full: preconditions, what to hunt, the required evidence, and
    # the clean not-applicable exit. An angle without a prompt degrades to the
    # old behaviour (lens + look_for only) rather than failing the round.
    prompt_block = ""
    pr = (angle or {}).get("prompt") or {}
    if pr:
        pid = (angle or {}).get("prompt_id") or "(unnamed)"
        applies = (pr.get("applies_when") or "").strip()
        hunt = (pr.get("hunt") or "").strip()
        ev = (pr.get("evidence") or "").strip()
        na = (pr.get("not_applicable") or "").strip()
        parts = ["", "THIS ROUND'S PROMPT: %s" % pid]
        if applies:
            parts += ["", "APPLIES WHEN (check this BEFORE editing; if it does not "
                      "hold, see NOT APPLICABLE below)", applies]
        if hunt:
            parts += ["", "WHAT TO HUNT", hunt]
        if ev:
            parts += ["", "EVIDENCE REQUIRED (this is what makes the round "
                      "count as done)", ev]
        if na:
            parts += ["", "NOT APPLICABLE (the clean exit -- consume the prompt, "
                      "invent nothing)", na]
        parts.append("")
        prompt_block = "\n".join(parts)

    rounds = int((prior or {}).get("rounds") or 0)
    prior_block = ""
    if rounds:
        prior_block = ("\nCARRIED CONTEXT: this thread has completed %d prior round(s). "
                       "Its compaction summary holds what they tried -- do not repeat a "
                       "finding already fixed.\n" % rounds)
    # ---- F3 ISOLATION BOUNDARY ----------------------------------------------
    worktree_block = ""
    if worktree:
        wt_lines = ["", "THIS ROUND'S WORKING COPY: %s" % worktree]
        if worktree_branch:
            wt_lines.append("    (git worktree on branch %s, created for THIS round)"
                            % worktree_branch)
        if canonical:
            wt_lines.append("    The ORIGINAL checkout at %s is OFF-LIMITS:"
                            % canonical)
            wt_lines.append("    do not open, edit, or commit there. Commit here, on this")
            wt_lines.append("    branch; the RUNNER merges accepted work into the")
            wt_lines.append("    original checkout itself.")
            wt_lines.append("    Pushing is disabled for this round (the runner publishes).")
            wt_lines.append("    Run the gate with THIS copy as your working directory (the")
            wt_lines.append("    original's .venv / node_modules are junctioned in, so the")
            wt_lines.append("    gate command works as-is).")
        wt_lines.append("")
        worktree_block = "\n".join(wt_lines) + "\n"

    # Offer the concrete reviewer seats so the round does not have to invent one.
    # The runner hands the live loop.json reviewer seat via `seats` (F3.7).
    _rev = (seats or {}).get("reviewer") or REVIEWER_MODEL
    _alt = (seats or {}).get("alt") or REVIEWER_ALT_MODEL
    review_block = (
        "  Reviewer seats available on this box (use a DIFFERENT family than yours):\n"
        "      primary: %s\n"
        "      alt    : %s\n"
        "  Drive it headlessly if you like:\n"
        "      hermes -p default --model %s -z \"<your diff + the claimed finding; "
        "try to falsify it>\"\n"
        "  If the reviewer endpoint is unreachable, say so explicitly and mark the "
        "review INCONCLUSIVE rather than claiming it passed."
        % (_rev, _alt, _rev))
    return TEMPLATE.format(project=project, round_no=round_no, angle_id=aid,
                               angle_text=atext, gate=gate, prior_block=prior_block,
                               prompt_block=prompt_block,
                               surface_block=surface_block,
                               worktree_block=worktree_block,
                               scope_marker=scope_marker,
                               scope_question=scope_question_block,
                               review_block=review_block,
                               human_answer=human_answer)
