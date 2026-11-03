#!/usr/bin/env bash
# Source-copy observation only. Does not install, attach, or access a device.
set -euo pipefail
[[ $# == 2 ]] || { echo 'usage: build_control.sh FRESH_SOURCE_COPY FRESH_OUTPUT' >&2; exit 2; }
here=$(cd -- "$(dirname -- "$0")" && pwd)
root=$(cd -- "$here/../../.." && pwd)
mkdir -p -- "$2"
output=$(cd -- "$2" && pwd)
test -z "$(find "$output" -mindepth 1 -maxdepth 1 -print -quit)"
python3 "$here/stage_control.py" --output "$1" > "$output/source-overlay.json"
source_copy=$(cd -- "$1" && pwd)
dotnet publish "$source_copy/linux/platform/integration/MadSense.Linux.CoreProbe.csproj" \
  --configuration Release --runtime linux-x64 --self-contained true -p:RestoreLockedMode=true \
  --output "$output/core-probe" > "$output/build.log" 2>&1
mkdir "$output/consumer-bin"
cc -std=c11 -O2 -Wall -Wextra -Werror "$here/control_probe.c" -o "$output/consumer-bin/control_probe"
git -C "$root" archive --format=tar.gz HEAD > "$output/source.tar.gz"
cp "$root/LICENSE" "$output/LICENSE"
{
  printf 'Scope: compiled source-copy class request observation; no native validation\nMadSense commit: '
  git -C "$root" rev-parse HEAD
  dotnet --version
  cc --version
} > "$output/provenance.txt"
(cd "$output" && find core-probe consumer-bin -type f -print0 | sort -z | xargs -0 sha256sum) > "$output/binary-sha256.txt"
(cd "$output" && sha256sum source.tar.gz source-overlay.json LICENSE) > "$output/source-sha256.txt"
