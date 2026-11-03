"""Scope check shared by the disposable host/owned QEMU guest experiment."""
import os
from pathlib import Path
import sys
import subprocess
import uuid


def validate_environment(owned_guest_token=None):
    if sys.platform != "linux":
        raise RuntimeError("Linux kernel required")
    if owned_guest_token:
        token = str(uuid.UUID(owned_guest_token))
        dmi_uuid = subprocess.check_output(
            ["sudo", "-n", "cat", "/sys/class/dmi/id/product_uuid"], text=True, timeout=5).strip().lower()
        if (Path("/etc/madsense-ci-owner").read_text().strip() != token
                or dmi_uuid != token
                or Path("/sys/class/dmi/id/sys_vendor").read_text().strip() != "QEMU"):
            raise RuntimeError("Guest ownership marker and QEMU DMI identity must match")
        return {"kind": "owned-qemu-guest", "instance": token}
    if (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"):
        return {"kind": "github-hosted"}
    raise RuntimeError("Disposable GitHub-hosted runner or explicitly owned QEMU guest required")


if __name__ == "__main__":
    print(validate_environment(sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None))
