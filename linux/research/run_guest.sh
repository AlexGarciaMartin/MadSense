#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/madsense
export USBIP_BINARY_FILE="$PWD/usbip-binary.txt"
bash research/prepare_kernel.sh "$1" > prepare.log 2>&1
python3 research/kernel_probe.py --persona "$2" --owned-guest-token "$1" \
    --self-contained --probe probe/MadSense.Linux.Probe --host-probe host_probe \
    --usbip "$(cat "$USBIP_BINARY_FILE")" --output result
