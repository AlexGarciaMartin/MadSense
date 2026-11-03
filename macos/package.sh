#!/bin/bash
# Build on a Mac (including GitHub-hosted macOS runners). No signing credentials
# or Apple entitlements are fabricated by this script.
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <osx-arm64|osx-x64> <output-directory>" >&2
  exit 2
fi
rid="$1"
minimum_macos=15.0
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
audio_package="$out/MadSenseAudio-$rid"
if [[ -e "$app" || -e "$audio_package" ]]; then
  echo "An app or optional audio package already exists. Choose an empty output directory." >&2
  exit 2
fi
for tool in dotnet cmake swiftc codesign iconutil ditto lipo file install_name_tool python3; do
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
lipo "$hidapi" -verify_arch "$arch"

# Managed PE assemblies are resources on macOS, not nested Mach-O code. Keeping
# the self-contained runtime together also preserves .NET's relative probing.
mkdir -p "$build" "$app/Contents/MacOS" "$app/Contents/Resources/backend"
cmake -S "$root/macos/native" -B "$build/native" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_OSX_ARCHITECTURES="$arch" -DCMAKE_OSX_DEPLOYMENT_TARGET="$minimum_macos"
cmake --build "$build/native" --config Release
cmake -S "$root/macos/audio" -B "$build/audio" -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_OSX_ARCHITECTURES="$arch" -DCMAKE_OSX_DEPLOYMENT_TARGET="$minimum_macos"
cmake --build "$build/audio" --config Release
dotnet publish "$root/macos/backend/MadSense.Mac.csproj" -c Release -r "$rid" \
  --self-contained true -p:PublishSingleFile=false -p:DebugType=None \
  -o "$app/Contents/Resources/backend"
cp "$build/native/libmadsense_hid.dylib" "$app/Contents/Resources/backend/"
cp "$build/audio/libmadsense_audio.dylib" "$app/Contents/Resources/backend/"
cp -L "$hidapi" "$app/Contents/Resources/backend/libhidapi.dylib"
install_name_tool -id '@rpath/libhidapi.dylib' "$app/Contents/Resources/backend/libhidapi.dylib"
cp "$root/macos/app/Info.plist" "$app/Contents/Info.plist"
cp "$root/macos/app/Localizations.json" "$app/Contents/Resources/Localizations.json"
cp "$root/LICENSE" "$app/Contents/Resources/LICENSE.txt"
cp "$root/THIRD-PARTY-NOTICES.txt" "$app/Contents/Resources/THIRD-PARTY-NOTICES.txt"
mkdir -p "$app/Contents/Resources/licenses"
cp "$root"/vendor/licenses/*.txt "$app/Contents/Resources/licenses/"
cp "$root/macos/README.md" "$app/Contents/Resources/macOS-README.md"
cp "$root/macos/audio/README.md" "$app/Contents/Resources/Audio-README.md"
swiftc -swift-version 5 -O -target "$arch-apple-macosx$minimum_macos" -framework AppKit -framework ServiceManagement \
  "$root/macos/app/MadSense.swift" -o "$app/Contents/MacOS/MadSense"
swift "$root/macos/app/MakeIcon.swift" "$build/MadSense.iconset"
iconutil -c icns "$build/MadSense.iconset" -o "$app/Contents/Resources/MadSense.icns"

# Ad-hoc signatures allow integrity checks and arm64 execution, but do not grant
# the restricted virtual HID entitlement. A development package reports this
# limitation at runtime; it must never claim the bridge is ready without it.
while IFS= read -r -d '' binary; do
  if [[ "$(file -b "$binary")" == *Mach-O* ]]; then
    if codesign --display "$binary" >/dev/null 2>&1; then
      # Preserve the SDK apphost/runtime JIT entitlements when replacing its
      # signature. Do not attach the restricted HID entitlement to this build.
      codesign --force --sign - --preserve-metadata=entitlements "$binary"
    else
      codesign --force --sign - "$binary"
    fi
    codesign --verify --strict "$binary"
  fi
done < <(find "$app/Contents/Resources/backend" -type f -print0)
codesign --force --sign - "$app/Contents/MacOS/MadSense"
codesign --force --sign - "$app"
codesign --verify --deep --strict "$app"
lipo "$app/Contents/MacOS/MadSense" -verify_arch "$arch"
lipo "$app/Contents/Resources/backend/libmadsense_hid.dylib" -verify_arch "$arch"
lipo "$app/Contents/Resources/backend/libmadsense_audio.dylib" -verify_arch "$arch"

# The optional HAL plugin remains a separate artifact. Packaging never copies
# into /Library/Audio or restarts CoreAudio on the user's computer.
mkdir -p "$audio_package"
ditto "$build/audio/MadSenseAudio.driver" "$audio_package/MadSenseAudio.driver"
cp "$root/macos/audio/README.md" "$audio_package/README.md"
cp "$root/LICENSE" "$audio_package/LICENSE.txt"
codesign --force --sign - "$audio_package/MadSenseAudio.driver/Contents/MacOS/MadSenseAudio"
codesign --force --sign - "$audio_package/MadSenseAudio.driver"
codesign --verify --deep --strict "$audio_package/MadSenseAudio.driver"
lipo "$audio_package/MadSenseAudio.driver/Contents/MacOS/MadSenseAudio" -verify_arch "$arch"
# Validate every actual executable/native library, including .NET and Homebrew
# dependencies. Build flags cannot lower a prebuilt dependency's requirement.
# Keep this report even when validation prevents creation of the ZIP artifacts.
python3 "$root/macos/verify_deployment.py" --arch "$arch" --minimum "$minimum_macos" \
  --report "$out/deployment.json" "$app" "$audio_package"
ditto -c -k --sequesterRsrc --keepParent "$app" "$out/MadSense-$rid-development.zip"
ditto -c -k --sequesterRsrc --keepParent "$audio_package" "$out/MadSenseAudio-$rid-development.zip"
(cd "$out" && shasum -a 256 "MadSense-$rid-development.zip" "MadSenseAudio-$rid-development.zip" > SHA256SUMS.txt)
echo "Development package: $out/MadSense-$rid-development.zip"
echo "Optional experimental audio package: $out/MadSenseAudio-$rid-development.zip"
echo "Virtual HID requires an Apple-authorized signing identity/profile; this package is not a production release."
