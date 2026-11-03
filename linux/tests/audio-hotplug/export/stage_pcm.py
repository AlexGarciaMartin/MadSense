"""Add bounded ingress observation only to a fresh committed export source copy."""
import argparse
import json
from pathlib import Path

from stage_export import ROOT, DEVICE, HELPER, TARGET, digest, stage, transform, verify_overlay

ACTOR = "linux/tests/app-integration/Program.cs"
RECORDER = "linux/tests/audio-hotplug/export/ExportPcmCapture.cs"
RECORDER_TARGET = "vendor/DualSenseClient/DualSenseClient.Emulation/ResearchExportPcmCapture.cs"
OBSERVE_OLD = "        packet[..audioLength].CopyTo(_audioOutScratch);"
OBSERVE_NEW = OBSERVE_OLD + "\n        if (audioLength > 0) DualSenseClient.Emulation.Research.ExportPcmCapture.Observe(_audioOutScratch.AsSpan(0, audioLength));"
ACTOR_HOOKS = (
    ('        var bridge = new LinuxBridgeController(false, "Auto", "Alto");',
     '        DualSenseClient.Emulation.Research.ExportPcmCapture.Start();\n        var bridge = new LinuxBridgeController(false, "Auto", "Alto");'),
    ('                        case "exit": requestedExit = true; break;',
     '                        case "pcm-mark" when value is not null && System.Text.RegularExpressions.Regex.IsMatch(value, "^[a-z][a-z0-9-]{0,39}$"):\n'
     '                            Atomic("pcm-mark-" + value + ".json", DualSenseClient.Emulation.Research.ExportPcmCapture.Mark()); break;\n'
     '                        case "exit": requestedExit = true; break;'),
    ('                await bridge.DisposeAsync();',
     '                await bridge.DisposeAsync();\n                DualSenseClient.Emulation.Research.ExportPcmCapture.StopAndWrite(output);'),
)

def replace_one(text, old, new):
    if text.count(old) != 1 or new in text: raise ValueError("Expected one unchanged PCM hook anchor")
    return text.replace(old, new)

def transform_device(original):
    return replace_one(transform(original), OBSERVE_OLD, OBSERVE_NEW)

def transform_actor(original):
    for old,new in ACTOR_HOOKS: original = replace_one(original,old,new)
    return original

def expected_changes(original, actor, helper, recorder):
    return {
        DEVICE:dict(originalSha256=digest(original),instrumentedSha256=digest(transform_device(original.decode().replace("\r\n","\n")).encode())),
        ACTOR:dict(originalSha256=digest(actor),instrumentedSha256=digest(transform_actor(actor.decode().replace("\r\n","\n")).encode())),
        TARGET:dict(originalSha256=None,instrumentedSha256=digest(helper),copiedFrom=HELPER),
        RECORDER_TARGET:dict(originalSha256=None,instrumentedSha256=digest(recorder),copiedFrom=RECORDER),
    }

def verify_pcm_overlay(value, original, actor, helper, recorder):
    descriptor = dict(value,kind="audio-interface-research-copy",changes={
        key:row for key,row in expected_changes(original,actor,helper,recorder).items() if key in (DEVICE,TARGET)})
    descriptor["changes"][DEVICE]["instrumentedSha256"] = digest(transform(original.decode().replace("\r\n","\n")).encode())
    verify_overlay(descriptor,original,helper)
    if (value.get("kind") != "audio-interface-pcm-research-copy"
            or value.get("changes") != expected_changes(original,actor,helper,recorder)):
        raise ValueError("PCM source copy differs from exact committed hooks")
    return True

def stage_pcm(destination, root=ROOT):
    value=stage(destination,root); destination=Path(destination)
    device=destination/DEVICE; actor=destination/ACTOR; recorder=destination/RECORDER
    before_device=device.read_bytes(); before_actor=actor.read_bytes()
    device.write_text(replace_one(before_device.decode(),OBSERVE_OLD,OBSERVE_NEW),encoding="utf-8",newline="\n")
    actor.write_text(transform_actor(before_actor.decode().replace("\r\n","\n")),encoding="utf-8",newline="\n")
    target=destination/RECORDER_TARGET
    if target.exists(): raise ValueError("Existing PCM research recorder")
    target.write_bytes(recorder.read_bytes())
    value["kind"]="audio-interface-pcm-research-copy"
    value["changes"][DEVICE]["instrumentedSha256"]=digest(device.read_bytes())
    value["changes"][ACTOR]=dict(originalSha256=digest(before_actor),instrumentedSha256=digest(actor.read_bytes()))
    value["changes"][RECORDER_TARGET]=dict(originalSha256=None,instrumentedSha256=digest(target.read_bytes()),copiedFrom=RECORDER)
    (destination/"research-audio-overlay.json").unlink()
    (destination/"research-pcm-overlay.json").write_text(json.dumps(value,indent=2)+"\n")
    return value

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--output",type=Path,required=True)
    print(json.dumps(stage_pcm(parser.parse_args().output),indent=2))
