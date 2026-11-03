"""Capture both the actual X11 window and its live Avalonia render for review.

Run only in the workflow's private dbus-run-session + Xvfb. Every app launch is
the disconnected UI smoke mode, which also exits through normal app cleanup.
"""
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    executable, evidence = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    cases = []
    for language in ('en', 'es', 'fr', 'de', 'it', 'pt-BR'):
        with (evidence / f'desktop-{language}.stdout').open('wb') as stdout, (evidence / f'desktop-{language}.stderr').open('wb') as stderr:
            process = subprocess.Popen([str(executable), '--ui-smoke', str(evidence), '--language', language],
                                       stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            try:
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        raise AssertionError('App exited before its X11 window could be captured')
                    found = subprocess.run(['xdotool', 'search', '--onlyvisible', '--pid', str(process.pid), '--name', '^MadSense$'],
                                           capture_output=True, text=True, timeout=2)
                    windows = found.stdout.split()
                    if found.returncode == 0 and len(windows) == 1:
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError('Expected exactly one visible app window')
                    time.sleep(.05)
                subprocess.run(['xdotool', 'windowfocus', '--sync', windows[0]], check=True, timeout=2)
                time.sleep(.5)  # Let the mapped window present its first frames.
                screen = evidence / f'desktop-{language}.png'
                subprocess.run(['scrot', '--focused', str(screen)], check=True, timeout=3)
                if process.wait(timeout=10) != 0:
                    raise AssertionError('UI smoke did not shut down normally')
                if not screen.read_bytes().startswith(b'\x89PNG\r\n\x1a\n') or screen.stat().st_size < 10000:
                    raise AssertionError('Missing live X11 screenshot')
                cases.append({'language': language, 'processId': process.pid, 'windowId': windows[0],
                              'x11Screenshot': screen.name, 'liveControlRender': f'ui-{language}.png'})
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
    (evidence / 'desktop-captures.json').write_text(json.dumps({
        'method': 'scrot focused window on private Xvfb', 'cases': cases,
        'allLaunchesDisconnectedSmoke': True, 'physicalControllerTested': False}, indent=2) + '\n')


if __name__ == '__main__':
    main()
