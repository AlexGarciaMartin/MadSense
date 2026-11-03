#!/usr/bin/env bash
set -euo pipefail
python3 "$(dirname "$0")/ci_environment.py" "${1:-}"
uname -a
sudo apt-get update -qq
sudo apt-get install -y linux-tools-generic linux-tools-common usbutils alsa-utils
missing_module=false
for module in vhci_hcd hid_playstation snd_usb_audio xpad; do
    if ! modinfo "$module" >/dev/null 2>&1; then missing_module=true; fi
done
if [[ "$missing_module" == true ]]; then
    # Generic modules cannot satisfy a running Azure kernel. Fail if its exact
    # supported package is unavailable; never skip or boot an unrecorded kernel.
    sudo apt-get install -y "linux-modules-extra-$(uname -r)"
fi
for module in vhci_hcd hid_playstation snd_usb_audio xpad; do
    sudo modprobe "$module"
    modinfo -F vermagic "$module"
done
usbip_binary="$(find /usr/lib/linux-tools -type f -name usbip -executable | sort -V | tail -1)"
[[ -n "$usbip_binary" && -x "$usbip_binary" ]]
"$usbip_binary" --version
if [[ -n "${GITHUB_ENV:-}" ]]; then printf 'USBIP_BINARY=%s\n' "$usbip_binary" >> "$GITHUB_ENV"; fi
if [[ -n "${USBIP_BINARY_FILE:-}" ]]; then printf '%s\n' "$usbip_binary" > "$USBIP_BINARY_FILE"; fi
