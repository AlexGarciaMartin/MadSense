#!/usr/bin/env bash
# Cross-compile only. The resulting executable is never run by this script.
set -euo pipefail
test "$#" = 1 || { echo "usage: build_wine_consumer.sh FRESH_OUTPUT" >&2; exit 2; }
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
mkdir -p -- "$1"
output=$(cd -- "$1" && pwd)
test -z "$(find "$output" -mindepth 1 -maxdepth 1 -print -quit)"
mkdir -p "$output/bin" "$output/source/linux/research" "$output/notices"
cp "$source_dir/wine_consumer.cpp" "$source_dir/wine_overlapped.hpp" "$source_dir/test_wine_overlapped.cpp" "$source_dir/build_wine_consumer.sh" "$source_dir/WINE-CONSUMER.md" "$output/source/linux/research/"
cp "$source_dir/../../LICENSE" "$output/source/LICENSE"
cp "$source_dir/../../LICENSE" "$output/notices/MadSense-GPL-3.0.txt"
find "$output/source" -type f -print0 | sort -z | xargs -0 sha256sum > "$output/source-sha256.txt"
cp /usr/share/doc/mingw-w64-common/copyright "$output/notices/MinGW-w64-copyright.txt"
cp /usr/share/doc/gcc-mingw-w64-base/copyright "$output/notices/GCC-MinGW-copyright.txt"
{
  printf 'Scope: Windows API observer compilation only; no Wine or controller execution\n'
  printf 'MadSense commit: '
  git -C "$source_dir" rev-parse HEAD 2>/dev/null || printf 'source artifact; see original provenance\n'
  uname -a
  dpkg-query -W g++-mingw-w64-x86-64 gcc-mingw-w64-base mingw-w64-common
  x86_64-w64-mingw32-g++ --version
  g++ --version
} > "$output/provenance.txt"
g++ -std=c++17 -O2 -Wall -Wextra -Werror "$source_dir/test_wine_overlapped.cpp" -o "$output/test-wine-overlapped"
"$output/test-wine-overlapped" | tee "$output/overlapped-tests.txt"
x86_64-w64-mingw32-g++ -std=c++17 -DUNICODE -D_UNICODE -municode -O2 -Wall -Wextra -Werror \
  -static -static-libgcc -static-libstdc++ "$source_dir/wine_consumer.cpp" \
  -o "$output/bin/wine_consumer.exe" -lhid -lsetupapi -lole32 -lpropsys -luuid -lxinput9_1_0 -luser32
x86_64-w64-mingw32-objdump -p "$output/bin/wine_consumer.exe" > "$output/pe-imports.txt"
sha256sum "$output/bin/wine_consumer.exe" > "$output/binary-sha256.txt"
printf 'Compilation: passed\nWine validation: not performed\nProton validation: not performed\n' >> "$output/provenance.txt"
