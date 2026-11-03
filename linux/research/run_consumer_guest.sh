#!/usr/bin/env bash
# Runs after the native broker baseline, inside the owned hardware-free guest.
set -euo pipefail
[[ $# -ge 1 ]] || { echo 'usage: run_consumer_guest.sh OWNER_UUID [--observe-audio-defaults] [--observe-pipewire-defaults] [--adjust-owned-rear-unity] [--wine-udev-variant] [--ge-umu] [--hidapi-windows]' >&2; exit 2; }
owner_uuid=$1
shift
observer_args=()
pipewire_observer=false
ge_umu=false
wine_udev=false
hidapi_windows=false
for option in "$@"; do
  case "$option" in
    --observe-audio-defaults) observer_args+=("$option") ;;
    --observe-pipewire-defaults) observer_args+=("$option"); pipewire_observer=true ;;
    --adjust-owned-rear-unity) observer_args+=("$option"); pipewire_observer=true ;;
    --wine-udev-variant) observer_args+=("$option"); wine_udev=true ;;
    --ge-umu) ge_umu=true ;;
    --hidapi-windows) hidapi_windows=true ;;
    *) echo "Unknown consumer experiment option: $option" >&2; exit 2 ;;
  esac
done
cd /home/ubuntu/madsense
if "$ge_umu"; then
  "$wine_udev" || { echo '--ge-umu requires --wine-udev-variant' >&2; exit 2; }
  test -d "$PWD/ge-umu-private-cache"
  observer_args+=(--ge-umu-cache "$PWD/ge-umu-private-cache")
fi
if "$hidapi_windows"; then
  test -f "$PWD/consumer-bin/hidapi_windows_consumer.exe"
  observer_args+=(--hidapi-windows-consumer "$PWD/consumer-bin/hidapi_windows_consumer.exe")
fi
python3 linux/research/ci_environment.py "$owner_uuid"
python3 -c 'import json; r=json.load(open("result/result.json")); assert r["result"] == "passed" and not r.get("cleanupError")'
sudo -n systemctl start madsense-broker.service
sudo -n python3 linux/platform/integration/repeat_install_guest.py --owned-guest-token "$owner_uuid" --output "$PWD/result/idle-upgrade"
# Distribution packages only; no physical/radio passthrough or production policy changes.
sudo -n apt-get install -y --no-install-recommends libhidapi-hidraw0 build-essential \
  libsdl2-2.0-0 libudev1 acl wine wine64 xvfb pulseaudio pulseaudio-utils dbus liblua5.4-0
if "$pipewire_observer"; then
  # Explicit distribution binaries in this guest; no desktop meta-package and
  # no host service/session changes. The observer starts only its private UID.
  sudo -n apt-get install -y --no-install-recommends pipewire pipewire-bin wireplumber alsa-utils
fi
python3 -m unittest discover -s linux/research -p test_consumer_session.py -v
python3 -m unittest discover -s linux/research -p test_consumer_files.py -v
python3 -m unittest discover -s linux/research -p test_consumer_phases.py -v
python3 -m unittest discover -s linux/research -p test_consumer_stop.py -v
python3 -m unittest discover -s linux/research -p test_wine_variant.py -v
python3 -m unittest discover -s linux/research -p test_hidapi_windows_runtime.py -v
python3 -m unittest discover -s linux/research -p test_audio_defaults.py -v
python3 -m unittest discover -s linux/research -p test_pipewire_defaults.py -v
python3 -m unittest discover -s linux/research -p test_pipewire_gain.py -v
if "$ge_umu"; then
  python3 -m unittest discover -s linux/research -p test_ge_umu.py -v
  python3 -m unittest discover -s linux/research -p test_ge_umu_consumer.py -v
fi
cc -std=c11 -O2 -Wall -Wextra -Werror linux/research/host_probe.c -o host_probe
chmod +x core-probe/MadSense.Linux.CoreProbe consumer-bin/consumer_sdl2 consumer-bin/consumer_sdl3 consumer-bin/ff_probe
sudo -n python3 linux/platform/integration/run_core_guest.py --owned-guest-token "$owner_uuid" \
  --probe-directory "$PWD/core-probe" --host-probe "$PWD/host_probe" --output "$PWD/result/core-experiment" \
  --consumer-binaries "$PWD/consumer-bin" --wine-consumer "$PWD/consumer-bin/wine_consumer.exe" "${observer_args[@]}"
