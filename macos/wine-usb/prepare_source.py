#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare a NEW, opt-in Wine USB source tree; never modify an installed runner."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen

KIT = Path(__file__).resolve().parent


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verified(data: bytes, expected: str, label: str) -> bytes:
    actual = digest(data)
    if actual != expected:
        raise ValueError(f"SHA256 mismatch for {label}: expected {expected}, got {actual}")
    return data


def fetch(url: str) -> bytes:
    with urlopen(url, timeout=30) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Unexpectedly large source download")
    return data


def git(directory: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(directory), *args], check=True,
                          capture_output=True, text=True, timeout=120).stdout.strip()


def load_manifest() -> dict:
    manifest = json.loads((KIT / "manifest.json").read_text(encoding="utf-8"))
    for patch in manifest["patches"]:
        verified((KIT / patch["path"]).read_bytes(), patch["sha256"], patch["path"])
    return manifest


def prepare(output: Path, source_tree: Path | None = None, downloader=fetch) -> dict:
    """No mutation occurs until the destination and every source hash pass."""
    output = output.expanduser().absolute()
    if os.path.lexists(output):
        raise ValueError("Output already exists; choose a NEW directory")
    output = output.resolve()
    if not output.parent.is_dir():
        raise ValueError("Output parent must already exist")
    manifest = load_manifest()
    original = {}
    if source_tree is not None:
        source_tree = source_tree.expanduser().resolve(strict=True)
        if output.is_relative_to(source_tree) or source_tree.is_relative_to(output):
            raise ValueError("Output must be separate from the input checkout")
        if git(source_tree, "rev-parse", "HEAD") != manifest["wine_commit"]:
            raise ValueError("Input is not the pinned public Wine commit")
        if git(source_tree, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("Input Wine checkout must be clean")
    for source in manifest["sources"]:
        if source_tree is None:
            data = downloader(source["url"])
        else:
            path = source_tree / source["path"]
            if path.is_symlink() or not path.resolve().is_relative_to(source_tree):
                raise ValueError("Source file must be a regular file inside the checkout")
            data = path.read_bytes()
        original[source["path"]] = verified(data, source["sha256"], source["path"])

    output.mkdir()  # Exclusively creates a new directory; no overwrite mode.
    if source_tree is None:
        for relative, data in original.items():
            path = output / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        kind = "three-file-verification-fixture-not-buildable"
    else:
        # Export only committed content; ignored build files and .git are not
        # copied, and the input checkout is never patched or otherwise changed.
        with tempfile.TemporaryDirectory(prefix="madsense-wine-export-") as temporary:
            archive = Path(temporary) / "wine.tar"
            git(source_tree, "archive", "--format=tar", f"--output={archive}", manifest["wine_commit"])
            with tarfile.open(archive) as source_archive:
                source_archive.extractall(output, filter="data")
        kind = "full-public-wine-source-for-manual-macos-build"
    # Make patch paths relative to this new tree even if its parent is a repo.
    git(output, "init", "--quiet")
    patches = [str(KIT / patch["path"]) for patch in manifest["patches"]]
    git(output, "apply", "--check", "--whitespace=error-all", *patches)
    git(output, "apply", "--whitespace=error-all", *patches)
    result = {
        "kind": kind, "wine_commit": manifest["wine_commit"],
        "patch_commit": manifest["patch_commit"],
        "scope": "USB HID/audio PropertyStore association only; no Bluetooth or game validation",
        "sources": [{"path": source["path"], "original_sha256": source["sha256"],
                     "patched_sha256": digest((output / source["path"]).read_bytes())}
                    for source in manifest["sources"]],
    }
    (output / "MADSENSE-WINE-USB.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="new directory, never an installed runner")
    parser.add_argument("--source-tree", type=Path, help="optional clean checkout of the exact pinned public Wine commit")
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.output, args.source_tree), indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"Source preparation refused/failed: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
