#!/usr/bin/env python3
"""Opt-in private PipeWire rear-channel gain experiment. No production policy."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import secrets
import shutil
import signal
import stat
import struct
import subprocess
import time
import wave

from consumer_session import ConsumerSession, bounded_bytes, process_identity, require
from pipewire_defaults import (capture, default_sink, describe_nodes, object_serial,
                               objects, private_stack, props, select_device, volume_evidence)

CHANNELS = ["FL", "FR", "RL", "RR"]


def target_state(snapshot, card_number, card_sysfs):
    card = select_device(snapshot, card_number, card_sysfs)
    nodes = [node for node in describe_nodes(snapshot, card) if node["mediaClass"] == "Audio/Sink"]
    require(len(nodes) == 1, "Exactly one sink on the owned card is required")
    node = nodes[0]
    formats = node["parameters"].get("EnumFormat", [])
    require(any(row.get("format") == "S16LE" and row.get("rate") == 48000 and row.get("channels") == 4
                and row.get("position") == CHANNELS for row in formats), "Owned sink does not advertise the exact quad format")
    profile_device = node["properties"].get("card.profile.device")
    require(type(profile_device) is int and profile_device >= 0, "Missing exact sink profile-device index")
    parameters = card["info"]["params"]
    profiles = parameters.get("Profile", [])
    require(len(profiles) == 1, "Expected one active card profile")
    routes = [route for route in parameters.get("Route", [])
              if route.get("direction") == "Output" and route.get("device") == profile_device]
    require(len(routes) == 1, "The owned sink needs one exact active output route")
    route = routes[0]
    require(type(route.get("index")) is int and route["index"] >= 0 and route.get("save") is False,
            "Only an unsaved route in the fresh private session is supported")
    route_props = route.get("props", {})
    volumes = volume_evidence(route_props)
    require(volumes["quadMap"] is True and route_props.get("mute") is False,
            "Unmuted, explicit quad route volumes are required; no profile or mute substitution")
    node_volumes = [row["raw"] for row in node["volumes"] if row["quadMap"] is True]
    require(len(node_volumes) == 1 and node_volumes[0].get("mute") is False,
            "Expected one unmuted quad node volume object")
    for value in (route_props, node_volumes[0]):
        soft = value.get("softVolumes")
        require(isinstance(soft, list) and len(soft) == 4 and
                all(type(item) in (int, float) and math.isfinite(item) and item >= 0 for item in soft),
                "Raw and software volumes must both be explicit")
    identity = dict(cardId=card["id"], cardSerial=object_serial(card),
        cardSysfs=str(card_sysfs), cardNumber=card_number, nodeId=node["id"], nodeSerial=node["serial"],
        nodeName=node["name"], routeIndex=route["index"], routeDevice=profile_device)
    return dict(identity=identity, profiles=profiles, defaultSink=default_sink(snapshot),
                routes=parameters.get("Route", []), routeProps=route_props, nodeProps=node_volumes[0])


def same_target(current, original):
    require(current["identity"] == original["identity"] and current["profiles"] == original["profiles"]
            and current["defaultSink"] == original["defaultSink"],
            "Owned card/sink/route identity, profile or default changed; no write to replacement")


def route_request(state, volumes):
    require(len(volumes) == 4 and all(type(value) in (int, float) and math.isfinite(value)
                                    and 0 <= value <= 1 for value in volumes), "Invalid bounded channel-volume request")
    return dict(index=state["identity"]["routeIndex"], device=state["identity"]["routeDevice"],
                props=dict(channelVolumes=volumes), save=False)


def control_snapshot(text):
    """Keep all metadata; only supported writable control values can be restored."""
    chunks = re.split(r"(?=^numid=)", text, flags=re.MULTILINE)
    result = {}
    for chunk in chunks:
        if not chunk.strip():
            continue
        lines = chunk.rstrip().splitlines()
        match = re.fullmatch(r"numid=(\d+),.*", lines[0])
        require(match is not None and int(match[1]) not in result, "Ambiguous ALSA control inventory")
        values = [line for line in lines if line.startswith("  : values=")]
        definitions = [line for line in lines if line.startswith("  ; type=")]
        require(len(values) == len(definitions) == 1, "Unsupported ALSA control schema")
        access = re.search(r"access=([^,]+)", definitions[0])
        require(access is not None, "Missing ALSA access flags")
        writable = "w" in access[1]
        value = values[0].split("=", 1)[1]
        if writable:
            require(re.search(r"type=(BOOLEAN|INTEGER),", definitions[0]) is not None
                    and re.fullmatch(r"(?:on|off|-?\d+)(?:,(?:on|off|-?\d+)){0,31}", value),
                    "Only numeric/boolean controls are eligible for scoped restoration")
        result[int(match[1])] = dict(metadata=[line for line in lines if line != values[0]],
                                    values=value, writable=writable)
    require(result, "Empty ALSA control snapshot")
    return result


def restore_values(before, current):
    require(set(before) == set(current), "ALSA controls changed identity")
    changes = []
    for number, original in before.items():
        observed = current[number]
        require(original["metadata"] == observed["metadata"] and original["writable"] == observed["writable"],
                "ALSA control metadata changed; no restoration to a replacement")
        if original["values"] != observed["values"]:
            require(original["writable"], "Read-only fixture control changed during the experiment")
            changes.append((number, original["values"]))
    return changes


def marked_pcm(seed, frames=48000):
    rng = random.Random(seed)
    return b"".join(struct.pack("<hhhh", *(rng.choice((-1, 1)) * rng.randrange(1000, 8001)
                                          for _ in range(4))) for _ in range(frames))


def measure_pcm(reference, captured):
    """One unique sign anchor, then all marked frames; no per-window realignment."""
    require(len(reference) % 8 == len(captured) % 8 == 0 and len(reference) >= 512,
            "Complete quad PCM frames and a 64-frame marker are required")
    source = list(struct.iter_unpack("<hhhh", reference))
    observed = list(struct.iter_unpack("<hhhh", captured))
    mask = (1 << 64) - 1
    signature = 0
    for frame in source[:64]:
        signature = ((signature << 1) | (frame[0] > 0)) & mask
    rolling, nonzero, candidates = 0, 0, []
    for index, frame in enumerate(observed):
        rolling = ((rolling << 1) | (frame[0] > 0)) & mask
        nonzero = ((nonzero << 1) | (frame[0] != 0)) & mask
        if index >= 63 and nonzero == mask and rolling == signature:
            candidates.append(index - 63)
    require(len(candidates) == 1 and candidates[0] + len(source) <= len(observed),
            "Marked PCM has no unique complete alignment; do not fit gain to missing/reordered samples")
    start = candidates[0]
    actual = observed[start:start + len(source)]
    channels = []
    for channel in range(4):
        xx = sum(frame[channel] ** 2 for frame in source)
        xy = sum(a[channel] * b[channel] for a, b in zip(source, actual))
        gain = xy / xx
        residuals = [b[channel] - gain * a[channel] for a, b in zip(source, actual)]
        channels.append(dict(channel=CHANNELS[channel], fittedGain=gain,
            exactUnity=all(a[channel] == b[channel] for a, b in zip(source, actual)),
            maximumResidualLsb=max(abs(value) for value in residuals),
            residualRmsLsb=math.sqrt(sum(value * value for value in residuals) / len(source))))
    return dict(markerOffsetFrames=start, markedFrames=len(source), capturedFrames=len(observed),
                channels=channels, rearUnityPcmExact=all(row["exactUnity"] for row in channels[2:]),
                alignmentIsLatency=False, physicalGainValidated=False), b"".join(struct.pack("<hhhh", *row) for row in actual)


def stream_binding(snapshot, pid, uid, gid, target):
    # Native 1.0.5 exports process credentials on Client, not necessarily Node.
    # Use the protocol's peer credentials, then the explicit client.id edge.
    clients = [client for client in objects(snapshot, "Client") if props(client).get("pipewire.sec.pid") == pid]
    if not clients:
        return None
    require(len(clients) == 1, "Ambiguous owned playback client")
    client = clients[0]
    require(all(type(props(client).get(key)) is int and props(client)[key] == value for key, value in
                (("pipewire.sec.pid", pid), ("pipewire.sec.uid", uid), ("pipewire.sec.gid", gid))),
            "Playback client peer credentials differ from the owned child")
    streams = [node for node in objects(snapshot, "Node") if
               props(node).get("media.class") == "Stream/Output/Audio" and props(node).get("client.id") == client["id"]]
    if not streams:
        return None
    require(len(streams) == 1, "Ambiguous owned playback stream")
    stream = streams[0]
    ports = {port["id"]: props(port) for port in objects(snapshot, "Port")}
    links = [link["info"] for link in objects(snapshot, "Link")
             if link.get("info", {}).get("output-node-id") == stream["id"]]
    if len(links) < 4:
        return None
    channels = []
    for link in links:
        require(link.get("input-node-id") == target["identity"]["nodeId"], "Playback linked to a different sink")
        left, right = ports[link["output-port-id"]], ports[link["input-port-id"]]
        require(left.get("node.id") == stream["id"] and right.get("node.id") == target["identity"]["nodeId"],
                "Playback ports belong to another node")
        require(left.get("audio.channel") == right.get("audio.channel"), "Playback channel link was remapped")
        channels.append(left.get("audio.channel"))
    require(len(channels) == 4 and set(channels) == set(CHANNELS), "Playback needs exactly four attributed channel links")
    if any(link.get("state") != "active" for link in links):
        return None
    return dict(clientId=client["id"], clientSerial=object_serial(client),
                streamId=stream["id"], streamSerial=object_serial(stream),
                processId=pid, uid=uid, gid=gid, links=links)


def stop_private_processes(session):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid, identity in session.owned_processes():
            if process_identity(pid) == identity:
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 2
        while session.owned_processes() and time.monotonic() < deadline:
            for child in session.processes:
                child.poll()
            time.sleep(.05)
    require(not session.owned_processes(), "Private audio processes remain; ALSA restoration would race policy")


def run(ready_path, output, physical_parent, *, adjust_owned_rear_unity=False):
    require(adjust_owned_rear_unity, "Explicit --adjust-owned-rear-unity opt-in required")
    session = ConsumerSession(ready_path, output, physical_parent)
    original = applied = pre_controls = None
    process = manager = None
    mutation_attempted = False
    graph_restored = hardware_restored = False
    try:
        with session:
            session.result.update(scope="owned-private-pipewire-gain-experiment", result="failed", phases=[],
                                  productionPolicyModified=False, defaultDesktopStackValidated=False,
                                  physicalGainValidated=False, graphMutationAttempted=False)
            for program in ("pw-play", "pw-cli", "pipewire", "wireplumber", "amixer"):
                require(shutil.which(program), f"Missing guest prerequisite: {program}")
            cards = list(Path("/sys/class/sound").glob("card[0-9]*"))
            require(session.card is not None and len(cards) == 1 and cards[0].name == f"card{session.card}",
                    "Only the exact owned virtual ALSA card may exist")
            require(not list(Path("/sys/class/bluetooth").glob("hci*")) and not list(Path("/sys/class/video4linux").glob("video*")),
                    "Physical radio/camera devices are outside this experiment")
            card_sysfs = cards[0].resolve(strict=True)
            require(card_sysfs.is_relative_to(session.parent), "ALSA card moved outside owned USB")
            card_identity = (card_sysfs.stat().st_dev, card_sysfs.stat().st_ino)
            control = Path("/dev/snd", f"controlC{session.card}")
            control_info = control.lstat()
            require(stat.S_ISCHR(control_info.st_mode), "ALSA control must be a direct character node")
            control_identity = (control_info.st_dev, control_info.st_ino, control_info.st_rdev)
            sequence = 0

            def check_card():
                session.check_device()
                current_card = Path("/sys/class/sound", f"card{session.card}").resolve(strict=True)
                info = control.lstat()
                require(current_card == card_sysfs and (current_card.stat().st_dev, current_card.stat().st_ino) == card_identity
                        and stat.S_ISCHR(info.st_mode) and (info.st_dev, info.st_ino, info.st_rdev) == control_identity,
                        "ALSA card or control-node identity changed; no operation on a replacement")

            def controls(label):
                check_card()
                _, data = session.command(label, ["amixer", "--card", str(session.card), "contents"])
                return control_snapshot(data.decode())

            def snapshot(label):
                nonlocal sequence
                check_card()
                sequence += 1
                raw = capture(session, process, manager, f"{sequence:03}-{label}")
                return raw, target_state(raw, session.card, card_sysfs)

            def wait_state(label, predicate):
                for _ in range(30):
                    raw, state = snapshot(label)
                    if predicate(state):
                        return raw, state
                    time.sleep(.1)
                raise RuntimeError("Private PipeWire state did not confirm " + label)

            def set_route(label, state, volumes):
                _, current = snapshot(label + "-before")
                same_target(current, state)
                request = route_request(state, volumes)
                session.result.setdefault("routeRequests", []).append(dict(label=label, request=request))
                session.save()
                session.command(label, ["pw-cli", "--remote", session.env["PIPEWIRE_REMOTE"], "set-param",
                    str(state["identity"]["cardId"]), "Route", json.dumps(request)])

            def play(label, state, source_path, marker):
                session.check_device()
                capture_path = Path(session.ready["coreOutputDirectory"]) / "audio-ingress.pcm"
                before_size = capture_path.stat().st_size
                require(before_size % 8 == 0 and before_size <= 16 * 1024 * 1024, "Invalid core PCM capture offset")
                player = session.start(label, ["pw-play", "--remote", session.env["PIPEWIRE_REMOTE"],
                    "--target", str(state["identity"]["nodeSerial"]), "--channel-map", ",".join(CHANNELS), str(source_path)])
                started = time.monotonic_ns()
                binding = None
                failure = None
                playback = dict(label=label, processId=player.pid, startedMonotonicNs=started,
                                captureOffsetBytes=before_size)
                session.result.setdefault("playbacks", []).append(playback)
                deadline = time.monotonic() + 15
                try:
                    player_identity = process_identity(player.pid)
                    require(player_identity is not None and player_identity[0] == (session.uid,) * 4,
                            "Playback child identity is unavailable or outside the owned account")
                    playback["processStart"] = player_identity[1]; session.save()
                    while player.poll() is None:
                        require(time.monotonic() < deadline, "Owned PCM playback exceeded deadline")
                        require(process_identity(player.pid) == player_identity, "Playback child identity changed")
                        raw, current = snapshot(label)
                        same_target(current, state)
                        require(current == state, "Recorded route/node volumes changed during marked playback")
                        # Do not attribute a dump after reaping this child; its
                        # PID could then refer to an unrelated new process.
                        if player.poll() is not None:
                            break
                        require(process_identity(player.pid) == player_identity, "Playback child identity changed during observation")
                        binding = stream_binding(raw, player.pid, session.uid, session.gid, state) or binding
                        time.sleep(.05)
                except Exception as error:
                    failure = error
                finally:
                    # Finish this exact child before any route rollback, even
                    # when link/identity evidence fails during playback.
                    if player.poll() is None:
                        player.terminate()
                        try:
                            player.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            player.kill(); player.wait(timeout=2)
                    playback.update(exitCode=player.returncode, binding=binding, endedMonotonicNs=time.monotonic_ns(),
                                    error=str(failure) if failure is not None else None)
                    session.save()
                last = None
                for _ in range(20):
                    size = capture_path.stat().st_size
                    if size == last:
                        break
                    last = size; time.sleep(.05)
                data = bounded_bytes(capture_path, 16 * 1024 * 1024)[before_size:]
                (session.output / (label + ".pcm")).write_bytes(data)
                playback["capturedSha256"] = hashlib.sha256(data).hexdigest(); session.save()
                if failure is not None:
                    raise failure
                require(player.returncode == 0 and binding is not None,
                        "Playback failed or its exact four links were not observed; inspect playback PID/exit and retained PCM")
                measurement, aligned = measure_pcm(marker, data)
                (session.output / (label + "-aligned.pcm")).write_bytes(aligned)
                phase = dict(label=label, startedMonotonicNs=started, endedMonotonicNs=time.monotonic_ns(),
                             binding=binding, state=state, measurement=measurement,
                             capturedSha256=hashlib.sha256(data).hexdigest())
                session.result["phases"].append(phase); session.save()
                return aligned

            try:
                pre_controls = controls("alsa-before-private-stack")
                process, manager = private_stack(session)
                daemon = session.result["audioDaemon"]
                daemon["forcedVolumeAtStartup"] = daemon.pop("forcedVolume")
                # Capture may see no ALSA objects while policy starts. Keep that
                # evidence and retry only until the device has appeared.
                prior = None
                for _ in range(30):
                    raw = capture(session, process, manager, f"startup-{sequence:03}"); sequence += 1
                    try:
                        require(any(str(props(client).get("application.process.id")) == str(manager.pid)
                                    for client in objects(raw, "Client")), "Owned WirePlumber has not connected")
                        current = target_state(raw, session.card, card_sysfs)
                        if current == prior:
                            original = current; break
                        prior = current
                    except RuntimeError as error:
                        # No mutation is possible before a complete stable
                        # target exists; preserve incomplete/invalid dumps too.
                        session.result["lastStartupObservationError"] = str(error)
                        prior = None
                    time.sleep(.1)
                require(original is not None, "Owned card policy did not stabilize")
                route_request(original, original["routeProps"]["channelVolumes"])
                session.result["original"] = original
                session.result["alsaBeforeAdjustment"] = controls("alsa-before-adjustment")
                marker = marked_pcm(secrets.randbits(64))
                source_path = session.output / "gain-source.wav"
                with wave.open(str(source_path), "wb") as stream:
                    stream.setnchannels(4); stream.setsampwidth(2); stream.setframerate(48000)
                    stream.writeframes(bytes(48000 * 8) + marker + bytes(24000 * 8))
                session.result["sourceSha256"] = hashlib.sha256(source_path.read_bytes()).hexdigest()
                staged = session.stage(source_path)
                baseline_pcm = play("baseline", original, staged, marker)
                wanted = list(original["routeProps"]["channelVolumes"]); wanted[2:] = [1.0, 1.0]
                mutation_attempted = True
                session.result["graphMutationAttempted"] = True; session.save()
                set_route("set-owned-rear-unity", original, wanted)
                _, applied = wait_state("adjustment", lambda state: state["routeProps"]["channelVolumes"] == wanted)
                same_target(applied, original)
                session.result["applied"] = applied
                session.result["volumeAdjustmentPerformed"] = True
                session.result["alsaAfterAdjustment"] = controls("alsa-after-adjustment")
                play("adjusted", applied, staged, marker)
                _, before_restore = snapshot("restore-preflight")
                same_target(before_restore, original)
                require(before_restore == applied, "Policy state changed outside the recorded adjustment; no blind graph restore")
                set_route("restore-owned-route", original, original["routeProps"]["channelVolumes"])
                _, restored = wait_state("restore", lambda state: state == original)
                graph_restored = True
                restored_pcm = play("restored", restored, staged, marker)
                _, final_state = snapshot("restored-final")
                graph_restored = final_state == original
                require(graph_restored, "Restored graph changed during the final PCM measurement")
                session.result.update(result="measured", liveGraphRestored=True,
                    restoredMarkedPcmEqualsBaseline=restored_pcm == baseline_pcm,
                    rearUnityPcmExact=session.result["phases"][1]["measurement"]["rearUnityPcmExact"])
            finally:
                # A failed write/measurement still restores only the attributed
                # route. Stop private policy before returning ALSA to pre-session
                # state; ConsumerSession then removes this UID and device ACLs.
                restore_errors = []
                if mutation_attempted and not graph_restored and original is not None:
                    try:
                        _, observed = snapshot("failure-restore-preflight")
                        same_target(observed, original)
                        require(observed["routeProps"]["channelVolumes"] in
                                (original["routeProps"]["channelVolumes"], wanted), "Unexpected route volume during restore")
                        set_route("failure-restore-owned-route", original, original["routeProps"]["channelVolumes"])
                        wait_state("failure-restore", lambda state: state == original)
                        graph_restored = True
                    except Exception as error:
                        restore_errors.append("graph: " + str(error))
                try:
                    stop_private_processes(session)
                    if pre_controls is not None:
                        current = controls("alsa-before-final-restore")
                        for number, values in restore_values(pre_controls, current):
                            check_card()
                            session.command(f"restore-alsa-{number}", ["amixer", "--card", str(session.card),
                                            "cset", f"numid={number}", values])
                        require(controls("alsa-restored") == pre_controls, "ALSA state did not return to the exact pre-session snapshot")
                        hardware_restored = True
                except Exception as error:
                    restore_errors.append("ALSA/processes: " + str(error))
                session.result.update(liveGraphRestored=graph_restored if mutation_attempted else None,
                    alsaPreSessionRestored=hardware_restored, stateRestorationErrors=restore_errors)
                session.save()
                require(not restore_errors, "Private gain experiment state restoration was not verified")
    except Exception as error:
        session.result.update(result="failed", error=f"{type(error).__name__}: {error}")
    finally:
        session.result["experimentCleanupVerified"] = (hardware_restored and
            (not mutation_attempted or graph_restored) and session.result.get("accountProcessesAndFilesRemoved") is True
            and not session.result.get("cleanupErrors") and not session.result.get("stateRestorationErrors"))
        if session.output.is_dir():
            session.save()
    return session.result


def optional_result(result_path, expected_ready):
    """Verify this independent experiment before any later consumer may start."""
    path = Path(result_path)
    require(path.is_file() and not path.is_symlink(), "Gain experiment has no regular cleanup record")
    raw = bounded_bytes(path)
    result = json.loads(raw)
    require(result.get("scope") == "owned-private-pipewire-gain-experiment" and result.get("ready") == expected_ready,
            "Gain experiment belongs to another scope or virtual-device contract")
    mutated = result.get("graphMutationAttempted")
    require(type(mutated) is bool and result.get("experimentCleanupVerified") is True
            and result.get("accountProcessesAndFilesRemoved") is True and not result.get("cleanupErrors")
            and not result.get("stateRestorationErrors") and result.get("alsaPreSessionRestored") is True
            and (not mutated or result.get("liveGraphRestored") is True),
            "Gain experiment state/ACL/account cleanup was not verified; consumers were not started")
    require(result.get("result") in ("measured", "failed"), "Unknown gain experiment collection result")
    if result["result"] == "measured":
        require(mutated and [phase.get("label") for phase in result.get("phases", [])] ==
                ["baseline", "adjusted", "restored"] and type(result.get("rearUnityPcmExact")) is bool
                and type(result.get("restoredMarkedPcmEqualsBaseline")) is bool,
                "Measured gain experiment lacks all three PCM phases")
    return dict(result=result["result"], cleanupVerified=True, resultSha256=hashlib.sha256(raw).hexdigest(),
                rearUnityPcmExact=result.get("rearUnityPcmExact"),
                restoredMarkedPcmEqualsBaseline=result.get("restoredMarkedPcmEqualsBaseline"),
                error=result.get("error"), gatesConsumerCompatibility=False,
                productionPolicyModified=False, physicalGainValidated=False)


def run_optional(ready_path, evidence, physical_parent):
    path = Path(evidence)
    require(not path.exists() and not path.is_symlink(), "Fresh gain experiment evidence directory required")
    ready = json.loads(bounded_bytes(Path(ready_path)))
    run(ready_path, path, physical_parent, adjust_owned_rear_unity=True)
    summary = optional_result(path / "result.json", ready)
    summary["evidenceDirectory"] = str(path)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--physical-parent", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--adjust-owned-rear-unity", action="store_true")
    args = parser.parse_args()
    result = run(args.ready, args.output, args.physical_parent, adjust_owned_rear_unity=args.adjust_owned_rear_unity)
    print(json.dumps({key: result.get(key) for key in ("result", "rearUnityPcmExact", "experimentCleanupVerified", "error")}))
    raise SystemExit(0 if result.get("result") == "measured" and result["experimentCleanupVerified"] else 1)
