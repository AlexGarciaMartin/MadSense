"""Read policy and test normal user/network namespaces in the owned guest.

No sysctl, AppArmor profile, capability or sandbox configuration is changed.
This early check is not GE execution or container compatibility evidence.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from ci_environment import validate_environment

PATHS = ("/proc/sys/kernel/unprivileged_userns_clone", "/proc/sys/user/max_user_namespaces",
    "/proc/sys/kernel/apparmor_restrict_unprivileged_userns",
    "/proc/sys/kernel/apparmor_restrict_unprivileged_unconfined",
    "/sys/module/apparmor/parameters/enabled", "/proc/self/attr/current")
CHILD = """import errno,json,os,socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
try:
 s.connect(('192.0.2.1',9))
except OSError as e:
 assert e.errno == errno.ENETUNREACH
else:
 raise RuntimeError('Unexpected route in private network namespace')
assert os.geteuid() != 0
print(json.dumps({'ordinaryUid':os.geteuid(),'ordinaryGid':os.getegid(),'networkRouteAbsent':True}))
"""


def policy_snapshot():
    values = {}
    for name in PATHS:
        try:
            values[name] = {"value": Path(name).read_text()[:8192].strip()}
        except OSError as error:
            values[name] = {"error": str(error)}
    fields = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in ("Uid", "Gid", "Groups", "CapInh", "CapPrm", "CapEff", "CapBnd", "NoNewPrivs", "Seccomp"):
            fields[key] = value.strip()
    return {"paths": values, "processStatus": fields}


def validate_child(result, uid, gid):
    if (type(uid) is not int or type(gid) is not int or uid <= 0 or gid <= 0
            or type(result.get("ordinaryUid")) is not int or result["ordinaryUid"] != uid
            or type(result.get("ordinaryGid")) is not int or result["ordinaryGid"] != gid
            or result.get("networkRouteAbsent") is not True):
        raise RuntimeError("Namespace did not preserve the ordinary UID/GID and offline network")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owned-guest-token", required=True)
    args = parser.parse_args()
    validate_environment(args.owned_guest_token)
    if os.geteuid() <= 0 or os.getegid() <= 0:
        raise RuntimeError("Namespace preflight must run as an ordinary guest account")
    result = {"schema": 1, "result": "failed", "scope": "owned-guest-namespace-preflight",
        "ownedGuestToken": args.owned_guest_token, "securityPolicyModified": False,
        "runtimeExecutionConfirmed": False, "policyBefore": policy_snapshot(),
        "startedMonotonicNs": time.monotonic_ns()}
    try:
        completed = subprocess.run(["unshare", "--user", "--map-current-user", "--net", "--", "python3", "-c", CHILD],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        result.update(exitCode=completed.returncode, stdout=completed.stdout, stderr=completed.stderr)
        if completed.returncode:
            raise RuntimeError("Ordinary namespace operation refused; preserve the original guest policy")
        observation = json.loads(completed.stdout)
        validate_child(observation, os.geteuid(), os.getegid())
        result.update(result="passed", observation=observation)
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
    finally:
        result.update(endedMonotonicNs=time.monotonic_ns(), policyAfter=policy_snapshot())
        # Read only, bounded to recent kernel records. A missing journal is
        # retained as unavailable, never treated as proof that no denial occurred.
        try:
            journal = subprocess.run(["sudo", "-n", "journalctl", "-k", "-n", "150", "--no-pager", "-o", "short-monotonic"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
            result["kernelJournal"] = {"exitCode": journal.returncode, "stdout": journal.stdout[-131072:],
                                       "stderr": journal.stderr[-8192:]}
        except Exception as error:
            result["kernelJournal"] = {"error": str(error)}
        print(json.dumps(result), flush=True)
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
