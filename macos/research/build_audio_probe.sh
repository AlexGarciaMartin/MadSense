#!/bin/bash
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
build="${1:-$root/.build/audio-research}"
mkdir -p "$build"
xcrun clang++ -std=c++17 -O2 -Wall -Wextra -Werror -mmacosx-version-min=15.0 \
  "$root/macos/research/audio_probe_signal_test.cpp" -o "$build/audio-probe-signal-test"
"$build/audio-probe-signal-test"
xcrun clang++ -std=c++17 -O2 -mmacosx-version-min=15.0 \
  -c "$root/macos/audio/consumer.cpp" -o "$build/audio-consumer.o"
xcrun clang++ -std=c++17 -O2 -Wall -Wextra -Werror -mmacosx-version-min=15.0 \
  "$root/macos/research/audio_path_probe.cpp" "$build/audio-consumer.o" \
  -framework CoreAudio -framework CoreFoundation -o "$build/audio-path-probe"
