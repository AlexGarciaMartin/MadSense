# Application controller integration over synthetic UHID

This separate harness executes the actual LinuxBridgeController source, including
SettingsFor, Connect, Update, Stop, retries, output metering and diagnostics. The
project links the production files and calls its normal constructor, with no
connection/cleanup overrides, alternate socket or replacement virtual factory.
The ordinary actor observes the sole attributed Bluetooth UHID fixture through
the real HID enumerator. Only the executable entry point is different.

It builds locally; a complete native app pass is still pending. It does
not exercise Avalonia, tray, preferences UI or singleton behavior, and cannot
validate radio, hardware timing, acoustic output or actuator response.

The shared fixture waits for the real hid-playstation driver and its gamepad,
motion and touchpad interfaces; firmware, pairing and calibration feature replies
carry the Bluetooth CRC checked by that driver. No driver is unbound or blacklisted.

The root observer validates the QEMU ownership marker and UUID, requires the
existing broker baseline to have passed, and rejects existing Sony devices,
leases, recovery state and imports. A fresh ordinary account gets a temporary
polkit grant only for the production acquire action. The broker still checks
SO_PEERCRED, pidfd, physical identity and isolation. The exact UHID fixture gets
a temporary test udev rule. These files never enter the production installer.

Ten required cases cover:

- Actual Connect/SettingsFor and a Ready state withheld until physical input.
- Alto, Bajo and Off: saved settings plus corresponding volume/pre-gain bytes
  in CRC-valid physical UHID output generated during four-channel ALSA playback.
- Auto to Xbox and back: real application settings and attributed kernel VID/PID.
- Pause: Paused state only when journal/rule/imports are gone and original node
  identities, modes, ownership and ACLs have been restored.
- Resume and automatic reconnection after destroying/recreating the UHID fixture.
- Dispose: fresh helper idle acknowledgement and independent root cleanup checks.

The native speaker observation proves delivered control bytes, not decoded audio
waveform or audible volume. It complements the core harness's exact PCM ingress
assertion. Standard DualSense is covered; Edge variant selection, authorization
denial/dialog interaction, delayed-feature cancellation and helper-crash UI
transitions remain additional scenarios. Existing unit and broker tests are not
relabelled as coverage of those whole application flows.

The actor accepts sequential JSON files in its private `commands` directory:
`000001.json`, `000002.json`, etc., each containing `operation` and optional
`value`. Operations are resume, pause, mode (Auto/Xbox), speaker (Alto/Bajo/Off)
and exit. `lastQueuedCommand` acknowledges dispatch only. The root observer must
also wait for State and kernel evidence. `events.jsonl` records transitions and
commands; `status.json` and `stopped.json` preserve actual diagnostics/metrics.
Sampling, capture and TCG scheduling preclude physical latency claims.

Prepare and validate without accessing devices:

```sh
dotnet publish linux/tests/app-integration/MadSense.Linux.AppProbe.csproj -c Release -r linux-x64 --self-contained true -o /tmp/app-probe
python3 -m unittest discover -s linux/tests/app-integration -p 'test_*.py'
```

The platform workflow runs this actor after the broker baseline, two idle upgrades
and all nine production-core cases in the same owned VM. It preserves the
application result separately in `result/app-experiment/`; ten unique successful
cases, exact synthetic scope and confirmed cleanup are required. A failed prior
stage prevents this actor from starting. Native execution of this combined path
is pending; compilation is preparation, not proof that the runtime passed.

For isolated diagnosis on a disposable GitHub-hosted Linux runner, after publishing
the existing neutral probe too, the separate launcher can build a hashed payload,
boot the pinned signed image and run the broker baseline followed by this experiment:

```sh
python3 linux/tests/app-integration/qemu_app.py --neutral-probe-directory /tmp/neutral-probe --app-probe-directory /tmp/app-probe --output /tmp/app-evidence --work /tmp/app-private
```

Alternatively, after the baseline in an already validated owned guest, install
HIDAPI/ALSA, start the installed broker and invoke `run_app_guest.py` with
`--owned-guest-token UUID --baseline-result /path/result.json
--probe-directory /path/app-probe --output /path/fresh-evidence` as root.
The C# actor itself runs as the fresh ordinary test account.
Its root-owned publish tree is staged under a fresh `/opt` UUID directory with
explicit modes, using the shared core `actor_staging.py`. An execution smoke
under that ordinary UID and saved mount/permission diagnostics precede fixture
creation; no mount options or production permissions are relaxed.
## Linux enumeration regression

Run 41626531451 at `7d9f906` first passed all nine production-core cases, but the
app actor stopped at its initial enumeration guard. Its fixture was deliberately
silent until attachment to test that Ready requires a parsed input report. The
shared enumerator applied the Windows Bluetooth liveness probe on Linux: opening
hidraw and demanding a nonzero report within 200 ms removed that silent device
from the current snapshot. The fixture trace contained no injected input.

The installed guest package was `libhidapi-hidraw0 0.14.0-1build1`. Upstream
[HIDAPI 0.14.0 Linux enumeration](https://github.com/libusb/hidapi/blob/hidapi-0.14.0/linux/hid.c#L788)
scans current udev `hidraw` devices and reads identity/usage from sysfs. Linux now
uses that current snapshot without the extra probe-open/read; Windows retains
its previous behavior, and VID/PID, bus, usage and broker acquisition checks
remain. A portable Linux regression invokes the actual snapshot policy; the
native actor remains the end-to-end check for present-but-silent enumeration and
Ready staying false until input begins. Its preflight now records the ordinary
user's descriptor access, open access and returned identity/usage metadata, with
temporary trace logging only during that initial enumeration. Native validation
of this correction is pending.
