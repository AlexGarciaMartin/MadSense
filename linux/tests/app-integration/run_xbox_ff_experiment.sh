#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]] || { echo 'usage: run_xbox_ff_experiment.sh OWNER_UUID' >&2; exit 2; }
cd /home/ubuntu/madsense
python3 linux/research/ci_environment.py "$1"
# This fresh app/fixture session begins only after the original functional gates.
PYTHONPATH=linux/platform/integration:linux/tests/app-integration python3 - "$1" <<'PY'
import json
import sys
from qemu_core import verify_core_result
from qemu_app import verify_app_result
verify_core_result(json.load(open("result/core-experiment/result.json")), sys.argv[1])
verify_app_result(json.load(open("result/app-experiment/result.json")), sys.argv[1])
PY
cc -std=c11 -O2 -Wall -Wextra -Werror linux/research/ff_probe.c -o ff_probe
sudo -n python3 linux/tests/app-integration/run_app_guest.py --owned-guest-token "$1" \
  --baseline-result "$PWD/result/result.json" --probe-directory "$PWD/app-probe" \
  --xbox-ff-probe "$PWD/ff_probe" --output "$PWD/result/xbox-ff-app"
PYTHONPATH=linux/tests/app-integration python3 - "$1" <<'PY'
import json
import sys
from xbox_ff import verify_result
verify_result(json.load(open("result/xbox-ff-app/result.json")), sys.argv[1],
              "result/xbox-ff-app/xbox-consumer-ready.json")
PY
