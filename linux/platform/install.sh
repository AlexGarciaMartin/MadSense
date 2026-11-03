#!/bin/sh
# Explicit administrator installation only. The desktop application never runs this itself.
set -eu
test "$(id -u)" = 0 || { echo "Run this installer as an administrator." >&2; exit 1; }
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for name in broker.py broker_core.py madsense-broker.service org.madsense.bridge.policy madsense-modules.conf; do
    test -f "$source_dir/$name" && test ! -L "$source_dir/$name" || exit 1
done
test ! -L /usr/libexec/madsense || exit 1
install -d -o root -g root -m 0755 /usr/libexec/madsense
install -o root -g root -m 0644 "$source_dir/broker.py" "$source_dir/broker_core.py" /usr/libexec/madsense/
install -o root -g root -m 0644 "$source_dir/madsense-broker.service" /etc/systemd/system/madsense-broker.service
install -o root -g root -m 0644 "$source_dir/org.madsense.bridge.policy" /usr/share/polkit-1/actions/org.madsense.bridge.policy
install -o root -g root -m 0644 "$source_dir/madsense-modules.conf" /etc/modules-load.d/madsense.conf
modprobe vhci_hcd
systemctl daemon-reload
systemctl enable --now madsense-broker.service
