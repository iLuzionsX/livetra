#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="LiveTR3"
PACKAGE_DIR="$ROOT/macos/LiveTR3Mac"
BUNDLE="$ROOT/dist/$APP_NAME.app"
APP_ICON="$PACKAGE_DIR/Resources/AppIcon.icns"

verify=false
if [[ "${1:-}" == "--verify" ]]; then
  verify=true
fi

pkill -x "$APP_NAME" >/dev/null 2>&1 || true

cd "$PACKAGE_DIR"
swift build -c release

EXECUTABLE="$(swift build -c release --show-bin-path)/$APP_NAME"

rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources"
cp "$EXECUTABLE" "$BUNDLE/Contents/MacOS/$APP_NAME"
cp "$APP_ICON" "$BUNDLE/Contents/Resources/AppIcon.icns"
cat > "$BUNDLE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>$APP_NAME</string>
  <key>CFBundleIdentifier</key>
  <string>com.livetr3.mac</string>
  <key>CFBundleName</key>
  <string>$APP_NAME</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>LiveTR3 captures microphone audio for local transcription and translation.</string>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
</dict>
</plist>
PLIST

"$ROOT/script/package_engine.sh" "$BUNDLE"

/usr/bin/open -n "$BUNDLE"

if [[ "$verify" == true ]]; then
  sleep 2
  pgrep -x "$APP_NAME" >/dev/null
  echo "$APP_NAME is running from $BUNDLE"
fi
