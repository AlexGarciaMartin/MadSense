#!/usr/bin/env bash
# Compile real Windows HIDAPI and the isolated observer; never execute the PE.
set -euo pipefail
test "$#" = 1 || { echo 'usage: build_hidapi_windows_consumer.sh FRESH_OUTPUT' >&2; exit 2; }
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
mkdir -p -- "$1"
output=$(cd -- "$1" && pwd)
test -z "$(find "$output" -mindepth 1 -maxdepth 1 -print -quit)"
upstream=$(python3 "$source_dir/hidapi_windows_source.py" --output "$output/upstream")
mkdir -p "$output/bin" "$output/notices" "$output/source/linux/research"
cp "$upstream"/LICENSE*.txt "$output/notices/"
cp "$source_dir/../../LICENSE" "$output/notices/MadSense-GPL-3.0.txt"
cp /usr/share/doc/mingw-w64-common/copyright "$output/notices/MinGW-w64-copyright.txt"
cp /usr/share/doc/gcc-mingw-w64-base/copyright "$output/notices/GCC-MinGW-copyright.txt"
cp "$source_dir/hidapi_windows_consumer.cpp" "$source_dir/hidapi_windows_source.py" \
   "$source_dir/build_hidapi_windows_consumer.sh" "$source_dir/HIDAPI-WINDOWS.md" \
   "$source_dir/hidapi_windows_evidence.py" "$source_dir/run_hidapi_windows.py" \
   "$source_dir/test_hidapi_windows.py" "$source_dir/test_hidapi_windows_runtime.py" "$output/source/linux/research/"
cp "$source_dir/../../LICENSE" "$output/source/LICENSE"
{
  printf 'Scope: real upstream Windows HIDAPI compilation; no consumer execution\nMadSense commit: '
  git -C "$source_dir" rev-parse HEAD
  uname -a
  x86_64-w64-mingw32-gcc --version
  x86_64-w64-mingw32-g++ --version
  dpkg-query -W g++-mingw-w64-x86-64 gcc-mingw-w64-base mingw-w64-common
} > "$output/provenance.txt"
x86_64-w64-mingw32-gcc -std=c11 -O2 -Wall -Wextra -DHID_API_NO_EXPORT_DEFINE \
  -I"$upstream/hidapi" -I"$upstream/windows" -c "$upstream/windows/hid.c" -o "$output/hidapi-windows.o"
x86_64-w64-mingw32-g++ -std=c++17 -DUNICODE -D_UNICODE -DHID_API_NO_EXPORT_DEFINE -municode \
  -O2 -Wall -Wextra -Werror -static -static-libgcc -static-libstdc++ \
  -I"$upstream/hidapi" -I"$upstream/windows" "$source_dir/hidapi_windows_consumer.cpp" \
  "$output/hidapi-windows.o" -o "$output/bin/hidapi_windows_consumer.exe" -lole32 -lsetupapi
x86_64-w64-mingw32-objdump -p "$output/bin/hidapi_windows_consumer.exe" > "$output/pe-imports.txt"
sha256sum "$output/bin/hidapi_windows_consumer.exe" > "$output/binary-sha256.txt"
find "$output/source" "$output/upstream/source" -type f -print0 | sort -z | xargs -0 sha256sum > "$output/source-sha256.txt"
printf 'Compilation: passed\nWine/GE execution: not performed\nNative Windows parity: not verified\n' >> "$output/provenance.txt"
