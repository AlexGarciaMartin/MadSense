#!/usr/bin/env python3
"""Isolated read-only security-policy comparison; no physical device required.

Builds only research executables. A denied API, SIGKILL, or timeout is recorded
as evidence, never converted into successful virtual-controller validation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / ".build" / "hid-access"
OUT = ROOT / "dist" / "hid-access"


def redact(text: str) -> str:
    return re.sub(r"/Users/[^/\s]+", "/Users/<redacted>", text)


def invoke(command: list[str], stem: str, timeout: int = 15) -> dict:
    """Run one owned process group, killing that group only on timeout."""
    started = time.monotonic()
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = child.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except PermissionError:
            # A sudo child may be root. The negative ID is exactly the process
            # group we just created, not a name match or unrelated process.
            subprocess.run(["sudo", "-n", "/bin/kill", "-KILL", "--", f"-{child.pid}"],
                           capture_output=True, timeout=3, check=False)
        except ProcessLookupError:
            pass
        stdout, stderr = child.communicate(timeout=5)
    (OUT / f"{stem}.stdout").write_text(redact(stdout.decode(errors="replace")))
    (OUT / f"{stem}.stderr").write_text(redact(stderr.decode(errors="replace")))
    result = {"exitCode": child.returncode, "timedOut": timed_out,
              "nativeAlarmExpired": child.returncode in [-signal.SIGALRM, 128 + signal.SIGALRM],
              "elapsedSeconds": round(time.monotonic() - started, 3)}
    (OUT / f"{stem}.result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def descriptors() -> dict[str, Path]:
    # Two centered signed axes and eight released buttons. No keyboard/mouse
    # usages, output reports, or physical device input are involved.
    minimal = bytes.fromhex("05 01 09 04 a1 01 05 01 09 30 09 31 15 81 25 7f "
                            "75 08 95 02 81 02 05 09 19 01 29 08 15 00 25 01 "
                            "75 01 95 08 81 02 c0")
    source = ROOT / "vendor/DualSenseClient/DualSenseClient.Emulation/Devices/DualSense/DualSenseDescriptorSet.cs"
    match = re.search(r"private static byte\[\] CreateHidReportDescriptor\(\)\s*\{\s*return\s*\[(.*?)\];",
                      source.read_text(encoding="utf-8-sig"), re.S)
    if not match:
        raise RuntimeError("Cannot locate the current DualSense descriptor; refusing a guessed fixture")
    body = re.sub(r"//[^\n]*|/\*.*?\*/", "", match.group(1), flags=re.S)
    literals = re.findall(r"0x[0-9a-fA-F]{2}\b", body)
    if re.sub(r"0x[0-9a-fA-F]{2}\b|[\s,]", "", body):
        raise RuntimeError("Descriptor contains nonliteral bytes; extraction needs review")
    dualsense = bytes(int(value, 16) for value in literals)
    if len(dualsense) != 321:
        raise RuntimeError(f"Expected current 321-byte descriptor, got {len(dualsense)}")
    paths = {}
    manifest = {}
    for name, content in {"minimal": minimal, "dualsense": dualsense}.items():
        path = BUILD / f"{name}.bin"
        path.write_bytes(content)
        paths[name] = path
        manifest[name] = {"length": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    (OUT / "descriptors.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return paths


def main() -> int:
    if sys.platform != "darwin":
        raise RuntimeError("This access experiment must run on macOS")
    BUILD.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for name, command in {
        "os": ["sw_vers"], "architecture": ["uname", "-m"],
        "sip-read-only": ["csrutil", "status"], "sdk": ["xcrun", "--show-sdk-version"],
        "swift": ["xcrun", "swiftc", "--version"],
    }.items():
        invoke(command, name)
    data = descriptors()
    programs = {
        "iohid": ("hid-access-probe", ["xcrun", "clang++", "-std=c++17", "-fblocks", "-mmacosx-version-min=15.0",
                   "macos/research/hid_access_probe.mm", "-framework", "Foundation", "-framework", "IOKit"]),
        "corehid": ("corehid-access-probe", ["xcrun", "swiftc", "-parse-as-library", "-O",
                     "macos/research/hid_access_probe.swift"]),
    }
    compiled = {}
    compile_failed = False
    os.chdir(ROOT)
    for api, (name, command) in programs.items():
        executable = BUILD / name
        result = invoke(command + ["-o", str(executable)], f"compile-{api}", timeout=90)
        if result["exitCode"] != 0 or result["timedOut"]:
            compile_failed = True
        else:
            compiled[api] = executable
    entitlement = BUILD / "declared-hid.entitlements"
    entitlement.write_bytes(plistlib.dumps({"com.apple.developer.hid.virtual.device": True}))
    all_results = []
    for api, original in compiled.items():
        for signature in ["adhoc-no-entitlement", "adhoc-declared-entitlement"]:
            directory = BUILD / api / signature
            directory.mkdir(parents=True, exist_ok=True)
            executable = directory / original.name
            shutil.copy2(original, executable)
            command = ["codesign", "--force", "--sign", "-"]
            if signature == "adhoc-declared-entitlement":
                command += ["--entitlements", str(entitlement)]
            result = invoke(command + [str(executable)], f"sign-{api}-{signature}")
            if result["exitCode"] != 0:
                compile_failed = True
                continue
            invoke(["codesign", "--display", "--verbose=4", "--entitlements", ":-", str(executable)],
                   f"signature-{api}-{signature}")
            for descriptor, path in data.items():
                for privilege in ["user", "root"]:
                    stem = f"{api}-{signature}-{descriptor}-{privilege}"
                    command = [str(executable), str(path), descriptor]
                    if privilege == "root":
                        command = ["sudo", "-n", "--"] + command
                    result = invoke(command, stem)
                    record = {"api": api, "signature": signature, "descriptor": descriptor,
                              "privilege": privilege, **result}
                    all_results.append(record)
                    print(json.dumps(record), flush=True)
    # Restrict diagnostic capture to this probe's process names/messages. Do not
    # dump IORegistry, user devices, signing identities, or the whole system log.
    predicate = '(eventMessage CONTAINS[c] "hid-access-probe") OR (process == "hid-access-probe") OR (process == "corehid-access-probe")'
    invoke(["sudo", "-n", "log", "show", "--last", "10m", "--style", "compact", "--predicate", predicate],
           "kernel-gate-evidence", timeout=30)
    summary = {"scope": "Access experiment only; rejection is evidence, not successful controller validation",
               "securitySettingsChanged": False, "buildFailure": compile_failed, "cases": all_results}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 1 if compile_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
