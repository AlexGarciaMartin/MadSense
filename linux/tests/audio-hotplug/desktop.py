"""Private audio UID exists before attach; only exact arriving sound nodes get ACLs."""
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import uuid

from evidence import control_nodes, compare, metadata_changes, preservation
from consumer_session import ConsumerSession, require
from pipewire_defaults import private_stack, capture, select_device, describe_nodes


class PrivateDesktop(ConsumerSession):
    # Reuse the already tested UID/process/ACL cleanup mechanics. Its ordinary
    # ready-bound __enter__ cannot start before a USB exists, so setup is separate.
    def __init__(self, token, physical_parent, output):
        self.token, self.output = token, Path(output)
        self.physical_parent = Path(physical_parent).resolve(strict=True)
        self.root = self.uid = self.gid = self.parent = None
        self.user = "madsense-audio-"+uuid.uuid4().hex[:12]
        self.closed = False
        self.grants, self.processes, self.logs = [], [], []
        self.result = dict(schema=1, result="failed", consumers=[], cases=[], cleanup=[], grants=self.grants,
            retiredSoundEpochs=[], physicalDevices=False, gamesValidated=False, physicalLatencyValidated=False,
            protonValidated=False, scope="private-audio-hotplug", ownedGuestToken=token, noPhysicalAccessGranted=True)

    def __enter__(self):
        import pwd
        from ci_environment import validate_environment
        require(validate_environment(self.token)["kind"] == "owned-qemu-guest", "Owned QEMU required")
        require(os.geteuid() == 0 and str(self.physical_parent).startswith("/sys/devices/virtual/misc/uhid/"), "Owned guest orchestrator required")
        self.output.mkdir()
        try:
            self.root = Path(tempfile.mkdtemp(prefix="madsense-consumers-", dir="/opt")); self.root.chmod(0o755)
            home = self.root / "home"
            self.root_command(["useradd", "--no-create-home", "--user-group", "--home-dir", home,
                "--shell", "/usr/sbin/nologin", self.user])
            account = pwd.getpwnam(self.user); self.uid, self.gid = account.pw_uid, account.pw_gid
            require(self.uid > 0 and self.gid > 0, "Ordinary audio UID required")
            for path in (home, self.root / "runtime"):
                path.mkdir(mode=0o700); os.chown(path, self.uid, self.gid)
            self.env = dict(PATH="/usr/bin:/bin", HOME=str(home), LANG="C.UTF-8", USER=self.user, LOGNAME=self.user,
                XDG_RUNTIME_DIR=str(self.root / "runtime"), MADSENSE_CONSUMER_ISOLATED=self.token)
            self.result["account"] = dict(uid=self.uid, gid=self.gid, supplementaryGroups=[])
            self.audit_access([])
            self.save()
            return self
        except BaseException:
            self.close(); raise

    def check_device(self):
        require(self.physical_parent.exists(), "Synthetic physical fixture disappeared unexpectedly")
        cards = list(Path("/sys/class/sound").glob("card[0-9]*"))
        if self.parent is None:
            require(not cards, "Sound device exists outside a bound epoch")
        else:
            value = self.parent.stat()
            require((value.st_dev, value.st_ino) == self.parent_identity, "Virtual USB parent replaced")
            require(all(path.resolve().is_relative_to(self.parent) for path in cards), "Foreign ALSA device appeared")

    def bind_sound(self, parent, epoch):
        from kernel_probe import wait_for
        require(self.parent is None and not self.grants, "Prior sound epoch remains bound")
        parent = Path(parent).resolve(strict=True)
        require(parent.is_relative_to(Path("/sys/devices/platform")) and "vhci_hcd" in str(parent)
                and not parent.is_relative_to(self.physical_parent), "Only owned virtual USB ancestry is eligible")
        value = parent.stat(); self.parent = parent; self.parent_identity = (value.st_dev, value.st_ino)
        self.epoch = epoch
        cards = wait_for(lambda: list(Path("/sys/class/sound").glob("card[0-9]*")), 15)
        require(len(cards) == 1 and cards[0].resolve().is_relative_to(parent), "Expected only the exact owned ALSA card")
        self.card = int(cards[0].name[4:]); self.card_sysfs = cards[0].resolve()
        # udev settles before ACL snapshots; monitor is already alive. SPA's
        # IN_ATTRIB access watcher sees these grants, without policy or restart.
        self.root_command(["udevadm", "settle", "--timeout=5"])
        nodes = []
        for path in Path("/sys/class/sound").glob("*"):
            if path.name.startswith((f"controlC{self.card}", f"pcmC{self.card}D")) and path.resolve().is_relative_to(parent):
                node = Path("/dev/snd", path.name)
                if node.exists(): nodes.append(node)
        require(any(path.name.startswith("control") for path in nodes)
                and any(path.name.endswith("p") for path in nodes) and any(path.name.endswith("c") for path in nodes),
                "Complete playback/capture/control sound nodes required")
        before = time.monotonic_ns()
        for node in sorted(nodes): self.grant(node)
        self.audit_access(nodes)
        self.result.setdefault("arrivals", []).append(dict(epoch=epoch, usbParent=str(parent),
            parentIdentity=list(self.parent_identity), card=self.card, cardSysfs=str(self.card_sysfs),
            grantedNodes=list(map(str, nodes)), aclStartedNs=before, aclFinishedNs=time.monotonic_ns()))
        self.save()

    def retire_removed_sound(self):
        require(self.parent is not None and not self.parent.exists(), "USB must be removed before retiring sound ACL records")
        for record in self.grants:
            require(not os.path.lexists(record["path"]), "Removed sound node name exists; never restore ACL onto replacement")
        self.result["retiredSoundEpochs"].append(dict(epoch=self.epoch, usbParent=str(self.parent),
            nodesDestroyed=True, grants=self.grants, retiredMonotonicNs=time.monotonic_ns()))
        self.grants = []; self.result["grants"] = self.grants; self.parent = None
        self.audit_access([]); self.save()

    def start_graph(self, explicit):
        from kernel_probe import wait_for
        self.process, self.manager = private_stack(self)
        self.graph_started_ns = time.monotonic_ns()
        self.names = {kind: "madsense-control-"+kind+"-"+uuid.uuid4().hex[:12] for kind in ("sink", "source")}
        for kind, name in self.names.items():
            # wpctl 0.4 only accepts exact Audio/Source for explicit selection.
            # It remains a software-only adapter (node.virtual, no ALSA parent).
            media = "Audio/Sink" if kind == "sink" else "Audio/Source"
            properties = ('{ factory.name = support.null-audio-sink node.name = "'+name+'" '
                'node.description = "Owned audio control" media.class = "'+media+'" '
                'audio.position = [ FL FR ] audio.channels = 2 object.linger = true node.virtual = true '
                'monitor.passthrough = true priority.session = '+str(900 if kind == "sink" else 1900)+' }')
            self.command("create-control-"+kind, ["pw-cli", "--remote", self.env["PIPEWIRE_REMOTE"], "create-node", "adapter", properties])
        initialize_attempt = 0
        def initialized():
            nonlocal initialize_attempt
            initialize_attempt += 1
            snapshot = capture(self, self.process, self.manager, f"initializing-{initialize_attempt:02}")
            try:
                controls = control_nodes(snapshot, self.names)
                value = compare(snapshot, controls)
            except RuntimeError: return None
            return controls if all(value["defaultPreserved"].values()) else None
        self.controls = wait_for(initialized, 15)
        if explicit:
            for kind, node in self.controls.items(): self.command("select-control-"+kind, ["wpctl", "set-default", str(node["id"])])
        self.monitor = self.start("metadata-monitor", ["pw-metadata", "--remote", self.env["PIPEWIRE_REMOTE"], "--monitor", "--name", "default"])
        self.dump_monitor = self.start("graph-monitor", ["pw-dump", "--remote", self.env["PIPEWIRE_REMOTE"], "--no-colors", "--monitor"])
        def monitoring():
            self.check_monitors()
            try: return metadata_changes((self.output / "metadata-monitor.stdout").read_text(), self.controls)
            except (RuntimeError, json.JSONDecodeError): return None
        wait_for(monitoring, 10)
        value = self.observe("before-attach", False)
        require(all(value["defaultPreserved"].values()), "Controls are not defaults before attach")
        require(all(value["defaults"][kind]["configuredName"] == (node["name"] if explicit else None)
                    for kind, node in self.controls.items()), "Unexpected initial configured default")
        self.result.update(controlNodes=self.controls, selection="explicit" if explicit else "automatic",
            graphStartedMonotonicNs=self.graph_started_ns, distributionPolicyUnmodified=True,
            controlPriorities=dict(sink=900, source=1900), realDesktopSessionValidated=False)
        self.save()

    def check_monitors(self):
        require(self.monitor.poll() is None and self.dump_monitor.poll() is None, "Audio monitor exited")
        require((self.output / "metadata-monitor.stdout").stat().st_size <= 1024*1024
                and (self.output / "graph-monitor.stdout").stat().st_size <= 16*1024*1024, "Audio monitor evidence exceeded bound")

    def observe(self, label, attached):
        self.check_monitors()
        snapshot = capture(self, self.process, self.manager, label)
        value = compare(snapshot, self.controls)
        if attached:
            card = select_device(snapshot, self.card, self.card_sysfs)
            value["virtual"] = dict(usbParent=str(self.parent), card=self.card, cardSysfs=str(self.card_sysfs),
                deviceId=card["id"], deviceSerial=int(card["info"]["props"]["object.serial"]),
                deviceProperties=card["info"]["props"], deviceParameters=card["info"].get("params", {}),
                nodes=describe_nodes(snapshot, card))
            self.validate_virtual_nodes(value["virtual"])
        else:
            require(not [row for row in snapshot if row.get("type", "").endswith(":Device") and row.get("info", {}).get("props", {}).get("device.api") == "alsa"],
                    "ALSA device remains in graph after detach")
        value.update(label=label, monotonicNs=time.monotonic_ns(), metadataBytes=(self.output / "metadata-monitor.stdout").stat().st_size)
        self.result.setdefault("observations", []).append(value); self.save()
        return value

    def validate_virtual_nodes(self, virtual):
        require(any(row["mediaClass"] == "Audio/Sink" for row in virtual["nodes"])
                and any(row["mediaClass"] == "Audio/Source" for row in virtual["nodes"]), "Both virtual audio nodes required")

    def settle(self, label, attached):
        from kernel_probe import wait_for
        previous, serial = None, 0
        def stable():
            nonlocal previous, serial
            serial += 1
            try: value = self.observe(label+f"-{serial:02}", attached)
            except RuntimeError as error:
                # Missing ALSA nodes/defaults while policy settles is retained
                # in raw snapshots; daemon/identity failures still hit deadline.
                self.result.setdefault("settling", []).append(str(error)); self.save(); return None
            compared = {key:value.get(key) for key in ("defaults", "virtual", "defaultPreserved")}
            same = compared == previous; previous = compared
            return value if same else None
        return wait_for(stable, 15)

    def finish_observation(self):
        self.check_monitors()
        changes = metadata_changes((self.output / "metadata-monitor.stdout").read_text(), self.controls)
        preserved = preservation(self.result["observations"], changes)
        self.result.update(result="observed", metadataEvents=changes, preservationByKind=preserved,
            defaultPreservationObserved=all(preserved.values()))
        self.save()

    def close(self):
        if self.parent is not None and not self.parent.exists():
            try: self.retire_removed_sound()
            except BaseException as error:
                self.result["retirementError"] = str(error)  # Existing close remains fail-closed for live/reused nodes.
        return super().close()
