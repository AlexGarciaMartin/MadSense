#!/usr/bin/env bash
# No package/driver changes here. Build on the disposable Ubuntu host, then copy
# bin/ to its owned guest. SDL3 is statically linked; SDL2 uses Ubuntu's runtime.
set -euo pipefail
test "$#" = 1 || { echo "usage: build_consumers.sh FRESH_OUTPUT" >&2; exit 2; }
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
mkdir -p -- "$1"
output=$(cd -- "$1" && pwd)
test -z "$(find "$output" -mindepth 1 -maxdepth 1 -print -quit)"
commit=7f3ae3d57459e59943a4ecfefc8f6277ec6bf540
digest=7a8347c770b90b33daac2352858ca03f9c9a2ccc8ce711054870361d1a6b32e5
url="https://github.com/libsdl-org/SDL/archive/$commit.tar.gz"
{
  printf 'Scope: compilation only; no device or consumer runtime test\n'
  printf 'SDL3 release: 3.2.28\nSDL3 commit: %s\nSDL3 archive: %s\nSDL3 SHA256: %s\n' "$commit" "$url" "$digest"
  printf 'MadSense commit: '
  git -C "$source_dir" rev-parse HEAD 2>/dev/null || printf 'source artifact; see original provenance\n'
  uname -a
  pkg-config --modversion sdl2
  dpkg-query -W libsdl2-dev libsdl2-2.0-0 libudev-dev libc6-dev gcc cmake
  cc --version
} > "$output/provenance.txt"
curl --fail --location --retry 2 --max-time 180 "$url" --output "$output/SDL3.tar.gz"
printf '%s  %s\n' "$digest" "$output/SDL3.tar.gz" | sha256sum --check
tar -xzf "$output/SDL3.tar.gz" -C "$output"
mkdir -p "$output/notices" "$output/source/linux/research/consumer-build"
cp "$source_dir/../../LICENSE" "$output/notices/MadSense-GPL-3.0.txt"
cp "$source_dir/../../LICENSE" "$output/source/LICENSE"
cp "$source_dir/consumer-NOTICES.txt" "$output/notices/NOTICE.txt"
cp "$output/SDL-$commit/LICENSE.txt" "$output/notices/SDL3-LICENSE.txt"
cp "$output/SDL-$commit/src/hidapi/LICENSE.txt" "$output/notices/HIDAPI-license-choice.txt"
cp "$output/SDL-$commit/src/hidapi/LICENSE-bsd.txt" "$output/notices/HIDAPI-BSD.txt"
cp /usr/share/doc/libsdl2-2.0-0/copyright "$output/notices/Ubuntu-SDL2-copyright.txt"
cp "$source_dir/consumer_guard.h" "$source_dir/consumer_sdl.c" "$source_dir/ff_probe.c" \
  "$source_dir/build_consumers.sh" "$source_dir/consumer-NOTICES.txt" "$source_dir/CONSUMERS.md" \
  "$source_dir/run_consumers.py" "$source_dir/ci_environment.py" \
  "$source_dir/consumer_phases.py" "$source_dir/test_consumer_phases.py" "$source_dir/consumer_session.py" \
  "$output/source/linux/research/"
cp "$source_dir/consumer-build/CMakeLists.txt" "$output/source/linux/research/consumer-build/"
cmake -S "$source_dir/consumer-build" -B "$output/build" -DCMAKE_BUILD_TYPE=Release \
  -DSDL_SOURCE_DIRECTORY="$output/SDL-$commit" -DCMAKE_RUNTIME_OUTPUT_DIRECTORY="$output/bin"
cmake --build "$output/build" --parallel 2 --target consumer_sdl2 consumer_sdl3 ff_probe
sha256sum "$output/bin/consumer_sdl2" "$output/bin/consumer_sdl3" "$output/bin/ff_probe" > "$output/binary-sha256.txt"
for binary in consumer_sdl2 consumer_sdl3 ff_probe; do
  printf '\nELF dynamic dependencies: %s\n' "$binary" >> "$output/provenance.txt"
  readelf --dynamic "$output/bin/$binary" >> "$output/provenance.txt"
done
printf 'Compilation: passed\nRuntime validation: not performed\n' >> "$output/provenance.txt"
