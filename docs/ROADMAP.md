# Delivery roadmap

Milestones are evidence gates, not date promises. Finish the smallest uncertain end-to-end path before scaling implementation. M0 is the current next step. No prerequisite is implied complete by the existence of prototype code.

| Milestone | Work | Exit evidence | Dependencies |
|---|---|---|---|
| M0: physical feasibility | Synthetic MP4 → native AirPlay; same video + two soft subtitle languages via HLS; pause/seek/off; browser/app backgrounding | Video, audio, chosen language and sync confirmed on actual Apple TV. Record hardware/OS/network and startup measurements. | Reachable Apple TV; local network permission; macOS SDK; tiny generated fixture |
| M1: reliable local core | Split coordinator/inspector/planner/worker/relay; file/drop/Open With; SRT/VTT/embedded text; cancellable jobs and bounded cache | Local matrix, cleanup and subtitle timing tests pass; no original file changes; direct/remux/transcode route is visible | M0 passes; choose pinned worker packaging |
| M2: Firefox priority site | Shared extension core + Firefox native messaging; all permitted frames; media and track provenance; Anikoto/MegaPlay candidate selection | Actual priority page on TV with soft subs, seek and continued Mac use; no unrelated-tab collection | M0/M1; inspect real network/track structure with scoped permission; temporary extension validation |
| M3: Chrome and Safari parity | Chrome adapter; Safari containing app/extension; protocol compatibility; installation workflow | Same synthetic and real-site cases pass on each browser; browser restart and handoff acknowledgments verified | Full Xcode for Safari; signing/package decisions; M2 shared core |
| M4: compatibility/performance | MKV, HEVC/audio conversions, ASS loss reporting, media master preservation, seek restart, multi-interface network handling | Expanded matrix with measured budgets, error recovery, CPU/cache tests; every supported claim backed by evidence | M1–M3 stable |
| M5: usable beta | Permission onboarding, minimal settings, receiver reconnect, redacted diagnostics, signed distribution, dependency/license review | Clean-machine install/update/uninstall; 60-minute soak; 20 start/stop cycles; recorded known limits | All required browser/local/subtitle acceptance |
| Later, separately scoped | Image subtitles/burn-in, exact ASS rendering, live/DASH expansion, queues, auto-next, player-specific shortcuts, HDR/surround | Each adds its own fixtures, loss policy and device tests | Explicit priority and stable beta |

## Immediate next work

1. Record actual macOS version, Apple TV model/tvOS, network and AirPlay availability. No guessed compatibility matrix.
2. Use the synthetic local subtitle fixture to check AVPlayer's available `.legible` group and AirPlay playback. Confirm captions physically on TV; developer logs alone cannot do this.
3. Fix any timestamp/receiver-path issue before implementing extensions.
4. Convert prototype lifecycle into cancellable workers and isolate contracts. Fix known prototype limitations listed in STATUS.
5. Implement and test the Firefox discovery path against the identified embedded player. Treat signed URLs as transient and redact them from evidence.
6. Only then broaden to Chrome/Safari and expanded containers.

## Planning decisions still needing evidence

- Does the tested Apple TV receive the intended soft track and retain it after seeks/route reconnects?
- Can the priority site's stream and subtitle files play outside the browser without session credentials? Does the active server expose WebVTT/SRT, HLS subtitle groups, or only rendered cues?
- What does Safari's supported packaging workflow require on this machine once full Xcode is available?
- Which codecs/containers can stay direct or remuxed on this exact receiver? Which HDR/audio properties must be preserved?
- Is a signed app distribution needed beyond this Mac? Decide before choosing bundled FFmpeg licensing/build configuration and installation strategy.

## Work sizing rule

A task should own one observable behavior and its failure cases. Examples: "Sidecar SRT becomes a selectable HLS track and stays aligned after seek" or "Firefox identifies video in a permitted cross-origin frame." Avoid broad tasks such as "support all streaming sites." Preserve a failing sanitized fixture for every compatibility fix.
