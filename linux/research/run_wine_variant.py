"""Explicit Wine 9/10 UDEV diagnostic in a fresh account/prefix after default Wine.

This is a configured distribution-Wine experiment, never default compatibility
or GE/UMU/official Valve Proton evidence. All commands execute through the
ordinary ConsumerSession account. Existing user prefixes are never accepted.
"""
import hashlib
import json
from pathlib import Path
import re
import stat

from consumer_session import ConsumerSession, bounded_bytes, require


WINEBUS_KEY = r"HKLM\System\CurrentControlSet\Services\winebus"


def registry_sdl_disabled(output):
    rows = re.findall(r"^\s*Enable SDL\s+REG_DWORD\s+(0x[0-9a-fA-F]+)\s*$",
                      output.decode("utf-8", errors="strict"), re.MULTILINE)
    require(len(rows) == 1 and int(rows[0], 16) == 0, "Private Wine registry did not retain Enable SDL DWORD0")


def audited_wine_version(version):
    match = re.fullmatch(r"wine-(9\.0|10\.0)(?:[ \t]+[^\r\n]+)?[ \t\r\n]*", version)
    require(match is not None, "UDEV diagnostic is audited for Wine 9.0 and 10.0 only")
    return match.group(1)


def configure_udev(session, prefix, version):
    session.result["wineSession"]["configurationRequested"] = True
    session.result["wineSession"]["configured"] = False
    session.save()
    audited = audited_wine_version(version)
    prefix = Path(prefix)
    require(prefix == session.root / "home/wine-prefix" and session.env.get("WINEPREFIX") == str(prefix)
            and session.uid > 0 and session.gid > 0, "Only this ordinary account's fresh prefix is eligible")
    info = prefix.lstat()
    require(stat.S_ISDIR(info.st_mode) and not prefix.is_symlink()
            and (info.st_uid, info.st_gid) == (session.uid, session.gid), "Private prefix ownership changed")
    require(prefix.resolve() == prefix, "Private prefix path was redirected")
    session.command("wine-udev-registry-before", ["wine", "reg", "query", WINEBUS_KEY], required=False)
    session.command("wine-udev-configure", ["wine", "reg", "add", WINEBUS_KEY,
        "/v", "Enable SDL", "/t", "REG_DWORD", "/d", "0", "/f"])
    _, output = session.command("wine-udev-registry-check", ["wine", "reg", "query", WINEBUS_KEY, "/v", "Enable SDL"])
    registry_sdl_disabled(output)
    # WINEPREFIX and UID select only this newly initialized private wineserver.
    # Restarting it makes the next Wine bus instance reread the configured key.
    session.command("wine-udev-server-stop", ["wineserver", "--kill"], timeout=20)
    session.command("wine-udev-server-wait", ["wineserver", "--wait"], timeout=30)
    _, output = session.command("wine-udev-registry-after-restart", ["wine", "reg", "query", WINEBUS_KEY, "/v", "Enable SDL"])
    registry_sdl_disabled(output)
    session.result["wineSession"]["configuration"] = dict(key=WINEBUS_KEY, value="Enable SDL", type="REG_DWORD",
        data=0, registryReadbackVerified=True, restartedOwnedWineserver=True, disableHidrawUnmodified=True,
        actualUdevBackendConfirmed=False, auditedWineVersion=audited,
        source=f"https://github.com/wine-mirror/wine/blob/wine-{audited}/dlls/winebus.sys/main.c")
    session.result["wineSession"]["configured"] = True
    session.save()


def clean_result(path, ready):
    result = json.loads(bounded_bytes(path))
    require(result.get("accountProcessesAndFilesRemoved") is True and not result.get("cleanupErrors"),
            "Wine experiment cleanup is not verified; next account cannot start")
    for key in ("ownedGuestToken", "usbParent", "virtualMacHex"):
        require(ready.get(key) and result.get("ready", {}).get(key) == ready[key],
                "Wine experiment belongs to another guest or virtual device")
    return result


def run_variant(ready_path, executable, fixture_state_callback, evidence, physical_parent, *, hidapi_executable=None):
    from run_wine_consumer import run_wine_suite
    with ConsumerSession(ready_path, evidence, physical_parent) as session:
        session.result.update(scope="configured-distribution-wine-udev", defaultWinebus=False,
                              replacesDefaultWineResult=False, wineRequested=True)
        try:
            run_wine_suite(session, executable, fixture_state_callback, winebus_mode="udev-diagnostic",
                           hidapi_executable=hidapi_executable)
            session.result["result"] = "passed"
        finally:
            fixture_state_callback("neutral")
        session.save()
    return session.result


def run_after_default(primary, ready_path, executable, fixture_state_callback, evidence, physical_parent, *, hidapi_executable=None):
    """Retain default failure, but still collect the separate variant after cleanup."""
    require(executable is not None, "UDEV diagnostic requires the Windows consumer")
    evidence = Path(evidence)
    variant = evidence.parent / "wine-udev-variant"
    require(not variant.exists(), "Fresh UDEV diagnostic evidence directory required")
    error = None
    try:
        primary()
    except Exception as failure:
        error = failure
    # A mandatory observer cleanup failure may precede creation of the primary
    # consumer result. Retain that failure instead of masking it with ENOENT.
    if error is not None and not (evidence / "result.json").is_file():
        raise error
    ready = json.loads(bounded_bytes(Path(ready_path)))
    baseline = clean_result(evidence / "result.json", ready)
    require(baseline.get("wineSession", {}).get("kind") == "distribution-wine"
            and baseline["wineSession"].get("defaultWinebus") is True,
            "Default Wine must reach its recorded session before a configured variant")
    variant_error = None
    try:
        optional = {"hidapi_executable": hidapi_executable} if hidapi_executable is not None else {}
        run_variant(ready_path, executable, fixture_state_callback, variant, physical_parent, **optional)
    except Exception as failure:
        variant_error = failure
    observed = clean_result(variant / "result.json", ready)
    require(observed.get("scope") == "configured-distribution-wine-udev"
            and observed.get("defaultWinebus") is False, "Incorrect Wine variant scope")
    baseline["wineUdevVariant"] = dict(result=observed.get("result"), evidenceDirectory=str(variant),
        resultSha256=hashlib.sha256((variant / "result.json").read_bytes()).hexdigest(), cleanupVerified=True,
        replacesDefaultWineResult=False, error=str(variant_error) if variant_error else None)
    (evidence / "result.json").write_text(json.dumps(baseline, indent=2) + "\n")
    if error is not None:
        raise error
    if variant_error is not None:
        raise variant_error
    require(observed.get("result") == "passed", "Configured UDEV diagnostic did not pass")
    return baseline
