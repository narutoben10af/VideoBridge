# Quality, performance and regression strategy

## Levels of evidence

1. Compile/static: native APIs and schemas compile; not playback proof.
2. Deterministic unit: parsing, planning, timestamps, state transitions and validation behave on fixtures.
3. Local integration: real FFmpeg output, HTTP serving, AVPlayer media selection and cancellation work on this Mac.
4. Browser integration: an installed extension handles an actual tab/frame and hands off the chosen video/track.
5. Physical receiver: Apple TV shows video and selected soft subtitles while the Mac remains usable.
6. Release: signed installation, permissions, updates and the supported matrix pass on declared systems.

Record these separately. Do not upgrade a result from one level to the next without running its checks.

## Required acceptance matrix

| Case | Media / behavior | Gate |
|---|---|---|
| L01 | MP4 H.264/AAC, no captions, direct/remux | TV video/audio; no desktop capture |
| L02 | MP4 + SRT containing Latin/CJK and two timed cues | Select/off; correct glyphs and timing |
| L03 | Local WebVTT with overlapping cues, cue settings and offset | Timing and expected rendering recorded |
| L04 | Embedded text subtitles, multiple languages and default/forced flags | Correct track identity/default and switching |
| L05 | MKV HEVC + text subtitles | Explicit codec plan, quality policy and stable conversion |
| L06 | ASS/SSA styled captions | Explicit styling loss before conversion; selectable output or separately chosen burn-in |
| L07 | PGS/VobSub | Clear unsupported status until a tested image path exists; never silent omission |
| B01 | Generic HTML video and track elements in each browser | End-to-end source/track handoff |
| B02 | Cross-origin embedded player | Scoped access and correct frame provenance |
| B03 | Anikoto selected server | Actual stream + soft subtitles on TV; preserve source fallback |
| B04 | HLS master with audio/subtitle groups | Preserve groups, language and timestamp mapping |
| B05 | Blob/MSE player, separately fetched captions | Resolve supported underlying media or precise unsupported result |
| B06 | Multiple videos/ads, navigation/reload, expired URL | Correct selection; stale candidates cleared; useful retry |
| X01 | DRM/login-protected/malformed source | Fail clearly; no credential leakage or false success |
| N01 | Wi-Fi, Ethernet, VPN, multiple interfaces | Receiver can reach selected interface; no loopback URL sent to TV |
| N02 | Receiver disconnect/reconnect and PIN prompt | State recovers or offers bounded retry without replaying unwanted media |
| S01 | Pause/seek +/-10s/long seek/track change | Video/audio/subtitle sync stays within target |
| S02 | Background browser, switch app/tab, minimize windows | Continuous TV playback for 10 minutes |
| S03 | Stop during inspect, subtitle extraction, encode and playback | All workers end; URLs revoked; caches cleaned |
| S04 | Low disk, 8 GiB session bound, restart after crash | Predictable stop and recovery; no original deletion |
| P01 | Path traversal, invalid token, oversized IPC/HTTP, protocol downgrade | Rejected without data exposure or process failure |
| P02 | Hostile nested manifests/redirects/local paths | Source access policy enforced at every hop |
| P03 | 20 start/stop cycles and 60-minute session | No orphan processes, monotonically growing cache, or subtitle drift |

## Performance targets to measure (not achieved claims)

Reference machine/receiver/network must be stated for every measurement. Use a small synthetic clip for correctness and a bounded representative-duration fixture for performance.

- UI remains responsive; interactive actions normally complete within 100 ms without synchronous media inspection on the main thread.
- Direct/local playback first frame: target <=3 seconds after receiver connection; remux <=5 seconds; hardware transcode <=10 seconds. Measure connection negotiation separately from media preparation.
- Subtitle timing error: target <=100 ms at known cues before and after a seek. Audio/video error target <=80 ms where measurable. Record actual methodology/receiver observation precision.
- Direct/remux route: target average process CPU <15% of one core and steady RSS <300 MiB. Hardware transcode gets its own measured budget, not a promise of negligible load.
- Stop/cancel: worker group exits and session URL stops serving within 2 seconds in ordinary conditions, with a bounded forced-termination deadline.
- Cold/warm startup, repeated seeks, signed-URL expiry and reconnect timings recorded separately; three runs for basic comparison, larger samples before p95 claims.
- Generated cache has an explicit configurable ceiling (prototype 8 GiB) and respects free-space safety. Production should avoid preparing an entire movie when a bounded window suffices.

If targets fail, profile the failing stage before changing architecture. Do not erase functionality, subtitles, validation or security to meet a benchmark.

## Test design for fewer future regressions

- Keep synthetic media generation reproducible; never check in commercial videos, private paths or live signed URLs.
- Parser tests cover alternate HLS groups, quoted attributes, relative URLs, redirects, discontinuities, timestamps and non-ASCII metadata.
- Subtitle tests include SRT comma timestamps, VTT headers/notes, overlap, empty cues, negative offsets, CJK, ASS dialogue escapes and expected styling-loss metadata.
- Contract tests run the same protocol corpus against each browser transport and native receiver. Include version mismatch, duplicate request, timeout and stale session callbacks.
- Worker tests use actual child processes for cancellation and exit behavior; mocked success is insufficient for orphan/cache cleanup claims.
- Local server tests cover GET/HEAD, range requests, missing files, malformed headers, token revocation, concurrency and traversal. No wildcard CORS to arbitrary websites.
- Live website checks remain a small opt-in compatibility suite. The deterministic suite must run without the site being available.
- Device acceptance is a concise human-verifiable checklist with exact clip/cue/time/browser/receiver data. A screenshot of the Mac cannot prove TV rendering.

## Diagnostics

Use stable event/error codes and a session correlation ID. Record stage timings, route, codec names, subtitle count/language, bytes and worker exit reason. Redact URLs/queries, cookies, titles and local paths by default. Keep a bounded ring buffer; export only on explicit user action. Errors distinguish discovery, permission, expired source, unsupported media, packaging, network reachability, receiver and subtitle selection failures.
