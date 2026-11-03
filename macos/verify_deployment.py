#!/usr/bin/env python3
"""Verify the actual selected Mach-O slices, not just compiler command lines.

Reads Apple's mach_header[_64], fat_arch[_64], LC_BUILD_VERSION and
LC_VERSION_MIN_MACOSX layouts. No third-party Python packages or execution of
the inspected files is needed, so the checks can also be tested on Windows.
"""
import argparse
import json
from pathlib import Path
import plistlib
import struct
import sys

THIN = {b"\xcf\xfa\xed\xfe": ("<", 32), b"\xfe\xed\xfa\xcf": (">", 32),
        b"\xce\xfa\xed\xfe": ("<", 28), b"\xfe\xed\xfa\xce": (">", 28)}
FAT = {b"\xca\xfe\xba\xbe": (">", 20), b"\xbe\xba\xfe\xca": ("<", 20),
       b"\xca\xfe\xba\xbf": (">", 32), b"\xbf\xba\xfe\xca": ("<", 32)}
CPUS = {"arm64": (0x0100000C, 0), "x86_64": (0x01000007, 3)}


def version(text):
    parts = tuple(int(part) for part in text.split("."))
    if not 1 <= len(parts) <= 3 or any(part < 0 for part in parts):
        raise ValueError("invalid macOS version: " + text)
    return parts + (0,) * (3 - len(parts))


def version_text(value):
    return f"{value >> 16}.{(value >> 8) & 255}.{value & 255}"


def inspect_macho(data, architecture):
    """Return selected-slice metadata, None for resources; fail closed on Mach-O."""
    magic = data[:4]
    expected_cpu, expected_subtype = CPUS[architecture]
    if magic in FAT:
        endian, entry_size = FAT[magic]
        if len(data) < 8:
            raise ValueError("truncated universal header")
        count = struct.unpack_from(endian + "I", data, 4)[0]
        if count > (len(data) - 8) // entry_size:
            raise ValueError("truncated universal slice table")
        selected = []
        for i in range(count):
            position = 8 + i * entry_size
            cpu, subtype = struct.unpack_from(endian + "II", data, position)
            offset, length = struct.unpack_from(endian + ("II" if entry_size == 20 else "QQ"), data, position + 8)
            if offset < 8 + count * entry_size or length < 28 or offset + length > len(data):
                raise ValueError("invalid universal slice bounds")
            if cpu == expected_cpu and subtype & 0x00FFFFFF == expected_subtype:
                selected.append(data[offset:offset + length])
        if len(selected) != 1 or selected[0][:4] not in THIN:
            raise ValueError("expected exactly one " + architecture + " Mach-O slice")
        return inspect_macho(selected[0], architecture)
    if magic not in THIN:
        return None
    endian, header_size = THIN[magic]
    if len(data) < header_size:
        raise ValueError("truncated Mach-O header")
    cpu, subtype, _, commands, command_bytes = struct.unpack_from(endian + "IIIII", data, 4)
    if cpu != expected_cpu or subtype & 0x00FFFFFF != expected_subtype:
        raise ValueError("binary does not contain the declared " + architecture + " architecture")
    if command_bytes > len(data) - header_size or commands > command_bytes // 8:
        raise ValueError("invalid Mach-O load command table")
    position, end = header_size, header_size + command_bytes
    requirements = []
    for _ in range(commands):
        if position + 8 > end:
            raise ValueError("truncated Mach-O load command")
        command, size = struct.unpack_from(endian + "II", data, position)
        if size < 8 or position + size > end:
            raise ValueError("invalid Mach-O load command size")
        if command == 0x32:  # LC_BUILD_VERSION
            if size < 24:
                raise ValueError("truncated LC_BUILD_VERSION")
            platform, minimum, sdk = struct.unpack_from(endian + "III", data, position + 8)
            if platform != 1:
                raise ValueError("selected binary targets a non-macOS platform")
            requirements.append((minimum, sdk, "LC_BUILD_VERSION"))
        elif command == 0x24:  # LC_VERSION_MIN_MACOSX
            if size < 16:
                raise ValueError("truncated LC_VERSION_MIN_MACOSX")
            minimum, sdk = struct.unpack_from(endian + "II", data, position + 8)
            requirements.append((minimum, sdk, "LC_VERSION_MIN_MACOSX"))
        position += size
    if position != end or not requirements:
        raise ValueError("missing or malformed macOS minimum-version metadata")
    minimum, sdk, command = max(requirements)
    return {"architecture": architecture, "minimum_os": version_text(minimum),
            "sdk": version_text(sdk), "load_command": command}


def verify(roots, architecture, minimum):
    report = {"schema_version": 1, "architecture": architecture,
              "declared_minimum_os": minimum, "binaries": [], "errors": []}
    for supplied in roots:
        root = Path(supplied).resolve()
        if not root.is_dir():
            report["errors"].append(f"Missing package directory: {root}")
            continue
        plist = root / "Contents" / "Info.plist"
        if root.suffix == ".app":
            try:
                declared = plistlib.loads(plist.read_bytes()).get("LSMinimumSystemVersion", "")
                if not declared or version(declared) != version(minimum):
                    raise ValueError(f"LSMinimumSystemVersion differs from {minimum}")
            except (OSError, ValueError, plistlib.InvalidFileException) as error:
                report["errors"].append(f"{root.name}: invalid Info.plist: {error}")
        before = len(report["binaries"])
        for path in sorted(root.rglob("*")):
            if path.is_symlink() and not path.resolve().is_relative_to(root):
                report["errors"].append(f"{root.name}/{path.relative_to(root)}: package symlink points outside its root")
                continue
            if not path.is_file():
                continue
            relative = (Path(root.name) / path.relative_to(root)).as_posix()
            try:
                if not path.resolve().is_relative_to(root):
                    raise ValueError("package symlink points outside its root")
                with path.open("rb") as source:
                    magic = source.read(4)
                    if magic not in THIN and magic not in FAT:
                        continue
                    source.seek(0)
                    metadata = inspect_macho(source.read(), architecture)
                metadata["path"] = relative
                report["binaries"].append(metadata)
                if version(metadata["minimum_os"]) > version(minimum):
                    raise ValueError(f"requires macOS {metadata['minimum_os']}, above declared {minimum}")
            except (OSError, ValueError, struct.error) as error:
                report["errors"].append(f"{relative}: {error}")
        if len(report["binaries"]) == before:
            report["errors"].append(f"{root.name}: no compatible Mach-O binaries found")
    report["maximum_binary_minimum_os"] = max(
        (item["minimum_os"] for item in report["binaries"]), key=version, default=None)
    report["compatible"] = not report["errors"]
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=CPUS, required=True)
    parser.add_argument("--minimum", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("roots", nargs="+")
    args = parser.parse_args()
    version(args.minimum)
    report = verify(args.roots, args.arch, args.minimum)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for error in report["errors"]:
        print(error, file=sys.stderr)
    print(f"Deployment check: {len(report['binaries'])} Mach-O files, macOS {args.minimum}, {args.arch}; "
          f"{'passed' if report['compatible'] else 'FAILED'}")
    return 0 if report["compatible"] else 1


if __name__ == "__main__":
    sys.exit(main())
