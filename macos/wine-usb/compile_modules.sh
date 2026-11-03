#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build the three affected public Wine modules; never install or run them.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "$(uname -s)" == Darwin ]] || {
  echo 'This compilation check runs only in disposable macOS GitHub CI.' >&2
  exit 2
}
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"
mkdir -p dist/wine-usb-compile
evidence="$root/dist/wine-usb-compile"
scratch="$(mktemp -d "$root/.build/wine-usb-compile.XXXXXX")"
export MACOSX_DEPLOYMENT_TARGET=15.0
export SDKROOT="$(xcrun --show-sdk-path)"
case "$(uname -m)" in
  arm64) host=aarch64-apple-darwin; archs=aarch64,arm64ec; cpu=arm64; pe=aarch64-windows; unix=aarch64-unix ;;
  x86_64) host=x86_64-apple-darwin; archs=x86_64; cpu=x86_64; pe=x86_64-windows; unix=x86_64-unix ;;
  *) exit 2 ;;
esac

python3 macos/wine-usb/prepare_source.py --source-tree .build/wine-original --output "$scratch/source" > "$evidence/source-preparation.log" 2>&1
cp "$scratch/source/MADSENSE-WINE-USB.json" "$evidence/"
toolchain=llvm-mingw-20261006-ucrt-macos-universal
curl --fail --location --retry 3 --max-time 180 \
  "https://github.com/mstorsjo/llvm-mingw/releases/download/20261006/$toolchain.tar.xz" \
  --output "$scratch/toolchain.tar.xz"
python3 - "$scratch" <<'PY'
import hashlib, pathlib, sys, tarfile
directory = pathlib.Path(sys.argv[1])
archive = directory / 'toolchain.tar.xz'
with archive.open('rb') as stream:
    actual = hashlib.file_digest(stream, 'sha256').hexdigest()
expected = 'a7a19b11048e89372637d26279cdb7c430ed3cf8c1e224310421312bf986ebf6'
if actual != expected:
    raise SystemExit('Official llvm-mingw archive SHA256 mismatch')
with tarfile.open(archive) as source:
    source.extractall(directory, filter='data')
PY
export PATH="$scratch/$toolchain/bin:$(brew --prefix bison)/bin:$PATH"
export CC="/usr/bin/clang -arch $cpu -isysroot $SDKROOT -mmacosx-version-min=15.0"
export CXX="/usr/bin/clang++ -arch $cpu -isysroot $SDKROOT -mmacosx-version-min=15.0"
export LDFLAGS="-isysroot $SDKROOT -mmacosx-version-min=15.0"
{
  sw_vers
  uname -m
  xcrun --show-sdk-version
  /usr/bin/clang --version
  x86_64-w64-mingw32-clang --version
  bison --version
  brew list --versions bison sdl2
} > "$evidence/toolchain.txt"
mkdir "$scratch/build"
cd "$scratch/build"
save_config() {
  [[ ! -f config.log ]] || cp config.log "$evidence/config.log"
}
trap save_config EXIT
"$scratch/source/configure" --host="$host" --enable-archs="$archs" --disable-tests \
  --with-coreaudio --with-sdl --without-freetype --without-x --without-gstreamer \
  --without-vulkan --without-opengl --without-cups --without-dbus --without-fontconfig \
  --without-gnutls --without-krb5 --without-gssapi --without-opencl --without-pcap \
  --without-usb --without-v4l2 --without-sane --without-capi --without-gphoto \
  --without-netapi --without-pulse --without-alsa --without-oss --without-udev \
  --without-unwind --without-ffmpeg --without-inotify \
  SDL2_CFLAGS="-I$(brew --prefix sdl2)/include/SDL2 -D_THREAD_SAFE" \
  ac_cv_lib_soname_SDL2=libSDL2-2.0.0.dylib ac_cv_func_pipe2=no \
  > "$evidence/configure.log" 2>&1 || { tail -60 "$evidence/configure.log"; exit 1; }
make -j"$(sysctl -n hw.ncpu)" dlls/winebus.sys/all dlls/mmdevapi/all dlls/winecoreaudio.drv/all \
  > "$evidence/build.log" 2>&1 || { tail -80 "$evidence/build.log"; exit 1; }
python3 - "$evidence" "$pe" "$unix" <<'PY'
import hashlib, json, pathlib, subprocess, sys
evidence, pe, unix = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
paths = [f'dlls/winebus.sys/{pe}/winebus.sys', f'dlls/mmdevapi/{pe}/mmdevapi.dll',
         f'dlls/winebus.sys/{unix}/winebus.so', f'dlls/winecoreaudio.drv/{unix}/winecoreaudio.so']
files = []
for relative in paths:
    path = pathlib.Path(relative)
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    description = subprocess.check_output(['file', str(path)], text=True).strip()
    files.append({'path': relative, 'bytes': path.stat().st_size, 'sha256': digest, 'file': description})
    if relative.endswith('.so'):
        output = subprocess.check_output(['otool', '-L', '-l', str(path)], text=True)
        (evidence / (path.name + '.otool.txt')).write_text(output)
result = {'wine_commit': '36b6a2cf679fb395f668a917b76537190e212d9c',
          'toolchain_tag': '20261006',
          'toolchain_sha256': 'a7a19b11048e89372637d26279cdb7c430ed3cf8c1e224310421312bf986ebf6',
          'module_compilation_passed': True, 'full_wine_built': False,
          'installed': False, 'wine_executed': False, 'hardware_validation': False,
          'game_validation': False, 'files': files}
(evidence / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
PY
