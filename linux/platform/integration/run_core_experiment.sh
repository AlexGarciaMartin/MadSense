#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]] || { echo 'usage: run_core_experiment.sh OWNER_UUID' >&2; exit 2; }
cd /home/ubuntu/madsense
python3 linux/research/ci_environment.py "$1"
# The launcher must have completed the native broker baseline in this guest.
python3 -c 'import json; r=json.load(open("result/result.json")); assert r["result"] == "passed" and not r.get("cleanupError")'
# The baseline deliberately stops its service at teardown.
sudo -n systemctl start madsense-broker.service
sudo -n python3 linux/platform/integration/repeat_install_guest.py --owned-guest-token "$1" --output "$PWD/result/idle-upgrade"
sudo -n apt-get install -y libhidapi-hidraw0 build-essential
cc -std=c11 -O2 -Wall -Wextra -Werror linux/research/host_probe.c -o host_probe
chmod +x core-probe/MadSense.Linux.CoreProbe
sudo -n python3 linux/platform/integration/run_core_guest.py --owned-guest-token "$1" \
  --probe-directory "$PWD/core-probe" --host-probe "$PWD/host_probe" --output "$PWD/result/core-experiment"
