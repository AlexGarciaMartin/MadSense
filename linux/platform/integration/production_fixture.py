"""Synthetic protocol fixtures for the production C# core experiment.

No physical device is opened. The descriptor is deliberately a generic HID
gamepad carrying complete DualSense-sized reports, not a copied hardware descriptor.
"""
import argparse
import binascii
import errno
import json
import os
from pathlib import Path
import select
import struct
import threading
import time


def descriptor():
    # Report 31: one flags byte, four gamepad axes, 72 opaque bytes. Having
    # real input axes creates evdev children so the real broker must grab them.
    data = bytearray.fromhex(
        "05010905a10185310600ff0901750895018103"
        "05010930093109320935150026ff00750895048102"
        "0600ff0901750895488103")
    for report, size in ((0x31, 78), (0x32, 142), (0x35, 334), (0x36, 398)):
        data += bytes([0x85, report, 0x09, report, 0x75, 8, 0x96]) + struct.pack("<H", size - 1) + b"\x91\x02"
    for report, size in ((0x05, 41), (0x09, 20), (0x20, 64)):
        data += bytes([0x85, report, 0x09, report, 0x75, 8, 0x95, size - 1, 0xB1, 2])
    return bytes(data + b"\xc0")


def input_report(marker=False, sequence=0, south_only=False):
    data = bytearray(78)
    data[0:2] = b"\x31\x01"
    data[2:6] = bytes([17, 229, 63, 201] if marker else [128] * 4)
    data[6:8] = bytes([91, 173] if marker else [0, 0])
    data[8] = sequence & 255
    data[9] = 0x28 if marker or south_only else 8  # Cross, neutral dpad.
    struct.pack_into("<hhhhhhI", data, 17, -1234 if marker else 0, 2345 if marker else 0,
                     -3456 if marker else 0, 0, 0, 8192, sequence * 3000)
    data[34] = data[38] = 0x80  # Both touch points inactive.
    data[54] = 5  # Discharging; deterministic battery field.
    struct.pack_into("<I", data, 74, binascii.crc32(b"\xa1" + data[:74]))
    return bytes(data)


def features():
    firmware = bytearray(64)
    firmware[0] = 0x20
    firmware[1:12] = b"Nov 03 2026"
    firmware[12:20] = b"12:00:00"
    struct.pack_into("<I", firmware, 24, 0x00000300)
    struct.pack_into("<H", firmware, 44, 0x0224)
    pairing = bytearray(20)
    pairing[0] = 9
    pairing[1:7] = bytes.fromhex("44332211d502")
    pairing[10:16] = bytes.fromhex("aabbccddd502")
    calibration = bytearray(41)
    calibration[0] = 5
    # Synthetic symmetric calibration, nonzero denominators. This is delivered
    # through the real feature RPC, not applied to the physical parser by it.
    struct.pack_into("<hhh", calibration, 1, 0, 0, 0)
    struct.pack_into("<hhhhhh", calibration, 7, 16000, -16000, 16000, -16000, 16000, -16000)
    struct.pack_into("<hh", calibration, 19, 1024, 1024)
    struct.pack_into("<hhhhhh", calibration, 23, 8192, -8192, 8192, -8192, 8192, -8192)
    return {0x20: bytes(firmware), 9: bytes(pairing), 5: bytes(calibration)}


def manifest():
    inputs = {}
    for name, marker in (("neutral", False), ("marker", True), ("south", False)):
        report = input_report(marker, south_only=name == "south")
        inputs[name] = {"bluetooth": report.hex(), "usb": (b"\x01" + report[2:65]).hex()}
    return {"schema": 1, "scope": "synthetic-protocol-only", "descriptor": descriptor().hex(),
            "input": inputs, "features": {f"{key:02x}": value.hex() for key, value in features().items()}}


class SyntheticDualSense:
    """Use only after the root harness validates its owned QEMU guest.

    The single I/O thread records exact UHID output and answers GET_REPORT.
    Input injection defaults to 100 Hz: TCG evidence is not a throughput claim.
    """
    def __init__(self, label, output, edge=False):
        self.label, self.output = label, Path(output)
        self.fd = os.open("/dev/uhid", os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        self.gate = threading.Lock()
        self.running, self.marker, self.south_only, self.input_enabled = True, False, False, False
        self.error, self.outputs, self.feature_requests = None, [], []
        self.trace = (self.output / "uhid-events.jsonl").open("x", buffering=1)
        rd = descriptor()
        product = 0x0DF2 if edge else 0x0CE6
        payload = (label.encode().ljust(128, b"\0") + b"madsense-core-ci".ljust(64, b"\0")
                   + label.encode().ljust(64, b"\0") + struct.pack("=HHIIII", len(rd), 5, 0x054C, product, 1, 0)
                   + rd.ljust(4096, b"\0"))
        self.send(11, payload)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for candidate in Path("/sys/bus/hid/devices").glob(f"0005:054C:{product:04X}.*"):
                if f"HID_UNIQ={label}" in (candidate / "uevent").read_text().splitlines():
                    self.parent = candidate.resolve()
                    raw = [p for p in Path("/sys/class/hidraw").glob("hidraw*")
                           if (p / "device").resolve().is_relative_to(self.parent)]
                    events = [p for p in Path("/sys/class/input").glob("event*")
                              if (p / "device").resolve().is_relative_to(self.parent)]
                    if len(raw) == 1 and events:
                        if (self.parent / "driver").resolve().name != "hid-generic":
                            self.close()
                            raise RuntimeError("Synthetic fixture must bind generic HID, not hardware firmware driver")
                        self.hidraw = Path("/dev", raw[0].name)
                        self.events = [Path("/dev/input", p.name) for p in events]
                        return
            if self.error:
                self.close()
                raise self.error
            time.sleep(.05)
        self.close()
        raise TimeoutError("UHID generic gamepad nodes")

    def send(self, kind, payload=b""):
        data = struct.pack("=I", kind) + payload
        with self.gate:
            if os.write(self.fd, data) != len(data):
                raise IOError("Short UHID write")

    def record(self, kind, **value):
        self.trace.write(json.dumps({"kind": kind, "monotonicNs": time.monotonic_ns(), **value}) + "\n")

    def run(self):
        sequence, next_input = 0, time.monotonic()
        try:
            while self.running:
                if select.select([self.fd], [], [], .002)[0]:
                    packet = os.read(self.fd, 4376).ljust(4376, b"\0")
                    kind = struct.unpack_from("=I", packet)[0]
                    if kind == 6:
                        length = struct.unpack_from("=H", packet, 4100)[0]
                        data = packet[4:4 + length]
                        self.outputs.append(data)
                        self.record("output", hex=data.hex())
                    elif kind == 9:
                        request, report, report_type = struct.unpack_from("=IBB", packet, 4)
                        data = features().get(report, b"") if report_type == 0 else b""
                        self.feature_requests.append(report)
                        self.record("get-feature", report=report, reportType=report_type, hex=data.hex())
                        self.send(10, struct.pack("=IHH", request, 0 if data else errno.EIO, len(data)) + data)
                    elif kind == 13:
                        request = struct.unpack_from("=I", packet, 4)[0]
                        self.send(14, struct.pack("=IH", request, errno.EOPNOTSUPP))
                now = time.monotonic()
                if self.input_enabled and now >= next_input:
                    data = input_report(self.marker, sequence, self.south_only)
                    self.send(12, struct.pack("=H", len(data)) + data)
                    self.record("input", marker=self.marker, southOnly=self.south_only, sequence=sequence, hex=data.hex())
                    sequence = (sequence + 1) & 0xfffff
                    next_input = now + .01
        except BaseException as error:
            self.error = error

    def close(self):
        if self.running:
            self.running = False
            self.thread.join(timeout=2)
            try:
                self.send(1)
            finally:
                os.close(self.fd)
                self.trace.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-manifest", type=Path, required=True)
    args = parser.parse_args()
    args.write_manifest.write_text(json.dumps(manifest(), indent=2) + "\n")
