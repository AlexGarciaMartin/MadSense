#!/usr/bin/env bash
# Test-only setup in the marked, disposable QEMU guest. Never ship these policies.
set -euo pipefail
[[ $# == 4 ]] || { echo 'usage: run_native_broker_guest.sh OWNER_UUID PROBE_EXE USBIP_EXE OUTPUT_DIR' >&2; exit 2; }
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
python3 "$script_dir/../../research/ci_environment.py" "$1"
[[ "$(cat /sys/class/dmi/id/sys_vendor)" == QEMU ]]
sudo -n apt-get update -qq
sudo -n apt-get install -y acl polkitd
sudo -n modprobe uhid
sudo -n modprobe vhci_hcd
sudo -n sh "$script_dir/../install.sh"
sudo -n python3 "$script_dir/native_broker_integration.py" --owned-guest-token "$1" \
    --probe "$2" --usbip "$3" --output "$4"
