"""Private stock PipeWire graph with a client-owned, preconfigured ALSA adapter."""
import json
import os
from pathlib import Path
import signal
import time

from export_policy import PREFIX, require, validate_node, alsa_node, one_shot_snapshot, snapshot_source
from desktop import PrivateDesktop
from evidence import compare
from pipewire_defaults import check_peer

class ExportDesktop(PrivateDesktop):
    def __init__(self, *args, plan, executable):
        super().__init__(*args)
        self.plan, self.executable = plan, Path(executable)
        self.exporter = self.lifetime_write = None
        self.exports = []
        self.expected_export = False

    def bind_sound(self, parent, epoch):
        require(str(parent) == self.plan["actualParent"], "Lease differs from predicted exact parent")
        # Read and verify identity/ignore state before any ACL is granted.
        from kernel_probe import wait_for
        cards = wait_for(lambda: list(Path("/sys/class/sound").glob("card[0-9]*")), 15)
        require(len(cards) == 1 and cards[0].resolve().is_relative_to(parent), "Unexpected sound card")
        interface = parent / (parent.name+":1.0")
        interface_text = (interface / "interface").read_text()
        require(interface_text.strip() == PREFIX+self.plan["nonce"], "Interface nonce was not served")
        (self.output / f"interface-{epoch}.txt").write_text(interface_text)
        self.root_command(["udevadm", "settle", "--timeout=5"])
        completed = self.root_command(["udevadm", "info", "--query=property", "--path", str(cards[0].resolve())])
        # root_command returns bytes in the existing ConsumerSession contract.
        raw = completed.stdout if hasattr(completed, "stdout") else completed
        if isinstance(raw, bytes): raw = raw.decode()
        properties = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        expected = "1" if self.plan["matched"] else None
        require(properties.get("ACP_IGNORE") == expected and properties.get("PULSE_IGNORE") == expected
            and properties.get("MADSENSE_AUDIO_LEASE") == (self.plan["nonce"] if self.plan["matched"] else None),
            "Udev ignore scope differs from the exact parent/nonce")
        (self.output / f"udev-card-{epoch}.json").write_text(json.dumps(properties, indent=2)+"\n")
        (self.output / f"usb-descriptors-{epoch}.bin").write_bytes((parent / "descriptors").read_bytes())
        super().bind_sound(parent, epoch)
        self.result["arrivals"][-1]["udevProperties"] = properties

    def start_export(self, label):
        require(self.exporter is None and self.plan["matched"] and self.parent is not None, "Unexpected export state")
        if not hasattr(self, "program"): self.program = self.stage(self.executable)
        read_fd, self.lifetime_write = os.pipe()
        try:
            self.exporter = self.start(label, [self.program, self.token, self.parent, self.card_sysfs,
                self.plan["nonce"], str(read_fd)], pass_fds=(read_fd,))
        finally: os.close(read_fd)
        self.expected_export = True
        self.exports.append(dict(label=label, pid=self.exporter.pid, startedMonotonicNs=time.monotonic_ns(),
            epoch=self.epoch, usbParent=str(self.parent), cardSysfs=str(self.card_sysfs)))
        self.result["exports"] = self.exports; self.save()

    def stop_export(self, crash=False):
        require(self.exporter is not None, "No export client to stop")
        if crash: self.exporter.kill()
        if self.lifetime_write is not None:
            os.close(self.lifetime_write); self.lifetime_write = None
        try: self.exporter.wait(timeout=10)
        except BaseException:
            self.exporter.kill(); self.exporter.wait(timeout=5); raise
        require(self.exporter.returncode == (-signal.SIGKILL if crash else 0), "Export client exited abnormally")
        self.exports[-1].update(stopKind="sigkill" if crash else "owner-eof", exitCode=self.exporter.returncode,
            stoppedMonotonicNs=time.monotonic_ns())
        self.exporter = None; self.expected_export = False; self.save()

    def capture_snapshot(self, label):
        self.check_device(); check_peer(self, self.process)
        require(self.manager.poll() is None, "Private WirePlumber exited")
        _, raw = self.command(label+"-pw-dump", ["pw-dump", "--no-colors", "--remote", self.env["PIPEWIRE_REMOTE"]], timeout=15)
        snapshot = one_shot_snapshot(raw)
        normalized = (json.dumps(snapshot, indent=2)+"\n").encode()
        (self.output / (label+".json")).write_bytes(normalized)
        self.result.setdefault("snapshotSources", {})[label] = snapshot_source(raw, normalized)
        self.save()
        return snapshot

    def observe(self, label, attached):
        if not self.plan["matched"]: return super().observe(label, attached)
        self.check_monitors()
        snapshot = self.capture_snapshot(label)
        value = compare(snapshot, self.controls)
        owned = []
        for row in snapshot:
            p = row.get("info", {}).get("props", {})
            if row.get("type") == "PipeWire:Interface:Device":
                require(p.get("device.api") != "alsa", "Automatic ALSA card escaped ignore")
            if row.get("type") != "PipeWire:Interface:Node": continue
            if alsa_node(p):
                owned.append(validate_node(row, self.plan, str(self.card_sysfs) if attached else None, require_format=True))
        require(len(owned) == int(self.expected_export), "Missing exported sink or automatic duplicate")
        if self.expected_export:
            require(self.exporter.poll() is None, "Export client exited")
            client = [row for row in snapshot if row.get("type") == "PipeWire:Interface:Client"
                and row.get("id") == owned[0]["clientId"]]
            require(len(client) == 1 and str(client[0].get("info", {}).get("props", {}).get("application.process.id"))
                == str(self.exporter.pid), "Node is not owned by this ordinary client")
            value["export"] = owned[0]
            self.exports[-1]["nodeIdentity"] = {key:owned[0][key] for key in ("id", "serial", "clientId")}
        value.update(label=label, attached=attached, expectedExport=self.expected_export,
            monotonicNs=time.monotonic_ns(), metadataBytes=(self.output / "metadata-monitor.stdout").stat().st_size)
        self.result.setdefault("observations", []).append(value); self.save()
        return value

    def close(self):
        if self.exporter is not None:
            try: self.stop_export()
            except BaseException as error: self.result["exportCleanupError"] = str(error)
        return super().close()
