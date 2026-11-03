#!/usr/bin/env python3
"""Opt-in empty-host CI experiment with the unmodified official signed OJD app.

No physical device access, installation, TCC grants, security changes, HID output,
or effects. Gates must pass before the downloaded executable is started.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

VERSION = "0.6.0-alpha.1"
COMMIT = "3552be925d7dacef3fd895ac0c6cbf39e9d3cd70"
ASSET = f"https://github.com/xsyetopz/OpenJoystickDriver/releases/download/{VERSION}/OpenJoystickDriver-{VERSION}-macOS.dmg"
SHA256 = "d2b771ffe4382b7bfd218932c7003227216d334a14154e95976c4f114b1c2056"
TEAM = "9PQP6CDMQT"
BUNDLE = "com.openjoystickdriver"
CAPABILITY = "com.apple.developer.hid.virtual.device"
GENERIC_DESCRIPTOR = bytes.fromhex(
    "05010905a101a1000509190129061500250175019506810219092912950a8102"
    "050109300931093209330934093516018026ff7f751095068102c0c0")
LOG_LIMIT = 1024 * 1024


class Blocked(Exception):
    pass


def check_identity(entitlements: dict, profile: dict) -> None:
    expected = {"com.apple.application-identifier": f"{TEAM}.{BUNDLE}",
                "com.apple.developer.team-identifier": TEAM}
    if entitlements.get(CAPABILITY) is not True or any(entitlements.get(k) != v for k, v in expected.items()):
        raise Blocked("released executable entitlement identity differs from the pinned audit")
    if entitlements.get("com.apple.security.app-sandbox") is not False:
        raise Blocked("pinned release must explicitly be unsandboxed for the audited Foundation-home isolation")
    granted = profile.get("Entitlements", {})
    if granted.get(CAPABILITY) is not True or any(granted.get(k) != v for k, v in expected.items()) or profile.get("ProvisionsAllDevices") is not True:
        raise Blocked("embedded profile does not grant the audited universal entitlement identity")


def check_empty_inventory(inventory: dict) -> None:
    keys = ("unsafeHIDCount", "usbDeviceCount", "bluetoothControllerCount")
    if any(type(inventory.get(k)) is not int or inventory[k] != 0 for k in keys) or inventory.get("feeds") != []:
        raise Blocked("host is not empty: physical/controller/Bluetooth/preexisting broker devices present")


def check_inventory_capture(inventory: dict) -> None:
    """Validate capture completeness, not hardware absence or permission to launch."""
    if (inventory.get("schemaVersion") != 2 or type(inventory.get("schemaVersion")) is not int
            or inventory.get("mode") != "read-only-registry" or inventory.get("enumerationComplete") is not True
            or inventory.get("queryErrors") != []):
        raise Blocked("read-only registry enumeration is incomplete or has an unexpected schema")
    for count, rows in (("usbDeviceCount", "usbInventory"), ("bluetoothControllerCount", "bluetoothInventory")):
        if type(inventory.get(count)) is not int or type(inventory.get(rows)) is not list or inventory[count] != len(inventory[rows]):
            raise Blocked(f"inconsistent read-only inventory: {count}")
    for key in ("hidInventory", "feeds"):
        if type(inventory.get(key)) is not list:
            raise Blocked(f"missing read-only inventory: {key}")
    interfaces = []
    for device in inventory["usbInventory"]:
        if type(device) is not dict or type(device.get("interfaces")) is not list:
            raise Blocked("USB interface enumeration is missing")
        interfaces.extend(device["interfaces"])
    if type(inventory.get("usbInterfaceCount")) is not int or inventory["usbInterfaceCount"] != len(interfaces):
        raise Blocked("USB interface enumeration is inconsistent")
    queries = inventory.get("bluetoothTransportQueries")
    expected = {"IOBluetoothHostControllerTransport", "IOBluetoothHostController",
                "IOBluetoothHostControllerUSBTransport", "IOBluetoothHostControllerUARTTransport",
                "IOBluetoothHostControllerPCIeTransport"}
    if type(queries) is not dict or set(queries) != expected or any(type(rows) is not list for rows in queries.values()):
        raise Blocked("Bluetooth transport enumeration is incomplete")
    all_rows = inventory["hidInventory"] + inventory["usbInventory"] + inventory["bluetoothInventory"] + interfaces
    all_rows += [row for rows in queries.values() for row in rows]
    for row in all_rows:
        if type(row) is not dict or type(row.get("class")) is not str or type(row.get("metadataErrors")) is not list:
            raise Blocked("registry metadata must retain its class and explicit field errors")
    for controller in inventory["bluetoothInventory"]:
        if "BluetoothTransportConnected" not in controller or type(controller["BluetoothTransportConnected"]) not in (bool, type(None)):
            raise Blocked("Bluetooth transport evidence must be an exact Boolean or explicit unknown")
        if controller["BluetoothTransportConnected"] is None and not controller["metadataErrors"]:
            raise Blocked("unknown Bluetooth evidence must retain its diagnostic")
    vm = inventory.get("vmProvenance")
    if (type(vm) is not dict or vm.get("source") != "sysctl:kern.hv_vmm_present"
            or type(vm.get("complete")) is not bool or vm.get("provesHardwareIsolation") is not False
            or type(vm.get("hypervisorPresent")) not in (bool, type(None))
            or any(type(vm.get(key)) is not int for key in ("queryStatus", "queryErrno", "valueSize"))
            or (vm["complete"] and (vm["queryStatus"] != 0 or type(vm["hypervisorPresent"]) is not bool))):
        raise Blocked("virtualization evidence is missing or overstates hardware isolation")


def stop_owned(process: subprocess.Popen) -> None:
    """Only process groups created by this runner, never a process-name match."""
    # A CLI can spawn a bounded systemextensionsctl query. Its group can outlive
    # the leader if that leader crashes. Keep the group ID from start_new_session
    # and clean the entire group even after the Popen leader has been reaped.
    def group_exists():
        try:
            os.killpg(process.pid, 0)
            return True
        except ProcessLookupError:
            return False
    process.poll()
    if not group_exists():
        if process.poll() is None:
            process.wait(timeout=3)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=3)
        return
    deadline = time.monotonic() + 3
    while group_exists() and time.monotonic() < deadline:
        process.poll()
        time.sleep(0.05)
    if group_exists():
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.wait(timeout=3)


class Experiment:
    def __init__(self, output: Path):
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=True)
        self.events = []
        self.children = []
        self.files = []
        self.readers = []
        self.overflow = threading.Event()
        self.env = dict(os.environ)

    def record(self, event: str, **details):
        row = {"event": event, **details}
        self.events.append(row)
        print(json.dumps(row), flush=True)
        (self.output / "events.json").write_text(json.dumps(self.events, indent=2) + "\n")

    def run(self, label, args, timeout=15, *, env=None, required=True, save_stdout=True):
        process = subprocess.Popen([str(x) for x in args], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env or self.env, start_new_session=True)
        self.children.append(process)
        deadline = time.monotonic() + timeout
        while True:
            try:
                out, err = process.communicate(timeout=max(0.01, min(0.2, deadline - time.monotonic())))
                break
            except subprocess.TimeoutExpired as partial:
                if len(partial.output or b"") + len(partial.stderr or b"") > LOG_LIMIT:
                    stop_owned(process)
                    raise Blocked(f"{label} exceeded the diagnostic output limit")
                if time.monotonic() >= deadline:
                    stop_owned(process)
                    self.record(label, result="timeout")
                    raise Blocked(f"{label} exceeded its bounded deadline")
        if len(out) + len(err) > LOG_LIMIT:
            raise Blocked(f"{label} exceeded the diagnostic output limit")
        if save_stdout:
            (self.output / f"{label}.stdout").write_bytes(out)
        (self.output / f"{label}.stderr").write_bytes(err)
        self.record(label, exit=process.returncode)
        if required and process.returncode:
            raise Blocked(f"{label} rejected or failed (exit {process.returncode}); no permission granted")
        return process.returncode, out, err

    def background(self, label, args, *, input_pipe=False):
        logs = [open(self.output / f"{label}.{stream}", "wb") for stream in ("stdout", "stderr")]
        self.files.extend(logs)
        process = subprocess.Popen([str(x) for x in args], stdin=subprocess.PIPE if input_pipe else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env, start_new_session=True)
        self.children.append(process)
        def drain(source, target):
            retained = 0
            try:
                while chunk := source.read(4096):
                    remaining = max(0, LOG_LIMIT - retained)
                    target.write(chunk[:remaining])
                    target.flush()
                    retained += len(chunk)
                    if retained > LOG_LIMIT:
                        self.overflow.set()
            finally:
                source.close()
        for source, target in zip((process.stdout, process.stderr), logs):
            reader = threading.Thread(target=drain, args=(source, target), daemon=True)
            reader.start()
            self.readers.append(reader)
        return process

    def guard(self):
        if self.overflow.is_set():
            raise Blocked("child exceeded diagnostic output limit")

    def execute_inventory(self):
        """No download, signature assessment, OJD launch, Bluetooth API or device open."""
        if sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true":
            raise Blocked("inventory capture is restricted to disposable GitHub macOS CI")
        self.record("scope", mode="inventory-only", originalReleaseExecuted=False, signingRechecked=False)
        with ExitStack() as cleanup:
            directory = cleanup.enter_context(tempfile.TemporaryDirectory(prefix="madsense-ojd-inventory-", dir=self.output))
            cleanup.callback(self.close)
            observer = Path(directory) / "observer"
            self.run("build-observer", ["/usr/bin/xcrun", "clang", "-fobjc-arc", "-Wall", "-Wextra",
                "-framework", "Foundation", "-framework", "IOKit", Path(__file__).with_name("ojd_broker_observer.m"),
                "-o", observer], timeout=90)
            self.run("observer-parser-self-test", [observer, "self-test"])
            _, data, _ = self.run("empty-host", [observer, "inventory"])
            inventory = json.loads(data)
            check_inventory_capture(inventory)
            try:
                check_empty_inventory(inventory)
                current_gate = "would-pass"
            except Blocked:
                current_gate = "blocked"
            self.record("result", result="inventory-collected", currentUnchangedGate=current_gate,
                        originalReleaseExecuted=False, signingRechecked=False,
                        physicalDeviceOpened=False, hardwareIsolationValidated=False)
            return 0

    def execute(self):
        if sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true":
            raise Blocked("execution is restricted to explicitly opted-in disposable GitHub macOS CI")
        if Path(f"/tmp/com.openjoystickdriver.{os.getuid()}.rpc").exists():
            raise Blocked("preexisting broker socket; refusing to contact or stop it")
        for path in (Path("/Applications/OpenJoystickDriver.app"),
                     Path.home() / "Library/Application Support/OpenJoystickDriver"):
            if path.exists():
                raise Blocked("preexisting broker installation/data; refusing to alter or launch it")
        self.record("pin", version=VERSION, sourceCommit=COMMIT, assetSHA256=SHA256,
                    scope="neutral generic controller only; not DualSense compatibility")
        self.run("sip-readonly", ["/usr/bin/csrutil", "status"], required=False)
        with ExitStack() as cleanup:
            directory = cleanup.enter_context(tempfile.TemporaryDirectory(prefix="madsense-ojd-", dir=self.output))
            # Processes must exit before their app/data tree is removed, including errors.
            cleanup.callback(self.close)
            root = Path(directory)
            dmg = root / "official.dmg"
            with urllib.request.urlopen(ASSET, timeout=40) as source, dmg.open("wb") as target:
                total = 0
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > 40 * 1024 * 1024:
                        raise Blocked("download exceeds pinned release size bound")
                    target.write(chunk)
            if hashlib.sha256(dmg.read_bytes()).hexdigest() != SHA256:
                raise Blocked("official release SHA256 mismatch")
            self.record("asset-hash", matched=True, bytes=total)
            mount = root / "mount"
            mount.mkdir()
            attached = False
            try:
                self.run("mount-readonly", ["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse",
                    "-noautoopen", "-mountpoint", mount, dmg], timeout=40)
                attached = True
                candidates = list(mount.glob("*.app"))
                if len(candidates) != 1 or candidates[0].name != "OpenJoystickDriver.app":
                    raise Blocked("unexpected release app layout")
                app = root / "OpenJoystickDriver.app"
                self.run("copy-original", ["/usr/bin/ditto", candidates[0], app], timeout=40)
            finally:
                if attached:
                    self.run("detach", ["/usr/bin/hdiutil", "detach", mount], timeout=20)
            self.run("codesign-verify", ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", app])
            _, assessment_out, assessment_err = self.run("gatekeeper-assess", ["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", app], timeout=45)
            if b"accepted" not in (assessment_out + assessment_err).lower():
                raise Blocked("Gatekeeper did not explicitly report accepted; app was not started")
            _, ent, _ = self.run("entitlements", ["/usr/bin/codesign", "-d", "--entitlements", ":-", app])
            _, raw_profile, _ = self.run("profile", ["/usr/bin/security", "cms", "-D", "-i", app / "Contents/embedded.provisionprofile"], save_stdout=False)
            entitlements = plistlib.loads(ent[ent.index(b"<?xml"):])
            profile = plistlib.loads(raw_profile)
            check_identity(entitlements, profile)
            # Retain only capability metadata, not embedded certificates or profile identifiers.
            self.record("approved-profile", team=TEAM, bundle=BUNDLE, virtualHID=True,
                        expires=profile["ExpirationDate"].isoformat())
            info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            if info.get("CFBundleIdentifier") != BUNDLE or info.get("CFBundleShortVersionString") != VERSION:
                raise Blocked("unexpected official bundle version/identity")
            observer = root / "observer"
            self.run("build-observer", ["/usr/bin/xcrun", "clang", "-fobjc-arc", "-Wall", "-Wextra",
                "-framework", "Foundation", "-framework", "IOKit", Path(__file__).with_name("ojd_broker_observer.m"),
                "-o", observer], timeout=90)
            isolated = root / "foundation-home"
            isolated.mkdir()
            self.env["CFFIXED_USER_HOME"] = str(isolated)
            self.env["OJD_NO_INPUT"] = "1"
            self.env["OJD_COLOR"] = "never"
            _, data, _ = self.run("foundation-home", [observer, "home"])
            if Path(json.loads(data)["foundationHome"]).resolve() != isolated.resolve():
                raise Blocked("Foundation home isolation unavailable; app was not started")
            settings = isolated / "Library/Application Support/OpenJoystickDriver/Settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(json.dumps({"apiVersion": "openjoystickdriver/v1alpha1", "kind": "Settings",
                "launchAtLoginOptOut": True, "updates": {"checkAutomatically": False},
                "notify": {"controllerConnected": False, "controllerDisconnected": False,
                    "profileActivated": False, "profileDeactivated": False, "sounds": False}}))
            _, data, _ = self.run("empty-host", [observer, "inventory"])
            check_empty_inventory(json.loads(data))
            binary = app / "Contents/MacOS/OpenJoystickDriver"
            cli = root / "ojd"
            cli.symlink_to(binary)
            # Direct launch preserves our environment and exact PID/process-group ownership.
            # `service start` would delegate to LaunchServices and lose those guarantees.
            host = self.background("host", [binary])
            deadline = time.monotonic() + 12
            status = None
            while time.monotonic() < deadline and host.poll() is None:
                _, data, _ = self.run("broker-status", [cli, "status", "--json", "--no-input", "--timeout", "1"], required=False)
                try:
                    candidate = json.loads(data)
                except (ValueError, UnicodeError):
                    candidate = {}
                if candidate.get("service", {}).get("state") == "Running":
                    status = candidate
                    break
                self.guard()
                time.sleep(0.2)
            if status is None:
                raise Blocked("signed broker did not become ready; permission/startup may require interaction")
            if status.get("controllers") or status.get("unboundDevices") or status.get("passThroughDevices"):
                raise Blocked("broker discovered a non-test device; experiment stopped")
            self.record("broker-ready", permissions=status.get("permissions"),
                        physicalControllers=0, permissionGrantsAttempted=False)
            feed = self.background("neutral-feed", [cli, "virtual", "feed", "--as", "generic",
                "--json", "--no-input", "--timeout", "2"], input_pipe=True)
            assert feed.stdin
            feed.stdin.write(b"{}\n"); feed.stdin.flush()
            deadline = time.monotonic() + 6
            registered = None
            while time.monotonic() < deadline and feed.poll() is None and host.poll() is None:
                _, data, _ = self.run("feed-inventory", [observer, "inventory"])
                inventory = json.loads(data)
                if any(inventory.get(k) for k in ("unsafeHIDCount", "usbDeviceCount", "bluetoothControllerCount")):
                    raise Blocked("unexpected device arrived during broker experiment")
                feeds = inventory.get("feeds", [])
                if len(feeds) == 1:
                    registered = feeds[0]
                    break
                if feeds:
                    raise Blocked("ambiguous virtual-device ownership")
                self.guard()
                time.sleep(0.1)
            if registered is None:
                raise Blocked("neutral feed did not publish a registry device; no permission was granted")
            if base64.b64decode(registered["descriptor"], validate=True) != GENERIC_DESCRIPTOR:
                raise Blocked("published generic descriptor differs from audited source")
            self.record("registered", registryID=registered["registryID"], transport=registered["transport"],
                        descriptorSHA256=hashlib.sha256(GENERIC_DESCRIPTOR).hexdigest(), outputReports=False, featureReports=False)
            reader = self.background("neutral-observer", [observer, "inspect", str(registered["registryID"])])
            # Keep every report neutral. OJD deduplicates identical packets, so
            # independent input GET, not callback count, is the deterministic check.
            deadline = time.monotonic() + 7
            while reader.poll() is None and time.monotonic() < deadline:
                if feed.poll() is not None or host.poll() is not None:
                    raise Blocked("broker/feed exited while validating neutral input")
                feed.stdin.write(b"{}\n"); feed.stdin.flush()
                self.guard()
                time.sleep(0.05)
            if reader.poll() is None:
                raise Blocked("independent HID observer timed out")
            if reader.returncode != 0:
                raise Blocked(f"independent HID observer rejected or failed (exit {reader.returncode})")
            feed.stdin.close()
            try:
                feed.wait(timeout=4)
            except subprocess.TimeoutExpired:
                raise Blocked("feed did not close after EOF")
            if feed.returncode:
                raise Blocked("feed ended unsuccessfully")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                _, data, _ = self.run("after-close", [observer, "inventory"])
                if not json.loads(data).get("feeds"):
                    break
                time.sleep(0.1)
            else:
                raise Blocked("test virtual device remained after feed closed")
            stop_owned(host)
            self.record("result", result="passed", neutralInputGetVerified=True, testDeviceRemoved=True,
                        fullDualSense=False, physicalDeviceTested=False)
            return 0

    def close(self):
        for process in reversed(self.children):
            stop_owned(process)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
        for reader in self.readers:
            reader.join(timeout=1)
        for handle in self.files:
            handle.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-on-empty-ci", action="store_true")
    mode.add_argument("--inventory-only-ci", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("dist/ojd-broker"))
    args = parser.parse_args()
    experiment = Experiment(args.output)
    try:
        return experiment.execute_inventory() if args.inventory_only_ci else experiment.execute()
    except Blocked as error:
        experiment.record("result", result="blocked", reason=str(error))
        return 77
    except Exception as error:
        experiment.record("result", result="error", reason=f"{type(error).__name__}: {error}")
        return 1
    finally:
        experiment.close()


if __name__ == "__main__":
    sys.exit(main())
