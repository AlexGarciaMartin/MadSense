"""Independent wire assertions for the owned synthetic core/app experiments."""
import binascii
from pathlib import Path
import os
import select
import stat
import struct
import time


def valid_output(data):
    return (bool(data) and len(data) == {0x31: 78, 0x32: 142, 0x35: 334, 0x36: 398}.get(data[0])
            and struct.unpack_from("<I", data, len(data) - 4)[0] == binascii.crc32(b"\xa2" + data[:-4]))


def state_payload(data):
    if not valid_output(data):
        return None
    if data[0] == 0x31:
        return data[3:50]
    if data[0] == 0x36 and data[11:13] == b"\x90\x3f":
        return data[13:60]
    return None


def trigger_request(disabled=False):
    """USB02: distinct official Weapon markers, then explicit Off for both."""
    data = bytearray(48)
    data[0:2] = b"\x02\x0c"
    # R2: zones 2..6, force 4. L2: zones 3..7, force 7.
    data[11:22] = bytes.fromhex("2544000300000000000000")
    data[22:33] = bytes.fromhex("2588000600000000000000")
    if disabled:
        data[11:22] = data[22:33] = b"\x05" + bytes(10)
    return bytes(data)


def trigger_matches(data, request):
    state = state_payload(data)
    return (len(request) == 48 and request[0] == 2 and state is not None
            and state[0] & 0x0c == 0x0c and state[10:32] == request[11:33])


def audio_route_matches(data, headset, volume=100, gain=2):
    """Require the route and its state in the same valid combined BT frame."""
    state = state_payload(data)
    return (len(data) == 398 and data[0] == 0x36 and state is not None
            and data[142:144] == bytes([0x96 if headset else 0x93, 200])
            and state[0] & 0xb0 == 0xb0 and state[1] & 0x80 == 0x80
            and state[7] & 0x30 == (0 if headset else 0x30)
            and state[4] == (volume if headset else 0x3f) and state[5] == volume
            and state[37] & 7 == gain)


def open_virtual_hidraw(usb):
    """Open only this already attributed VHCI Sony node, never by VID/PID alone."""
    usb = Path(usb).resolve(strict=True)
    if (not usb.is_relative_to(Path("/sys/devices")) or not any(part.startswith("vhci_hcd") for part in usb.parts)
            or not (usb / "idVendor").is_file()):
        raise AssertionError("Observer requires the exact imported USB parent")
    vendor, product = (int((usb / field).read_text(), 16) for field in ("idVendor", "idProduct"))
    if vendor != 0x054c or product not in (0x0ce6, 0x0df2):
        raise AssertionError("Observer requires a virtual Sony persona")
    import fcntl
    from kernel_probe import owned_nodes
    nodes = owned_nodes(usb, "hidraw", "hidraw*")
    if len(nodes) != 1:
        raise AssertionError("Virtual hidraw identity is ambiguous")
    node = Path("/dev", nodes[0].name)
    fd = os.open(node, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        raw = bytearray(8)
        fcntl.ioctl(fd, 0x80084803, raw, True)  # HIDIOCGRAWINFO
        actual_bus, actual_vendor, actual_product = struct.unpack("Ihh", raw)
        numbers = (nodes[0] / "dev").read_text().strip()
        if (not stat.S_ISCHR(info.st_mode) or numbers != f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}"
                or (actual_bus, actual_vendor & 0xffff, actual_product & 0xffff) != (3, vendor, product)
                or not (nodes[0] / "device").resolve().is_relative_to(usb)):
            raise AssertionError("Opened observer descriptor no longer matches the owned virtual parent")
        return fd
    except BaseException:
        os.close(fd)
        raise


def exercise_triggers(usb, fixture):
    from kernel_probe import wait_for
    fd = open_virtual_hidraw(usb)
    observations = []
    try:
        for disabled in (False, True):
            request = trigger_request(disabled)
            start = len(fixture.outputs)
            if os.write(fd, request) != len(request):
                raise AssertionError("Short virtual trigger write")
            report = wait_for(lambda: next((data for data in fixture.outputs[start:]
                                           if trigger_matches(data, request)), None), 5)
            observations.append(dict(disabled=disabled, requestHex=request.hex(), deliveredHex=report.hex()))
    finally:
        # The normal pass already observes Off. Failure still attempts it before
        # the actor's independently asserted ordered reset/lease cleanup.
        if len(observations) != 2:
            try:
                os.write(fd, trigger_request(True))
            except OSError:
                pass
        os.close(fd)
    return observations


def observe_edge_buttons(usb, fixture):
    fd = open_virtual_hidraw(usb)
    observations = []
    try:
        for mask in (0x10, 0x20, 0x40, 0x80, 0):
            fixture.edge_buttons = mask
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if not select.select([fd], [], [], .1)[0]:
                    continue
                data = os.read(fd, 4096)
                if (len(data) == 64 and data[0] == 1 and data[10] == mask
                        and data[1:7] == b"\x80\x80\x80\x80\0\0" and data[8:10] == b"\x08\0"):
                    observations.append(dict(edgeButtons=mask, virtualInputHex=data.hex()))
                    break
            else:
                raise AssertionError("Edge button marker did not traverse the real app and virtual USB")
    finally:
        fixture.edge_buttons = 0
        os.close(fd)
    return observations
