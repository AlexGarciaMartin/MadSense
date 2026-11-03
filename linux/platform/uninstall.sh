#!/bin/sh
# Explicit administrator removal of this exact manually installed helper build.
# Pending recovery and locally modified files are preserved, never forced away.
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
fail() { echo "$*" >&2; exit 1; }
test "$(id -u)" = 0 || fail "Run this uninstaller as an administrator."
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for name in systemctl cmp; do
    command -v "$name" >/dev/null 2>&1 || fail "Missing prerequisite: $name"
done
require_released() {
    for path in /run/madsense/lease.json /run/udev/rules.d/72-madsense-lease.rules; do
        if test -e "$path" || test -L "$path"; then
            fail "Pause MadSense and finish service recovery before uninstalling. Pending lease state: $path"
        fi
    done
}
verify_file() {
    source=$1
    target=$2
    test -f "$source_dir/$source" && test ! -L "$source_dir/$source" || fail "Missing original helper source: $source"
    test ! -L "$target" || fail "Refusing to remove a symbolic link: $target"
    if test -e "$target"; then
        test -f "$target" && cmp -s "$source_dir/$source" "$target" ||
            fail "Installed file differs from this build; use its original package or review local changes: $target"
    fi
}
verify_files() {
    test ! -L /usr/libexec/madsense || fail "The installed helper directory must not be a symbolic link."
    verify_file broker.py /usr/libexec/madsense/broker.py
    verify_file broker_core.py /usr/libexec/madsense/broker_core.py
    verify_file madsense-broker.service /etc/systemd/system/madsense-broker.service
    verify_file org.madsense.bridge.policy /usr/share/polkit-1/actions/org.madsense.bridge.policy
    verify_file madsense-modules.conf /etc/modules-load.d/madsense.conf
}
require_released
verify_files
load_state=$(systemctl show madsense-broker.service --property=LoadState --value)
case "$load_state" in
    loaded)
        fragment=$(systemctl show madsense-broker.service --property=FragmentPath --value)
        test "$fragment" = /etc/systemd/system/madsense-broker.service ||
            fail "This is not the manually installed service; use its package manager: $fragment"
        systemctl stop madsense-broker.service
        require_released
        stored=$(systemctl show madsense-broker.service --property=NFileDescriptorStore --value)
        test "$stored" = 0 || fail "The service still owns recovery descriptors; restore it before uninstalling."
        systemctl disable madsense-broker.service
        ;;
    not-found) ;;
    *) fail "The existing MadSense service is not in a loadable state: $load_state" ;;
esac
require_released
verify_files
rm -f -- /usr/libexec/madsense/broker.py /usr/libexec/madsense/broker_core.py \
    /etc/systemd/system/madsense-broker.service \
    /usr/share/polkit-1/actions/org.madsense.bridge.policy \
    /etc/modules-load.d/madsense.conf
# Remove only an empty directory. Unknown files and user preferences are retained.
if test -d /usr/libexec/madsense; then rmdir /usr/libexec/madsense 2>/dev/null || true; fi
systemctl daemon-reload
test "$(systemctl show madsense-broker.service --property=LoadState --value)" = not-found ||
    fail "Another service definition remains; inspect it before running MadSense."
echo "MadSense helper removed. User settings, logs and kernel modules were retained."
