"""Private Wine/Xvfb/PulseAudio session used only by consumer_session.run_suite.

Prerequisites in the owned guest: wine64, wine, xvfb, pulseaudio,
pulseaudio-utils, libsdl2-2.0-0 and acl. No packages or services are installed here.
The caller owns the fixture; this module never attaches or detaches USB.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import select
import shutil
import struct
import time
import uuid

from consumer_session import bt_state, clear_sony_state, read_rows, require, wait_delivery
from consumer_files import write_private_file


def xauthority(cookie, display):
    """FamilyWild entry; only this private cookie authorizes the owned server."""
    require(len(cookie) == 16 and str(display).isdigit(), "Invalid Xauthority input")
    fields = (b"", str(display).encode("ascii"), b"MIT-MAGIC-COOKIE-1", cookie)
    return struct.pack(">H", 65535) + b"".join(struct.pack(">H", len(value)) + value for value in fields)


def user_file(session, path, content):
    write_private_file(session, path, content)


def start_display(session):
    authority = session.root / "home/Xauthority"
    cookie = secrets.token_bytes(16)
    user_file(session, authority, xauthority(cookie, 0))
    read_fd, write_fd = os.pipe()
    try:
        process = session.start("xvfb", ["Xvfb", "-displayfd", write_fd, "-screen", "0", "640x480x24",
                                        "-nolisten", "tcp", "-auth", authority, "-noreset"], pass_fds=(write_fd,))
        os.close(write_fd); write_fd = None
        require(select.select([read_fd], [], [], 15)[0], "Owned Xvfb did not return a display number")
        number = os.read(read_fd, 64).decode().strip()
        require(number.isdigit() and int(number) < 65536 and process.poll() is None, "Invalid owned Xvfb display")
        # Xserver reads authorization protocol/cookie pairs; clients additionally
        # match their display number. Keep the same cookie when assigning it.
        write_private_file(session, authority, xauthority(cookie, int(number)), replace=True)
        session.env.update(DISPLAY=":" + number, XAUTHORITY=str(authority))
        session.result["xvfb"] = dict(display=int(number), tcpListening=False, privateAuthorization=True)
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)


def start_pulse(session):
    require(session.card is not None, "Owned virtual ALSA card required for Wine audio")
    cards = list(Path("/sys/class/sound").glob("card[0-9]*"))
    require(len(cards) == 1 and cards[0].name == f"card{session.card}",
            "Private udev audio session requires the owned virtual card to be the only guest ALSA card")
    config = session.root / "home/.config/pulse"
    daemon = ("daemonize = no\nexit-idle-time = -1\nenable-shm = no\nflat-volumes = no\n"
              "default-sample-format = s16le\ndefault-sample-rate = 48000\nalternate-sample-rate = 48000\n"
              "default-sample-channels = 4\ndefault-channel-map = front-left,front-right,rear-left,rear-right\n"
              "default-fragments = 4\ndefault-fragment-size-msec = 25\n")
    user_file(session, config / "daemon.conf", daemon)
    cookie = session.root / "home/pulse-cookie"
    user_file(session, cookie, secrets.token_bytes(256))
    socket_path = session.root / "runtime/pulse-native"
    script = (f"load-module module-native-protocol-unix socket={socket_path} auth-cookie={cookie}\n"
              "load-module module-udev-detect tsched=0\n")
    script_path = session.root / "home/consumer.pa"
    user_file(session, script_path, script)
    process = session.start("pulseaudio", ["pulseaudio", "--daemonize=no", "--use-pid-file=no", "--log-target=stderr",
                                          "--disallow-exit", "-n", "--file", script_path])
    deadline = time.monotonic() + 15
    while not socket_path.exists() and time.monotonic() < deadline:
        require(process.poll() is None, "Owned PulseAudio exited before its socket was ready")
        time.sleep(.05)
    require(socket_path.exists(), "Owned PulseAudio socket was not created")
    session.env.update(PULSE_SERVER="unix:" + str(socket_path), PULSE_COOKIE=str(cookie))
    _, raw = session.command("pulse-sinks", ["pactl", "--format=json", "list", "sinks"])
    sinks = json.loads(raw)
    require(len(sinks) == 1 and str(sinks[0].get("properties", {}).get("alsa.card")) == str(session.card),
            "PulseAudio did not expose exactly the owned ALSA sink")
    sink = sinks[0]
    require(sink.get("channel_map") == "front-left,front-right,rear-left,rear-right",
            "PulseAudio selected a different channel map; no silent profile substitution")
    session.command("pulse-volume", ["pactl", "set-sink-volume", sink["name"], "100%"])
    session.command("pulse-unmute", ["pactl", "set-sink-mute", sink["name"], "0"])
    _, card_raw = session.command("pulse-cards", ["pactl", "--format=json", "list", "cards"])
    session.result["audioSession"] = dict(kind="private-pulseaudio-controlled-quad", sink=sink, cards=json.loads(card_raw),
                                         daemonConfiguration=daemon, udevProfileUnmodified=True,
                                         defaultDesktopStackValidated=False)
    session.save()


def parse_pe_output(stdout):
    rows = [json.loads(line) for line in stdout.decode("utf-8").splitlines() if line]
    require(rows and rows[-1].get("kind") == "result", "Windows observer did not return a final result")
    return rows


def matching_container(rows):
    containers = {str(uuid.UUID(row["containerId"].strip("{}"))) for row in rows
                  if row.get("kind") == "hid-candidate" and row.get("macMatched") is True
                  and row.get("containerKnown") is True}
    require(len(containers) == 1 and str(uuid.UUID(int=0)) not in containers,
            "Sony HID did not identify one nonzero container")
    return containers.pop()


def select_audio_endpoint(rows, container):
    candidates = [row for row in rows if row.get("kind") == "audio-endpoint" and row.get("containerKnown") is True
                  and str(uuid.UUID(row["containerId"].strip("{}"))) == container]
    require(len(candidates) == 1, "Wine audio endpoint lacks an unambiguous virtual HID container association")
    return candidates[0]["id"]


def wait_rgb(session, record):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        for row in read_rows(Path(session.ready["uhidEventsPath"])):
            if row.get("kind") == "output" and row["monotonicNs"] >= record["startedMonotonicNs"]:
                payload = bt_state(bytes.fromhex(row["hex"]))
                if payload is not None and payload[1] & 4 and payload[44:47] == bytes.fromhex("123456"):
                    record["delivery"] = dict(outputDeliveryVerified=True, rgbMarkerMonotonicNs=row["monotonicNs"],
                                              physicalActuationVerified=False)
                    session.save(); return
        time.sleep(.05)
    raise RuntimeError("Wine RGB packet did not reach the synthetic physical writer")


def check_pcm(session, record, offsets):
    from kernel_probe import contains_exact
    directory = Path(session.ready["coreOutputDirectory"])
    source = b"".join(struct.pack("<hhhh", 1000 + i % 997, -2000 - i % 991, 4000 + i % 983, -6000 - i % 977)
                      for i in range(48000))
    rear = b"".join(source[i + 4:i + 8] for i in range(0, len(source), 8))
    expected = {"audio-ingress.pcm": (source, 8), "haptics-ingress.pcm": (rear, 4)}
    deadline = time.monotonic() + 3
    last = None
    while time.monotonic() < deadline:
        try:
            checked = {}
            for name, (data, frame_bytes) in expected.items():
                path = directory / name
                require(path.stat().st_size <= 16 * 1024 * 1024, "Core PCM capture exceeded its limit")
                with path.open("rb") as stream:
                    stream.seek(offsets[name]); actual = stream.read()
                checked[name] = contains_exact(actual, data, frame_bytes, True)
                (session.output / ("wine-" + name)).write_bytes(actual)
            record["pcmDelivery"] = dict(pcmDeliveryVerified=True, streams=checked,
                                          physicalAudioOrHapticsVerified=False)
            session.save(); return
        except (AssertionError, RuntimeError) as error:
            last = error; time.sleep(.05)
    raise last or RuntimeError("Wine PCM did not reach core capture")


def run_wine_suite(session, executable, fixture_state_callback, *, winebus_mode="default", hidapi_executable=None):
    require(winebus_mode in ("default", "udev-diagnostic"), "Unknown Wine bus experiment mode")
    for command in ("wine", "wineserver", "Xvfb"):
        require(shutil.which(command), f"Missing test guest prerequisite: {command}")
    program = session.stage(executable)
    program_windows = "Z:" + str(program).replace("/", "\\")
    prefix = session.root / "home/wine-prefix"
    session.env.update(WINEPREFIX=str(prefix), WINEARCH="win64", WINEDEBUG="-all", WINEDLLOVERRIDES="mscoree,mshtml=")
    start_display(session)
    _, version = session.command("wine-version", ["wine", "--version"])
    session.command("wine-packages", ["dpkg-query", "-W", "wine", "wine64", "libwine", "xvfb", "pulseaudio"], required=False)
    session.command("wineboot", ["wine", "wineboot", "--init"], timeout=120)
    session.command("wineboot-wait", ["wineserver", "--wait"], timeout=60)
    session.result["wineSession"] = dict(kind="distribution-wine" if winebus_mode == "default" else "configured-distribution-wine-udev",
                                        freshPrefix=True, defaultWinebus=winebus_mode == "default",
                                        disabledOptionalManagedAndHtmlInstallers=True,
                                        observerSha256=hashlib.sha256(program.read_bytes()).hexdigest(),
                                        protonValidated=False, cases=[])
    session.save()
    if winebus_mode == "udev-diagnostic":
        from run_wine_variant import configure_udev
        try:
            configure_udev(session, prefix, version.decode("utf-8", errors="strict"))
        except Exception as error:
            session.result["wineSession"]["configurationError"] = str(error)
            session.save()
            raise
    def invoke(label, arguments, *, required=False):
        return session.command(label, ["wine", program_windows, *arguments], required=required)
    from run_hidapi_windows import after_api
    after_api(lambda: run_windows_cases(session, invoke, fixture_state_callback),
              session, hidapi_executable, fixture_state_callback)


def run_windows_cases(session, invoke, fixture_state_callback, *, result_key="wineSession", label_prefix="wine"):
    """The same PE/device/output/PCM assertions for explicitly separate runtimes."""
    token = session.ready["ownedGuestToken"]
    sony = session.ready["vendorId"] == 0x054c
    containers, failures = set(), []
    for state in ("south", "neutral"):
        fixture_state_callback(state); time.sleep(.1)
        label = label_prefix + "-" + ("sony-" if sony else "xinput-") + state
        try:
            clear_sony_state(session, label + "-clear")
            if sony:
                persona = "edge" if session.ready["productId"] == 0x0df2 else "dualsense"
                arguments = ["sony", token, persona, session.ready["virtualMacHex"], state]
            else:
                arguments = ["xinput", token, state]
            record, stdout = invoke(label, arguments, required=False)
            rows = parse_pe_output(stdout); record["evidence"] = rows
            if sony:
                containers.add(matching_container(rows))
            require(record["exitCode"] == 0 and rows[-1].get("result") == "passed", label + " API assertions failed")
            if sony:
                seen = {f"{row['reportId']:02x}": row["hex"] for row in rows if row.get("kind") == "hid-feature"}
                require(all(seen.get(key) == value.lower() for key, value in session.ready["features"].items()),
                        "Wine feature bytes differ from the production virtual persona")
                wait_rgb(session, record)
            else:
                wait_delivery(session, record, rgb=False)
            session.result[result_key]["cases"].append(dict(name=label, passed=True))
        except Exception as error:
            session.check_device()
            failures.append(f"{label}: {error}")
            session.result[result_key]["cases"].append(dict(name=label, passed=False, error=str(error)))
        session.save()
    fixture_state_callback("neutral")
    if sony:
        try:
            for command in ("pulseaudio", "pactl"):
                require(shutil.which(command), f"Missing test guest prerequisite: {command}")
            require(len(containers) == 1, "No stable MAC-verified HID container for Wine audio")
            start_pulse(session)
            record, stdout = invoke(label_prefix + "-audio-inventory", ["audio-inventory", token], required=True)
            rows = parse_pe_output(stdout); record["evidence"] = rows
            container = next(iter(containers)); endpoint = select_audio_endpoint(rows, container)
            directory = Path(session.ready["coreOutputDirectory"])
            offsets = {name: (directory / name).stat().st_size for name in ("audio-ingress.pcm", "haptics-ingress.pcm")}
            require(offsets["audio-ingress.pcm"] % 8 == 0 and offsets["haptics-ingress.pcm"] % 4 == 0,
                    "Core PCM capture offset is not frame aligned")
            record, stdout = invoke(label_prefix + "-wasapi", ["audio", token, endpoint, "{" + container + "}"], required=True)
            rows = parse_pe_output(stdout); record["evidence"] = rows
            require(rows[-1].get("result") == "passed", "Windows WASAPI assertions failed")
            check_pcm(session, record, offsets)
            session.result[result_key]["cases"].append(dict(name=label_prefix + "-wasapi", passed=True))
        except Exception as error:
            session.check_device()
            failures.append(f"{label_prefix}-wasapi: {error}")
            session.result[result_key]["cases"].append(dict(name=label_prefix + "-wasapi", passed=False, error=str(error)))
    session.save()
    require(not failures, "; ".join(failures))
