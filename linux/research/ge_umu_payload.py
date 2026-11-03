"""Stage an already acquired GE/UMU cache into private guest work, not artifacts.

Call before guest boot/baseline/actor startup. Download and archive inventory are
separate ge_umu_acquire operations with their existing exact bounds and lock.
"""
from pathlib import Path
import shutil

from ge_umu_acquire import load_lock, require, verify_bundle


def stage(cache, payload, collected_output):
    cache = Path(cache).resolve(strict=True)
    payload = Path(payload).resolve(strict=True)
    collected_output = Path(collected_output).resolve()
    require(cache.is_dir() and payload.is_dir(), "Existing private cache/payload directories required")
    for path in (cache, payload):
        require(not path.is_relative_to(collected_output) and not collected_output.is_relative_to(path),
                "GE archives must remain outside the collected output tree")
    require(not cache.is_relative_to(payload) and not payload.is_relative_to(cache),
            "Cache and private payload trees must be separate")
    destination = payload / "ge-umu-private-cache"
    require(not destination.exists(), "Fresh private GE cache destination required")
    lock = load_lock()
    verified = verify_bundle(cache, lock)
    destination.mkdir(mode=0o700)
    for item in lock["downloads"]:
        source = cache / item["name"]
        require(source.is_file() and not source.is_symlink(), "Private cache asset must be a regular file")
        shutil.copyfile(source, destination / item["name"])
    # Recheck copied bytes, not merely the source's earlier digest.
    copied = verify_bundle(destination, lock)
    require(copied == verified, "Private copied GE cache differs from its locked inputs")
    research = Path(__file__).resolve().parent
    target = payload / "linux/research"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("ge-umu-lock.json", "ge_umu_acquire.py", "ge_umu_session.py", "ge_umu_consumer.py",
                 "test_ge_umu.py", "test_ge_umu_consumer.py"):
        shutil.copyfile(research / name, target / name)
    return dict(cacheGuestRelativePath="ge-umu-private-cache", assets=copied,
                stageBeforeActorStartupRequired=True, runtimeExecuted=False,
                runtimeCacheCollected=False, officialValveProton=False)
