#!/bin/bash
# Research-only replacement binary inside an already-built disposable bundle.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
build="${1:?research build directory required}"
bundle="${2:?existing research-only HAL bundle required}"
period="${3:-256}"
recovery="${4:-observe}"
generator_args=(--timestamp-period "$period")
if [[ "$recovery" == reject ]]; then generator_args+=(--reject-resync)
elif [[ "$recovery" != observe ]]; then echo "Unknown recovery variant" >&2; exit 2
fi
test -f "$bundle/Contents/Info.plist"
mkdir -p "$build"
python3 -m unittest discover -s "$root/macos/research" -p test_instrument_audio_plugin.py
python3 "$root/macos/research/instrument_audio_plugin.py" "$root/macos/audio/plugin.cpp" "$build/plugin-ingress.cpp" \
  "${generator_args[@]}" > "$build/ingress-source.json"
xcrun clang++ -std=c++17 -O2 -Wall -Wextra -Werror -mmacosx-version-min=15.0 \
  "$root/macos/research/audio_ingress_trace_test.cpp" -o "$build/audio-ingress-trace-test"
"$build/audio-ingress-trace-test"
xcrun clang++ -std=c++17 -O2 -mmacosx-version-min=15.0 \
  -I "$build" -I "$root/macos/audio" -I "$root/macos/research" \
  "$root/macos/research/audio_ingress_plugin_test.cpp" "$build/plugin-ingress.cpp" \
  -framework CoreAudio -framework CoreFoundation -o "$build/audio-ingress-plugin-test"
"$build/audio-ingress-plugin-test" "$period" "$recovery"
if [[ "$recovery" == reject ]]; then
  xcrun clang++ -std=c++17 -O2 -Wall -Wextra -Werror -mmacosx-version-min=15.0 \
    -I "$build" "$root/macos/audio/resync_test.cpp" -o "$build/audio-resync-test"
  "$build/audio-resync-test"
  cp "$root/macos/audio/ring_test.cpp" "$build/ring_test.cpp"
  xcrun clang++ -std=c++17 -O2 -mmacosx-version-min=15.0 "$build/ring_test.cpp" -o "$build/existing-ring-test"
  "$build/existing-ring-test"
fi
xcrun clang++ -std=c++17 -O2 -mmacosx-version-min=15.0 -bundle \
  -I "$root/macos/audio" -I "$root/macos/research" "$build/plugin-ingress.cpp" \
  -framework CoreAudio -framework CoreFoundation -o "$bundle/Contents/MacOS/MadSenseAudio"
