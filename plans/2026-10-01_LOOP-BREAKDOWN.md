# KARPATHY LOOP — FULL BREAKDOWN (what's built, what's fixed, what to expect)
Companion report to the chat reply, 2026-10-01. Evidence: plans/2026-09-30_*
and plans/2026-10-01_* logs; suite 235/0; live runner pid in state/runner.pid.

## 1. WHAT WAS BUILT (the four layers)

### Layer 1 — Safety (F0)
- Dirty/unsafe repo starts REFUSE before any child launches (rollback can
  never destroy pre-existing work).
- Atomic registry/config saves (torn writes salvage instead of wiping).
- Notification failures never break round bookkeeping; pause --now only
  kills verified loop children (quote-aware PID matching).

### Layer 2 — Honesty (F1/F5)
Acceptance = rc==0 AND gate ran+passed AND HEAD provably advanced (full-SHA
ancestry check) AND the scheduler proposal computed cleanly. Publish =
tag kp/<repo>/rNN + push ONLY after the complete entry reached disk.
A rejected round is contained: worktree+branch deleted, canonical untouched,
reason recorded. Nothing unverified ever gets published.

### Layer 3 — Isolation + rotation (F2/F3/F4)
- Every round runs in a disposable git worktree (dep junctions at ANY depth
  now, push blocked at env level, verified by real-git drills).
- Rotation: LRU across repos; an OPEN campaign holds its repo at the front
  (lifetime quota 15); refusals consume their slot (persisted); no hot loops.
- Answer pipeline: your reply in state/answer.json reaches the round prompt
  exactly once (audited in answers_consumed/).

### Layer 4 — Surfaces scheduler (B1-B5) — Gene's three questions answered

Q: Can it tell surfaces apart per repo?
A: YES — surfaces are DECLARED per repo in improve.yaml (the loop's own
   contract: the manifest is authoritative). secondbrain declares
   server/android/chrome-extension; single-surface repos are byte-for-byte
   unchanged (no behavior change). Each surface carries its own 'see' info
   (how to look at it). The scheduler enforces the declared vocabulary: a
   child CANNOT invent a surface that isn't declared (refused + logged).
   Adding a surface = one line in improve.yaml (deliberate: Gene decides
   what counts as a surface; automatic discovery of undeclared nested apps
   was tried and retired as unreliable).

Q: Does it change multiple surfaces when needed?
A: YES — the campaign system. When a child's change belongs on several
   surfaces, it declares CROSS_CUTTING with the surface list; the opening
   gate requires the child to classify SCOPE: CROSS_CUTTING (UNCERTAIN
   cannot open a campaign; LOCAL contradicts and refuses). The campaign =
   N consecutive rounds sharing one checklist (not one giant round that
   would hit the 70-min kill), the rotation HOLDS on that repo so its
   rounds run back-to-back, and widening is bounded (max +2 surfaces,
   vocabulary-checked).

Q: Does it add features to a single surface where it makes sense?
A: YES — SCOPE: LOCAL is the default: one finding, scoped to ONE surface,
   with the prompt telling the child not to fix other surfaces (they get
   their own turns).

Q: Can one surface fall behind?
A: Protected at four levels: (1) surfaces rotate ROUND-ROBIN (not
   least-visited — deliberately, so a high-churn surface can't starve the
   rest); (2) inside a campaign the picker now rotates among the
   OUTSTANDING surfaces only (a held round can't be wasted on an
   already-done surface); (3) 'deferred' is NOT completion — a campaign
   cannot close on deferrals; (4) nothing runs forever: 3 attempts per
   surface, 12 rounds per campaign, 15 holds — exhaustion is a LOUD stop
   with the checklist preserved for you, never a silent drop.

Q: Do repos improve every round?
A: Every ACCEPTED round is a real improvement: gated, committed on the
   repo's branch, tagged kp/<repo>/rNN, pushed (T&T and zip-ranker push to
   GitHub; secondbrain and ai-consultancy have no remote — tag is local).
   Rejected rounds improve nothing by design and cost nothing (worktree
   deleted). The loop keeps trying; rounds_done counts accepted work only.

Q: Is there a skip when a prompt doesn't make sense for a repo?
A: YES — two levels. (1) The work order has an explicit NOT APPLICABLE
   exit: 'if after real investigation the angle genuinely does not apply,
   say so with the evidence that convinced you and stop... do not invent
   work.' The child commits its evidence writeup (e.g. secondbrain r34
   committed a 98-line not-applicable analysis) so the skip is itself a
   published, auditable artifact. (2) In campaigns, not-applicable is a
   first-class TERMINAL verdict ('this surface genuinely does not need the
   change'), tracked distinctly for your audit. A 'done' claim additionally
   needs a real repo citation — vibes don't close campaigns.

## 2. WHAT WAS FIXED (headline defects, by origin)
- The loop ran 27 tests green while the runner was DEAD (campaign module
  shadowed; timeouts left state unbound) — found by behavioral testing,
  fixed with propose/commit split + orchestration tests.
- Unconditional tag+push published FAILED rounds to GitHub (checkpoint
  authorship + publish-last ordering).
- Unbounded campaign widening via arbitrary CROSS_CUTTING (vocabulary +
  caps).
- Rejected-work contamination (rollback verified, else quarantine).
- pause --now killing unrelated sessions (quote-aware scoped kill).
- Dead model seats silently used (fail-fast validation).
- Hot-loop on persistently-refusing repos (refusals now consume their slot
  AND persist — verified live today).
- secondbrain's nested server/.venv never provisioned (nested dep-dir
  discovery — verified by live drill).
- Crash-window detection dead code (sweep condition now satisfiable).
- Test isolation leak (real registry polluted by a test) + residue cleaned.
- 2-agent debug session (GLM 5.3 Flash): 17 findings -> 11 fixed pre-arm,
  6 deferred with reasons. Suite 141 -> 235, all drills RED->GREEN.

## 3. WHAT TO EXPECT (expectation-setting)
- Cadence: ONE round at a time, 20-60+ min each, rotating across the 4
  repos by LRU. A full cycle is roughly 1.5-4h depending on outcomes.
- The first real round (T&T r36) ran 20 min and was REJECTED cleanly:
  the child's edit didn't fully land, the gate caught it, worktree
  deleted, canonical untouched, loop rotated. THAT IS THE SYSTEM WORKING.
  Expect some rounds to reject — that is the price of never publishing
  unverified work. Current: ai-consultancy r14 in flight (18+ min).
- Dirty repo -> refuse -> rotate (safe, now persists).
- Discord is currently SILENT (no bot token). Watch via:
    tail -f C:/CODING/project-improver/logs/runner.log
    python loopctl.py status / show-campaign
    monitor.html (auto-refresh dashboard)
- To pause: python loopctl.py pause (clean, mid-round drain) or
  pause --now (kills the round child; worktree contained on next start).
- If you want Discord pings per round, we need to configure the bot token.

## 4. WHAT'S LEFT (honest list)
- Observe the full accept->publish cycle LIVE (rounds are running; a
  published kp/ tag will be the final Phase C proof).
- 6 deferred low findings (documented in plans/2026-10-01_DEBUG-SESSION.md):
  threads.resolve marker-adoption hardening, pushed_to_github config honor,
  cmd_start spawn window, junction rmdir edge, monitor 0-default, cite-gate
  fail-open logging. None block rounds.
- surface_discover stays RETIRED (manual one-line manifest edits are the
  surface-discovery mechanism, by design).
- hyperframes/impeccable/video-use are in the manifest but DISABLED (no
  gates configured) — enable by adding gates + enabled:true when wanted.
- Nothing else: the master plan is fully built. Phase C = live operation.