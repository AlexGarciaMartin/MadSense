"""Pinned research payload acquisition. No installation, execution or credentials.

Archives remain upstream archives. Notices and source inventories are separate
evidence, not a claim that all GE/Valve corresponding source is bundled here.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import time
import urllib.parse
import urllib.request
import zipfile

LOCK = Path(__file__).with_name("ge-umu-lock.json")
MAX_TOTAL = 1_100_000_000
MAX_ARCHIVE_BYTES = 3_000_000_000
MAX_ENTRIES = 40_000
MAX_NOTICE_BYTES = 64 * 1024 * 1024
ALLOWED_HOSTS = {"github.com", "codeload.github.com", "api.github.com",
                 "release-assets.githubusercontent.com", "objects.githubusercontent.com",
                 "repo.steampowered.com"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def safe_name(name):
    parts = PurePosixPath(name).parts
    require(bool(parts) and not name.startswith("/") and "\\" not in name
            and ":" not in name and ".." not in parts and "\x00" not in name,
            f"Unsafe archive/download name: {name!r}")
    return PurePosixPath(name)


def checked_url(url):
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname in ALLOWED_HOSTS
            and parsed.port in (None, 443) and not parsed.username and not parsed.password,
            "Download URL must use HTTPS on a listed upstream host, without credentials")
    return url


class UpstreamRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        checked_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def digest(path, algorithm="sha256"):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def load_lock(path=LOCK):
    lock = json.loads(Path(path).read_text(encoding="utf-8"))
    require(lock.get("schema") == 1, "Unsupported lock schema")
    seen, total = set(), 0
    for item in lock["downloads"]:
        name = item["name"]
        require(safe_name(name).name == name and name not in seen, "Duplicate or nested asset name")
        checked_url(item["url"])
        require(re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), "Invalid SHA-256")
        require(type(item["size"]) is int and 0 < item["size"] <= 600_000_000, "Invalid asset size")
        total += item["size"]
        seen.add(name)
    require(total <= MAX_TOTAL, "Payload exceeds total network budget")
    return lock


def verify_asset(path, item):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f"Missing regular asset: {item['name']}")
    require(path.stat().st_size == item["size"], f"Size mismatch: {item['name']}")
    actual = digest(path)
    require(actual == item["sha256"], f"SHA-256 mismatch: {item['name']}")
    return dict(name=item["name"], bytes=item["size"], sha256=actual)


def copy_response(response, output, expected_size, deadline):
    received = 0
    while True:
        require(time.monotonic() < deadline, "Download exceeded wall time budget")
        block = response.read(min(1024 * 1024, expected_size + 1 - received))
        if not block:
            break
        received += len(block)
        require(received <= expected_size, "Download exceeded locked size")
        output.write(block)
    require(received == expected_size, "Truncated download")


def acquire_asset(cache, item):
    final = cache / item["name"]
    if final.exists():
        return verify_asset(final, item)
    partial = cache / (item["name"] + ".partial")
    # An interrupted or rejected payload is retained. Do not silently resume it.
    require(not partial.exists(), f"Partial evidence already exists: {partial}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), UpstreamRedirect())
    request = urllib.request.Request(checked_url(item["url"]), headers={
        "User-Agent": "MadSense-owned-VM-research/1", "Accept-Encoding": "identity"})
    deadline = time.monotonic() + 300
    with opener.open(request, timeout=20) as response, partial.open("xb") as output:
        checked_url(response.url)
        require(response.status == 200, "Expected a full upstream response")
        content_length = response.headers.get("Content-Length")
        require(content_length is None or int(content_length) == item["size"], "Unexpected Content-Length")
        copy_response(response, output, item["size"], deadline)
    verified = verify_asset(partial, item)
    partial.rename(final)
    return verified


def checksum_entry(text, name, digits=64):
    entries = []
    for line in text.splitlines():
        match = re.fullmatch(rf"([0-9a-fA-F]{{{digits}}}) [ *](.+)", line)
        if match and match[2] == name:
            entries.append(match[1].lower())
    require(len(entries) == 1, f"Expected exactly one upstream checksum for {name}")
    return entries[0]


def verify_bundle(cache, lock):
    cache = Path(cache)
    result = [verify_asset(cache / item["name"], item) for item in lock["downloads"]]
    by_name = {item["name"]: item for item in lock["downloads"]}
    ge = "GE-Proton11-11-x86_64.tar.gz"
    require(checksum_entry((cache / "GE-Proton11-11-x86_64.sha512sum").read_text(), ge, 128)
            == lock["geSha512"] == digest(cache / ge, "sha512"), "GE SHA-512 mismatch")
    sums = (cache / "SHA256SUMS").read_text()
    for item in lock["downloads"]:
        if item["url"].startswith("https://repo.steampowered.com/") and item["name"] != "SHA256SUMS":
            upstream_name = urllib.parse.urlsplit(item["url"]).path.rsplit("/", 1)[1]
            require(checksum_entry(sums, upstream_name) == by_name[item["name"]]["sha256"],
                    f"Valve checksum mismatch: {upstream_name}")
    require((cache / "BUILD_ID.txt").read_text().strip() == lock["runtimeBuild"], "Runtime build mismatch")
    tree = json.loads((cache / "ge-tree.json").read_text())
    require(not tree.get("truncated") and tree["sha"] == lock["geCommit"], "Incomplete or wrong GE source tree")
    return result


def notice_name(name):
    base = PurePosixPath(name).name.lower()
    return any(word in base for word in ("license", "copying", "copyright", "notice", "patents"))


def inventory_archive(path, evidence, *, save_notices=True):
    """Read members without executing/extracting links; preserve notice bytes."""
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    notices = evidence / "notices"
    notices.mkdir(exist_ok=True)
    entries, expanded, notice_bytes, selected = [], 0, 0, {}
    with tarfile.open(path, "r|*") as archive:
        for member in archive:
            safe_name(member.name)
            require(len(entries) < MAX_ENTRIES, "Archive entry limit exceeded")
            expanded += member.size
            require(expanded <= MAX_ARCHIVE_BYTES, "Archive expanded-size limit exceeded")
            row = dict(name=member.name, size=member.size, type=member.type.decode("ascii"), mode=member.mode)
            if member.issym() or member.islnk():
                row["link"] = member.linkname
            if member.isfile() and save_notices and notice_name(member.name):
                require(member.size <= 8 * 1024 * 1024, "Individual notice too large")
                notice_bytes += member.size
                require(notice_bytes <= MAX_NOTICE_BYTES, "Notice budget exceeded")
                data = archive.extractfile(member).read()
                sha = hashlib.sha256(data).hexdigest()
                (notices / sha).write_bytes(data)
                row["noticeSha256"] = sha
            if member.isfile() and len(PurePosixPath(member.name).parts) == 2 and PurePosixPath(member.name).name in (
                    "version", "VERSIONS.txt", "toolmanifest.vdf", ".gitmodules"):
                require(member.size < 65536, "Manifest too large")
                selected[member.name] = archive.extractfile(member).read().decode("utf-8")
            entries.append(row)
    result = dict(archive=Path(path).name, sha256=digest(path), entries=entries,
                  expandedBytes=expanded, noticeBytes=notice_bytes, selected=selected)
    (evidence / (Path(path).name + ".inventory.json")).write_text(json.dumps(result, indent=2) + "\n")
    return result


def zipapp_inventory(cache, evidence):
    with tarfile.open(Path(cache) / "umu-launcher-1.4.4-zipapp.tar") as archive:
        member = archive.getmember("umu/umu-run")
        require(member.isfile() and member.size < 1_000_000, "Unexpected UMU zipapp")
        payload = archive.extractfile(member).read()
    files, notices = [], Path(evidence) / "notices"
    with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
        total = sum(info.file_size for info in zipped.infolist())
        require(total <= 8 * 1024 * 1024 and len(zipped.infolist()) <= 1000, "Zipapp expansion exceeded budget")
        for info in zipped.infolist():
            safe_name(info.filename)
            row = dict(name=info.filename, bytes=info.file_size)
            if not info.is_dir() and (notice_name(info.filename) or info.filename.endswith("/METADATA")):
                data = zipped.read(info)
                sha = hashlib.sha256(data).hexdigest()
                (notices / sha).write_bytes(data)
                row["noticeOrMetadataSha256"] = sha
            files.append(row)
        with tarfile.open(Path(cache) / "umu-source.tar.gz") as source:
            for name in ("umu_run.py", "umu_runtime.py", "umu_util.py"):
                matches = [m for m in source if m.name.endswith("/umu/" + name)]
                require(len(matches) == 1, "Expected UMU source file")
                require(zipped.read("umu/" + name) == source.extractfile(matches[0]).read(),
                        "UMU executable Python differs from pinned source")
    (Path(evidence) / "umu-zipapp.inventory.json").write_text(json.dumps(files, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    lock = load_lock()
    args.cache.mkdir(parents=True, exist_ok=True)
    args.evidence.mkdir(parents=True, exist_ok=False)
    result = dict(schema=1, result="failed", lockSha256=digest(LOCK), label=lock["label"],
                  runtimeExecuted=False, accountRequired=False, steamInstalled=False,
                  officialValveProtonValidated=False, completeCorrespondingSourceBundled=False)
    try:
        if not args.verify_only:
            for item in lock["downloads"]:
                acquire_asset(args.cache, item)
        result["assets"] = verify_bundle(args.cache, lock)
        for name in ("GE-Proton11-11-x86_64.tar.gz", "umu-launcher-1.4.4-zipapp.tar",
                     "SteamLinuxRuntime_4.tar.xz", "ge-source.tar.gz", "umu-source.tar.gz"):
            inventory_archive(args.cache / name, args.evidence)
        zipapp_inventory(args.cache, args.evidence)
        (args.evidence / "ge-umu-lock.json").write_bytes(LOCK.read_bytes())
        result["result"] = "verified-acquisition"
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        (args.evidence / "acquisition.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
