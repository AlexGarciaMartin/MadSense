"""Exercise real X11 startup, singleton/show/stop and crash recovery, paused.

Run under an isolated dbus-run-session + Xvfb. No helper is installed or acquired.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def wait_for(predicate, seconds=12):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError('Timed out waiting for GUI lifecycle condition')


def main():
    if sys.platform != 'linux':
        raise RuntimeError('This evidence requires the real Linux desktop backend')
    executable, evidence = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='ms-gui-') as temporary:
        root = Path(temporary)
        env = dict(os.environ)
        for variable, name in (('XDG_CONFIG_HOME', 'config'), ('XDG_STATE_HOME', 'state'), ('XDG_RUNTIME_DIR', 'run')):
            directory = root / name
            directory.mkdir(mode=0o700)
            env[variable] = str(directory)
        def command(*args):
            result = subprocess.run([str(executable), *args], env=env, capture_output=True, timeout=15)
            if result.returncode:
                raise AssertionError(f'Command failed: {result.returncode}: {result.stderr.decode(errors="replace")}')
        def visible(process):
            if process.poll() is not None:
                raise AssertionError('App exited before displaying its paused window')
            found = subprocess.run(['xdotool', 'search', '--onlyvisible', '--pid', str(process.pid), '--name', '^MadSense$'], capture_output=True, timeout=2)
            return found.returncode == 0 and bool(found.stdout.strip())
        socket = root / 'run/madsense/control.sock'
        process = None
        try:
            with (evidence / 'gui-lifecycle.stdout').open('wb') as stdout, (evidence / 'gui-lifecycle.stderr').open('wb') as stderr:
                process = subprocess.Popen([str(executable), '--start-paused', '--tray'], env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                wait_for(lambda: visible(process))
                assert socket.exists()
                results.append('tray-request-without-watcher-retains-visible-window')
                command('--start-paused')
                assert process.poll() is None and visible(process)
                results.append('second-instance-shows-existing-window')
                command('--stop')
                assert process.wait(timeout=10) == 0
                assert not socket.exists()
                results.append('stop-releases-singleton-and-control-socket')
                process = subprocess.Popen([str(executable), '--start-paused'], env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                wait_for(lambda: visible(process))
                process.terminate()
                assert process.wait(timeout=10) == 0 and not socket.exists()
                results.append('sigterm-runs-application-cleanup')
                process = subprocess.Popen([str(executable), '--start-paused'], env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                wait_for(lambda: visible(process))
                process.kill()
                process.wait(timeout=5)
                assert socket.exists(), 'SIGKILL should leave the stale socket fixture'
                process = subprocess.Popen([str(executable), '--start-paused'], env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
                wait_for(lambda: visible(process))
                command('--stop')
                assert process.wait(timeout=10) == 0 and not socket.exists()
                results.append('restart-recovers-only-its-stale-control-socket')
                command('--stop')
                assert not socket.exists()
                results.append('stop-with-no-instance-does-not-launch-app')
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        assert not (root / 'config/madsense/controllers.json').exists(), 'Paused runs must not acquire controllers'
    (evidence / 'gui-lifecycle.json').write_text(json.dumps({'cases': results, 'physical_controller_tested': False, 'all_launches_start_paused': True}, indent=2) + '\n')
    print(f'{len(results)} real Linux desktop lifecycle cases passed')


if __name__ == '__main__':
    main()
