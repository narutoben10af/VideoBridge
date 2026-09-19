# VideoBridge

A planned macOS app for sending **video and selectable subtitles** to Apple TV while the Mac remains available for normal work.

Sources: Safari, Chrome, Firefox, and local video files. The priority real-world browser case is Anikoto's embedded player. Local files may be opened directly in VideoBridge even if normally watched in QuickTime, VLC or another player.

**Status: early local-playback prototype.** A user-assisted Apple TV test confirmed video, English and Chinese selectable soft subtitles, captions Off, seeking, and continued TV playback while using another Mac app. The current video path accepts 8-bit H.264 SDR only; audio becomes stereo AAC. No browser extension exists yet, and broad format/device compatibility is not established. Safari packaging requires full Xcode. This is not a production release.

Start with:

- [Product and scope](docs/PRODUCT.md)
- [Architecture and decisions](docs/ARCHITECTURE.md)
- [Delivery sequence](docs/ROADMAP.md)
- [Quality and performance acceptance](docs/QUALITY.md)
- [Current evidence and blockers](docs/STATUS.md)
- [Protocol v1 proposal](docs/PROTOCOL.md)

`app/` contains the initial Swift/AVPlayer and Python/FFmpeg experiment. Its limitations are documented in STATUS; production boundaries in ARCHITECTURE supersede the experiment. `scripts/build.sh` compiles and ad-hoc signs a prototype archive for this Apple Silicon Mac. It does not install anything, sign for distribution, or create browser extensions. FFmpeg and Python are currently external local dependencies.

## Development

Read [CONTRIBUTING.md](CONTRIBUTING.md) for PR gates and independent review requirements. Native build: `scripts/build.sh`. Tests: `python3 -m unittest discover -s tests -v`. Optional synthetic test clip: `python3 scripts/make_m0_fixture.py`, then rebuild and choose **Load subtitle test** in the app.

Python 3 and FFmpeg/ffprobe must be installed for this prototype. The current build targets Apple Silicon macOS 13+; observed device testing is narrower than that compile target. Keep the app running and Mac awake during relay playback. No commercial/user media is included in this repository.

## Working practice

Complete the physical Apple TV proof in milestone M0 before extending the product shell. Keep user media out of Git, fixtures synthetic and small, and status claims tied to exact evidence. No cloud service, accounts, analytics or paid dependency is required by the planned core design.
