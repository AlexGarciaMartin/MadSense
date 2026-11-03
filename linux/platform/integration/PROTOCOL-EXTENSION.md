# Optional synthetic protocol extension

Run [41628578777](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41628578777)
at `0325da7` passed the one core and six app extension assertions after the
original nine core and ten app cases. Separate result hashes are recorded in
the native provenance; the downloaded archive SHA-256 is
`d28f52bae14af9d59f7751ccadc4b0c42bf127c10f91a840c4ec3360acca5c58`.
This does not establish physical trigger force, audible
headset/speaker output, radio behavior, timing, or game compatibility.

Run the usual `qemu_core.py` command with `--extended-protocol` to opt in. The
original broker baseline, idle upgrade/removal, nine core cases and ten app cases
must pass first. The same owned VM then runs fresh core and app actors with fresh
fixtures/leases. Their output is retained separately in `result/protocol-core`
and `result/protocol-app`; original evidence is never overwritten. A failed new
case prevents an overall extended pass. The platform workflow enables this flag.

The new assertions are:

- Core and the real app each receive a USB output 02 with distinct eleven-byte
  Weapon effects for R2 and L2. The observer requires both allow flags and the
  exact effect bytes in a CRC-valid Bluetooth output, followed by explicit Off
  for both. Accepted USB writes alone do not prove delivery.
- The app observes headset insertion and removal through the physical fixture's
  connection bit. Each transition must change `State.Headset` and produce a
  combined Bluetooth audio frame with the correct 96/93 route tag, matching
  audio-control path, volume, gain, validity flags and CRC. These are protocol
  routing assertions, not an acoustic measurement or an independent codec test.
- After the standard session pauses and restores its nodes, a fresh Edge UHID
  fixture is introduced. The real app's Auto path must enumerate it, persist Edge
  settings, import USB 054c:0df2 and wait for parsed input before Ready. FnL, FnR,
  L4 and R4 are injected separately and observed on the exact virtual HID stream,
  followed by all four released. Edge pause and final disposal must confirm both
  physical restoration and virtual removal.

The writer/reader opens only one hidraw node attributed to the already validated
VHCI parent. It requires the `/sys/devices` ancestry, USB bus, Sony persona,
matching sysfs device number and opened character descriptor identity; a VID/PID
match alone is insufficient. It never opens the physical fixture's hidraw node.
The production actor has no new output callback or synchronous evidence writer.

`protocolExtension.cases` is separate from the original case list.
`protocol_checks.py` requires exactly one core and six app extension assertions,
as well as all original cases, synthetic scope and clean teardown. Portable
checks exercise wrong trigger side/flags/CRC, explicit Off, wrong route/control/
volume/gain/validity, marker field isolation, unsafe observer paths and incomplete
extension results. The C# fixture command uses the actual parser for every
headset and Edge marker.
