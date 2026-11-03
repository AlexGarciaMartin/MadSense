#!/usr/bin/env python3
"""Separate opt-in owned VM: unmodified functional gates, then source-copy export."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qemu_audio_hotplug import add_payload
from run_guest import verify_gates, require
from run_export_guest import verify_result
from stage_export import ROOT, DEVICE, HELPER, verify_overlay

def add_export_payload(payload, marked_probe, reference):
    shutil.copytree(marked_probe, payload/"marked-app-probe")
    shutil.copyfile(reference, payload/"descriptor-reference.json")
    overlay = json.loads((payload/"marked-app-probe/research-audio-overlay.json").read_text())
    require(overlay.get("kind") == "audio-interface-research-copy" and overlay.get("productionBinary") is False,
        "Missing separate source overlay provenance")
    return add_payload(payload), overlay

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-private-ignore-export", action="store_true")
    for name in ("neutral-probe-directory","core-probe-directory","app-probe-directory","marked-probe-directory",
                 "descriptor-reference","output","work"):
        parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args()
    if not args.run_private_ignore_export: parser.error("Explicit private experiment required")
    from qemu_core import OwnedQemu, prepare_combined_payload, run_stages
    guest=OwnedQemu(args.output,args.work); p=guest.provenance
    p.update(experiment="private-ignore-and-client-export",functionalResult="not-validated",exportResult="not-run")
    try:
        p["sourceCommit"] = subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()
        payload=guest.work/"payload"
        prepare_combined_payload(payload,args.neutral_probe_directory.resolve(),args.core_probe_directory.resolve(),args.app_probe_directory.resolve())
        hashes,overlay=add_export_payload(payload,args.marked_probe_directory.resolve(),args.descriptor_reference.resolve())
        require(overlay["sourceCommit"] == p["sourceCommit"],"Overlay and control commits differ")
        original = subprocess.check_output(["git", "show", p["sourceCommit"]+":"+DEVICE], cwd=ROOT)
        helper = subprocess.check_output(["git", "show", p["sourceCommit"]+":"+HELPER], cwd=ROOT)
        verify_overlay(overlay, original, helper)
        p["sourceOverlay"] = overlay
        (guest.output/"payload-files.json").write_text(json.dumps(hashes,indent=2)+"\n")
        guest.boot();guest.upload(payload)
        guest.run("payload-check","cd /home/ubuntu/madsense && sha256sum --check --strict PAYLOAD-SHA256SUMS")
        guest.run("neutral-executable","chmod +x /home/ubuntu/madsense/probe/MadSense.Linux.Probe")
        try:
            run_stages(guest)
            require(all(p.get(name+"Exit") == 0 for name in ("baseline","core","app")),"Functional gates required")
            guest.run("export-dependencies","sudo -n apt-get install -y --no-install-recommends pipewire pipewire-bin wireplumber alsa-utils pulseaudio pulseaudio-utils dbus acl libpipewire-0.3-dev pkg-config gcc",timeout=300)
            guest.run("export-build","cd /home/ubuntu/madsense && cc -std=c11 -Wall -Wextra -Werror -O2 linux/tests/audio-hotplug/export/client.c $(pkg-config --cflags --libs libpipewire-0.3) -o audio-export-client")
            response=guest.run("audio-export","sudo -n python3 /home/ubuntu/madsense/linux/tests/audio-hotplug/export/run_export_guest.py "
                "--owned-guest-token "+shlex.quote(guest.token)+" --functional-results /home/ubuntu/madsense/result "
                "--marked-probe-directory /home/ubuntu/madsense/marked-app-probe --export-client /home/ubuntu/madsense/audio-export-client "
                "--descriptor-reference /home/ubuntu/madsense/descriptor-reference.json --output /home/ubuntu/madsense/result/audio-export",timeout=660,required=False)
            p["audioExportExit"] = response.returncode
        finally:
            for name in ("prepare.log","usbip-binary.txt","result"):guest.collect(name)
        gates=verify_gates(guest.output/"result",guest.token);p.update(functionalResult="passed",functionalGates=gates)
        value=verify_result(guest.output/"result/audio-export",guest.token,gates)
        p.update(exportResult=value["result"],exportResultSha256=hashlib.sha256((guest.output/"result/audio-export/result.json").read_bytes()).hexdigest())
        require(p.get("audioExportExit") == 0,"Export process failed")
        p["result"]="passed"
    except BaseException as error:
        p["reason"]=f"{type(error).__name__}: {error}"
        try:p.update(functionalResult="passed",functionalGates=verify_gates(guest.output/"result",guest.token))
        except BaseException:pass
    finally:
        try:guest.close()
        except BaseException as error:p.update(result="failed",cleanupError=str(error))
        (guest.output/"provenance.json").write_text(json.dumps(p,indent=2)+"\n");print(json.dumps(p),flush=True)
    return 0 if p["result"]=="passed" and p["ownedQemuStopped"] else 1

if __name__=="__main__":raise SystemExit(main())
