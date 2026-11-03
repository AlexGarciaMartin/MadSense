#!/usr/bin/env bash
set -euo pipefail
[[ $# == 1 ]]
cd /home/ubuntu/madsense
python3 linux/research/ci_environment.py "$1"
export USBIP_BINARY_FILE="$PWD/usbip-binary.txt"
bash linux/research/prepare_debian_kernel.sh "$1" > prepare.log 2>&1
bash linux/platform/tests/run_native_broker_guest.sh "$1" \
  "$PWD/probe/MadSense.Linux.Probe" "$(cat "$USBIP_BINARY_FILE")" "$PWD/result"
