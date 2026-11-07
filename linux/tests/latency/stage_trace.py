"""Build-source overlay for attribution, never a production source mutation.

The caller supplies a fresh directory. Only committed source is exported;
specific hooks are inserted once and the original/changed hashes are retained.
The normal app and functional actors must still come from the untouched checkout.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[3]
PREFIX = "vendor/DualSenseClient/"
TRACE = "DualSenseClient.Emulation.Research.ReportTrace"
INPUT_TRACE = "MadSense.Linux.Research.InputTrace"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("Research hook no longer has one exact source match: " + old[:100])
    return text.replace(old, new, 1)


def input_overlays():
    trace = INPUT_TRACE
    return {
        "linux/platform/LinuxPhysicalSession.cs": [
            ("sealed record InputReport(byte[] Bytes, long ReceivedTimestamp, long Sequence);",
             "sealed record InputReport(byte[] Bytes, long ReceivedTimestamp, long Sequence, long ResearchBrokerReadNs);"),
            ("            var inputReport = new InputReport(report, timestamp, sequence);",
             f"            {trace}.Record(1, report, timestamp, brokerReadNs, input.Reader.Count);\n"
             "            var inputReport = new InputReport(report, timestamp, sequence, brokerReadNs);"),
            ("                lock (inputAvailable) Monitor.PulseAll(inputAvailable);\n                return true;",
             "                lock (inputAvailable) Monitor.PulseAll(inputAvailable);\n"
             f"                {trace}.Record(2, report.Bytes, report.ReceivedTimestamp, report.ResearchBrokerReadNs, input.Reader.Count);\n"
             "                return true;"),
            ("            Interlocked.Decrement(ref queued);\n            return false;",
             f"            {trace}.Record(3, report.Bytes, report.ReceivedTimestamp, report.ResearchBrokerReadNs, input.Reader.Count);\n"
             "            Interlocked.Decrement(ref queued);\n            return false;"),
            ("            BeginReading();\n            long deadline",
             f"            BeginReading();\n            {trace}.ReadStarting();\n            long deadline"),
            ("            Interlocked.Decrement(ref queued);\n            EnsureConnected();",
             "            Interlocked.Decrement(ref queued);\n"
             f"            {trace}.Record(4, report.Bytes, report.ReceivedTimestamp, report.ResearchBrokerReadNs, input.Reader.Count);\n"
             "            EnsureConnected();")],
        "linux/tests/app-integration/Program.cs": [
            ("        long queued = 0;", f"        {trace}.Start(output);\n        long queued = 0;"),
            ("                await bridge.DisposeAsync();",
             f"                await bridge.DisposeAsync();\n                {trace}.StopAndSave();")],
    }


def overlays(trace_kind="stages"):
    if trace_kind == "input":
        return input_overlays()
    if trace_kind != "stages":
        raise ValueError("Unknown research trace kind")
    return {
        PREFIX + "DualSenseClient.Controllers/Devices/DualSenseDevice.cs": [
            ("InputReport report = new InputReport(data, offset, usbRaw);",
             "InputReport report = new InputReport(data, offset, usbRaw);\n"
             f"        if (usbRaw is not null) {TRACE}.Record(1, usbRaw);")],
        PREFIX + "DualSenseClient.Emulation/Core/VirtualHidDevice.cs": [
            ("            _rawInputReport = snapshot;",
             f"            _rawInputReport = snapshot;\n            {TRACE}.Record(2, snapshot);")],
        PREFIX + "DualSenseClient.Emulation/UsbIp/UsbIpServer.cs": [
            # Delivery events (no payload): host URB arrival, worker scheduling and
            # start/exit, completion-gate entry, stale skip, URB-less and reportless visits.
            ("                MaxTransferLength = transferLength\n            });\n",
             "                MaxTransferLength = transferLength\n            });\n"
             f"            {TRACE}.Event(5, Volatile.Read(ref _inputGeneration), Volatile.Read(ref endpoint.LastDeliveredGeneration));\n"),
            ("            if (Interlocked.CompareExchange(ref _inputWorkerQueued, 1, 0) != 0) return;\n",
             "            if (Interlocked.CompareExchange(ref _inputWorkerQueued, 1, 0) != 0)\n"
             f"            {{ {TRACE}.Event(6, Volatile.Read(ref _inputGeneration), 0); return; }}\n"
             f"            {TRACE}.Event(6, Volatile.Read(ref _inputGeneration), 1);\n"),
            ("            long observed = Volatile.Read(ref _deliveryRequestVersion);\n            try\n",
             "            long observed = Volatile.Read(ref _deliveryRequestVersion);\n"
             f"            {TRACE}.Event(7, Volatile.Read(ref _inputGeneration), observed);\n            try\n"),
            ("                Interlocked.Exchange(ref _inputWorkerQueued, 0);\n",
             "                Interlocked.Exchange(ref _inputWorkerQueued, 0);\n"
             f"                {TRACE}.Event(11, Volatile.Read(ref _inputGeneration), observed != Volatile.Read(ref _deliveryRequestVersion) ? 1 : 0);\n"),
            ("                long generation = Volatile.Read(ref _inputGeneration);\n"
             "                if (onlyIfFresh && generation == endpoint.LastDeliveredGeneration) return;\n"
             "                CompleteInterruptLocked(endpoint, generation);\n",
             "                long generation = Volatile.Read(ref _inputGeneration);\n"
             f"                {TRACE}.Event(8, generation, endpoint.LastDeliveredGeneration, onlyIfFresh ? (byte)2 : (byte)1);\n"
             "                if (onlyIfFresh && generation == endpoint.LastDeliveredGeneration)\n"
             f"                {{ {TRACE}.Event(9, generation, endpoint.LastDeliveredGeneration, (byte)2); return; }}\n"
             "                CompleteInterruptLocked(endpoint, generation, onlyIfFresh);\n"),
            ("        private void CompleteInterruptLocked(InterruptEndpoint endpoint, long generation)\n        {\n\n"
             "            if (!endpoint.TryTakeNext(out UsbTransfer transfer))\n            {\n                return;\n            }\n",
             "        private void CompleteInterruptLocked(InterruptEndpoint endpoint, long generation, bool onlyIfFresh)\n        {\n\n"
             "            if (!endpoint.TryTakeNext(out UsbTransfer transfer))\n            {\n"
             f"                {TRACE}.Event(10, generation, endpoint.LastDeliveredGeneration, onlyIfFresh ? (byte)2 : (byte)1);\n"
             "                return;\n            }\n"),
            ("                if (count <= 0)\n                {\n"
             "                    endpoint.Block(transfer); // no report yet: the URB stays pending\n",
             "                if (count <= 0)\n                {\n"
             f"                    {TRACE}.Event(12, generation, endpoint.LastDeliveredGeneration, onlyIfFresh ? (byte)2 : (byte)1);\n"
             "                    endpoint.Block(transfer); // no report yet: the URB stays pending\n"),
            ("                int actualLength = Math.Min(count, payload.Length);\n"
             "                SendRetSubmit(transfer.SeqNum, UsbIpStatus.Success, payload.AsMemory(0, actualLength));\n"
             "                Volatile.Write(ref endpoint.LastDeliveredGeneration, generation);",
             "                int actualLength = Math.Min(count, payload.Length);\n"
             f"                {TRACE}.Record(3, payload.AsSpan(0, actualLength), generation, onlyIfFresh ? (byte)2 : (byte)1);\n"
             "                SendRetSubmit(transfer.SeqNum, UsbIpStatus.Success, payload.AsMemory(0, actualLength));\n"
             f"                {TRACE}.Record(4, payload.AsSpan(0, actualLength), generation, onlyIfFresh ? (byte)2 : (byte)1);\n"
             "                Volatile.Write(ref endpoint.LastDeliveredGeneration, generation);")],
        "linux/tests/app-integration/Program.cs": [
            ("        long queued = 0;", f"        {TRACE}.Start(output);\n        long queued = 0;"),
            ("                await bridge.DisposeAsync();",
             f"                await bridge.DisposeAsync();\n                {TRACE}.StopAndSave();")],
    }


def recorder_paths(trace_kind="stages"):
    if trace_kind == "input":
        return ("linux/tests/latency/trace/InputTrace.cs", "linux/platform/ResearchInputTrace.cs")
    if trace_kind == "stages":
        return ("linux/tests/latency/trace/ReportTrace.cs", PREFIX + "DualSenseClient.Emulation/ResearchReportTrace.cs")
    raise ValueError("Unknown research trace kind")


def stage(destination, root=ROOT, trace_kind="stages"):
    destination = Path(destination).resolve()
    root = Path(root).resolve()
    if destination == root or destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError("The overlay must be outside the source checkout.")
    if destination.exists():
        raise ValueError("Fresh overlay directory required.")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    data = subprocess.check_output(["git", "archive", "--format=tar", revision], cwd=root)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for entry in archive.getmembers():
            if not (entry.isfile() or entry.isdir()):
                raise ValueError("The source overlay does not accept links or special entries.")
        destination.mkdir(parents=True)
        archive.extractall(destination, filter="data")
    changes = {}
    for relative, edits in overlays(trace_kind).items():
        path = destination / relative
        original = path.read_bytes()
        text = original.decode("utf-8").replace("\r\n", "\n")
        for old, new in edits:
            text = replace_once(text, old, new)
        path.write_text(text, encoding="utf-8", newline="\n")
        changes[relative] = dict(originalSha256=digest(original), instrumentedSha256=digest(path.read_bytes()))
    # The recorder is taken from the same committed archive, never an unstaged
    # developer file. SDK globs include it only in the selected copied project.
    recorder_name, target_name = recorder_paths(trace_kind)
    recorder = destination / recorder_name
    target = destination / target_name
    if target.exists():
        raise ValueError("Research recorder collides with an existing source file.")
    target.write_bytes(recorder.read_bytes())
    changes[target.relative_to(destination).as_posix()] = dict(originalSha256=None,
        instrumentedSha256=digest(target.read_bytes()), copiedFrom=recorder.relative_to(destination).as_posix())
    value = dict(schema=1, sourceCommit=revision, kind="instrumented-research-copy",
                 productionBinary=False, sourceArchiveSha256=digest(data), changes=changes)
    if trace_kind != "stages":
        value["traceKind"] = trace_kind
    (destination / "research-trace-overlay.json").write_text(json.dumps(value, indent=2) + "\n")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--kind", choices=("stages", "input"), default="stages")
    options = parser.parse_args()
    print(json.dumps(stage(options.output, trace_kind=options.kind), indent=2))
