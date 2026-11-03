#!/usr/bin/env bash
# Separate Debian adapter; no kernel replacement, policy writes or host changes.
set -euo pipefail
python3 "$(dirname "$0")/ci_environment.py" "${1:-}"
. /etc/os-release
[[ "$ID" == debian && "$VERSION_ID" == 13 ]]
uname -a
sudo -n apt-get update -qq
sudo -n apt-get install -y --no-install-recommends usbip usbutils alsa-utils kmod \
  libicu76 libssl3t64 libgssapi-krb5-2 libstdc++6 zlib1g
for module in vhci_hcd hid_playstation snd_usb_audio xpad uhid; do
  /usr/sbin/modinfo "$module" >/dev/null
  sudo -n /usr/sbin/modprobe "$module"
  /usr/sbin/modinfo -F vermagic "$module"
done
test -x /usr/sbin/usbip
/usr/sbin/usbip version
[[ -n "${USBIP_BINARY_FILE:-}" ]]
printf '%s\n' /usr/sbin/usbip > "$USBIP_BINARY_FILE"
