#!/usr/bin/env bash
set -euo pipefail
[[ $# -ge 1 && $# -le 2 ]] || { echo 'usage: run_ge_only_guest.sh OWNER_UUID [--observe-kernel-waits]' >&2; exit 2; }
owner_uuid=$1
shift
observer_args=()
if [[ $# == 1 ]]; then
  [[ $1 == --observe-kernel-waits ]] || exit 2
  observer_args+=("$1")
fi
cd /home/ubuntu/madsense
python3 linux/research/ci_environment.py "$owner_uuid"
test -d ge-umu-private-cache
# First actor completes the unmodified nine-case core baseline and disposal.
bash linux/platform/integration/run_core_experiment.sh "$owner_uuid"
python3 -c 'import sys; sys.path.insert(0,"linux/research"); from ge_umu_standalone import prior_gates; prior_gates("result",sys.argv[1])' "$owner_uuid"
# No distribution Wine, SDL consumer or desktop-profile observer is run here.
sudo -n apt-get install -y --no-install-recommends libudev1 acl xvfb pulseaudio pulseaudio-utils dbus
# Host payload construction is tested before boot; guest runs only gate/error
# regressions that are supported by the deliberately smaller staged tree.
python3 -m unittest discover -s linux/research -p test_ge_umu_standalone.py -k StandaloneTests -v
sudo -n python3 linux/platform/integration/run_core_guest.py --owned-guest-token "$owner_uuid" \
  --probe-directory "$PWD/core-probe" --host-probe "$PWD/host_probe" --output "$PWD/result/core-ge-experiment" \
  --consumer-binaries "$PWD/consumer-bin" --wine-consumer "$PWD/consumer-bin/wine_consumer.exe" \
  --hidapi-windows-consumer "$PWD/consumer-bin/hidapi_windows_consumer.exe" \
  --ge-umu-cache "$PWD/ge-umu-private-cache" --ge-umu-only --functional-results "$PWD/result" "${observer_args[@]}"
