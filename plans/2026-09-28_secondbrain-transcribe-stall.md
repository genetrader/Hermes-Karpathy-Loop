# SecondBrain: audio notes never leave `transcribing` — 2026-09-28

## The symptom, stated precisely

**Every note with real audio duration is stuck in a non-terminal status. Every note without
audio succeeded.** Measured on the phone's own DB at 2026-09-28 21:0x:

| Note type | Count | Status |
|---|---|---|
| `durationSec = 0.0` (email / briefing notes) | 359 | **all `ready`, all carry a title** |
| `durationSec > 0` (actual recordings) | 5 | **all stuck** |

The five stuck notes:

```
4468a3f88ac4  enriching     dur=0.0      age 781.7 h
4665b8cd0316  enriching     dur=0.0      age 781.7 h
ce60161df3fe  queued        dur=1.86     age  34.7 h
fdfb0d269802  transcribing  dur=2.18     age  35.6 h
2448f1eaa49a  transcribing  dur=10.9     age   0.1 h   <- test recording, 2026-09-28
```

## Reproduction (clean, and it is the whole proof)

1. Power-button recording on the S24U, 10.9 s of audio.
2. It **uploaded on the first attempt** — queue row `id=32`, `attempts=0`,
   `uploadedAtMillis` set, `remoteNoteId=2448f1eaa49a42e6a745a40a80306b4f`.
3. The server **accepted** it: a note row appeared with status `transcribing`.
4. It **never left `transcribing`** — watched for 5 × 35 s, plus the ~35 h-old sibling.
5. **No error is written anywhere**: the note's `error` column is NULL, and the server log
   shows nothing about it.

## So the user's report was mis-attributed (by both of us)

Gene said *"my recordings did not get sent."* **They do get sent.** The upload path is
healthy — first-try success, zero retries. The loss is downstream: the server takes the
audio into `transcribing` and abandons it there, silently.

The server IS healthy in the parts that matter for this diagnosis:
- listening on `:8823`, `/health` answers 401 (proves the app is up; needs the bearer)
- log: `app.asr: Loading Whisper large-v3 on cuda (int8_float32)` … `Models warm`

**Whisper loaded and is warm. Nothing is ever handed to it.**

## RETRACTED CLAIM — do not repeat this

I reported that the process serving `:8823` was system Python missing `faster_whisper`,
and that this was the root cause. **That was wrong and has been retracted.**

- `PID 6756` ("system python") is a **child** of `PID 10812`
  (`.venv\Scripts\python.exe`) — the venv server is running and healthy.
- The `faster_whisper MISSING` reading came from probing the venv's `python.exe` as an
  interpreter. It is a launcher/redirector and does not carry the real environment.
- The server log proves the real stack is present and loads Whisper on CUDA.

**Lesson (same class as the `node --check` trap): when a CHECK reports a catastrophic
finding, verify the check before reporting the finding.** A probe that cannot see the thing
it claims to measure is not evidence.

## Where to look next (NOT yet investigated — do not guess)

Read, in this order:

1. `pipeline.py` — `submit()` and the worker that consumes `queued` notes. Specifically:
   **what marks a note `queued` → `transcribing`, and what is supposed to move it onward?**
   If nothing ever picks the note up, that is the bug and it is a wiring fault, not an ASR
   fault.
2. Is the worker a thread/background task that dies silently? Check whether an exception in
   the worker is swallowed (the project has a known pattern of empty `except` blocks —
   `_process_safely` exists, so check whether the ASR call is inside it).
3. `_handle_silent_note` (`pipeline.py`) + `auto_delete_empty` default — the 8s purge rule.
   It does NOT explain these five (they never reached a result to judge), but it is a real
   second bug and a likely contributor to notes vanishing after a late drain.
4. The two `enriching` notes at 781.7 h (32 days) — same shape, different stage. Almost
   certainly the same missing-pickup fault one step later.

## Do NOT do

- Do not restart the server expecting a fix — the restart was done and the notes did not move.
- Do not patch `run-server.ps1`'s 401-accepts-anything health check *for this bug*. That
  check is genuinely too weak (a 401 only proves *something* answers, not that the right
  server owns the port), but it is NOT the cause here and changing it mid-diagnosis adds a
  variable. Worth fixing separately.

## State at end of session

- Phone: updated, `com.gene.secondbrain` v0.3.3 (versionCode 6), `lastUpdateTime 2026-09-28 20:44:38`.
- Test recording `2448f1eaa49a` still in `transcribing`, deliberately left alone.
- Server: running healthy on `:8823`, Whisper warm. Backup written `secondbrain-20260929-005306.db` (41.3 MB).
- **No code was changed in the SecondBrain repo.** Diagnosis only.