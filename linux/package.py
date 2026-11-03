"""Package the already-tested native Linux publish directory with source/notices.

This creates development archives. It never installs a helper or alters devices.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tool(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def file_manifest(directory):
    result = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise RuntimeError('Review symlinks before packaging: ' + str(path))
        if path.is_file():
            result[path.relative_to(directory).as_posix()] = digest(path)
        elif not path.is_dir():
            raise RuntimeError('Only regular files/directories may be packaged: ' + str(path))
    return result


def licenses(destination, assets, runtime, rid):
    destination.mkdir()
    packages = []
    folders = [Path(path) for path in assets['packageFolders']]
    entries = {name: value for name, value in assets['libraries'].items() if value['type'] == 'package'}
    sources = json.loads((ROOT / 'linux/licenses/sources.json').read_text())
    for framework in runtime['runtimeOptions']['includedFrameworks']:
        if framework['name'] != 'Microsoft.NETCore.App':
            raise ValueError('Unexpected self-contained framework')
        name = 'Microsoft.NETCore.App.Runtime.' + rid
        entries[name + '/' + framework['version']] = {'path': name.lower() + '/' + framework['version']}
    for identity, metadata in sorted(entries.items()):
        package = next((folder / metadata['path'] for folder in folders if (folder / metadata['path']).is_dir()), None)
        if package is None:
            raise ValueError('Cannot preserve license for ' + identity)
        spec_files = list(package.glob('*.nuspec'))
        if len(spec_files) != 1:
            raise ValueError('Ambiguous package metadata: ' + identity)
        spec = ET.parse(spec_files[0]).getroot()
        for node in spec.iter():
            node.tag = node.tag.rsplit('}', 1)[-1]
        information = spec.find('metadata')
        repository = information.find('repository')
        repository = repository.attrib if repository is not None else {}
        package_id, version = identity.split('/')
        target = destination / (package_id + '-' + version)
        target.mkdir()
        shutil.copyfile(spec_files[0], target / 'package.nuspec')
        copied = []
        for path in sorted(package.rglob('*')):
            if path.is_file() and path.suffix.lower() in ('', '.txt', '.md') and path.name.lower().startswith(('license', 'licence', 'notice', 'third-party')):
                relative = path.relative_to(package)
                (target / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target / relative)
                copied.append(relative.as_posix())
        if not copied:
            fallback = ('Avalonia.BuildServices' if package_id == 'Avalonia.BuildServices' else
                        'Avalonia' if package_id.startswith('Avalonia') else
                        'MicroCom' if package_id == 'MicroCom.Runtime' else
                        package_id if package_id in ('Tmds.DBus', 'Tmds.DBus.Protocol') else None)
            if fallback is None:
                raise ValueError('A full license notice is required for ' + identity)
            if ('/blob/' + repository.get('commit', '') + '/') not in sources[fallback]:
                raise ValueError('Fallback license does not match the pinned package source: ' + identity)
            shutil.copyfile(ROOT / 'linux/licenses' / (fallback + '.txt'), target / 'LICENSE.txt')
            copied.append('LICENSE.txt')
        packages.append({'package': identity, 'authors': information.findtext('authors'),
                         'copyright': information.findtext('copyright'),
                         'license': information.findtext('license'), 'licenseFiles': copied,
                         'source': repository})
    (destination / 'packages.json').write_text(json.dumps(packages, indent=2) + '\n')
    shutil.copytree(ROOT / 'linux/licenses', destination / 'upstream-license-sources')
    shutil.copytree(ROOT / 'vendor/licenses', destination / 'shared-engine')
    shutil.copyfile(ROOT / 'vendor/DualSenseClient/LICENSE', destination / 'DualSenseClient-LICENSE.txt')
    for name in ('Anton-OFL.txt', 'NOTICE.txt'):
        shutil.copyfile(ROOT / 'linux/app/Assets' / name, destination / name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--app', type=Path, required=True)
    parser.add_argument('--rid', choices=('linux-x64', 'linux-arm64'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'linux':
        raise RuntimeError('Build and verify Linux artifacts on Linux')
    native_rid = {'x86_64': 'linux-x64', 'aarch64': 'linux-arm64'}.get(os.uname().machine)
    if args.rid != native_rid:
        raise RuntimeError('Package and execute the requested architecture on its native Linux runner')
    app, output = args.app.resolve(), args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError('Package output must be fresh')
    if app == output or output.is_relative_to(app):
        raise RuntimeError('The output must be separate from the tested app')
    changes = tool('git', 'diff', '--name-only', 'HEAD').splitlines()
    if any(name != 'linux/app/packages.lock.json' for name in changes):
        raise RuntimeError('Package committed sources only')
    untracked = tool('git', 'ls-files', '--others', '--exclude-standard').splitlines()
    if untracked:
        raise RuntimeError('Untracked files are absent from source.tar.gz: ' + ', '.join(untracked[:10]))
    # RID restore can extend the lock with runtime packs. Preserve that exact
    # resolved lock, and verify its common graph against the committed lock.
    lock = json.loads((ROOT / 'linux/app/packages.lock.json').read_text())
    original_lock = json.loads(tool('git', 'show', 'HEAD:linux/app/packages.lock.json'))
    if lock['dependencies']['net10.0'] != original_lock['dependencies']['net10.0']:
        raise RuntimeError('Resolved managed dependency graph changed')
    executable = app / 'MadSense.Linux'
    header = executable.read_bytes()[:20]
    machine = 62 if args.rid == 'linux-x64' else 183
    if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != machine:
        raise RuntimeError('Apphost is not the requested native Linux architecture')
    if not os.access(executable, os.X_OK):
        raise RuntimeError('Apphost lost executable permission')
    runtime = json.loads((app / 'MadSense.Linux.runtimeconfig.json').read_text())
    if not runtime['runtimeOptions'].get('includedFrameworks'):
        raise RuntimeError('A self-contained publish is required')
    tested_files = file_manifest(app)
    revision = tool('git', 'rev-parse', 'HEAD')
    name = f'MadSense-{args.rid}-development-{revision[:7]}'
    output.mkdir(parents=True, exist_ok=True)
    payload = output / name
    payload.mkdir()
    shutil.copytree(app, payload / 'app')
    if file_manifest(payload / 'app') != tested_files or file_manifest(app) != tested_files:
        raise RuntimeError('The tested publish changed during packaging or differs from the staged application')
    helper = payload / 'platform'
    helper.mkdir()
    for filename in ('broker.py', 'broker_core.py', 'install.sh', 'uninstall.sh', 'madsense-broker.service',
                     'madsense-modules.conf', 'org.madsense.bridge.policy', 'README.md'):
        shutil.copyfile(ROOT / 'linux/platform' / filename, helper / filename)
    (helper / 'install.sh').chmod(0o755)
    (helper / 'uninstall.sh').chmod(0o755)
    shutil.copyfile(ROOT / 'linux/RUNNING.txt', payload / 'RUNNING.txt')
    shutil.copyfile(ROOT / 'linux/README.md', payload / 'DEVELOPMENT.md')
    shutil.copyfile(ROOT / 'LICENSE', payload / 'LICENSE.txt')
    shutil.copyfile(ROOT / 'THIRD-PARTY-NOTICES.txt', payload / 'ENGINE-HISTORY-AND-NOTICES.txt')
    shutil.copyfile(ROOT / 'src/assets/madsense.svg', payload / 'madsense.svg')
    shutil.copyfile(ROOT / 'linux/app/packages.lock.json', payload / 'resolved-packages.lock.json')
    assets = json.loads((ROOT / 'linux/app/obj/project.assets.json').read_text())
    licenses(payload / 'licenses', assets, runtime, args.rid)
    (payload / 'run.sh').write_text('#!/bin/sh\nset -eu\nbase=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$base/app/MadSense.Linux" "$@"\n')
    (payload / 'run.sh').chmod(0o755)
    source = payload / 'source.tar.gz'
    subprocess.run(['git', 'archive', '--format=tar.gz', '--prefix=MadSense-source/', '-o', str(source), revision], cwd=ROOT, check=True)
    metadata = {'schema': 1, 'sourceCommit': revision, 'branch': tool('git', 'branch', '--show-current') or None, 'rid': args.rid,
                'sdkVersion': tool('dotnet', '--version'), 'kernel': tool('uname', '-a'),
                'selfContainedFrameworks': runtime['runtimeOptions']['includedFrameworks'],
                'developmentBuild': True, 'physicalControllerValidated': False, 'gameParityValidated': False,
                'files': file_manifest(payload)}
    (payload / 'build.json').write_text(json.dumps(metadata, indent=2) + '\n')
    archive = output / (name + '.tar.gz')
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(payload, arcname=name)
    (output / (archive.name + '.sha256')).write_text(digest(archive) + '  ' + archive.name + '\n')
    print(json.dumps({'archive': str(archive), 'sha256': digest(archive), 'source': revision}))


if __name__ == '__main__':
    main()
