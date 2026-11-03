#!/bin/bash
# This opt-in system integration test is only for disposable GitHub macOS VMs.
set -euo pipefail
if [[ "$(uname -s)" != Darwin || "${CI:-}" != true || "${RUNNER_ENVIRONMENT:-}" != github-hosted ]]; then
  echo "This system installation test requires a disposable GitHub-hosted macOS runner." >&2
  exit 2
fi
build="$1"
output="$2"
mkdir -p "$output"
test ! -e /Library/Audio/Plug-Ins/HAL/MadSenseAudio.driver
codesign --force --sign - "$build/MadSenseAudio.driver"
sudo mkdir -p /Library/Audio/Plug-Ins/HAL
sudo ditto "$build/MadSenseAudio.driver" /Library/Audio/Plug-Ins/HAL/MadSenseAudio.driver
sudo chown -R root:wheel /Library/Audio/Plug-Ins/HAL/MadSenseAudio.driver
sudo chmod -R go-w /Library/Audio/Plug-Ins/HAL/MadSenseAudio.driver
sudo killall coreaudiod
set +e
python3 - "$build/madsense-audio-installed-probe" "$output" <<'PY'
import pathlib, subprocess, sys, time
probe, output = sys.argv[1], pathlib.Path(sys.argv[2])
for attempt in range(15):
    with (output / "audio-installed.stdout").open("w") as stdout, (output / "audio-installed.stderr").open("w") as stderr:
        try:
            result = subprocess.run([probe], stdout=stdout, stderr=stderr, timeout=8)
        except subprocess.TimeoutExpired:
            print("Installed CoreAudio probe timed out", file=stderr)
            result = None
    if result is not None and result.returncode == 0:
        print((output / "audio-installed.stdout").read_text())
        sys.exit(0)
    time.sleep(1)
print((output / "audio-installed.stderr").read_text(), file=sys.stderr)
sys.exit(1)
PY
result=$?
# The actual plugin runs in a separate sandboxed helper; coreaudiod alone omits
# the useful custom-property diagnostics. Never log PCM or controller payloads.
log show --last 3m --style compact --info --debug \
  --predicate 'process == "coreaudiod" OR subsystem == "org.madsense.audio"' \
  > "$output/coreaudio.stdout" 2> "$output/coreaudio.stderr"
exit "$result"
