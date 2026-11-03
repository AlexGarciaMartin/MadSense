"""Owned-guest ignore rule; full interface nonce prevents foreign-port reuse matches."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from policy import validate_parent
from evidence import require

PREFIX = "MadSense-audio-lease-"
MARKER = "madsense.audio.lease"

def validate_plan(plan, case_id):
    require(case_id in ("owned-automatic", "owned-explicit", "foreign-parent", "foreign-nonce"), "Unknown export case")
    validate_parent(plan["actualParent"]); validate_parent(plan["targetParent"])
    require(re.fullmatch(r"[0-9a-f]{32}", plan["nonce"]) and re.fullmatch(r"[0-9a-f]{32}", plan["ruleNonce"]), "Invalid plan nonce")
    require(plan["matched"] is case_id.startswith("owned-")
        and (plan["actualParent"] != plan["targetParent"]) == (case_id == "foreign-parent")
        and (plan["nonce"] != plan["ruleNonce"]) == (case_id == "foreign-nonce"), "Case and ignore scope disagree")

def alsa_node(properties):
    return (properties.get("device.api") == "alsa" or "api.alsa.pcm.card" in properties
        or "api.alsa.path" in properties or MARKER in properties
        or str(properties.get("factory.name", "")).startswith("api.alsa."))

def configuration(parent, nonce):
    parent = validate_parent(parent)
    require(re.fullmatch(r"[0-9a-f]{32}", nonce), "Invalid interface nonce")
    busid = parent.rsplit("/", 1)[1]
    # All ATTRS keys match the SAME AudioControl interface. Vendor/product are
    # revalidated by the coordinator; mixing its USB ancestor here would fail.
    return ('ACTION=="add|change", SUBSYSTEM=="sound", KERNEL=="card[0-9]*", '
        f'DEVPATH=="{parent[4:]}/{busid}:1.0/sound/card[0-9]*", '
        'ATTRS{bInterfaceNumber}=="00", ATTRS{bInterfaceClass}=="01", '
        f'ATTRS{{bInterfaceSubClass}}=="01", ATTRS{{interface}}=="{PREFIX+nonce}", '
        'ENV{ACP_IGNORE}:="1", ENV{PULSE_IGNORE}:="1", '
        f'ENV{{MADSENSE_AUDIO_LEASE}}:="{nonce}"\n')

class IgnoreRule:
    def __init__(self, plan, output):
        self.plan, self.output = plan, Path(output)
        self.path = Path("/run/udev/rules.d", "70-madsense-audio-"+plan["ruleNonce"]+".rules")
        self.content = configuration(plan["targetParent"], plan["ruleNonce"]).encode()
        self.record = dict(path=str(self.path), sha256=hashlib.sha256(self.content).hexdigest(),
            plan=plan, installed=False, removed=False, reloadAfterRemoval=False)
        self.identity = None
        self.temporary = self.path.with_suffix(".pending")
        self.temporary_identity = None

    def save(self):
        (self.output / "ignore-rule.json").write_text(json.dumps(self.record, indent=2)+"\n")

    def remove_temporary(self):
        if self.temporary_identity is None: return
        info = self.temporary.lstat()
        require(stat.S_ISREG(info.st_mode) and not info.st_uid
            and (info.st_dev, info.st_ino) == self.temporary_identity,
            "Temporary ignore rule changed externally; preserve replacement")
        self.temporary.unlink(); self.temporary_identity = None

    def publish(self):
        # link() atomically refuses an existing target. Neither an earlier
        # exists() check nor rename() gives that guarantee on Linux.
        with self.temporary.open("xb") as stream:
            info = os.fstat(stream.fileno())
            self.temporary_identity = (info.st_dev, info.st_ino)
            stream.write(self.content); stream.flush(); os.fsync(stream.fileno())
        self.temporary.chmod(0o644)
        os.link(self.temporary, self.path)
        self.identity = self.temporary_identity
        self.remove_temporary()

    def __enter__(self):
        require(os.geteuid() == 0 and not Path(self.plan["actualParent"]).exists()
            and not Path(self.plan["targetParent"]).exists(), "Rule must precede any candidate USB device")
        self.output.mkdir(); self.save()
        require(not self.path.exists() and not self.temporary.exists(), "Fresh unique ignore rule required")
        try:
            self.publish()
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
            self.record.update(installed=True, installedMonotonicNs=time.monotonic_ns()); self.save()
            subprocess.run(["udevadm", "control", "--reload-rules"], check=True, timeout=10)
            self.record["reloadAcknowledgedNs"] = time.monotonic_ns(); self.save()
            (self.output / "ignore.rules").write_bytes(self.content)
            return self
        except BaseException:
            try: self.close()
            finally: self.remove_temporary()
            raise

    def close(self):
        self.remove_temporary()
        # Never make a still-imported card visible as a side effect of cleanup.
        require(not Path(self.plan["actualParent"]).exists(), "Owned USB must disappear before unignoring")
        if self.identity is not None:
            info = self.path.lstat()
            require(stat.S_ISREG(info.st_mode) and not info.st_uid and not info.st_mode & 0o022
                and (info.st_dev, info.st_ino) == self.identity and self.path.read_bytes() == self.content,
                "Ignore rule changed externally")
            self.path.unlink(); self.record.update(removed=True, removedMonotonicNs=time.monotonic_ns()); self.save()
        subprocess.run(["udevadm", "control", "--reload-rules"], check=True, timeout=10)
        self.record["reloadAfterRemoval"] = True; self.save()

    def __exit__(self, kind, error, traceback): self.close()

def validate_node(row, plan, card_path=None, require_format=False):
    p = row.get("info", {}).get("props", {})
    require(row.get("type") == "PipeWire:Interface:Node" and p.get("media.class") == "Audio/Sink"
        and p.get(MARKER) == plan["nonce"] and p.get("node.name") == "madsense-owned-"+plan["nonce"],
        "Exported node identity differs")
    require(str(p.get("priority.session")) == "1" and str(p.get("audio.channels")) == "4"
        and p.get("audio.position") in ("FL,FR,RL,RR", "[ FL FR RL RR ]")
        and str(p.get("device.vendor.id")) == "0x054c" and str(p.get("device.product.id")) == "0x0ce6"
        and p.get("device.bus") == "usb", "Export lost initial priority/quad/USB properties")
    path = p.get("device.sysfs.path", "")
    require(path.startswith(plan["actualParent"][4:] + "/") and "/sound/card" in path,
        "Export is associated with another USB parent")
    if card_path is not None: require(path == card_path[4:], "Export belongs to another card epoch")
    require(type(p.get("object.serial")) is int and type(p.get("client.id")) is int,
        "Node lacks a unique object serial/client owner")
    parameters = row.get("info", {}).get("params", {})
    if require_format:
        formats = parameters.get("EnumFormat", [])
        require(any(value.get("channels") == 4 and value.get("position") == ["FL", "FR", "RL", "RR"]
            for value in formats), "Export does not advertise an actual four-channel format")
    return dict(id=row["id"], serial=p["object.serial"], clientId=p["client.id"],
        sysfsPath=path, properties=p, parameters=parameters)

def publication(text, plan, expected_nodes):
    decoder = json.JSONDecoder(); offset = 0; seen = {}
    require(len(text.encode()) <= 16*1024*1024, "Monitor evidence too large")
    while offset < len(text):
        while offset < len(text) and text[offset].isspace(): offset += 1
        if offset == len(text): break
        rows, offset = decoder.raw_decode(text, offset)
        require(isinstance(rows, list), "Malformed graph stream")
        for row in rows:
            require(isinstance(row, dict), "Malformed graph object")
            p = (row.get("info") or {}).get("props", {})
            if row.get("type") == "PipeWire:Interface:Device":
                require(p.get("device.api") != "alsa", "Automatic ALSA device was published transiently")
            if row.get("type") != "PipeWire:Interface:Node": continue
            if not alsa_node(p): continue
            value = validate_node(row, plan)
            seen[(value["id"], value["serial"])] = value
    require(len(seen) == expected_nodes, "Missing or extra client-owned publication epochs")
    return dict(exportedNodes=len(seen), automaticAlsaDevices=0, desktopCaptureNodes=0,
        noAutomaticTransientObserved=True, nodeIdentities=[list(key) for key in seen])
