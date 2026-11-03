#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 || ( $# == 2 && $2 == --require-core ) ]] || { echo 'usage: run_app_experiment.sh OWNER_UUID [--require-core]' >&2; exit 2; }
cd /home/ubuntu/madsense
python3 linux/research/ci_environment.py "$1"
python3 -c 'import json; r=json.load(open("result/result.json")); assert r["result"] == "passed" and not r.get("cleanupError")'
if [[ $# == 2 ]]; then
  PYTHONPATH=linux/platform/integration python3 - "$1" <<'PY'
import json
import sys
from qemu_core import verify_core_result
verify_core_result(json.load(open("result/core-experiment/result.json")), sys.argv[1])
upgrade = json.load(open("result/idle-upgrade/result.json"))
assert upgrade.get("result") == "passed" and len(upgrade.get("installations", [])) == 2
PY
fi
sudo -n systemctl start madsense-broker.service
sudo -n apt-get install -y libhidapi-hidraw0 alsa-utils
chmod +x app-probe/MadSense.Linux.AppProbe
sudo -n python3 linux/tests/app-integration/run_app_guest.py --owned-guest-token "$1" \
  --baseline-result "$PWD/result/result.json" --probe-directory "$PWD/app-probe" \
  --output "$PWD/result/app-experiment"
