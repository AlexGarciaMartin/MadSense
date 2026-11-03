# Signed OpenJoystickDriver broker experiment

This isolated opt-in experiment tests whether the original free MIT-licensed
[OpenJoystickDriver 0.6.0-alpha.1 release](https://github.com/xsyetopz/OpenJoystickDriver/releases/tag/0.6.0-alpha.1)
can publish a **neutral Generic HID controller** for an unsigned MadSense-side
client. It does not establish DualSense emulation, game compatibility, physical
controller input, rumble, adaptive triggers, haptics, audio or latency.

The Python runner pins the official DMG SHA256 and source revision
`3552be925d7dacef3fd895ac0c6cbf39e9d3cd70`. Before executing that downloaded app it
requires strict `codesign` verification, an explicitly accepted `spctl` assessment,
and matching executable/provisioning-profile virtual-HID entitlements. It never
re-signs or modifies the app. The existing signed app remains responsible for
creating its own device; no Apple signing identity is copied or impersonated.

The runner refuses a sandboxed release (the audited app explicitly declares
`com.apple.security.app-sandbox: false`), non-CI use, existing OJD installation/data/socket, USB devices,
Bluetooth hardware, controller/vendor HID interfaces, and preexisting OJD feeds.
It copies the untouched app to a new temporary task directory and invokes its
signed CLI through a temporary `ojd` symlink. The app runs directly as an owned
process so cleanup cannot target another app by name or rely on LaunchServices.

OJD normally registers a login item on first launch. The probe first verifies
Foundation's `CFFIXED_USER_HOME` behavior with its own helper, then places a valid
`Settings.json` with `launchAtLoginOptOut: true` in that isolated directory. Update
checks and notifications are disabled there. The actual user's home variables,
existing settings, login items and security configuration are not modified.
The variable behavior is an Apple implementation detail, so failure of that check
stops execution; see [Apple CFPlatform.c](https://github.com/apple-oss-distributions/CF/blob/main/CFPlatform.c).
The pinned upstream `SettingsDocument` decoder explicitly accepts this schema:
`apiVersion`, `kind`, `launchAtLoginOptOut`, `updates.checkAutomatically` and the
five `notify` booleans. `SettingsStore` throws on damaged/unsupported documents;
`HeadlessApplicationHost.registerForLoginIfNeeded` catches that error and skips
registration, rather than silently falling back to registering a login item.

The audited `ServiceConnection` never starts the service implicitly. `status`
only connects and queries; its DriverKit status check can spawn the read-only
`systemextensionsctl` utility. `service start` is the sole CLI launch operation
and this probe never calls it. `InstalledCLIForwarder` can only forward to an
existing `/Applications/OpenJoystickDriver.app`, which preflight refuses. Cleanup
signals only process groups created with `start_new_session`, including groups
whose leader exited but left a status-query child running.

It grants no TCC permissions, installs no DriverKit extension, and changes neither
SIP nor AMFI. OJD's Bluetooth startup can request permission; an unattended startup
or access failure becomes bounded `blocked` evidence, never an automatic grant.
The preflight prevents it from reaching physical controller hardware in this test.

The only feed frames are `{}` (all controls neutral). An independent observer
matches the new OJD Generic registry device, verifies the exact pinned descriptor,
opens only that device, GETs its 14-byte neutral input report, and records feature
GET refusal. It never sends an HID output/feature SET. Input callbacks are counted
when available, but OJD suppresses repeated identical packets, so callback count
is not a success criterion. Closing the feed must remove the test registry device.

The signed release only supports three fixed Generic/Xbox profiles. Its client API
accepts normalized controls and returns conventional rumble; it cannot accept an
arbitrary DualSense descriptor or proxy DualSense feature/output reports. The
experiment records the actual registry transport rather than assuming the source's
requested `USB` transport survives Apple's public-entitlement restrictions.

The earlier [run 41568186124](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41568186124)
verified the unchanged release's signature and received an explicit Gatekeeper
`accepted` result on ARM and Intel. Both runs stopped at the empty-host gate, before
executing OJD. The generic `IOBluetoothHCIController` registry service alone does
not establish a usable radio, and the earlier inventory omitted transport and USB
interface evidence needed to distinguish virtual peripherals safely.

The workflow currently runs **`--inventory-only-ci`**: it compiles this project's
observer and reads registry metadata. It does not download, assess signatures or
execute OJD, open a device, initialize Bluetooth or change permissions. Exit 0 in
this mode means `inventory-collected`, not that the broker experiment passed.
Missing/wrongly typed facts remain null with diagnostics; failed enumeration is an
error. Data includes strict `BluetoothTransportConnected` Booleans, transport-class
queries, all observed USB interface triples correlated to their parent devices,
HID usage pairs and the read-only kernel hypervisor flag. No names, serials,
addresses or location strings are exported. A hypervisor flag does not prove the
absence of hardware passthrough. The original empty-host launch gate is unchanged.

The transport-connected property follows Chromium's
[read-only presence check](https://chromium.googlesource.com/chromium/src/+/refs/tags/142.0.7444.98/device/bluetooth/bluetooth_adapter_mac.mm#204).
Registry access uses public IOKit APIs; these particular Bluetooth registry names
are implementation evidence, not an Apple guarantee of physical hardware absence.

Run only through `.github/workflows/macos-ojd-broker.yml` (macOS 26 ARM and Intel).
In the separately selected `--run-on-empty-ci` mode, exit 77 means blocked, exit 1
means an unexpected probe error, and exit 0 requires the limited neutral-device
experiment to pass. No such runtime success has been established. Offline gate tests can run anywhere:

```sh
python3 -m unittest discover -s macos/research -p test_ojd_broker.py
```
