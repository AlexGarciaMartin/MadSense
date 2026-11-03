#!/usr/bin/env bash
# Explicit owned guest experiment after the existing functional stages.
set -euo pipefail
[[ $# == 1 ]] || { echo 'usage: run_experiment.sh OWNER_UUID' >&2; exit 2; }
cd /home/ubuntu/madsense
python3 linux/research/ci_environment.py "$1"
sudo -n apt-get install -y --no-install-recommends pipewire pipewire-bin wireplumber \
  alsa-utils pulseaudio pulseaudio-utils dbus acl
python3 -m unittest discover -s linux/tests/audio-hotplug -p 'test_*.py' -v
sudo -n python3 linux/tests/audio-hotplug/run_guest.py --owned-guest-token "$1" \
  --functional-results "$PWD/result" --probe-directory "$PWD/app-probe" \
  --output "$PWD/result/audio-hotplug"
