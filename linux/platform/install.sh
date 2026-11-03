#!/bin/sh
# Explicit administrator installation only. The desktop application never runs this itself.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
test "$(id -u)" = 0 || { echo "Run this installer as an administrator." >&2; exit 1; }
fail() { echo "$*" >&2; exit 1; }
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for name in broker.py broker_core.py madsense-broker.service org.madsense.bridge.policy madsense-modules.conf; do
    test -f "$source_dir/$name" && test ! -L "$source_dir/$name" || exit 1
done
# Check the supported host before replacing any installed service files.
for name in python3 systemctl udevadm getfacl setfacl pkcheck modprobe modinfo install; do
    command -v "$name" >/dev/null 2>&1 || fail "Missing prerequisite: $name"
done
python3 -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || fail "Python 3.10 or later is required."
manager_version=$(systemctl --version | sed -n '1s/^systemd \([0-9][0-9]*\).*/\1/p')
case "$manager_version" in ''|*[!0-9]*) fail "Cannot determine the systemd version.";; esac
test "$manager_version" -ge 254 || fail "systemd 254 or later is required for recovery descriptor storage."
for module in vhci_hcd hid_playstation snd_usb_audio xpad; do
    modinfo "$module" >/dev/null 2>&1 || fail "The running kernel is missing $module. Install its matching distro module package."
done
require_released() {
    for path in /run/madsense/lease.json /run/udev/rules.d/72-madsense-lease.rules; do
        if test -e "$path" || test -L "$path"; then
            fail "Pause MadSense and finish service recovery before installing. Pending lease state: $path"
        fi
    done
}
require_released
test ! -L /usr/libexec/madsense || fail "The installed helper directory must not be a symbolic link."
for path in /etc/systemd/system/madsense-broker.service /usr/share/polkit-1/actions/org.madsense.bridge.policy /etc/modules-load.d/madsense.conf; do
    test ! -L "$path" || fail "Refusing to replace a symbolic link: $path"
done
load_state=$(systemctl show madsense-broker.service --property=LoadState --value)
if test "$load_state" != not-found; then
    test "$load_state" = loaded || fail "The existing MadSense service is not in a loadable state: $load_state"
    # enable --now does not replace a running process. Stop the old version and
    # let its own cleanup finish before updating its Python files or unit.
    systemctl stop madsense-broker.service
    require_released
    stored=$(systemctl show madsense-broker.service --property=NFileDescriptorStore --value)
    test "$stored" = 0 || fail "The old service still owns recovery descriptors; restore it before updating."
fi
install -d -o root -g root -m 0755 /usr/libexec/madsense
install -o root -g root -m 0644 "$source_dir/broker.py" "$source_dir/broker_core.py" /usr/libexec/madsense/
install -o root -g root -m 0644 "$source_dir/madsense-broker.service" /etc/systemd/system/madsense-broker.service
install -o root -g root -m 0644 "$source_dir/org.madsense.bridge.policy" /usr/share/polkit-1/actions/org.madsense.bridge.policy
install -o root -g root -m 0644 "$source_dir/madsense-modules.conf" /etc/modules-load.d/madsense.conf
modprobe vhci_hcd
systemctl daemon-reload
systemctl enable madsense-broker.service
# Explicit installation of this new version may follow recent crash recovery
# or repeated upgrades. Reset only this idle unit's old start-limit counters;
# automatic runtime restarts retain the configured rate limit.
systemctl reset-failed madsense-broker.service
systemctl start madsense-broker.service
systemctl is-active --quiet madsense-broker.service ||
    fail "The installed MadSense service did not become active; inspect its systemd journal."
