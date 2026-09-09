#!/usr/bin/env bash
# Build SecondBrain.app and install it to ~/Applications.
#
#   ./mac/build.sh            build + install to ~/Applications
#   ./mac/build.sh --no-install   build into mac/build only
#
# Needs only the Command Line Tools (swiftc); no Xcode project involved.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
build="$here/build"
bundle="$build/SecondBrain.app"
install_dir="$HOME/Applications"

rm -rf "$build"
mkdir -p "$bundle/Contents/MacOS" "$bundle/Contents/Resources"

echo "Compiling..."
swiftc -O \
  -o "$bundle/Contents/MacOS/SecondBrain" \
  "$here/SecondBrain.swift" \
  -framework Cocoa -framework WebKit -framework Carbon

project_root="$(cd "$here/.." && pwd)"

cat > "$bundle/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Second Brain</string>
  <key>CFBundleDisplayName</key><string>Second Brain</string>
  <key>CFBundleExecutable</key><string>SecondBrain</string>
  <key>CFBundleIdentifier</key><string>com.secondbrain.menubar</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <!-- Menu bar only: no Dock icon, no app switcher entry. -->
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <!-- Both the project and the vault live under ~/Documents, which macOS
       gates behind TCC. As a GUI app we get asked once; a launchd agent
       would simply be refused. -->
  <key>NSDocumentsFolderUsageDescription</key>
  <string>Second Brain reads your Obsidian vault to answer questions about your notes.</string>
  <!-- Where the Python project lives, baked in at build time. -->
  <key>SBProjectPath</key><string>${project_root}</string>
  <!-- The server is plain http on loopback. -->
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key><true/>
  </dict>
</dict>
</plist>
PLIST

# Extended attributes picked up from the working directory make codesign refuse
# the bundle ("resource fork, Finder information, or similar detritus").
xattr -cr "$bundle" 2>/dev/null || true

# Ad-hoc signature keeps macOS from re-prompting on every launch.
if codesign --force --sign - "$bundle" 2>/dev/null; then
  echo "Signed (ad-hoc)."
else
  echo "note: ad-hoc codesign failed; the app still runs"
fi

echo "Built $bundle"

if [ "${1:-}" != "--no-install" ]; then
  mkdir -p "$install_dir"
  rm -rf "$install_dir/SecondBrain.app"
  cp -R "$bundle" "$install_dir/"
  echo "Installed to $install_dir/SecondBrain.app"
  echo
  echo "Open it once from Finder, then it lives in your menu bar."
  echo "Global hotkey: Option+Space"
  echo "To launch at login: System Settings > General > Login Items > +"
fi
