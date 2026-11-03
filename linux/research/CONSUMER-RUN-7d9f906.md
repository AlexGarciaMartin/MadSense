# First SDL and Wine execution: 7d9f906

[Run 41626531215](https://github.com/AlexGarciaMartin/MadSense/actions/runs/41626531215)
executed the consumer experiment against commit
`7d9f9061ba89522ba84ef75d5877dc315fffaff2`. Compilation and evidence tests passed;
the runtime job **failed**. Its partial results and cleanup evidence are retained.
This was an owned Ubuntu QEMU guest, synthetic Bluetooth UHID input, production
MadSense bridge, USB/IP and Linux VHCI. No physical controller, radio, game or
physical latency was tested.

The runtime artifact ZIP SHA-256 is
`2798928e12e18b9058422239a8ddd6ff4c9ac54f56ce84f0e09daef5f4912ecd`.
The native build artifact SHA-256 is
`39177be17bf65ea5218ea85787ab4f16be0ae7f5c072ff2c4c5415ea6fa0d879`;
the Windows build artifact is
`4dd8480a8fcefbfe900cc17691064227ef300ed1e966e040b6f1d8c5b626e28a`.
All were checked against the GitHub artifact digests before extraction.
The evidence paths below are relative to the runtime artifact's
`consumer-runtime/result/core-experiment` directory.

## Core and identity

All eight core gates passed, including strict feature/descriptor checks, full
Bluetooth input through the production parser and virtual USB, RGB output back
to the Bluetooth writer, and four-channel ALSA PCM with nonzero Bluetooth
haptics. The deterministic 48,000-frame PCM stream appeared exactly in the
captured ingress; this is a data-path observation, not physical sound.

The physical fixture address was `02:FA:11:22:33:44`; the owned virtual address
was `02:D5:A5:AC:9F:40`. The client recorded 3471 reports received/read, zero input
overflows and no evidence overflow or isolation loss. Core cleanup was confirmed.
The ordinary consumer account and all of its processes/files were removed, with
all five exact device ACL snapshots restored and no reported cleanup error.

## SDL results and explicit stop

SDL2 2.30.0 and SDL3 3.2.28 both found exactly the Sony `054c:0ce6` virtual
controller, `/dev/hidraw1`, serial `02-d5-a5-ac-9f-40`. Both returned successful
API results for south-only and neutral input, rumble, explicit stop and RGB.
Gyro and accelerometer capabilities were advertised; trigger rumble was not.
The SDL2 error string retained `Unexpected controller element crc` despite the
successful API return values; it is retained separately from those return values.

Each of the four runs failed the existing strict output assertion:
`No later zero rumble state reached the synthetic physical writer`.
The complete phase capture explains what was actually transmitted:

| Phase | Decoded virtual USB report 0x02 | CRC-valid Bluetooth output |
| --- | --- | --- |
| Rumble | One selected nonzero `left=0x12,right=0x56` marker | One matching selected marker |
| Explicit stop, before LED/close | One report with all decoded values zero/false | One 78-byte report 0x31 whose complete 47-byte state payload is zero |
| LED, later | One RGB-only marker | One matching RGB-only marker |
| Close and after close | No report observed in those bounded intervals | No report observed in those bounded intervals |

The stop-only window lasted at least 300 ms; the requested rumble duration was
60,000 ms and had not expired. Both the decoded USB and raw Bluetooth stop
observations have cleared rumble selectors, so neither meets the existing
selected-zero-amplitude criterion. This demonstrates transmission of SDL's
selector-clear transition before the unrelated LED operation. It does **not**
establish whether a real motor stopped or whether audio haptics resumed.
No acceptance rule was changed to turn this result into a pass.

For example, SDL2 south's Bluetooth stop packet was:

```text
3190100000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000d65c2c08
```

`consumer-suite/result.json` contains API results and `CLOCK_MONOTONIC` boundaries;
`core/events.jsonl` contains decoded virtual outputs; `uhid-events.jsonl` contains
the original Bluetooth bytes. Complete USB output bytes were **not** captured.
The deferred USB observer can record its timestamp after the Bluetooth write;
these timestamps do not prove one-to-one causality or transport latency.
Use `consumer_phases.py` to classify the same-clock windows without modifying
the independent stop assertion. The first local correlation JSON SHA-256 was
`d59ce3f6d226165ed83b8b2318f90f28b66b49a5c2544d87c2b438fb7e21ff68`.

## Wine and audio observers

Distribution Wine 9.0 initialized a fresh private prefix. Both Sony PE runs
failed with `Invalid HID feature capability length (Win32=0)` before emitting
a MAC-verified HID candidate/container. The wrapper consequently also reported
the missing container. WASAPI was not executed because its required stable
HID-to-audio container association had not been established. This is not GE,
UMU or official Valve Proton evidence. Further backend/capability diagnostics
must retain this default Wine result rather than substitute another setup.

The separate private PipeWire/WirePlumber and Pulse default observers completed
collection and verified their independent cleanup before SDL/Wine ran. Their
results are in `pipewire-defaults/result.json` and `audio-defaults/result.json`.
Profile, channel map and actual volume interpretation remain separate from
the explicitly configured private Pulse session used by the Wine harness.
No default desktop compatibility or physical haptic strength is claimed.
