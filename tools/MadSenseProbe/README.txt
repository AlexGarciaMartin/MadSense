MadSenseProbe — host transport comparison (2026-10-31)

Build:
  dotnet publish tools/MadSenseProbe/MadSenseProbe.csproj -c Release -r win-x64 --self-contained true -o tools/MadSenseProbe/app
Offline check (no device access):
  tools/MadSenseProbe/app/MadSenseProbe.exe --self-test

The operator must coordinate device ownership. Run one bridge at a time, same controller,
position, Bluetooth adapter, power mode and audio workload. No games during an
idle comparison. Wait5seconds after bridge ready. Temporarily authorize the exact
probe EXE path in HidHide so the independent observer can see physical inputs.
Remove only that authorization when done; preserve pre-existing apps/devices.

List:
  tools/MadSenseProbe/app/MadSenseProbe.exe --list
Capture:
  tools/MadSenseProbe/app/MadSenseProbe.exe --seconds 15 --label MadSense-idle1 --echo --trigger-probe --out outputs/MadSense/evidence/madsense-idle1.json
Repeat3times, then the identical3runs with DSX. Alternate app order if doing more
runs to reduce time/order effects. Specify --physical and --virtual only if
multiple candidate devices exist; never silently pick a random pad.

Default reads only. --echo emits zero-valid-flags output with host timestamp tags.
--trigger-probe additionally emits mild Feedback(position3,strength2) for400ms,
then explicit Off. Use it only when no gameplay effects need preserving.

Input metric:
Two normal-priority dedicated threads timestamp completed HID reads using the
same Stopwatch clock. Match exact controller motion timestamp and keep the first
arrival per stream. Report full signed delta virtualMinusPhysical; negative
values are expected observer-scheduling evidence, never clamp them to zero.
Exclude first1second of queued/warmup reports. Report matchrate and counts;
low matchrate means the pairing assumption failed. The motion timestamp can
wrap, but 15second samples are far shorter than its normal wrap period.

This does not measure controller sensor sampling→game, button→photon, radio-only,
haptic onset, trigger mechanical latency or tactile fidelity. Observing both
streams can itself affect scheduling. HIDAPI normal thread arrival jitter may
be comparable to the forwarding cost, so tiny differences are inconclusive.

Output echo:
Virtual HID write timestamp to the first observed physical input whose host echo
contains that tag. Includes virtual output bridge, Bluetooth output, firmware
response, Bluetooth input and observer scheduling. It is not actuator motion.
Missing echo is reported unsupported/missing, never as zero latency. The virtual
input echo is also captured to detect echo forwarding differences.

Trigger probe:
Captures physical/virtual status byte sets before/during/after. Trigger status may
not change without physical finger pressure. If unchanged, outcome is inconclusive.
Exact input status passthrough can still be compared for matching input frames.

Calibration:
Reads feature05 from both endpoints before timing. Exact equality is useful but
includes trailer bytes/CRC, so check differing offsets before interpreting a
mismatch as gyro calibration corruption. Real calibration fields occupy bytes1–34.

Files: JSON aggregate stats and CSV raw matched observations. Retain per-run
results; don't collapse them into one unqualified latency score.

Primary layout cross-check: Sony-authored Linux hid-playstation driver, sensor_timestamp offset27 of63-byte common input and calibration report05 length41: https://raw.githubusercontent.com/torvalds/linux/master/drivers/hid/hid-playstation.c

