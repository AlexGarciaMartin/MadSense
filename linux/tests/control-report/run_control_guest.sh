#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]] || { echo 'usage: run_control_guest.sh OWNER_UUID' >&2; exit 2; }
cd /home/ubuntu/madsense
python3 linux/research/ci_environment.py "$1"
# Keep original broker and core assertions/disposal, then use a fresh actor and
# fresh ordinary consumer account for the independently instrumented copy.
bash linux/platform/integration/run_core_experiment.sh "$1"
python3 -c 'import json,sys;sys.path.insert(0,"linux/platform/integration");from qemu_core import verify_core_result;verify_core_result(json.load(open("result/core-experiment/result.json")),sys.argv[1])' "$1"
chmod +x control-core-probe/MadSense.Linux.CoreProbe consumer-bin/control_probe
sudo -n python3 linux/platform/integration/run_control_guest.py --owned-guest-token "$1" \
  --probe-directory "$PWD/control-core-probe" --host-probe "$PWD/host_probe" \
  --consumer-binaries "$PWD/consumer-bin" --output "$PWD/result/control-experiment"
