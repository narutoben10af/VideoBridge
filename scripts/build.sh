#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AVAILABLE=$(df -Pk / | awk 'NR==2 {print $4}')
if (( AVAILABLE < 16252928 )); then
  echo 'Build stopped: needs a 512 MiB allowance above the 15 GiB boundary.' >&2; exit 1
fi
STAGE=$(mktemp -d /private/tmp/videobridge-build.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT
APP="$STAGE/VideoBridge.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$ROOT/work/swift-cache"
xcrun swiftc -swift-version 5 -parse-as-library -O -target arm64-apple-macosx13.0 \
  -module-cache-path "$ROOT/work/swift-cache" \
  "$ROOT/app/VideoBridge.swift" "$ROOT/app/MenuBarControls.swift" -o "$APP/Contents/MacOS/VideoBridge" \
  -framework SwiftUI -framework AppKit -framework AVKit -framework AVFoundation
cp "$ROOT/app/relay.py" "$APP/Contents/Resources/relay.py"
cp "$ROOT/app/process_worker.py" "$ROOT/app/remote_media.py" "$APP/Contents/Resources/"
if [ -f "$ROOT/work/m0-fixture/video.mp4" ]; then
  mkdir -p "$APP/Contents/Resources/M0"
  cp "$ROOT/work/m0-fixture/"* "$APP/Contents/Resources/M0/"
fi
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>VideoBridge</string>
<key>CFBundleIdentifier</key><string>local.eian.videobridge</string>
<key>CFBundleName</key><string>VideoBridge</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.1.0</string>
<key>LSMinimumSystemVersion</key><string>13.0</string>
<key>NSHighResolutionCapable</key><true/>
<key>NSLocalNetworkUsageDescription</key><string>Send the selected video and subtitle tracks to Apple TV on your local network.</string>
<key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/><key>NSAllowsArbitraryLoadsForMedia</key><true/></dict>
<key>CFBundleURLTypes</key><array><dict><key>CFBundleURLName</key><string>VideoBridge handoff</string><key>CFBundleURLSchemes</key><array><string>videobridge</string></array></dict></array>
<key>CFBundleDocumentTypes</key><array><dict><key>CFBundleTypeName</key><string>Video</string><key>CFBundleTypeRole</key><string>Viewer</string><key>LSHandlerRank</key><string>Alternate</string><key>LSItemContentTypes</key><array><string>public.movie</string><string>public.mpeg-4</string><string>com.apple.quicktime-movie</string><string>org.matroska.mkv</string></array></dict></array>
</dict></plist>
PLIST
codesign --force --sign - "$APP"
plutil -lint "$APP/Contents/Info.plist"
codesign --verify --strict "$APP"
mkdir -p "$ROOT/artifacts"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ROOT/artifacts/VideoBridge-prototype.zip"
echo "Built and signature-verified prototype archive: $ROOT/artifacts/VideoBridge-prototype.zip"
