#!/usr/bin/env bash
# Runs after the native broker baseline, inside the owned hardware-free guest.
set -euo pipefail
[[ $# == 1 || ( $# == 2 && $2 == --observe-audio-defaults ) ]] || {
  echo 'usage: run_consumer_guest.sh OWNER_UUID [--observe-audio-defaults]' >&2; exit 2;
}
observer_args=()
if [[ $# == 2 ]]; then observer_args+=(--observe-audio-defaults); fi
cd /home/ubuntu/madsense
python3 linux/research/ci_environment.py "$1"
python3 -c 'import json; r=json.load(open("result/result.json")); assert r["result"] == "passed" and not r.get("cleanupError")'
sudo -n systemctl start madsense-broker.service
sudo -n python3 linux/platform/integration/repeat_install_guest.py --owned-guest-token "$1" --output "$PWD/result/idle-upgrade"
# Distribution packages only; no physical/radio passthrough or production policy changes.
sudo -n apt-get install -y --no-install-recommends libhidapi-hidraw0 build-essential \
  libsdl2-2.0-0 libudev1 acl wine wine64 xvfb pulseaudio pulseaudio-utils dbus
python3 -m unittest discover -s linux/research -p test_consumer_session.py -v
python3 -m unittest discover -s linux/research -p test_audio_defaults.py -v
cc -std=c11 -O2 -Wall -Wextra -Werror linux/research/host_probe.c -o host_probe
chmod +x core-probe/MadSense.Linux.CoreProbe consumer-bin/consumer_sdl2 consumer-bin/consumer_sdl3 consumer-bin/ff_probe
sudo -n python3 linux/platform/integration/run_core_guest.py --owned-guest-token "$1" \
  --probe-directory "$PWD/core-probe" --host-probe "$PWD/host_probe" --output "$PWD/result/core-experiment" \
  --consumer-binaries "$PWD/consumer-bin" --wine-consumer "$PWD/consumer-bin/wine_consumer.exe" "${observer_args[@]}"
