# Karpathy Loop handoff — SUPPLEMENT (2026-10-15 ~22:35 ET)

Supersedes the seat info in HANDOFF-KARPATHY-AUDIT.md (that file's lane-repoint
context still applies; this is what changed AFTER Gene's correction).

## Gene's directive (22:15 ET): the loop's MAIN model must be GLM-5.3-Flash-EXL3

- "Running on evo-x2" was endpoint-speak, not model-speak — my muddle. Evo-x2
  (192.168.1.49:8081) serves qwen3.8-flash-next ONLY. Every round since the
  10-15 repoint ran QWEN under the GLM FP8 name. Gene caught it; he was right.

## What's live now (all verified)

- **Implementer seat: `custom:glm53-flash-tp3:GLM-5.3-Flash-EXL3`**
  → http://127.0.0.1:18888/v1 (this box, 3x Spark EXL3 TP3, 1M ctx).
  Probe: requested GLM-5.3-Flash-EXL3 → served as GLM-5.3-Flash-EXL3. 0.2s.
- **Reviewer seat: `custom:d754-mia-flashnext:qwen3.8-flash-next`** (slug
  repointed to 49:8081 on 10-15). Kept DIFFERENT from the builder on purpose:
  karpathy_runner._validate_seats refuses implementer == reviewer
  ("same blind spot twice"), and adversarial review needs a different family.
  If Gene wants EXL3 on BOTH seats, the guard must be relaxed first — ask him.
- **PROOF the new seat is live**: T&T round 94 (started 22:27:44) is writing to
  session 20261015_200618_68864e with GLM-5.3-Flash-EXL3 | 21 calls |
  last_seen 22:32 (after the 22:26 restart). The FP8 row in the same session
  is the PRE-restart round.
- Runner pid 274560, round_timeout=10800s.

## Cleanup done during the switch

- Killed the orphaned ZCR r417 round children (270856/305024/308288 +
  2 bash holders) that locked the r417 worktree after the old runner was
  force-stopped; removed worktree + branch (contained in canonical:
  01301c5) + sidecars. ZCR is pickable again.

## Seat-switch recipe (for next time)

1. Edit state/loop.json seats (full custom:slug:model form).
2. `karpathy_runner._validate_seats(loopctl.load())` — must print SEATS VALID.
3. Force-stop old runner pids (loopctl stop, then kill leftover
   python.exe w/ karpathy_runner.py in argv — F-B4 matcher now finds them).
4. `loopctl start`; verify via session_model_usage: new seat name with
   last_seen AFTER the restart + the lane's response model field.
5. Beware: a resumed session keeps its STORED route until the first accounted
   API call (sticky-route trap) — the session_model_usage check is the only
   proof; sessions.model/billing_base_url lag.
