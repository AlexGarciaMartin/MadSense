#!/usr/bin/env bash
# One entry point after the owned guest has received its hash-checked payload.
set -euo pipefail
[[ $# == 1 ]] || { echo 'usage: run_broker_experiment.sh OWNER_UUID' >&2; exit 2; }
cd /home/ubuntu/madsense
python3 linux/research/ci_environment.py "$1"
export USBIP_BINARY_FILE="$PWD/usbip-binary.txt"
bash linux/research/prepare_kernel.sh "$1" > prepare.log 2>&1
bash linux/platform/tests/run_native_broker_guest.sh "$1" \
    "$PWD/probe/MadSense.Linux.Probe" "$(cat "$USBIP_BINARY_FILE")" "$PWD/result"
