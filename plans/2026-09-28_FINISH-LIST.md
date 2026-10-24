# FINISH-LIST — everything outstanding, in order

Gene, 2026-09-28: *"please complete the build-out of everything that we have talked about.
Get this working."*

## A. SecondBrain — the transcription stall (his actual bug)

| # | Item | Evidence | State |
|---|---|---|---|
| A1 | Root-cause why audio notes never leave `transcribing` | 359/359 non-audio = ready; 5/5 audio = stuck | **IN PROGRESS** |
| A2 | Fix it, then prove it against the existing stuck note `2448f1eaa49a` | that note is the test fixture | todo |
| A3 | `UploadWorker.kt:52` — `Result.success()` on config failure never retries | read verbatim by me | todo |
| A4 | No connectivity callback → nothing re-kicks the queue when the server returns | grep count 0 | todo |
| A5 | `UploadWorker.kt:62-66` — vanished file silently deleted, no dead-letter | read verbatim by me | todo |
| A6 | `auto_delete_empty` purges "silent" captures by default (≥93% of notes are ≤8s) | `runtime_settings.py:31` | todo — needs Gene's call |

## B. Surfaces (his requirement: cycle surfaces, round-robin, one turn can hit all)

| # | Piece | State |
|---|---|---|
| S1 | `surfaces_for` + round-robin picker + atomic state | **DONE, 13/13 verified by me** |
| S2 | Both manifest shapes | **DONE** |
| S3 | Thread surface into the round prompt | todo |
| S4 | CAMPAIGN mode (corrected design: N rounds, one checklist) | todo |
| S5 | `surface_see:` per-surface viewability | todo |
| S6 | Widget surface rows under `cot` (NOT per-row `see`) | todo |
| S7 | Payload budget tested under LIVE-round pressure | todo |
| S8 | Declare Gene's 5 real surfaces incl. `android-assistant` | todo |

## C. Housekeeping carried from the earlier review

| # | Item | State |
|---|---|---|
| C1 | B6: `pause --now` can kill unrelated interactive Hermes sessions | todo |
| C2 | `threads.json` non-atomic save (whole-registry loss risk) | todo |
| C3 | `run-server.ps1` 401-accepts-anything health check | todo — real, but NOT the transcribe cause |

## Order of work

1. **A1/A2 first** — it is the bug Gene actually reported and the only thing currently
   losing him data.
2. Then S3/S4 (the corrected design), then S5-S8.
3. Then A3-A5 (Android hardening) and C-items.

**Rule for every item:** small piece → gate → verify against the REAL system → report the
actual command output. No claim without a pasted result.