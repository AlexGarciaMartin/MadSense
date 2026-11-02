#!/bin/bash
# Build on a Mac (including GitHub-hosted macOS runners). No signing credentials
# or Apple entitlements are fabricated by this script.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <osx-arm64|osx-x64> <output-directory>" >&2
  exit 2
fi
rid="$1"
case "$rid" in
  osx-arm64) arch=arm64 ;;
  osx-x64) arch=x86_64 ;;
  *) echo "Unsupported runtime: $rid" >&2; exit 2 ;;
esac
if [[ "$(uname -s)" != Darwin ]]; then
  echo "The native application must be built on macOS." >&2
  exit 2
fi
root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$2"
out="$(cd "$2" && pwd)"
build="$root/.build/macos-$rid"
app="$out/MadSense.app"
if [[ -e "$app" ]]; then
  echo "Output already exists: $app. Choose an empty output directory." >&2
  exit 2
fi
for tool in dotnet cmake swiftc codesign iconutil ditto lipo; do
  command -v "$tool" >/dev/null || { echo "Required build tool is missing: $tool" >&2; exit 2; }
done

hidapi="${MADSENSE_HIDAPI:-}"
if [[ -z "$hidapi" ]] && command -v brew >/dev/null; then
  hidapi="$(brew --prefix hidapi 2>/dev/null || true)/lib/libhidapi.dylib"
fi
if [[ ! -f "$hidapi" ]]; then
  echo "Set MADSENSE_HIDAPI to a libhidapi.dylib containing $arch, or install hidapi with Homebrew." >&2
  exit 2
fi
lipo -verify_arch "$arch" "$hidapi"

mkdir -p "$build" "$app/Contents/MacOS/backend" "$app/Contents/Resources"
cmake -S "$root/macos/native" -B "$build/native" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_OSX_ARCHITECTURES="$arch" -DCMAKE_OSX_DEPLOYMENT_TARGET=14.0
cmake --build "$build/native" --config Release
dotnet publish "$root/macos/backend/MadSense.Mac.csproj" -c Release -r "$rid" \
  --self-contained true -p:PublishSingleFile=false -p:DebugType=None \
  -o "$app/Contents/MacOS/backend"
cp "$build/native/libmadsense_hid.dylib" "$app/Contents/MacOS/backend/"
cp -L "$hidapi" "$app/Contents/MacOS/backend/libhidapi.dylib"
install_name_tool -id '@rpath/libhidapi.dylib' "$app/Contents/MacOS/backend/libhidapi.dylib"
cp "$root/macos/app/Info.plist" "$app/Contents/Info.plist"
cp "$root/LICENSE" "$app/Contents/Resources/LICENSE.txt"
cp "$root/THIRD-PARTY-NOTICES.txt" "$app/Contents/Resources/THIRD-PARTY-NOTICES.txt"
cp "$root/macos/README.md" "$app/Contents/Resources/macOS-README.md"
swiftc -swift-version 5 -O -target "$arch-apple-macosx14.0" -framework AppKit \
  "$root/macos/app/MadSense.swift" -o "$app/Contents/MacOS/MadSense"
swift "$root/macos/app/MakeIcon.swift" "$build/MadSense.iconset"
iconutil -c icns "$build/MadSense.iconset" -o "$app/Contents/Resources/MadSense.icns"

# Ad-hoc signatures allow integrity checks and arm64 execution, but do not grant
# the restricted virtual HID entitlement. A development package reports this
# limitation at runtime; it must never claim the bridge is ready without it.
while IFS= read -r -d '' binary; do
  codesign --force --sign - "$binary"
done < <(find "$app/Contents/MacOS/backend" -type f \( -name '*.dylib' -o -name '*.so' -o -name 'MadSense.Backend' \) -print0)
codesign --force --sign - "$app/Contents/MacOS/MadSense"
codesign --force --sign - "$app"
codesign --verify --deep --strict "$app"
lipo -verify_arch "$arch" "$app/Contents/MacOS/MadSense"
lipo -verify_arch "$arch" "$app/Contents/MacOS/backend/libmadsense_hid.dylib"
ditto -c -k --sequesterRsrc --keepParent "$app" "$out/MadSense-$rid-development.zip"
shasum -a 256 "$out/MadSense-$rid-development.zip" > "$out/SHA256SUMS.txt"
echo "Development package: $out/MadSense-$rid-development.zip"
echo "Virtual HID requires an Apple-authorized signing identity/profile; this package is not a production release."
