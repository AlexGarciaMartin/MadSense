#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]] || { echo 'usage: run_protocol_experiment.sh OWNER_UUID' >&2; exit 2; }
cd /home/ubuntu/madsense
python3 linux/research/ci_environment.py "$1"
# Reuse the owned VM, but only after the original sessions passed and closed.
PYTHONPATH=linux/platform/integration:linux/tests/app-integration python3 - "$1" <<'PY'
import json
import sys
from qemu_core import verify_core_result
from qemu_app import verify_app_result
verify_core_result(json.load(open("result/core-experiment/result.json")), sys.argv[1])
verify_app_result(json.load(open("result/app-experiment/result.json")), sys.argv[1])
PY
sudo -n python3 linux/platform/integration/run_core_guest.py --owned-guest-token "$1" \
  --probe-directory "$PWD/core-probe" --host-probe "$PWD/host_probe" --extended-protocol \
  --output "$PWD/result/protocol-core"
sudo -n python3 linux/tests/app-integration/run_app_guest.py --owned-guest-token "$1" \
  --baseline-result "$PWD/result/result.json" --probe-directory "$PWD/app-probe" --extended-protocol \
  --output "$PWD/result/protocol-app"
PYTHONPATH=linux/platform/integration:linux/tests/app-integration python3 - "$1" <<'PY'
import json
import sys
from protocol_checks import verify_protocol_results
verify_protocol_results(json.load(open("result/protocol-core/result.json")),
                        json.load(open("result/protocol-app/result.json")), sys.argv[1])
PY
