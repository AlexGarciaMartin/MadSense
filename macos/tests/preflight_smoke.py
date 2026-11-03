"""Query the real packaged apphost's entitlement, without opening any device."""
import json
import pathlib
import subprocess
import sys

backend = pathlib.Path(sys.argv[1]).resolve()
output = pathlib.Path(sys.argv[2]).resolve()
output.mkdir(parents=True, exist_ok=True)
process = subprocess.run([str(backend), "--preflight-native"], capture_output=True,
                         text=True, timeout=10)
(output / "native-preflight.json").write_text(process.stdout)
(output / "native-preflight.stderr").write_text(process.stderr)
assert process.returncode == 0, f"Own-process preflight query failed: {process.stdout} {process.stderr}"
report = json.loads(process.stdout)
assert report["state"] == "native-preflight", report
assert report["entitlement"] == "missing", "CI development apphost must not carry the restricted HID entitlement"
assert report["processId"] > 0 and report["detail"], report
for key in ("physicalControllerOpened", "virtualHidCreated", "creationVerified", "hardwareValidated"):
    assert report[key] is False, (key, report)
print("PASS packaged own-process entitlement is missing; no physical capture or virtual creation; no hardware validation")
