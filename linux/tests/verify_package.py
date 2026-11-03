"""Extract the release archive and execute its packaged Linux app, paused.

This is a packaging check on the native CI architecture, not a controller or
game test. It compares the archive against the previously tested publish tree.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[2]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_hashes(directory):
    return {path.relative_to(directory).as_posix(): digest(path)
            for path in sorted(directory.rglob('*')) if path.is_file()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--tested-app', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'linux':
        raise RuntimeError('The packaged executable must be checked on Linux')
    archive, evidence = args.archive.resolve(), args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    archive_digest = digest(archive)
    checksum = archive.with_name(archive.name + '.sha256').read_text().strip()
    if checksum != archive_digest + '  ' + archive.name:
        raise AssertionError('Archive does not match its checksum file')
    expected_root = archive.name.removesuffix('.tar.gz')
    with tempfile.TemporaryDirectory(prefix='madsense-package-') as temporary:
        extracted = Path(temporary)
        with tarfile.open(archive, 'r:gz') as tar:
            seen = set()
            for member in tar.getmembers():
                path = PurePosixPath(member.name)
                if (path.is_absolute() or '..' in path.parts or not path.parts
                        or path.parts[0] != expected_root or member.name in seen
                        or not (member.isfile() or member.isdir())):
                    raise AssertionError('Unexpected archive entry: ' + member.name)
                seen.add(member.name)
            tar.extractall(extracted, filter='data')
        payload = extracted / expected_root
        metadata = json.loads((payload / 'build.json').read_text())
        revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        if metadata['sourceCommit'] != revision:
            raise AssertionError('Archive is not from the tested source revision')
        if metadata['physicalControllerValidated'] or metadata['gameParityValidated']:
            raise AssertionError('Packaging cannot certify physical hardware or games')
        hashes = file_hashes(payload)
        hashes.pop('build.json')
        if hashes != metadata['files']:
            raise AssertionError('Extracted payload differs from its manifest')
        original_app = file_hashes(args.tested_app.resolve())
        if file_hashes(payload / 'app') != original_app:
            raise AssertionError('Packaged binaries differ from the tested publish tree')
        for name in ('run.sh', 'app/MadSense.Linux', 'platform/install.sh', 'platform/uninstall.sh'):
            if not os.access(payload / name, os.X_OK):
                raise AssertionError('Executable permission missing: ' + name)
        # The SDK embeds SourceRevisionId into AssemblyInformationalVersion.
        # Check the running binary as well as the manifest and source archive:
        # a stale publish tree must not be relabelled as the current commit.
        build_info = json.loads(subprocess.check_output([str(payload / 'run.sh'), '--build-info'],
                                                       cwd=extracted, text=True, timeout=10))
        if not build_info['assemblyInformationalVersion'].endswith('+' + revision):
            raise AssertionError('Packaged assembly was compiled from another revision')
        if build_info['architecture'].lower() != metadata['rid'].removeprefix('linux-'):
            raise AssertionError('Running architecture differs from the package identity')
        # The entry point must also work outside its own directory. The wrapper
        # execs the real app, so its PID remains visible to the lifecycle probe.
        with (evidence / 'package-smoke.stdout').open('wb') as stdout, (evidence / 'package-smoke.stderr').open('wb') as stderr:
            subprocess.run(['dbus-run-session', '--', 'xvfb-run', '-a',
                            str(payload / 'run.sh'), '--ui-smoke', str(evidence), '--language', 'es'],
                           cwd=extracted, stdout=stdout, stderr=stderr, check=True, timeout=30)
        smoke = json.loads((evidence / 'ui-es.json').read_text())
        if smoke['ready'] or smoke['physical_device_opened'] or not smoke['preview']:
            raise AssertionError('Packaged smoke must remain disconnected')
        subprocess.run(['dbus-run-session', '--', 'xvfb-run', '-a', sys.executable,
                        str(ROOT / 'linux/tests/gui_lifecycle.py'), str(payload / 'run.sh'), str(evidence)],
                       cwd=extracted, check=True, timeout=90)
        if file_hashes(payload / 'app') != original_app:
            raise AssertionError('Launching the package modified application files')
        lifecycle = json.loads((evidence / 'gui-lifecycle.json').read_text())
        result = {'sourceCommit': revision, 'rid': metadata['rid'], 'archive': archive.name,
                  'sha256': archive_digest, 'manifestFilesVerified': len(hashes),
                  'runningAssembly': build_info,
                  'testedAppFilesIdentical': len(original_app), 'packagedGuiLaunched': True,
                  'desktopLifecycleCases': lifecycle['cases'],
                  'helperInstalled': False, 'physicalControllerTested': False,
                  'gameParityValidated': False}
    (evidence / 'package-validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
