#!/usr/bin/env python3
"""Owned neutral-device test on a disposable GitHub-hosted Linux kernel.

No USB export/bind, physical hidraw, Bluetooth API, game or physical latency test.
Only nodes below the USB parent created by our loopback import may be opened.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time
from ci_environment import validate_environment


def wait_for(callback, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = callback()
        if result:
            return result
        time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for {callback.__name__}")


def ports():
    result = {}
    for path in sorted(Path("/sys/devices/platform").glob("vhci_hcd.*/status*")):
        for line in path.read_text().splitlines()[1:]:
            row = line.split()
            if len(row) < 7 or row[0] not in ("hs", "ss"):
                raise RuntimeError(f"Unexpected vhci status row: {line}")
            port = int(row[1])
            result[port] = dict(hub=row[0], state=int(row[2]), speed=int(row[3]),
                                devid=int(row[4], 16), localBusId=row[6])
    if not result:
        raise RuntimeError("No readable vhci_hcd ports; kernel module is required")
    return result


def owned_nodes(root, subsystem, pattern):
    nodes = []
    for path in Path("/sys/class", subsystem).glob(pattern):
        if (path / "device").resolve().is_relative_to(root.resolve()):
            nodes.append(path)
    return sorted(nodes)


def sysfs_bitmap(text, word_bits=None):
    """input_print_bitmap uses unpadded native unsigned-long words, high first.

    Linux's compat reader uses 32-bit words; native Python's C unsigned-long
    size therefore matches the syscall view. Concatenating strings loses zero
    words and changes button indices (notably BTN_SOUTH at bit 304).
    """
    if word_bits is None:
        word_bits = struct.calcsize("L") * 8
    if word_bits not in (32, 64):
        raise ValueError("Unsupported sysfs bitmap word width")
    words = text.split()
    if not words:
        raise ValueError("Empty sysfs capability bitmap")
    result = 0
    for word in words:
        value = int(word, 16)
        if value < 0 or value >= (1 << word_bits):
            raise ValueError("Capability word exceeds its native width")
        result = (result << word_bits) | value
    return result


def device_inventory(usb):
    result = {"usbParent": str(usb), "nodes": []}
    for subsystem, pattern in (("input", "event*"), ("hidraw", "hidraw*"), ("sound", "card[0-9]*")):
        for node in owned_nodes(usb, subsystem, pattern):
            record = {"subsystem": subsystem, "name": node.name,
                      "device": str((node / "device").resolve())}
            for relative in ("device/name", "device/uevent", "device/capabilities/key", "device/capabilities/abs"):
                path = node / relative
                if path.is_file():
                    record[relative] = path.read_text().strip()
            result["nodes"].append(record)
    return result


def contains_exact(captured, expected, frame_bytes, allow_silence):
    if not expected or len(captured) % frame_bytes or len(expected) % frame_bytes:
        raise AssertionError("PCM is empty or not frame aligned")
    offset = captured.find(expected)
    if offset < 0 or offset % frame_bytes:
        raise AssertionError("Expected PCM sequence was lost, reordered, padded or changed")
    if allow_silence and (any(captured[:offset]) or any(captured[offset + len(expected):])):
        raise AssertionError("Unexpected nonzero PCM outside the expected stream")
    return dict(offsetFrames=offset // frame_bytes, expectedFrames=len(expected) // frame_bytes,
                capturedFrames=len(captured) // frame_bytes, sha256=hashlib.sha256(expected).hexdigest())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--persona", choices=("dualsense", "edge", "xbox"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", type=Path, required=True, help="Published managed probe DLL")
    parser.add_argument("--host-probe", type=Path, required=True)
    parser.add_argument("--usbip", type=Path, required=True)
    parser.add_argument("--dotnet", default="dotnet")
    parser.add_argument("--self-contained", action="store_true", help="--probe is a self-contained executable")
    parser.add_argument("--owned-guest-token")
    args = parser.parse_args()
    environment = validate_environment(args.owned_guest_token)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError("Use a fresh evidence directory")
    result = dict(schema=1, persona=args.persona, result="failed", physicalDeviceAccess=False,
                  radioInitialized=False, gamesValidated=False, physicalLatencyValidated=False)
    result["environment"] = environment
    def save(name, data):
        (output / name).write_text(json.dumps(data, indent=2) + "\n")
    def command(label, argv, timeout=30, required=True):
        completed = subprocess.run([str(x) for x in argv], stdin=subprocess.DEVNULL,
                                   capture_output=True, timeout=timeout)
        (output / f"{label}.stdout").write_bytes(completed.stdout)
        (output / f"{label}.stderr").write_bytes(completed.stderr)
        if required and completed.returncode:
            raise RuntimeError(f"{label} failed with exit {completed.returncode}")
        return completed
    owned_port = None
    owned_bus = None
    usb = None
    process = None
    try:
        command("uname", ["uname", "-a"])
        command("usbip-version", [args.usbip, "version"])
        for module in ("vhci_hcd", "hid_playstation", "snd_usb_audio", "xpad"):
            command(f"module-{module}", ["modinfo", module])
        before = ports()
        save("ports-before.json", before)
        # A clean hosted VM makes attribution unambiguous; leave other imports alone.
        if any(row["localBusId"] != "0-0" or row["devid"] for row in before.values()):
            raise RuntimeError("Pre-existing USB/IP import; refusing this isolated experiment")
        probe_output = output / "server"
        stdout = (output / "server.stdout").open("wb")
        stderr = (output / "server.stderr").open("wb")
        exporter = ([str(args.probe.resolve())] if args.self_contained
                    else [args.dotnet, str(args.probe.resolve())])
        process = subprocess.Popen([*exporter, "--persona", args.persona,
                                    "--output", str(probe_output), "--seconds", "180"],
                                   stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=True)
        def ready_file():
            if process.poll() is not None:
                raise RuntimeError("Managed exporter exited before readiness")
            path = probe_output / "ready.json"
            return json.loads(path.read_text()) if path.exists() else None
        ready = wait_for(ready_file)
        if (ready["physicalDeviceAccess"] is not False or ready["radioInitialized"] is not False
                or ready["busId"] != "1-1" or ready["packedIsoIn"] is not True):
            raise RuntimeError("Unexpected probe scope")
        command("devlist", [args.usbip, "--tcp-port", ready["port"], "list", "--remote", "127.0.0.1"])
        # Remember a newly claimed port even if enumeration later fails, so cleanup is bounded.
        attach = command("attach", ["sudo", "-n", args.usbip, "--tcp-port", ready["port"],
                                    "attach", "--remote", "127.0.0.1", "--busid", ready["busId"]], required=False)
        def imported_port():
            rows = ports()
            candidates = [(port, row) for port, row in rows.items()
                          if row != before.get(port) and row["devid"] == 0x10001 and row["localBusId"] != "0-0"]
            if len(candidates) > 1:
                raise RuntimeError("Multiple new imports; ownership is ambiguous")
            return candidates[0] if candidates else None
        owned_port, row = wait_for(imported_port)
        owned_bus = row["localBusId"]
        save("owned-port.json", {"port": owned_port, **row})
        if attach.returncode:
            raise RuntimeError("usbip attach failed despite creating a port")
        usb = Path("/sys/bus/usb/devices", owned_bus).resolve()
        def own_identity():
            try:
                return ((usb / "serial").read_text().strip() == ready["serial"]
                        and int((usb / "idVendor").read_text(), 16) == ready["vendor"]
                        and int((usb / "idProduct").read_text(), 16) == ready["product"])
            except FileNotFoundError:
                return False
        wait_for(own_identity)
        actual_descriptors = (usb / "descriptors").read_bytes()
        expected_descriptors = ((probe_output / "device-descriptor.bin").read_bytes()
                                + (probe_output / "configuration-descriptor.bin").read_bytes())
        (output / "kernel-usb-descriptors.bin").write_bytes(actual_descriptors)
        if actual_descriptors != expected_descriptors:
            raise AssertionError("Kernel USB device/configuration descriptors differ from exported persona")
        result["usbDescriptorsExact"] = True
        def primary_event():
            for path in owned_nodes(usb, "input", "event*"):
                text = (path / "device/capabilities/key").read_text()
                if (sysfs_bitmap(text) >> 304) & 1:  # BTN_SOUTH
                    return path
            return None
        event = wait_for(primary_event)
        command("evdev", ["sudo", "-n", args.host_probe.resolve(), "evdev", Path("/dev/input", event.name), output / "evdev.json"])
        result["evdevNeutral"] = True
        if args.persona != "xbox":
            def own_hidraw():
                nodes = owned_nodes(usb, "hidraw", "hidraw*")
                if len(nodes) > 1:
                    raise AssertionError("Unexpected multiple owned HID interfaces")
                return nodes[0] if nodes else None
            hidraw = wait_for(own_hidraw)
            driver = (hidraw / "device/driver").resolve().name
            if driver != "playstation":
                raise AssertionError(f"Expected hid-playstation binding, got {driver}")
            command("hid", ["sudo", "-n", args.host_probe.resolve(), "hid", Path("/dev", hidraw.name),
                            probe_output / "hid-descriptor.bin", output / "hid.json"])
            hid = json.loads((output / "hid.json").read_text())
            for report_id, expected in ready["features"].items():
                if hid["features"][report_id]["hex"] != expected:
                    raise AssertionError(f"Kernel feature {report_id} differs from expected persona bytes")
            result["hidDescriptorFeaturesInput"] = True
            def marker_output():
                path = probe_output / "events.jsonl"
                # The writer flushes complete JSONL records; a concurrent final
                # partial line is not yet evidence and will be retried.
                for line in path.read_text().split("\n")[:-1]:
                    row = json.loads(line)
                    report = row["value"]
                    if row["kind"] == "hid-output" and report.get("lightbarColorAllowed") and (
                            report.get("red"), report.get("green"), report.get("blue")) == (0x12, 0x34, 0x56):
                        return True
                return False
            wait_for(marker_output)
            result["hidOutputRoundTrip"] = True
            def own_card():
                cards = owned_nodes(usb, "sound", "card[0-9]*")
                if len(cards) > 1:
                    raise AssertionError("Unexpected multiple audio cards under test USB parent")
                return cards[0] if cards else None
            card = wait_for(own_card)
            number = int(card.name.removeprefix("card"))
            pcm = f"hw:{number},0"
            save("kernel-bindings.json", {"usbParent": str(usb), "hidDriver": driver,
                 "hidraw": hidraw.name, "event": event.name, "alsaCard": number})
            frames = 48000
            expected = b"".join(struct.pack("<hhhh", 1000 + i % 997, -2000 - i % 991,
                                             4000 + i % 983, -6000 - i % 977) for i in range(frames))
            expected_haptics = b"".join(expected[i + 4:i + 8] for i in range(0, len(expected), 8))
            (output / "playback-source.pcm").write_bytes(expected)
            command("alsa-playback", ["sudo", "-n", "aplay", "--dump-hw-params", "-D", pcm,
                    "-t", "raw", "-f", "S16_LE", "-r", "48000", "-c", "4", output / "playback-source.pcm"], timeout=30)
            time.sleep(0.25)
            result["audioFourChannels"] = contains_exact((probe_output / "audio-ingress.pcm").read_bytes(), expected, 8, True)
            result["hapticsRearChannels"] = contains_exact((probe_output / "haptics-ingress.pcm").read_bytes(), expected_haptics, 4, True)
            command("alsa-capture", ["sudo", "-n", "arecord", "--dump-hw-params", "-D", pcm,
                    "-t", "raw", "-f", "S16_LE", "-r", "48000", "-c", "2", "-d", "1", output / "microphone-received.pcm"], timeout=30)
            recorded = (output / "microphone-received.pcm").read_bytes()
            if len(recorded) != 48000 * 4:
                raise AssertionError("Kernel ISO IN capture returned an unexpected frame count")
            emitted = (probe_output / "microphone-source.pcm").read_bytes()
            result["syntheticIsoInExact"] = contains_exact(emitted, recorded, 4, False)
            result["syntheticIsoInExact"]["provesPhysicalMicrophone"] = False
        else:
            save("kernel-bindings.json", {"usbParent": str(usb), "event": event.name})
        if json.loads((probe_output / "capture.json").read_text())["overflow"]:
            raise AssertionError("Probe evidence exceeded bounded capture limits")
        result["result"] = "passed"
    except BaseException as error:
        result["reason"] = f"{type(error).__name__}: {error}"
    finally:
        if usb is not None:
            try:
                if (usb / "serial").read_text().strip() == ready["serial"]:
                    save("device-inventory.json", device_inventory(usb))
            except (OSError, ValueError) as error:
                result["inventoryError"] = str(error)
        if owned_port is not None:
            try:
                current = ports().get(owned_port)
                if current and current["devid"] == 0x10001 and current["localBusId"] == owned_bus:
                    command("detach", ["sudo", "-n", args.usbip, "detach", "--port", owned_port])
                    wait_for(lambda: ports()[owned_port]["localBusId"] == "0-0")
                    result["ownedPortDetached"] = True
                elif current and current["localBusId"] == "0-0":
                    result["ownedPortDetached"] = True
                else:
                    raise RuntimeError("Owned port changed identity; no detach sent")
            except BaseException as error:
                result["cleanupError"] = str(error)
                result["result"] = "failed"
        if process is not None:
            try:
                (output / "server/stop").touch()
                process.wait(timeout=10)
            except (subprocess.TimeoutExpired, OSError):
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=3)
            stdout.close()
            stderr.close()
            result["serverExit"] = process.returncode
            if process.returncode != 0:
                result["result"] = "failed"
        try:
            save("ports-after.json", ports())
            log = subprocess.run(["sudo", "-n", "dmesg", "--color=never"], capture_output=True, text=True, timeout=10)
            relevant = [line for line in log.stdout.splitlines() if (owned_bus and f"usb {owned_bus}:" in line)
                        or any(token in line for token in ("vhci_hcd", "usbip", "playstation", "snd_usb_audio"))]
            (output / "kernel-relevant.log").write_text("\n".join(relevant) + "\n")
        except BaseException as error:
            result["diagnosticsError"] = str(error)
        save("result.json", result)
        print(json.dumps(result), flush=True)
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
