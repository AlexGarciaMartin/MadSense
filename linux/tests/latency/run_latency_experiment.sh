#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 || ( $# == 2 && $2 == --trace ) ]] || { echo 'usage: run_latency_experiment.sh OWNER_UUID [--trace]' >&2; exit 2; }
cd /home/ubuntu/madsense
python3 linux/research/ci_environment.py "$1"
trace_arguments=()
if [[ $# == 2 ]]; then
  trace_arguments=(--trace-probe-directory "$PWD/app-probe-trace")
fi
# All packages and the active helper come from the functional gates. Do not
# reinstall, restart a service or rerun a kernel/broker baseline for measurement.
sudo -n python3 linux/tests/latency/run_latency_guest.py --owned-guest-token "$1" \
  --baseline-result "$PWD/result/result.json" \
  --core-result "$PWD/result/core-experiment/result.json" \
  --app-result "$PWD/result/app-experiment/result.json" \
  --upgrade-result "$PWD/result/idle-upgrade/result.json" \
  --probe-directory "$PWD/app-probe" --output "$PWD/result/latency-experiment" "${trace_arguments[@]}"
