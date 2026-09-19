# Current checkpoint — 2026-09-19

## Outcome and project

VideoBridge is an early local-first macOS AirPlay prototype. The user requested Safari/Chrome/Firefox sources, local file entrypoints, selectable soft subtitles and continued Mac use. The public repository is https://github.com/narutoben10af/VideoBridge. Source is developed on a feature branch and reviewed through a protected-main PR.

## Verified evidence

- Native Swift app compiled and an ad-hoc-signed archive passed signature verification.
- 14 local tests passed: real HLS video/audio decode, separate subtitle rendition/cue preservation, token/path rejection, byte ranges, unique subtitle names, cancellation, timeout, parent loss, output limits and descendant cleanup.
- Synthetic fixture: 30 seconds, 640×360 H.264/AAC, visible timer, separate English/Chinese subtitle tracks, approximately 2.41 MiB. No personal media used.
- Timeline inspection: output video is shifted +21.016 ms relative to source; constant across 750 frames, no measured drift in this fixture. Audio shift +21.333 ms. WebVTT cue timestamps unchanged. Broader timestamp behavior remains unverified.
- Native UI detected an Apple TV route and read back English selection.
- User confirmed actual Apple TV video and English captions, then Chinese, Off and seeking all worked. This is user-assisted physical receiver evidence, not automated TV capture.
- Testing host: Apple Silicon, macOS 27.0 build 26A428. Apple TV model/tvOS not yet recorded. User also confirmed normal TV playback/captions while using another Mac app or minimizing VideoBridge. This was a short clip check, not a 10-minute soak. The user described the receiver as latest generation/latest tvOS; exact versions remain unrecorded.

## Current implementation

SwiftUI/AppKit file/drop entrypoint, AVPlayer preview, public AirPlay picker, legible-track selection/readback, bundled synthetic test entrypoint and helper status. Python media inspection/preparation with an owned cancellable process worker, FFmpeg HLS output, text-to-WebVTT rendition conversion, token-scoped generated-file serving on the selected IPv4 interface and preliminary free-space/session ceilings.

Firefox/Chrome development extensions and versioned native messaging are implemented on the browser-handoff branch. Automated discovery/contract tests pass; both development extensions and companion registrations are installed. Complete browser-to-TV acceptance remains pending. The actual episode stream plus a selected English WebVTT file were handed from Firefox to the app and successfully prepared and played locally. Apple TV was selected, but AVPlayer did not report active external video playback; actual TV display remains unconfirmed. No original user video was modified.

## Known limitations

1. Browser handoff uses a private bounded inbox and opaque UUID launch token. Firefox/Chrome live acceptance and Safari implementation remain open.
2. An HTTPS broker validates and pins public DNS addresses and rewrites nested HLS references. Independent review found malformed-URI handling and expired-inbox recovery defects; both are fixed, along with oversized numeric request handling, and independent remediation checks pass. Login-bound/DRM sources are not supported claims.
3. Production HTTP limits, full range semantics, crash recovery and all lifecycle race cases need broader tests. Tested cancellation covers inspection/subtitle work and the normal relay lifecycle.
4. Preparation can create a whole-movie cache (8 GiB ceiling); seek ahead is limited to prepared content. Production needs bounded seekable caching.
5. The prototype accepts only 8-bit H.264 SDR and preserves video through stream copy. Other codecs, HDR and high-bit-depth video are rejected until their conversion paths are validated. Audio conversion to stereo AAC is visibly disclosed before preparation.
6. ASS/SSA becomes text with simplified styling; image subtitles are rejected. Arbitrary timestamp epochs/discontinuities need explicit normalization and regression fixtures.
7. Browser resume/selected-player pause acknowledgment is not implemented.
8. Broader receiver/network/VPN/firewall/reconnect combinations are unverified. No 10-minute background or 60-minute soak claim yet.
9. Local Python/FFmpeg dependencies are not bundled/pinned for distribution. Full Xcode for Safari, release signing, notarization, dependency licensing and clean-machine install remain open.
10. Refactor prototype responsibilities along the planned contracts after M0; no website-specific code belongs in playback/relay modules.

## Next gate

Record exact receiver metadata and perform longer background/soak testing; then work through M1 lifecycle/media contracts and M2 browser integration. The local prototype may merge with these limits explicit, but that is not a full-app release or universal compatibility claim.

## Decision history

- Chose public AVPlayer/AirPlay route APIs rather than desktop capture.
- User accepted reopening local files in this companion app.
- Established architecture, protocol, performance and regression plans before broad browser expansion.
- Two agents reviewed lifecycle/subtitles; implemented cancellable worker, synthetic fixture, unique rendition names and native selection readback.
- User authorized public repository, BeautyApp-style PR controls and merge to main when the scoped change is ready.

- Independent review requested a fix for silent lossy conversion. The M0 path now rejects unsupported/HDR video before conversion and exposes audio loss before preparation; a regression test covers these cases.

## Browser integration checkpoint

- Native workflow now emphasizes Open video, Prepare for TV, choose Apple TV, Play and selectable subtitles. Incoming browser requests require explicit Replace video when a session is active.
- Integrated builds passed; the current Python suite has 46 passing tests. Browser contract and native inbox/timeline tests run in CI. Physical Anikoto playback remains the next acceptance gate.
- Firefox extension was loaded with user assistance; actual Anikoto discovery found master/media playlists and nine subtitle resources. The selected English file transferred successfully. Chrome extension loaded; its megaplay.buzz access grant is pending explicit approval after automatic review rejected it.

- Browser selection now opens in a full tab to accommodate subtitle choices; recognizable filename language codes produce readable labels and are explicitly marked inferred.
- Native seek controls use actual AVPlayer seekable ranges; browser-position resume is explicit and range-gated.
- The bounded actual-episode test was stopped through the app; the helper reported prepared media removed. Source media and browsing tokens are not committed.
