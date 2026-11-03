#!/bin/bash
# Research-only replacement binary inside an already-built disposable bundle.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
build="${1:?research build directory required}"
bundle="${2:?existing research-only HAL bundle required}"
period="${3:-256}"
test -f "$bundle/Contents/Info.plist"
mkdir -p "$build"
python3 -m unittest discover -s "$root/macos/research" -p test_instrument_audio_plugin.py
python3 "$root/macos/research/instrument_audio_plugin.py" "$root/macos/audio/plugin.cpp" "$build/plugin-ingress.cpp" \
  --timestamp-period "$period" > "$build/ingress-source.json"
xcrun clang++ -std=c++17 -O2 -Wall -Wextra -Werror -mmacosx-version-min=15.0 \
  "$root/macos/research/audio_ingress_trace_test.cpp" -o "$build/audio-ingress-trace-test"
"$build/audio-ingress-trace-test"
xcrun clang++ -std=c++17 -O2 -mmacosx-version-min=15.0 \
  -I "$root/macos/audio" -I "$root/macos/research" \
  "$root/macos/research/audio_ingress_plugin_test.cpp" "$build/plugin-ingress.cpp" \
  -framework CoreAudio -framework CoreFoundation -o "$build/audio-ingress-plugin-test"
"$build/audio-ingress-plugin-test" "$period"
xcrun clang++ -std=c++17 -O2 -mmacosx-version-min=15.0 -bundle \
  -I "$root/macos/audio" -I "$root/macos/research" "$build/plugin-ingress.cpp" \
  -framework CoreAudio -framework CoreFoundation -o "$bundle/Contents/MacOS/MadSenseAudio"
