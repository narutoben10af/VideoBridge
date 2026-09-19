# Browser/native contract proposal v1

This is a proposed contract, not the current spike's URL scheme implementation. Implement schema validation and shared fixtures before browser integration.

## Requests

Every message has `protocolVersion: 1`, a unique `requestId`, `type` and `payload`. Reject unknown versions/types/fields that affect authority. Limit envelope size, URLs, captions, candidates and TTL. Native responses echo requestId. Duplicates are idempotent. The browser transport authenticates the installed extension identity; a web page cannot impersonate the extension with a page event.

`inspectMedia` payload:

- `source`: kind `remoteMedia`; HTTP(S) URL; source MIME/kind hint; tab/frame/origin provenance; discovery time and expiry if known.
- `title`: bounded display text, never executable HTML.
- `positionSeconds`: finite nonnegative position in the source timeline.
- `subtitles[]`: opaque ID, HTTP(S) source or explicitly bounded extracted cues, format, language, label, default/forced flags and offset seconds.
- `audioTracks[]`: declared language/role when known; verify during media inspection.
- `capabilities`: source-side seek/refresh information and DRM indication.
- `credentialContext`: absent by default; future opaque capability reference, never a bulk cookie/header dump.

Browser-origin messages cannot carry a filesystem path. Local files are created as descriptors by native user actions and authorized separately. Request origin is not trusted solely because it appears in JSON.

Other messages: `getCapabilities`, `prepare(requestId, planId)`, `startSession`, `pauseSession`, `seekSession`, `selectSubtitle`, `stopSession`, `getSessionStatus`. Any state-changing command names the session ID and current generation.

## Responses/events

`accepted` means schema/authority validation only. `planReady` reports route (direct/remux/transcode), supported operations, tracks, losses and estimated resource cost. `mediaReady` means local readiness. `externalPlaybackActive` means Apple's API reports an external route. None of these prove visual subtitle rendering on TV.

`failed` contains stable code, stage, retryable flag and user-readable next action. Initial codes: `VERSION_MISMATCH`, `SOURCE_EXPIRED`, `FRAME_PERMISSION_REQUIRED`, `SOURCE_AUTH_REQUIRED`, `DRM_UNSUPPORTED`, `FORMAT_UNSUPPORTED`, `SUBTITLE_UNSUPPORTED`, `SUBTITLE_PREPARATION_FAILED`, `RECEIVER_UNAVAILABLE`, `NETWORK_UNREACHABLE`, `STORAGE_LIMIT`, `CANCELLED`.

Browser source is paused only after explicit handoff policy and receiver readiness, and only for the selected player instance. If that player has navigated or changed source, do not pause a different video. A failed handoff leaves original playback recoverable.

## Compatibility

Major version changes require explicit negotiation. Optional future capabilities are discoverable and default off. Shared fixture corpus must exercise Firefox/Chrome native messaging and Safari's native extension adapter. Do not make Chrome-specific callback APIs part of the portable core.
