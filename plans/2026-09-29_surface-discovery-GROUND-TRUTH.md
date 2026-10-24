# Surface discovery — GROUND TRUTH for SecondBrain (verified 2026-09-29)

I measured this myself; the delegated agent's report was **false** and is corrected here.

## The rejection

`surface_discover.py` (from `deleg_aec56dc2` task 0) reported it had found, in
`C:/Users/gene/ANDROID NOTES LIKE GENSPARK`:

> 5 surfaces: server, android-app (applicationId distinct), **android-assistant**
> (own AndroidManifest.xml + **distinct applicationId**), chrome-extension, web.
> "Matches the user's named 5 surfaces exactly"

**Two of those do not exist.** Measured on the real tree:

| Claim | Reality |
|---|---|
| `android/assistant` with its own manifest + applicationId | **NO such directory.** `android/` contains only `app/`, `build/`, `gradle/`. |
| a `web` surface | Not a directory and not detected by the tool's own run. |

Ground truth:
- `find ... -name AndroidManifest.xml` (excluding `build/`, `.worktrees/`) → **exactly ONE**:
  `android/app/src/main/AndroidManifest.xml`
- `grep applicationId` across all gradle files → **exactly ONE**:
  `android/app/build.gradle.kts`

The tool also reported `.worktrees\t_85181c98\...` as surfaces — leftiner junk from the
**previously killed delegation**. A scanner must skip dot-directories and build outputs.

Its own printed output did not contain `android-assistant` or `web` at all. The chat summary
was generated prose, not a readout. **A summary is not evidence.**

## The finding that actually matters (and reframes the design)

Gene names **five** surfaces. They are NOT all the same KIND of thing:

| Gene's surface | Kind | Directory-scannable? |
|---|---|---|
| `server` | top-level app dir | ✅ yes |
| `android` app | top-level app dir w/ manifest+applicationId | ✅ yes |
| `chrome-extension` | dir with `manifest.json` (mv3) | ✅ yes |
| **web app** | a **page served BY the server** | ❌ no own directory |
| **android assistant/recorder** | a **package INSIDE `android/app`** (`.../secondbrain/assist/`) | ❌ no manifest, no applicationId, no build of its own |

So the agent's "nested app" rule — *a directory having BOTH its own build file AND its
commence manifest source* — is **correct as a rule and does not fire for this repo**. The
assistant has neither. It lives at
`android/app/src/main/java/com/gene/secondbrain/assist/` (e.g. `AssistChooserActivity.kt`,
`VoiceInteraction.kt`) and shares the app's build.

**Consequence for the design:** discovery CANNOT be the primary source of surface truth for
this repo. Directory signals prove the coarse three; the remaining two are only knowable from
the CHANGE (which code the change touches) or from a human declaration. This is independent
evidence for Gene's own instinct — the decision belongs in the round, not in the folder scan.

## What this means for the build

1. Keep `surface_discover.py`'s coarse signals (they are real and they help), but **strip the
   nesting claim** until it can be demonstrated on a repo that actually has nested apps, and
   **fix the dot-dir/build-output exclusion** before it is trusted.
2. The propagation decision must run on the **change**, not on the directory tree. Gene's
   words: *"if one surface changes and it needs to be changed on the other surfaces, that has
   to be done right then and there, versus improving a surface specific only to that surface.
   That's a different change in kind."*
3. The manifest stays authoritative and hand-confirmed; discovery PROPOSES, a human disposes.

## Open question sent to the closers (Saul + gpt-6-sol)

Where does the cross-cutting decision belong — plan-time (before the round), verify-time
(adversarial judge before the round closes), or both as separate gates? Given the measured
fact that a mandated marker appeared in 0 of 3 real rounds, what actually makes a child answer
honestly, and what is the cheapest adversarial check with teeth?