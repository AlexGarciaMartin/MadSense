#!/usr/bin/env python3
"""Private distribution-policy observation for PipeWire 1.0 / WirePlumber 0.4.

Owned synthetic VM only. No policy overrides, profile/volume/default changes,
playback, global services, package installation or production helper changes.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import socket
import struct
import time

from audio_defaults import digest, inventory, private_dbus, wait_socket
from consumer_session import ConsumerSession, bounded_bytes, require


PREFIX = "PipeWire:Interface:"
PACKAGES = ("pipewire", "pipewire-bin", "libpipewire-0.3-0t64", "libpipewire-0.3-modules",
            "libspa-0.2-modules", "wireplumber", "libwireplumber-0.4-0")


def supported_versions(pipewire, wireplumber):
    """Check compiled AND linked versions, without treating WP 0.5 as Lua 0.4."""
    result = {}
    for name, output, expected in (("pipewire", pipewire, (1, 0)), ("wireplumber", wireplumber, (0, 4))):
        versions = re.findall(r"(?:Compiled|Linked) with lib" + name + r" (\d+)\.(\d+)\.(\d+)\b", output)
        require(len(versions) == 2, f"Missing compiled/linked {name} version evidence")
        values = [tuple(map(int, value)) for value in versions]
        result[name] = dict(compiled=".".join(versions[0]), linked=".".join(versions[1]))
        require(all(value[:2] == expected for value in values),
                f"Unsupported {name} version for this adapter: {values}; retain inventory, validate a separate adapter")
    return result


def props(obj):
    return obj.get("info", {}).get("props", {})


def objects(snapshot, kind):
    return [obj for obj in snapshot if obj.get("type") == PREFIX + kind]


def object_serial(obj):
    value = props(obj).get("object.serial")
    require((type(value) is int and value >= 0) or
            (isinstance(value, str) and value.isascii() and value.isdecimal()),
            "Missing PipeWire object serial; a reused registry id cannot prove continuity")
    return int(value)


def validate_dump(snapshot):
    require(isinstance(snapshot, list) and len(snapshot) <= 4096, "Invalid or excessive pw-dump object list")
    ids = [obj.get("id") for obj in snapshot if isinstance(obj, dict)]
    require(len(ids) == len(snapshot) and all(type(value) is int and value >= 0 for value in ids)
            and len(set(ids)) == len(ids), "Missing or duplicate PipeWire object identity")


def select_device(snapshot, alsa_card, card_sysfs):
    validate_dump(snapshot)
    cards = [obj for obj in objects(snapshot, "Device") if props(obj).get("device.api") == "alsa"]
    matches = [obj for obj in cards if str(props(obj).get("api.alsa.card")) == str(alsa_card)]
    require(len(matches) == 1 and len(cards) == 1, "PipeWire must expose only the exact owned ALSA card")
    card = matches[0]
    path = props(card).get("device.sysfs.path")
    require(isinstance(path, str) and path.startswith("/devices/") and "/sys" + path == str(card_sysfs),
            "PipeWire ALSA index and exact card sysfs path do not agree")
    require(isinstance(props(card).get("device.name"), str), "Missing attributed PipeWire device name")
    return card


def volume_evidence(value):
    """Preserve channel arrays; never infer them from scalar/rounded wpctl volume."""
    channels, volumes = value.get("channelMap"), value.get("channelVolumes")
    complete = (isinstance(channels, list) and bool(channels) and
                all(isinstance(channel, str) for channel in channels) and len(set(channels)) == len(channels) and
                isinstance(volumes, list) and len(channels) == len(volumes) and
                all(type(volume) in (int, float) and math.isfinite(volume) and volume >= 0 for volume in volumes))
    rear = {channel: volume for channel, volume in zip(channels, volumes)} if complete else {}
    return dict(raw=value, perChannelValuesPresent=complete,
                quadMap=channels == ["FL", "FR", "RL", "RR"] if complete else None,
                rearChannelsReportedUnity=all(rear[channel] == 1.0 for channel in ("RL", "RR"))
                if "RL" in rear and "RR" in rear else None,
                scope="pw-dump reported SPA values; not hardware gain or delivered PCM")


def describe_nodes(snapshot, card):
    result = []
    for node in objects(snapshot, "Node"):
        properties = props(node)
        if properties.get("media.class") not in ("Audio/Sink", "Audio/Source"):
            continue
        parent_id = properties.get("device.id")
        if str(parent_id) != str(card["id"]):
            # A null/synthetic policy node can exist, but no foreign ALSA node
            # may be attributed using matching descriptions or ALSA numbers.
            require(not any(key in properties for key in ("api.alsa.pcm.card", "alsa.card")),
                    "An ALSA node is not owned by the exact PipeWire card object")
            continue
        require(str(properties.get("api.alsa.pcm.card")) == str(props(card)["api.alsa.card"]),
                "Owned PipeWire node refers to a different or missing ALSA PCM card")
        require(isinstance(properties.get("node.name"), str) and properties["node.name"], "Missing node name")
        parameters = node.get("info", {}).get("params", {})
        require(isinstance(parameters, dict), "Invalid node parameter schema")
        values = parameters.get("Props", [])
        require(isinstance(values, list) and all(isinstance(value, dict) for value in values), "Invalid SPA Props schema")
        result.append(dict(id=node["id"], serial=object_serial(node), name=properties["node.name"],
            mediaClass=properties["media.class"], state=node.get("info", {}).get("state"),
            properties=properties, parameters=parameters, volumes=[volume_evidence(value) for value in values]))
    return sorted(result, key=lambda node: node["id"])


def default_sink(snapshot):
    metadata = [obj for obj in objects(snapshot, "Metadata") if obj.get("props", {}).get("metadata.name") == "default"]
    require(len(metadata) <= 1, "Ambiguous default metadata object")
    values = {}
    for row in metadata[0].get("metadata", []) if metadata else []:
        if row.get("subject") != 0 or row.get("key") not in ("default.audio.sink", "default.configured.audio.sink"):
            continue
        key = row["key"]
        value = row.get("value")
        require(key not in values and row.get("type") == "Spa:String:JSON" and isinstance(value, dict)
                and isinstance(value.get("name"), str) and value["name"], "Malformed default sink metadata")
        values[key] = value["name"]
    name = values.get("default.audio.sink")
    matches = [node for node in objects(snapshot, "Node") if props(node).get("node.name") == name and
               props(node).get("media.class") == "Audio/Sink"] if name else []
    require(name is None or len(matches) == 1, "Default sink metadata does not identify one live sink")
    return dict(name=name, configuredName=values.get("default.configured.audio.sink"),
                id=matches[0]["id"] if matches else None,
                serial=object_serial(matches[0]) if matches else None)


def observe_defaults(before, after):
    return dict(before=before, after=after,
                preservedDuringObservation=(before == after) if before["name"] and after["name"] else None,
                changedDuringObservation=before != after,
                scope="post-enumeration queries in a fresh private account", hotplugPreservationValidated=False)


def summarize(snapshot, card_number, card_sysfs):
    card = select_device(snapshot, card_number, card_sysfs)
    parameters = card.get("info", {}).get("params", {})
    require(isinstance(parameters, dict), "Invalid device parameter schema")
    profiles = parameters.get("EnumProfile", [])
    require(isinstance(profiles, list) and all(isinstance(profile, dict) for profile in profiles), "Invalid profile schema")
    routes = parameters.get("Route", [])
    require(isinstance(routes, list) and all(isinstance(route, dict) for route in routes), "Invalid route schema")
    return dict(device=dict(id=card["id"], properties=props(card), parameters=parameters),
                directProfileAdvertised=any(profile.get("name") == "Direct" for profile in profiles),
                routeVolumes=[volume_evidence(route["props"]) for route in routes if isinstance(route.get("props"), dict)],
                nodes=describe_nodes(snapshot, card), defaultSink=default_sink(snapshot))


def configuration_inventory(session):
    inventory(session)  # OS/UCM/Sony Direct/license evidence shared with Pulse adapter.
    root = session.output / "pipewire-installed-files"
    root.mkdir()
    records, total = [], 0
    paths = []
    for directory in ("/etc/pipewire", "/usr/share/pipewire", "/etc/wireplumber", "/usr/share/wireplumber"):
        path = Path(directory)
        if not path.exists():
            records.append(dict(path=directory, absent=True)); continue
        paths.extend(item for item in path.rglob("*") if item.is_file())
    paths.extend(Path("/usr/share/doc", package, "copyright") for package in PACKAGES)
    require(len(paths) <= 5000, "Unexpectedly large PipeWire configuration tree")
    for path in sorted(set(paths)):
        if not path.exists():
            records.append(dict(path=str(path), absent=True)); continue
        data, sha = digest(path)
        total += len(data)
        require(total <= 32 * 1024 * 1024, "PipeWire configuration evidence exceeds 32 MiB")
        target = root / path.relative_to("/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        records.append(dict(path=str(path), resolved=str(path.resolve()), size=len(data), sha256=sha))
    (session.output / "pipewire-installed-files.json").write_text(json.dumps(records, indent=2) + "\n")
    session.command("pipewire-package-versions", ["dpkg-query", "--show", "--showformat=${binary:Package}\t${Version}\t${Architecture}\n", *PACKAGES], required=False)
    versions = {}
    for program in ("pipewire", "wireplumber", "pw-dump", "pw-config"):
        _, raw = session.command("adapter-" + program + "-version", [program, "--version"])
        versions[program] = raw.decode("utf-8", errors="replace")
    session.result["versionOutput"] = versions
    session.save()
    session.result["versions"] = supported_versions(versions["pipewire"], versions["wireplumber"])
    for name in ("pipewire.conf", "client.conf"):
        for action in ("paths", "list"):
            session.command(name + "-" + action, ["pw-config", "--name", name, "--no-colors", action])
    session.result["configurationEvidence"] = dict(installedTreesCopied=True, installedTreesUsedUnmodified=True,
        pipewireResolvedPathsAndSectionsCaptured=True, wireplumberEffectiveLuaExportAvailable=False,
        wireplumberConfigurationFamily="0.4 SPA-JSON plus Lua config and fragments", copiedToActiveConfiguration=False)


def check_peer(session, process):
    require(process.poll() is None, "Private PipeWire exited")
    path = session.root / "runtime/pipewire-0"
    wait_socket(path, process, session.uid, seconds=3)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
        endpoint.settimeout(3)
        endpoint.connect(str(path))
        identity = struct.unpack("3i", endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(identity == (process.pid, session.uid, session.gid), "PipeWire peer is not the exact owned PID/UID/GID")
    return dict(socket=str(path), pid=identity[0], uid=identity[1], gid=identity[2])


def private_stack(session):
    runtime = session.root / "runtime"
    session.env.update(XDG_CONFIG_HOME=str(session.root / "home/.config"),
                       XDG_STATE_HOME=str(session.root / "home/.local/state"),
                       XDG_DATA_HOME=str(session.root / "home/.local/share"),
                       XDG_CACHE_HOME=str(session.root / "home/.cache"),
                       PIPEWIRE_RUNTIME_DIR=str(runtime), PIPEWIRE_CORE="pipewire-0",
                       PIPEWIRE_REMOTE=str(runtime / "pipewire-0"), NO_COLOR="1")
    private_dbus(session)
    configuration_inventory(session)
    process = session.start("private-pipewire", ["pipewire"])
    wait_socket(runtime / "pipewire-0", process, session.uid)
    peer = check_peer(session, process)
    manager = session.start("private-wireplumber", ["wireplumber"])
    session.result["audioDaemon"] = dict(kind="private-pipewire-wireplumber-distribution-config", peer=peer,
        wireplumberPid=manager.pid, privateSessionAndSystemBus=True, environment=session.env,
        pipewirePulseStarted=False, forcedProfile=False, forcedVolume=False, forcedChannelMap=False,
        globalConfigurationModified=False, realDesktopSessionValidated=False)
    session.save()
    return process, manager


def capture(session, process, manager, label):
    session.check_device()
    check_peer(session, process)
    require(manager.poll() is None, "Private WirePlumber exited")
    _, raw = session.command(label + "-pw-dump", ["pw-dump", "--no-colors", "--remote", session.env["PIPEWIRE_REMOTE"]], timeout=15)
    snapshot = json.loads(raw)
    validate_dump(snapshot)
    (session.output / (label + ".json")).write_text(json.dumps(snapshot, indent=2) + "\n")
    return snapshot


def run(ready_path, evidence, physical_parent):
    with ConsumerSession(ready_path, evidence, physical_parent) as session:
        session.result.update(scope="owned-private-pipewire-default-observer", result="failed",
            defaultDesktopStackValidated=False, pcmDeliveryVerified=False, hotplugDefaultPreservationValidated=False,
            profileSelectionPerformed=False, volumeAdjustmentPerformed=False, wireplumber05Validated=False)
        for program in ("pipewire", "wireplumber", "pw-dump", "pw-config", "wpctl", "dbus-daemon", "amixer", "udevadm", "dpkg-query"):
            require(shutil.which(program), f"Missing observer prerequisite: {program}")
        cards = list(Path("/sys/class/sound").glob("card[0-9]*"))
        require(session.card is not None and len(cards) == 1 and cards[0].name == f"card{session.card}",
                "Only the attributed synthetic ALSA card may exist")
        require(not list(Path("/sys/class/bluetooth").glob("hci*")) and not list(Path("/sys/class/video4linux").glob("video*")),
                "Radio/camera devices are outside this private experiment")
        card_sysfs = cards[0].resolve(strict=True)
        require(card_sysfs.is_relative_to(session.parent), "ALSA card is outside exact owned virtual USB parent")
        session.result["alsaIdentity"] = dict(card=session.card, sysfs=str(card_sysfs), usbParent=str(session.parent))
        session.command("alsa-controls-before", ["amixer", "--card", str(session.card), "contents"])
        session.command("alsa-udev-properties", ["udevadm", "info", "--query=property", "--path", str(card_sysfs)])
        process, manager = private_stack(session)
        previous = before = None
        for attempt in range(30):
            snapshot = capture(session, process, manager, f"enumeration-{attempt:02}")
            present = any(str(props(card).get("api.alsa.card")) == str(session.card) for card in objects(snapshot, "Device"))
            manager_connected = any(str(props(client).get("application.process.id")) == str(manager.pid) for client in objects(snapshot, "Client"))
            if present and manager_connected:
                observed = summarize(snapshot, session.card, card_sysfs)
                # Two equal snapshots allow profile/routes/default policy to
                # settle without inventing a fixed successful profile or map.
                if observed == previous:
                    before = observed; break
                previous = observed
            time.sleep(.25)
        require(before is not None, "Owned card/policy did not stabilize; inspect private daemon/UCM evidence")
        session.command("before-wpctl-status", ["wpctl", "status", "--name"])
        for item in [before["device"], *before["nodes"]]:
            session.command(f"wpctl-inspect-{item['id']}", ["wpctl", "inspect", str(item["id"])])
        for node in before["nodes"]:
            session.command(f"wpctl-volume-{node['id']}", ["wpctl", "get-volume", str(node["id"])], required=False)
        session.command("alsa-controls-after-enumeration", ["amixer", "--card", str(session.card), "contents"])
        after = summarize(capture(session, process, manager, "after-observation"), session.card, card_sysfs)
        session.command("after-wpctl-status", ["wpctl", "status", "--name"])
        defaults = observe_defaults(before["defaultSink"], after["defaultSink"])
        session.result["observation"] = dict(before=before, after=after, defaultSink=defaults,
            settingsStable=before == after,
            limits=["Existing card, not hotplug", "Fresh private account without desktop services",
                    "WirePlumber may apply VM-specific rules", "Mixer initialization on owned card allowed by installed policy",
                    "No pipewire-pulse, playback, Wine, Proton or hardware test", "Raw SPA values do not prove effective PCM gain"])
        session.save()
        require(not defaults["changedDuringObservation"], "Private default changed during read-only observation")
        session.result["result"] = "observed"
        session.save()
    return session.result


def optional_result(result_path, expected_ready):
    """The guest and host both require cleanup evidence bound to this exact USB."""
    path = Path(result_path)
    require(path.is_file() and not path.is_symlink(), "PipeWire observation has no regular cleanup record")
    raw = bounded_bytes(path)
    result = json.loads(raw)
    require(result.get("scope") == "owned-private-pipewire-default-observer" and result.get("ready") == expected_ready,
            "PipeWire observation belongs to another scope or virtual-device contract")
    require(result.get("accountProcessesAndFilesRemoved") is True and not result.get("cleanupErrors"),
            "PipeWire observer cleanup was not verified; consumers were not started")
    require(result.get("result") in ("observed", "failed"), "Unknown PipeWire collection result")
    return dict(result=result["result"], cleanupVerified=True,
                resultSha256=hashlib.sha256(raw).hexdigest(), error=result.get("error"),
                gatesConsumerCompatibility=False, defaultDesktopStackValidated=False)


def run_optional(ready_path, evidence, physical_parent):
    """Collect independently; only a verified cleanup permits later consumers."""
    path = Path(evidence)
    require(not path.exists() and not path.is_symlink(), "Fresh PipeWire evidence directory required")
    ready = json.loads(bounded_bytes(Path(ready_path)))
    failure = None
    try:
        run(ready_path, path, physical_parent)
    except Exception as error:
        failure = f"{type(error).__name__}: {error}"
    summary = optional_result(path / "result.json", ready)
    require(failure is None or summary["result"] == "failed", "PipeWire failed without recording a failed collection")
    if failure is not None:
        summary["error"] = failure
    summary["evidenceDirectory"] = str(path)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready", required=True, type=Path)
    parser.add_argument("--physical-parent", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    return 0 if run(args.ready, args.output, args.physical_parent)["result"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
