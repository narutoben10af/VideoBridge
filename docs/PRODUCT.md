# Product requirements

## Outcome

Choose a video in Safari, Chrome, Firefox or Finder, select Apple TV, and watch only that video with its audio and chosen subtitles while continuing to use the Mac. No desktop, notifications, browser chrome or other applications appear on the TV. The Mac app may need to remain running and awake; this is not a promise of independent playback after shutting down the Mac.

Working name: VideoBridge. Local-first macOS application, initially for the user's Apple Silicon Mac and Apple TV. Exact macOS/tvOS versions, receiver model and network conditions must be recorded during M0 rather than inferred from this Mac's compiler version.

## User requirements and success criteria

| ID | Requirement | Observable acceptance |
|---|---|---|
| R1 | Safari, Chrome and Firefox | Each browser sends the selected video to the same installed app; a physical TV test passes for every claimed browser. |
| R2 | Variety of websites, especially Anikoto | Generic HTML video and an embedded cross-origin player pass. The supplied Anikoto page is tested explicitly, including its separate subtitles; unsupported servers are reported by name. |
| R3 | Local videos | Open, drag-and-drop and Finder Open With accept local files without modifying them. The original player's presence is irrelevant to playback. |
| R4 | QuickTime/other player workflow | User explicitly accepted reopening the file in our app. A share/service shortcut may be added later. Capturing arbitrary third-party player pixels is not required and must not be represented as implemented. |
| R5 | Selectable soft subtitles | Text track appears on Apple TV, can be switched/off, stays in sync after pause/seek and survives browser backgrounding. Local captions alone do not pass. |
| R6 | Mac remains usable | Switch apps, switch tabs and minimize browser for at least 10 minutes; TV continues and other Mac audio is unaffected. |
| R7 | Quality and speed | Choose a low-cost playback route; measure startup, CPU, memory, cache growth and subtitle timing. Meet declared performance targets or document limits before claiming support. |
| R8 | Future expansion | Add a source adapter/format handler through a contract and fixtures without changing the receiver session state machine. |
| R9 | Modern native macOS design | Main player and compact controller follow the approved Apple-inspired design direction; actual Liquid Glass uses supported native APIs and is verified in the running app. |
| R10 | Compact menu-bar remote | Play/pause, ten-second seeking, subtitle selection and reopening the main window work while the main window is closed or minimized, using the same session. |
| R11 | Stable pause/resume and seeking | Repeated pause/resume and ten-second seeks do not require corrective rewinds to restore audio/video/subtitle sync in the physical receiver test. |

## First supported tiers (targets, not current claims)

1. Finite MP4/MOV with compatible video/audio; local SRT/WebVTT and embedded text subtitles.
2. HLS VOD with alternate subtitles and browser-provided external subtitle tracks, including cross-origin players.
3. MKV and codec conversion through a bounded media pipeline. ASS/SSA may convert to selectable WebVTT with explicit styling loss; exact typesetting is a separate rendering mode.
4. Later: additional audio tracks, HDR preservation, image subtitles (PGS/VobSub), live streams, queues, chapter/episode continuity and player-specific shortcuts.

Image subtitles cannot be assumed convertible into accurate selectable text. Plan either explicit burn-in (no longer a soft subtitle) or an optional reviewed OCR workflow. Never call either equivalent to preserved soft subtitles. Preserve forced/default flags and language identity where supported; record expected differences on the receiver.

## Primary flows

Browser: start video → click extension → inspect/select the actual player and subtitle language → choose receiver → confirm TV playback → optionally pause the source video. Do not pause it until receiver readiness is established. A resumed/current position must transfer accurately when supported; otherwise the UI states where playback starts.

Local: Open video / drop file / Finder Open With → inspect tracks and choose sidecar if desired → show any conversion loss → select Apple TV → play. Originals are read-only. Stop ends playback, cancels workers and revokes session URLs.

Failure: show the failed stage and a useful next action (expired stream: refresh and resend; blocked embedding: grant access to this player domain; unavailable receiver: check network). Keep source playback usable. No screen-mirroring fallback.

## Explicit boundaries

No universal-every-site guarantee, DRM bypass, login/cookie export by default, credential storage, cloud video upload, account system or subscription backend. Authenticated sources are a later per-origin, explicitly scoped feature. Browser-local blob URLs must be resolved to their underlying supported source or reported unsupported. Separate audio/video DASH tracks require a tested demux/mux route rather than treating a page URL as a media URL.
