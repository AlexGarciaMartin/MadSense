"""Observe actual class request fields/data in a source copy, without changing dispatch."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[3]
DEVICE = 'vendor/DualSenseClient/DualSenseClient.Emulation/Core/VirtualUsbDevice.cs'
ACTOR = 'linux/platform/integration/Program.cs'
RUNNER = 'linux/platform/integration/run_core_guest.py'
HELPER = 'linux/tests/control-report/HidControlTrace.cs'
TARGET = 'vendor/DualSenseClient/DualSenseClient.Emulation/ResearchHidControlTrace.cs'
TRACE = 'DualSenseClient.Emulation.Research.HidControlTrace'


def digest(data): return hashlib.sha256(data).hexdigest()


def edits():
    return {
        DEVICE: [('public int HandleControlRequest(in UsbSetupPacket setup, ReadOnlySpan<byte> outData, Span<byte> response,\n        bool hidSetReportIncludesReportId)\n    {',
                  'public int HandleControlRequest(in UsbSetupPacket setup, ReadOnlySpan<byte> outData, Span<byte> response,\n        bool hidSetReportIncludesReportId)\n    {\n'
                  f'        {TRACE}.Record(this, setup, outData);')],
        ACTOR: [
            ('using Evidence evidence = new(output, observeUsbOutput);',
             f'using Evidence evidence = new(output, observeUsbOutput);\n{TRACE}.Start(output);'),
            ('            evidence.Atomic("stopped.json", new { completed = stopped && !evidence.Overflow && session.CleanupConfirmed,',
             f'            {TRACE}.StopAndSave();\n'
             f'            evidence.Atomic("stopped.json", new {{ completed = stopped && !evidence.Overflow && session.CleanupConfirmed && {TRACE}.Complete,'),
            ('                rawOutputObservation = evidence.RawOutputObservation,',
             f'                rawOutputObservation = evidence.RawOutputObservation, controlObservation = {TRACE}.Snapshot(),'),
        ],
        RUNNER: [
            ('                from consumer_session import run_suite',
             '                sys.path.insert(0, str(ROOT / "tests/control-report"))\n'
             '                from control_session import run_suite'),
        ],
    }


def transform(relative, source):
    if TRACE in source or 'from control_session import run_suite' in source:
        raise ValueError('Control observer is already present')
    for old, new in edits()[relative]:
        if source.count(old) != 1: raise ValueError('Control observer hook does not have one exact source match')
        source = source.replace(old, new, 1)
    return source


def verify_overlay(value, originals, helper):
    if (value.get('schema') != 1 or value.get('kind') != 'hid-control-research-copy'
        or value.get('productionBinary') is not False or value.get('dispatchModified') is not False
        or not re.fullmatch('[0-9a-f]{40}', value.get('sourceCommit', ''))
        or not re.fullmatch('[0-9a-f]{64}', value.get('sourceArchiveSha256', ''))):
        raise ValueError('Unexpected control observer provenance')
    expected = {name:dict(originalSha256=digest(data), instrumentedSha256=digest(
        transform(name, data.decode().replace('\r\n','\n')).encode())) for name,data in originals.items()}
    expected[TARGET] = dict(originalSha256=None,instrumentedSha256=digest(helper),copiedFrom=HELPER)
    if set(originals) != set(edits()) or value.get('changes') != expected:
        raise ValueError('Control observer differs from committed source transform')
    return True


def stage(destination, root=ROOT):
    root,destination=Path(root).resolve(),Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError('Fresh source copy outside checkout required')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    archive_bytes=subprocess.check_output(['git','archive','--format=tar',revision],cwd=root)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes)) as archive:
        if any(not (row.isfile() or row.isdir()) for row in archive): raise ValueError('Special archive entries refused')
        destination.mkdir(parents=True);archive.extractall(destination,filter='data')
    changes={}
    for name in edits():
        path=destination/name;original=path.read_bytes()
        path.write_text(transform(name,original.decode().replace('\r\n','\n')),encoding='utf8',newline='\n')
        changes[name]=dict(originalSha256=digest(original),instrumentedSha256=digest(path.read_bytes()))
    target=destination/TARGET
    if target.exists(): raise ValueError('Control recorder collides with existing source')
    target.write_bytes((destination/HELPER).read_bytes())
    changes[TARGET]=dict(originalSha256=None,instrumentedSha256=digest(target.read_bytes()),copiedFrom=HELPER)
    result=dict(schema=1,kind='hid-control-research-copy',productionBinary=False,dispatchModified=False,
        latencyValidated=False,sourceCommit=revision,sourceArchiveSha256=digest(archive_bytes),changes=changes)
    (destination/'research-control-overlay.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(stage(parser.parse_args().output),indent=2))
