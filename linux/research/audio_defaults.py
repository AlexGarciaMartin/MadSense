#!/usr/bin/env python3
"""Observe distribution PulseAudio policy in an isolated, attributed guest UID.

No profile/volume/default selection, playback, UCM installation or global service
operation is performed. This observes an already attached synthetic USB card;
it cannot establish default-device behavior during physical/virtual hotplug.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import struct
import time

from consumer_session import ConsumerSession, bounded_bytes, require


PACKAGES = ("pulseaudio", "pulseaudio-utils", "libpulse0", "alsa-ucm-conf", "libasound2", "libasound2t64",
            "alsa-utils", "pipewire", "pipewire-pulse", "wireplumber", "dbus")


def digest(path):
    data = bounded_bytes(path, 4 * 1024 * 1024)
    return data, hashlib.sha256(data).hexdigest()


def inventory(session):
    """Record installed versions and relevant source/configuration without loading UCM."""
    files = [Path("/etc/os-release"), Path("/etc/pulse/daemon.conf"), Path("/etc/pulse/default.pa"),
             Path("/etc/pulse/client.conf"), Path("/usr/share/alsa/ucm2/common/directm.conf"),
             Path("/usr/share/alsa/ucm2/USB-Audio/USB-Audio.conf"),
             Path("/usr/share/pulseaudio/alsa-mixer/profile-sets/default.conf")]
    files.extend(Path("/etc/pulse/default.pa.d").glob("*.pa"))
    files.extend(Path("/etc/pulse/daemon.conf.d").glob("*.conf"))
    files.extend(Path("/usr/share/alsa/ucm2/USB-Audio/Sony").glob("*.conf"))
    files.extend(Path("/usr/share/doc", package, "copyright") for package in PACKAGES)
    destination = session.output / "installed-files"
    destination.mkdir()
    records = []
    for path in sorted(set(files)):
        if not path.exists():
            records.append(dict(path=str(path), absent=True)); continue
        require(path.is_file(), "Expected regular installed configuration/license")
        data, sha = digest(path)
        target = destination / path.relative_to("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append(dict(path=str(path), resolved=str(path.resolve()), size=len(data), sha256=sha))
    # The manifest identifies the complete installed UCM tree, including generic
    # USB matching rules and includes outside the selected Sony file copies.
    ucm = []
    for path in sorted(Path("/usr/share/alsa/ucm2").rglob("*")):
        if path.is_file():
            require(len(ucm) < 20000, "Unexpectedly large UCM tree")
            data, sha = digest(path)
            ucm.append(dict(path=str(path), resolved=str(path.resolve()), size=len(data), sha256=sha))
    (session.output / "installed-files.json").write_text(json.dumps(records, indent=2) + "\n")
    (session.output / "ucm-tree.json").write_text(json.dumps(ucm, indent=2) + "\n")
    session.command("kernel-version", ["uname", "-a"])
    session.command("package-versions", ["dpkg-query", "--show", "--showformat=${binary:Package}\t${Version}\t${Architecture}\n", *PACKAGES], required=False)
    for name in ("pulseaudio", "pactl", "pipewire", "wireplumber", "alsaucm", "dbus-daemon"):
        if shutil.which(name):
            session.command(name + "-version", [name, "--version"], required=False)
    return records


def select_card(cards, alsa_card, card_sysfs):
    """Pure identity join; no first VID/PID/name or default-sink selection."""
    matches = [card for card in cards if str(card.get("properties", {}).get("alsa.card")) == str(alsa_card)]
    require(len(matches) == 1, "PulseAudio did not expose one exact owned ALSA card")
    card = matches[0]
    devpath = card.get("properties", {}).get("sysfs.path")
    require(isinstance(devpath, str) and devpath.startswith("/devices/")
            and "/sys" + devpath == str(card_sysfs), "PulseAudio card sysfs identity does not match owned ALSA card")
    require(type(card.get("index")) is int and isinstance(card.get("name"), str), "Incomplete PulseAudio card identity")
    return card


def describe_sinks(sinks, card):
    result = []
    for sink in sinks:
        # pactl 16.1 JSON does not expose pa_sink_info.card. Join the ALSA
        # index plus the exact owning module instead of assuming that field.
        if str(sink.get("properties", {}).get("alsa.card")) != str(card["properties"]["alsa.card"]):
            continue
        require(type(card.get("owner_module")) is int and sink.get("owner_module") == card["owner_module"],
                "ALSA sink is not owned by the attributed card module")
        channels = sink.get("channel_map", "").split(",")
        require(channels and all(channels) and len(set(channels)) == len(channels), "Invalid sink channel map")
        volumes = sink.get("volume", {})
        values = [volumes.get(channel, {}).get("value") for channel in channels]
        require(all(type(value) is int and value >= 0 for value in values), "Missing per-channel volume evidence")
        rear = [volumes.get(channel, {}).get("value") for channel in ("rear-left", "rear-right")]
        result.append(dict(index=sink["index"], name=sink["name"], channels=channels,
                           sampleSpecification=sink.get("sample_specification"), volume=volumes,
                           mute=sink.get("mute"), activePort=sink.get("active_port"),
                           fourChannels=len(channels) == 4,
                           quadMap=channels == ["front-left", "front-right", "rear-left", "rear-right"],
                           rearVolumeAt100Percent=all(value == 65536 for value in rear) if all(value is not None for value in rear) else None))
    return result


def observe_defaults(before, after):
    first, last = before.get("default_sink_name"), after.get("default_sink_name")
    return dict(before=first, after=last, preservedDuringObservation=bool(first) and first == last,
                scope="read-only snapshots after existing-card enumeration", hotplugPreservationValidated=False)


def wait_socket(path, process, uid, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        require(process.poll() is None, "Private daemon exited before socket creation")
        if path.exists():
            info = path.lstat()
            require(stat.S_ISSOCK(info.st_mode) and info.st_uid == uid, "Unexpected private daemon socket")
            return
        time.sleep(.05)
    raise TimeoutError("Private daemon socket was not created")


def private_dbus(session):
    path = session.root / "runtime/bus"
    address = "unix:path=" + str(path)
    process = session.start("private-dbus", ["dbus-daemon", "--session", "--nofork", "--address=" + address, "--print-address=1"])
    wait_socket(path, process, session.uid)
    # Prevent default audio modules from contacting the guest's global logind,
    # BlueZ, rtkit or another user's session bus. Missing services are an explicit
    # limitation of this private policy observation, not a desktop simulation.
    session.env.update(DBUS_SESSION_BUS_ADDRESS=address, DBUS_SYSTEM_BUS_ADDRESS=address)
    return process


def private_pulse(session):
    runtime = session.root / "runtime"
    session.env.update(XDG_CONFIG_HOME=str(session.root / "home/.config"),
                       XDG_STATE_HOME=str(session.root / "home/.local/state"),
                       XDG_CACHE_HOME=str(session.root / "home/.cache"),
                       PULSE_RUNTIME_PATH=str(runtime / "pulse"))
    private_dbus(session)
    argv = ["pulseaudio", "--system=no", "--daemonize=no", "--use-pid-file=no", "--exit-idle-time=-1", "--log-target=stderr"]
    session.command("pulse-effective-daemon-config", [*argv, "--dump-conf"])
    process = session.start("default-pulseaudio", argv)
    path = runtime / "pulse/native"
    wait_socket(path, process, session.uid)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
        endpoint.settimeout(3)
        endpoint.connect(str(path))
        pid, uid, gid = struct.unpack("3i", endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require((pid, uid, gid) == (process.pid, session.uid, session.gid), "Pulse socket peer is not the exact owned process")
    session.env.update(PULSE_SERVER="unix:" + str(path))
    session.result["audioDaemon"] = dict(kind="private-pulseaudio-distribution-config", argv=argv,
        socket=str(path), peerPid=pid, peerUid=uid, privateSessionAndSystemBus=True,
        forcedSampleRate=False, forcedChannelMap=False, forcedVolume=False, forcedProfile=False,
        globalConfigurationModified=False, realDesktopSessionValidated=False)
    session.save()
    return process


def capture(session, label):
    session.check_device()
    value = dict(monotonicNs=time.monotonic_ns())
    for kind in ("info", "cards", "sinks", "sources", "modules"):
        args = ["pactl", "--server", session.env["PULSE_SERVER"], "--format=json"]
        args += ["info"] if kind == "info" else ["list", kind]
        _, stdout = session.command(label + "-" + kind, args)
        value[kind] = json.loads(stdout)
    return value


def run(ready_path, evidence, physical_parent):
    # ConsumerSession owns UID/process lifetime, exact virtual-node grants and
    # restoration. The fixture/lease lifecycle belongs to the outer core harness.
    with ConsumerSession(ready_path, evidence, physical_parent) as session:
        session.result.update(scope="owned-private-default-audio-observer", result="failed",
            defaultDesktopStackValidated=False, pipeWireValidated=False, pcmDeliveryVerified=False,
            profileSelectionPerformed=False, volumeAdjustmentPerformed=False,
            hotplugDefaultPreservationValidated=False)
        for program in ("pulseaudio", "pactl", "dbus-daemon", "dpkg-query", "amixer", "udevadm"):
            require(shutil.which(program), f"Missing observer prerequisite: {program}")
        require(session.card is not None, "Owned virtual USB has no ALSA card")
        cards = list(Path("/sys/class/sound").glob("card[0-9]*"))
        require(len(cards) == 1 and cards[0].name == f"card{session.card}", "Only the attributed synthetic ALSA card may exist")
        require(not list(Path("/sys/class/bluetooth").glob("hci*")), "Radio interfaces are outside this experiment")
        card_sysfs = cards[0].resolve(strict=True)
        require(card_sysfs.is_relative_to(session.parent), "ALSA card is outside exact virtual USB parent")
        session.result["alsaIdentity"] = dict(card=session.card, sysfs=str(card_sysfs), usbParent=str(session.parent))
        inventory(session)
        session.command("alsa-controls-before", ["amixer", "--card", str(session.card), "contents"])
        session.command("alsa-udev-properties", ["udevadm", "info", "--query=property", "--path", str(card_sysfs)])
        process = private_pulse(session)
        before = None
        for attempt in range(30):
            require(process.poll() is None, "Private PulseAudio exited during enumeration")
            snapshot = capture(session, f"enumeration-{attempt:02}")
            (session.output / f"enumeration-{attempt:02}.json").write_text(json.dumps(snapshot, indent=2) + "\n")
            if any(str(card.get("properties", {}).get("alsa.card")) == str(session.card) for card in snapshot["cards"]):
                before = snapshot; break
            time.sleep(.25)
        require(before is not None, "Distribution PulseAudio did not expose the owned card; inspect daemon logs/UCM versions")
        card = select_card(before["cards"], session.card, card_sysfs)
        require(len(before["cards"]) == 1, "Unexpected additional audio card")
        sinks = describe_sinks(before["sinks"], card)
        profiles = card.get("profiles", {})
        require(isinstance(profiles, dict), "Unexpected PulseAudio profile schema")
        session.command("alsa-controls-after-enumeration", ["amixer", "--card", str(session.card), "contents"])
        after = capture(session, "after-observation")
        after_card = select_card(after["cards"], session.card, card_sysfs)
        require(len(after["cards"]) == 1, "An unexpected audio card appeared during observation")
        session.result["observation"] = dict(activeProfile=card.get("active_profile"), availableProfiles=profiles,
            directProfileAdvertised="Direct" in profiles, sinks=sinks,
            defaultSink=observe_defaults(before["info"], after["info"]),
            activeProfileStable=card.get("active_profile") == after_card.get("active_profile"),
            sinkSettingsStable=sinks == describe_sinks(after["sinks"], after_card),
            anyQuadSink=any(sink["quadMap"] for sink in sinks),
            limits=["Existing-card enumeration, not hotplug", "Private empty account state", "Private D-Bus without desktop services",
                    "No PCM/Wine/game playback", "Daemon can initialize mixer controls on the owned virtual card"])
        (session.output / "before.json").write_text(json.dumps(before, indent=2) + "\n")
        (session.output / "after.json").write_text(json.dumps(after, indent=2) + "\n")
        require(session.result["observation"]["defaultSink"]["preservedDuringObservation"], "Default sink changed or was absent during observation")
        session.result["result"] = "observed"
        session.save()
    return session.result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument("--physical-parent", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.ready, args.output, args.physical_parent)
    return 0 if result["result"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
