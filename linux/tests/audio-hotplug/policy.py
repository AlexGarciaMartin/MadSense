"""Private WP 0.4 policy experiment; never installed in system configuration."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import time
import uuid

from desktop import PrivateDesktop
from evidence import require
from actor import LINUX

sys.path.insert(0, str(LINUX / "platform"))
from broker import expected_import_path

MARKER = "api.alsa.card.madsense-session"
PARENT = re.compile(r"/sys/devices/platform/vhci_hcd\.[0-9]+/usb([1-9][0-9]*)/\1-([1-9][0-9]*)")


def validate_parent(parent):
    text = str(parent)
    require(PARENT.fullmatch(text), "Policy requires one exact VHCI USB2 parent")
    return text


def configuration(parent, nonce):
    parent = validate_parent(parent)
    require(re.fullmatch(r"[0-9a-f]{32}", nonce), "Invalid private policy nonce")
    # The slash following the complete USB parent prevents 1-1 matching 1-10.
    # Only the sound-interface/card suffix varies; no VID/PID-only rule exists.
    match = parent[4:] + "/*/sound/card*"
    return '''-- Owned experiment only: loaded before the private graph starts.
table.insert(alsa_monitor.rules, {
  matches = {{
    { "device.sysfs.path", "matches", "%s" },
    { "device.vendor.id", "equals", "0x054c" },
    { "device.product.id", "equals", "0x0ce6" },
  }},
  apply_properties = { ["%s"] = "%s" },
})
table.insert(alsa_monitor.rules, {
  matches = {{ { "%s", "equals", "%s" },
               { "api.alsa.pcm.stream", "equals", "playback" } }},
  apply_properties = { ["priority.session"] = 1 },
})
table.insert(alsa_monitor.rules, {
  matches = {{ { "%s", "equals", "%s" },
               { "api.alsa.pcm.stream", "equals", "capture" } }},
  apply_properties = { ["node.disabled"] = true },
})
''' % (match, MARKER, nonce, MARKER, nonce, MARKER, nonce)


def predict_parents():
    # Read the production mapping, without attaching or reserving any device.
    # This is valid only in this idle owned VM; each actual lease must agree.
    candidates, evidence = [], []
    for path in sorted(Path("/sys/devices/platform").glob("vhci_hcd.*/status*")):
        raw = path.read_bytes()
        evidence.append(dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), text=raw.decode()))
        for line in raw.decode().splitlines()[1:]:
            fields = line.split()
            require(len(fields) == 7, "Unknown VHCI status layout")
            require(fields[6] == "0-0", "Prediction requires every VHCI port to be idle")
            if fields[0] == "hs" and int(fields[2]) == 4:
                parent = expected_import_path(path, int(fields[1])).resolve()
                validate_parent(parent)
                require(not parent.exists(), "Predicted USB path already exists")
                candidates.append(str(parent))
    require(len(candidates) >= 2 and len(set(candidates)) == len(candidates),
            "Two distinct idle VHCI parents required for the negative control")
    return candidates[:2], evidence


def validate_virtual(virtual, plan):
    require(virtual["usbParent"] == plan["actualParent"], "Actual app lease differs from the pre-graph prediction")
    require(PurePosixPath(virtual["cardSysfs"]).is_relative_to(PurePosixPath(plan["actualParent"])),
            "Card is outside the actual owned USB parent")
    matched = plan["targetParent"] == plan["actualParent"]
    card = virtual["deviceProperties"]
    require(card.get(MARKER) == (plan["nonce"] if matched else None), "Device policy marker does not match exact parent scope")
    nodes = virtual["nodes"]
    sinks = [node for node in nodes if node["mediaClass"] == "Audio/Sink"]
    sources = [node for node in nodes if node["mediaClass"] == "Audio/Source"]
    require(len(sinks) == 1 and len(sources) == (0 if matched else 1), "Unexpected playback/capture policy result")
    for node in nodes:
        properties = node["properties"]
        require(properties.get(MARKER) == (plan["nonce"] if matched else None), "Node marker escaped device scope")
        if node["mediaClass"] == "Audio/Sink":
            require(str(properties.get("audio.channels")) == "4" and properties.get("audio.position") == "FL,FR,RL,RR",
                    "Policy changed four-channel playback layout")
            require(any(value.get("channels") == 4 and value.get("position") == ["FL", "FR", "RL", "RR"]
                        for value in node["parameters"].get("EnumFormat", [])), "Four-channel playback format missing")
        priority = properties.get("priority.session")
        require(str(priority) == "1" if matched else str(priority) in ("1009", "2008"),
                "Unexpected session priority in scoped/foreign case")
    return dict(policyMatched=matched, playbackChannels=4, desktopCaptureNodes=len(sources), usbAssociationPreserved=True)


def monitor_evidence(text, plan):
    """Audit every published ALSA node, including transient nodes absent in snapshots."""
    require(len(text.encode()) <= 16*1024*1024, "Graph monitor exceeded bound")
    decoder, offset = json.JSONDecoder(), 0
    devices, nodes, seen_devices = {}, {}, set()
    while offset < len(text):
        offset += len(text[offset:]) - len(text[offset:].lstrip())
        if offset == len(text): break
        rows, offset = decoder.raw_decode(text, offset)
        require(isinstance(rows, list), "Graph monitor must contain JSON arrays")
        for row in rows:
            require(isinstance(row, dict) and type(row.get("id")) is int, "Malformed graph monitor object")
            identity = row["id"]
            if row.get("info", False) is None:
                devices.pop(identity, None)
                continue
            properties = row.get("info", {}).get("props", {})
            if row.get("type") == "PipeWire:Interface:Device" and properties.get("device.api") == "alsa":
                path = properties.get("device.sysfs.path", "")
                require(path.startswith(plan["actualParent"][4:]+"/"), "Foreign ALSA parent appeared in private graph")
                expected = plan["nonce"] if plan["matched"] else None
                require(properties.get(MARKER) == expected, "Published device marker is outside policy scope")
                serial = properties.get("object.serial")
                require(type(serial) is int, "Missing published device serial")
                devices[identity] = serial; seen_devices.add((identity, serial))
            if row.get("type") != "PipeWire:Interface:Node" or "api.alsa.pcm.card" not in properties:
                continue
            require(properties.get("device.id") in devices, "Published ALSA node has no attributed live card")
            serial = properties.get("object.serial")
            require(type(serial) is int, "Missing published node serial")
            kind = properties.get("media.class")
            require(kind in ("Audio/Sink", "Audio/Source"), "Unexpected ALSA node class")
            if plan["matched"]:
                require(kind == "Audio/Sink", "Capture node was published even transiently")
                require(properties.get(MARKER) == plan["nonce"] and str(properties.get("priority.session")) == "1",
                        "Playback was published before policy applied")
            else:
                require(MARKER not in properties and str(properties.get("priority.session")) in ("1009", "2008"),
                        "Foreign-parent negative control was modified")
            if kind == "Audio/Sink":
                require(str(properties.get("audio.channels")) == "4" and properties.get("audio.position") == "FL,FR,RL,RR",
                        "Published playback lost four-channel format")
            nodes[(identity, serial)] = kind
    counts = {kind:sum(value == kind for value in nodes.values()) for kind in ("Audio/Sink", "Audio/Source")}
    require(len(seen_devices) == 2 and counts == {"Audio/Sink":2, "Audio/Source":0 if plan["matched"] else 2},
            "Continuous graph evidence does not cover both complete policy epochs")
    return dict(deviceEpochs=2, publishedNodeCounts=counts, captureEverPublished=counts["Audio/Source"] > 0,
                scope="complete private pw-dump monitor; node publication, not delivered PCM")


class PolicyDesktop(PrivateDesktop):
    def __init__(self, *args, foreign=False):
        super().__init__(*args)
        parents, inventory = predict_parents()
        self.plan = dict(actualParent=parents[0], targetParent=parents[1 if foreign else 0],
            matched=not foreign, nonce=uuid.uuid4().hex, vhciBefore=inventory,
            productionPolicyModified=False, wireplumberFamily="0.4", pcmDeliveryValidated=False)

    def start_graph(self, explicit):
        from consumer_files import write_private_file
        content = configuration(self.plan["targetParent"], self.plan["nonce"])
        path = self.root / "home/.config/wireplumber/main.lua.d/90-madsense-private.lua"
        write_private_file(self, path, content)
        (self.output / "policy.lua").write_bytes(content.encode())
        self.plan.update(configurationSha256=hashlib.sha256(content.encode()).hexdigest(),
            installedBeforeGraphNs=time.monotonic_ns(), privatePath=str(path))
        self.result["privatePolicy"] = self.plan
        super().start_graph(explicit)
        self.result["distributionPolicyUnmodified"] = False
        self.result["audioDaemon"]["kind"] = "private-pipewire-wireplumber-scoped-policy-experiment"
        self.save()

    def bind_sound(self, parent, epoch):
        require(str(parent) == self.plan["actualParent"], "Lease took a different port; do not grant audio access")
        super().bind_sound(parent, epoch)

    def validate_virtual_nodes(self, virtual):
        return validate_virtual(virtual, self.plan)
