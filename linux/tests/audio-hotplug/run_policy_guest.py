#!/usr/bin/env python3
"""Separate private policy cases, only after the unchanged hotplug baseline."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from run_guest import case, save, verify_gates, verify_result as verify_baseline, verify_case
from policy import PolicyDesktop, configuration, validate_virtual, monitor_evidence, require

CASES = (("scoped-automatic", False, False), ("scoped-explicit", True, False), ("foreign-parent", False, True))


def audit_case(path, row, token):
    verify_case(path, row, token)
    audio = json.loads((path / "audio/result.json").read_text())
    plan = audio.get("privatePolicy", {})
    foreign = path.name == "foreign-parent"
    require(plan.get("matched") is (not foreign) and plan.get("productionPolicyModified") is False
            and plan.get("wireplumberFamily") == "0.4" and plan.get("pcmDeliveryValidated") is False,
            "Missing private policy scope")
    require((plan["actualParent"] != plan["targetParent"]) == foreign, "Incorrect foreign-parent negative attribution")
    expected = configuration(plan["targetParent"], plan["nonce"]).encode()
    require((path / "audio/policy.lua").read_bytes() == expected
            and hashlib.sha256(expected).hexdigest() == plan["configurationSha256"], "Active policy bytes differ from recorded rules")
    require(plan["installedBeforeGraphNs"] < audio["graphStartedMonotonicNs"]
            and audio.get("distributionPolicyUnmodified") is False, "Policy was not installed before graph startup")
    checks = []
    for observed in audio["observations"]:
        if "virtual" in observed: checks.append(validate_virtual(observed["virtual"], plan))
    require(checks, "Missing policy graph snapshots")
    for arrival in audio["arrivals"]:
        names = [Path(name).name for name in arrival["grantedNodes"]]
        require(any(name.startswith("controlC") for name in names)
            and any(name.startswith("pcmC") and name.endswith("p") for name in names)
            and any(name.startswith("pcmC") and name.endswith("c") for name in names),
            "Policy must retain USB/ALSA playback, capture and control devices")
        require(arrival["usbParent"] == plan["actualParent"] and arrival["identity"]["usbParent"] == plan["actualParent"],
                "Live app identity does not match the pre-graph owned parent")
    publication = monitor_evidence((path / "audio/graph-monitor.stdout").read_text(), plan)
    expected_defaults = not foreign
    return dict(publication=publication, policyMatched=not foreign, playbackChannels=4,
        usbAndAlsaCapturePreserved=True, desktopCaptureSuppressed=not foreign,
        expectationsMet=all(audio["preservationByKind"][kind] == expected_defaults for kind in ("sink", "source")),
        defaultPreservation=audio["preservationByKind"], physicalAudioValidated=False)


def verify_result(directory, token, gates, baseline_directory):
    directory = Path(directory); baseline_directory = Path(baseline_directory)
    verify_baseline(baseline_directory, token, gates)
    result = json.loads((directory / "result.json").read_text())
    require(result.get("result") == "observed" and result.get("ownedGuestToken") == token
        and result.get("scope") == "private-prepublication-policy" and result.get("functionalGates") == gates
        and result.get("baselineSha256") == hashlib.sha256((baseline_directory / "result.json").read_bytes()).hexdigest()
        and result.get("productionPolicyModified") is False and result.get("wireplumber05Validated") is False,
        "Policy result is not attributed to the same baseline and private environment")
    require([row.get("caseId") for row in result.get("cases", [])] == [row[0] for row in CASES], "Missing separate policy cases")
    for row, (_, explicit, _) in zip(result["cases"], CASES):
        require(row.get("selection") == ("explicit" if explicit else "automatic"), "Policy selection case changed")
        require(row.get("policyObservation") == audit_case(directory / row["caseId"], row, token),
                "Policy summary differs from retained graph/metadata")
    require(result.get("expectationsMet") == all(row["policyObservation"]["expectationsMet"] for row in result["cases"]),
            "Policy summary hides a changed default")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("functional-results", "baseline-results", "probe-directory", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--owned-guest-token", required=True)
    args = parser.parse_args()
    from ci_environment import validate_environment
    require(os.geteuid() == 0 and validate_environment(args.owned_guest_token)["kind"] == "owned-qemu-guest", "Owned root guest required")
    gates = verify_gates(args.functional_results, args.owned_guest_token)
    verify_baseline(args.baseline_results, args.owned_guest_token, gates)
    output = args.output.resolve(); output.mkdir()
    result = dict(schema=1, result="invalid", ownedGuestToken=args.owned_guest_token,
        scope="private-prepublication-policy", functionalGates=gates,
        baselineSha256=hashlib.sha256((args.baseline_results / "result.json").read_bytes()).hexdigest(),
        productionPolicyModified=False, wireplumber05Validated=False, physicalDevices=False, cases=[])
    try:
        for name, explicit, foreign in CASES:
            factory = lambda *values, foreign=foreign: PolicyDesktop(*values, foreign=foreign)
            row = case(args, output / name, explicit, desktop_factory=factory)
            # Add attribution only after the original lifecycle has saved its result.
            row["caseId"] = name; save(output / name / "result.json", row)
            result["cases"].append(row)
            require(row["result"] == "observed" and row["cleanupConfirmed"], "Invalid policy case; no further session starts")
            row["policyObservation"] = audit_case(output / name, row, args.owned_guest_token)
            save(output / name / "result.json", row)
        result.update(result="observed", expectationsMet=all(row["policyObservation"]["expectationsMet"] for row in result["cases"]))
        save(output / "result.json", result)
        verify_result(output, args.owned_guest_token, gates, args.baseline_results)
    except BaseException as error:
        result.update(result="invalid", error=f"{type(error).__name__}: {error}")
    finally:
        save(output / "result.json", result)
    return 0 if result["result"] == "observed" and result["expectationsMet"] else 1


if __name__ == "__main__": raise SystemExit(main())
