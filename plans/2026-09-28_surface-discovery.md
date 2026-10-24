# Surface Discovery & Classification for the Karpathy Loop

STATUS: DRAFT — actively being refined by a live agent. Facts below marked [VERIFIED] have been checked against real code.

## Problem

`repo_survey.py:193 _multi_surface()` hardcodes a top-level directory allowlist
(`server`, `android`, `chrome-extension`, `web`, `app`, `client`, ...). Any surface nested
inside another (e.g. an assistant/recorder app inside `android/`) is invisible. The manifest
`improve.yaml` key `see.surfaces` is authoritative (`improver.py:505 surfaces_for`) but drifted
because nothing consumes it — it accepts either a flat comma string or a list of objects
(name/kind/where).

Goal: a standalone discovery module (`surface_discover.py`) that READS a repo and PROPOSES
surfaces into improve.yaml via human confirmation — never writes.

## What the Real Repos Actually Contain (verified facts)

SecondBrain (`C:\Users\gene\ANDROID NOTES LIKE GENSPARK`):
- `server/` — Python web server: pyproject.toml + uv.lock + app/main.py + start_server.vbs,
  egg-info `secondbrain_server` — a proper installable package. FastAPI.
- `android/` — ONE Gradle project (`settings.gradle.kts`: rootProject "SecondBrain", one
  module `:app`). `app/build.gradle.kts`: namespace/applicationId `com.gene.secondbrain`.
  ONE AndroidManifest.xml. So Gradle-wise it is a SINGLE Android app. BUT the manifest
  declares TWO user-visible feature clusters:
    * `android.service.voice.VoiceInteractionService` + `.assist.SecondBrainVoiceInteractionService`
      + `.assist.SecondBrainSessionService` (an ASSISTANT — replaces Google Assistant role)
    * `.capture.RecorderService` + `.capture.CaptureMediaService` + `.capture.CaptureTileService`
      + RECORD_AUDIO + FOREGROUND_SERVICE (a RECORDER with quick-settings tile)
  Same APK, same applicationId, two distinct workable subsystems in separate java packages
  (`com/gene/secondbrain/assist/` and `.../capture/`).
- `chrome-extension/` — manifest.json (MV3) + service-worker.js + capture.html/js.
- Top level also: docs/, scripts/, data/, testdata/ — NOT surfaces.

Tombs & Tyrants (`D:\ZCODE\TOMBS AND TYRANTS`): one Python game. `backend/` + `frontend/`
top-level dirs, play.py, start-game.bat, pytest.ini, server logs. NOT obviously single-surface
by the top-dir test — need to determine if backend+frontend are independently workable
surfaces or one coupled game. This is exactly the kind of judgment discovery must make.

lifestory: flat data-harvest scripts, no build files → likely ZERO surfaces proposed.

## Ranked Detection Signals

| # | Signal | PROVES | Merely SUGGESTS |
|---|--------|--------|-----------------|
| 1 | `build.gradle.kts`/`build.gradle` with `applicationId` + `com.android.*` plugin | An installable Android artifact exists at that dir | — |
| 2 | `manifest.json` containing `manifest_version` | A browser-extension artifact (MV2/MV3) | Which browser it targets |
| 3 | `pyproject.toml`/`requirements.txt` listing uvicorn/fastapi/flask/gunicorn | A runnable server process (needs an entrypoint to confirm) | Whether it is deployed/exposed |
| 4 | Separate gradle MODULE (settings.gradle.kts `include(...)`) | Distinct buildable artifact | Independent workability |
| 5 | Top-level dir NAMED server/web/client/... | — | A surface boundary (name alone proves nothing — this is the old broken heuristic) |
| 6 | `Dockerfile`/`docker-compose.yml` | A deployable unit exists | What it serves |
| 7 | Port binding / `uvicorn` line / `start_server.vbs` entry script | — | The server is user-facing |
| 8 | AndroidManifest `<service>` with `VoiceInteractionService` / `RECORD_AUDIO`+`RecorderService` | A distinct feature subsystem exists INSIDE the APK | Whether it deserves separate-surface status (human call) |
| 9 | README claims ("web app", "assistant", "recorder") | — | Surface boundaries — docs drift, as improve.yaml proves |

## Nested-App Rule (draft v2)

At each directory containing a build file, collect "artifact identity":
- Android: applicationId/namespace
- Node: package.json `name`
- Python: egg-info/project name

RULE: two build files in ancestor/descendant relation = SAME artifact unless identities
differ. One Gradle project with one applicationId = ONE android surface, no matter how many
apps the user thinks it contains. Splitting within it is a FEATURE-CLUSTER call, driven by
the manifest's service/permission declarations + separate java packages, and emitted at
LOWER confidence (0.5) with `kind: feature-cluster` so the human decides.

SecondBrain check: android/ has 1 settings.gradle.kts, 1 applicationId → 1 android surface
+ up to 2 feature clusters (assist/, capture/). The "5 surfaces" the user counts = server +
android app + assistant cluster + recorder cluster + extension. Discovery can find all 5
only by combining rule (identity, build) with rule (feature-cluster). Confirmed by manifest
evidence above.

## Confidence Model

(TBD — initial: build-identity signals 0.8-0.95; feature-cluster 0.5; name-only 0.2)

## Propose/Confirm Mechanism

- CLI `--propose` emits a YAML snippet under `see.surfaces` in the OBJECT-LIST shape
  (improver.py:505 accepts both shapes; object list is the richer one).
- The tool NEVER writes improve.yaml. It prints the snippet + a `propose:` diff vs what the
  manifest currently declares; human pastes/confirms.
- Confidence gate: only >= 0.5 proposals are emitted by default; `--all` includes 0.2-0.4.

## False Positive / False Negative Analysis

(TBD — fill with REAL misfires after runs)

## Real-Repo Test Results

(TBD — fill after runs)
