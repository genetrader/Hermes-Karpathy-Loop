# SecondBrain recording loss — investigation, 2026-09-28

Gene's report, verbatim:

> "we also have an android assistant app that records and supposed to send things to the
> app which when the server was down my recordings did not get sent even though they
> should be, if they're not able to be transcribed, they should be saved. And when the
> server is back up, then transcribed, that didn't happen."

## Verdict: the source already implements the durable queue I expected it to be missing

I told Gene this was probably "a real feature, not a small fix" — that if no queue existed,
the job was to build one. **I was wrong, and the correction matters.** The queue exists,
is committed, and is the design the behaviour calls for.

Independently verified by me (not taken from the agent's report):

| Claim | Evidence |
|---|---|
| Recording is written to app storage first | `AudioRecorder.kt` → `capture-<UUID>.ogg` in `filesDir` |
| The queue row is inserted BEFORE any network attempt | `CaptureRepository.kt:126` `database.pendingUploads().insert(upload)`; the doc comment at `CaptureRepository.kt:25` reads *"Enqueueing is deliberately durable-first: the row is written before any…"* |
| WorkManager retries under a network constraint, exponential backoff | `UploadWorker.kt` `enqueue(...)` at `CaptureRepository.kt:61`, `NetworkType.CONNECTED` |
| The code is committed, not a working-tree experiment | `git status --porcelain` → clean; last commit `1861785 feat: land the accumulated work across server, Android and extension` |
| The server never loses a payload to a transcription failure | `routers/notes.py` writes audio to disk, inserts status `queued`, THEN submits to the pipeline |

**So the reported symptom does not match the current source.** The likeliest explanation is
that the phone is running an older build. The source even carries comments describing this
exact historical incident as already fixed.

**This is NOT yet confirmed** — I have not seen the installed APK version. That is the one
test that settles it, and it needs the phone.

## The four REAL defects that survive, all independently verified

### 1. `UploadWorker.kt:52` — `return Result.success()` on a config failure (SEVERITY: real)

```kotlin
if (api == null) {
    val message = configurationMessage(apiResult.exceptionOrNull())
    outstanding.forEach { dao.markFailed(it.id, message) }
    textOutstanding.forEach { textDao.markFailed(it.captureId, message) }
    return Result.success()          // <-- gives up permanently
}
```

The rows are marked failed and recoverable, but the worker reports SUCCESS, so WorkManager
never retries. The queue then sits until the app is opened or settings are saved.

### 2. No connectivity callback anywhere in the Android source (SEVERITY: real)

Verified by grep across every `.kt`: `registerNetworkCallback` / `NetworkCallback` /
`connectivityManager` → **0 hits total.**

The worker is enqueued only at: recording finished, app cold-start, settings saved, or a
manual retry. WorkManager's own constraint covers most cases, but nothing re-kicks the
queue the moment the server comes back mid-lifecycle. **This is the closest match to
Gene's actual words: "when the server is back up, then transcribed, that didn't happen."**

### 3. `UploadWorker.kt:62-66` — a vanished file is silently deleted

```kotlin
if (!file.exists()) {
    Log.w(TAG, "File vanished for upload ${item.id}; dropping")
    dao.delete(item.id)
    continue
}
```

A log line is the only evidence. No dead-letter row, so it never appears in
Settings → Sync Issues.

### 4. Server-side silent purging (SEVERITY: needs a decision, not a patch)

`pipeline.py:_handle_silent_note` with `runtime_settings.py` defaulting
`auto_delete_empty=True` at ≤8s: a capture Whisper judges silent is **hard-deleted**,
audio unlinked, unless a toggle is off. A short recording that drains after downtime and
transcribes as empty is then purged with no trace. Plausible contributor to "did not get
sent."

## Next step (needs the phone)

Confirm the installed APK version vs the repo. If the phone is behind, the fix is an
install; if it is current, defects 1-2 are the live cause.

**No files were modified in the SecondBrain repo.** Investigation only.