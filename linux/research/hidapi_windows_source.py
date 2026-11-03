"""Acquire exactly the reviewed HIDAPI Windows source; never execute it."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
import time
import urllib.request

COMMIT = "d6b2a974608dec3b76fb1e36c189f22b9cf3650c"
URL = "https://codeload.github.com/libusb/hidapi/tar.gz/" + COMMIT
SHA256 = "a93a9bfb599831b82c6cef50f42359e0f5f31abfc8f6954862c328a464cbc4d0"
SIZE = 364135
ROOT = "hidapi-" + COMMIT


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Pinned codeload source must not redirect")


def verified_extract(archive, destination):
    if Path(archive).stat().st_size != SIZE:
        raise ValueError("HIDAPI source size mismatch")
    data = Path(archive).read_bytes()
    if len(data) != SIZE or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("HIDAPI source size/digest mismatch")
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Fresh source directory required")
    with tarfile.open(archive) as source:
        members = source.getmembers()
        if len(members) > 1000 or sum(m.size for m in members) > 8 * 1024 * 1024:
            raise ValueError("Source expansion exceeded bounds")
        for member in members:
            name = PurePosixPath(member.name)
            if (not name.parts or name.parts[0] != ROOT or name.is_absolute() or ".." in name.parts
                    or "\\" in member.name or ":" in member.name or not (member.isfile() or member.isdir())):
                raise ValueError("Unexpected source member")
        destination.mkdir()
        source.extractall(destination, members=members, filter="data")
    root = destination / ROOT
    for name in ("windows/hid.c", "hidapi/hidapi.h", "LICENSE.txt", "LICENSE-bsd.txt",
                 "LICENSE-gpl3.txt", "LICENSE-orig.txt"):
        if not (root / name).is_file():
            raise ValueError("Incomplete source: " + name)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--archive", type=Path, help="Verify an existing exact archive without network")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    archive = args.output / "hidapi-source.tar.gz"
    if args.archive is not None:
        if args.archive.stat().st_size != SIZE:
            raise ValueError("Local HIDAPI archive size mismatch")
        data = args.archive.read_bytes()
    else:
        deadline = time.monotonic() + 60
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RefuseRedirect())
        with opener.open(URL, timeout=15) as response:
            if response.url != URL or response.status != 200:
                raise ValueError("Unexpected source response/redirect")
            chunks, count = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError("Source download deadline exceeded")
                block = response.read(min(65536, SIZE + 1 - count))
                if not block:
                    break
                chunks.append(block); count += len(block)
                if count > SIZE:
                    raise ValueError("Source download exceeded locked size")
            data = b"".join(chunks)
    if len(data) != SIZE or hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("HIDAPI source size/digest mismatch before retention")
    archive.write_bytes(data)
    source = verified_extract(archive, args.output / "source")
    result = dict(version="0.15.0", commit=COMMIT, url=URL, bytes=SIZE, sha256=SHA256,
                  sourceDirectory=str(source), codeExecuted=False, signatureVerified=False,
                  provenance="Exact first HTTPS retrieval digest of commit-addressed upstream archive")
    (args.output / "acquisition.json").write_text(json.dumps(result, indent=2) + "\n")
    print(source)


if __name__ == "__main__":
    main()
