# MadSense virtual audio endpoint for macOS

This is an experimental, standalone CoreAudio `AudioServerPlugIn` and a consumer
library. It presents **MadSense DualSense Audio**, a four-channel, 48 kHz,
interleaved float32 output stream. Channels 1/2 are speaker left/right and 3/4 are
native haptic left/right. There is no microphone or recording stream.

The endpoint is not a virtual USB composite device. It does not automatically
associate with the virtual HID controller, convince a game to send native haptics,
or translate a Windows audio-device association to macOS. A game or test program
must choose this endpoint and provide the correct four channels. It cannot become
the default output or system-alert device.

## Build and verification

On a Mac with Xcode command-line tools and CMake:

```sh
cmake -S macos/audio -B .build/macos-audio -DCMAKE_BUILD_TYPE=Release
cmake --build .build/macos-audio --parallel
ctest --test-dir .build/macos-audio --output-on-failure
```

Build outputs are `MadSenseAudio.driver`, `libmadsense_audio.dylib`, and the test
executables. The ring and recovery tests are also portable C++17. The plugin
contract test invokes the real plugin's COM/HAL interface through an in-process host; it does not prove
the operating system will load the driver. `madsense-audio-installed-probe` is an
additional, opt-in end-to-end test against the installed HAL driver. It sends a
small test signal only to this virtual endpoint and needs no controller.
It checks complete frames on all four channels, stop/start generations, rejection
of another process's claim and PCM read, and recovery after a consumer is killed
without cleanup while the render client remains active. It reports silent and
dropped frames separately; passing does not establish uninterrupted realtime
delivery or controller latency. Exit 75 means initial discovery found no loaded
endpoint and may be retried; transfer or ownership failures return 1.

After the first 2048 verified frames, the probe compares two warm transfer windows
of about three seconds each: the former default-QoS/five-millisecond idle wait,
then the backend's scoped audio-QoS/one-millisecond idle wait. After enabling audio
QoS, it validates at least another 2048 frames before the second window to avoid
attributing the previous policy's remaining backlog to the new policy. The
`audio_qos_rewarm` line reports all drops during that transition; no queue is
flushed. Full packets are drained immediately in both windows. Each
`warm_measurement_not_latency_guarantee` line identifies the policy and reports
elapsed time, read calls (including empty/full counts), received frames/samples,
verified and silent frames, the dropped-frame increase (including a separate
first-read increase to expose any boundary backlog), total/maximum
`ms_audio_read` RPC duration, actual total/maximum idle wait, maximum inter-read
gap, and generation changes. This separates initial/reconnect gaps from loss
during continuing delivery and distinguishes idle scheduling gaps from time
inside CoreAudio. Corrupt or absent signal fails; VM scheduling delays or dropped
frames are reported without an arbitrary timing threshold. These metrics are
evidence about the tested host, not an end-to-end latency or realtime guarantee.

For a distributable build, sign the library and driver with the application's
Developer ID identity, notarize the package, and verify the install on a clean Mac.
Do not represent an unsigned CI artifact as a finished installer.

## Explicit developer installation

Installing a HAL plugin changes system audio and requires an administrator. The
app and build do not install it or restart audio automatically. Close audio apps
before running these commands on a dedicated development Mac:

```sh
sudo mkdir -p /Library/Audio/Plug-Ins/HAL
sudo ditto .build/macos-audio/MadSenseAudio.driver /Library/Audio/Plug-Ins/HAL/MadSenseAudio.driver
sudo chown -R root:wheel /Library/Audio/Plug-Ins/HAL/MadSenseAudio.driver
sudo chmod -R go-w /Library/Audio/Plug-Ins/HAL/MadSenseAudio.driver
sudo killall coreaudiod
.build/macos-audio/madsense-audio-installed-probe
```

For removal, remove the single `MadSenseAudio.driver` bundle from the same HAL
directory and restart CoreAudio after closing audio apps. Preserve other plugins.

## Consumer contract and privacy

First require `ms_audio_abi_version()` to return 2; a missing export or any other
version is incompatible. Call `ms_audio_open` once, then `ms_audio_read` on a
dedicated worker with the scoped audio QoS override. Drain full packets immediately
and use a blocking, cancellable one-millisecond idle wait after shorter packets.
QoS is a scheduling hint and does not confer real-time scheduling privileges.
Supply room for at least 480 frames × four float32 samples. Close with
`ms_audio_close` after joining the reader worker. The functions are not safe to
call concurrently on the same handle. The backend must separately forward the
four channels into its Bluetooth audio pipeline; the consumer does not synthesize
haptics from speaker channels.

The final `ms_audio_read` output is a `uint64_t` stream generation. Discard any
partially assembled PCM when it changes, before processing the returned frames.
The generation advances and the native ring is cleared on the first IO start after
all clients have stopped, including a stop/start completed between consumer polls.
Ownership transfer or recovery after lease expiry also advances the generation.
PCM generations are separate from the HAL clock seed: owner/lease transitions
flush PCM without resetting that clock; the first IO start advances both. Queue
frames carry their generation so an old producer completing after a flush cannot
publish stale PCM into the new stream.
Once a host cycle counter greater than one has established a stream,
every subsequent cycle-one WriteMix operation is rejected in full, including
consecutive cycle-one operations. Initial cycle-one operations remain accepted
until establishment. A successful compare/exchange advances only the producer's
captured PCM generation; an intervening owner, lease or start transition is never
overwritten. Recovery preserves the established state and the HAL clock seed;
an external generation change clears that state. Each rejected operation
increments the dropped-frame count; the reader also counts older queued frames
that it filters out.
After copying a read's candidates and acquiring its drop snapshot, a final
generation check counts and suppresses those candidates if recovery changed
their epoch. It does not relabel old samples as a new generation. This final
check completes the read; a later transition cannot revoke an already completed
packet. Recovery deliberately loses frames and does not repair the source's
resynchronization or establish uninterrupted delivery.
An increased dropped-frame count denotes a queue discontinuity and likewise
requires flushing partially assembled PCM before accepting subsequent frames.
The CFData packet protocol is version 2; package the matching plugin, consumer
library, and backend together. An older protocol is rejected by the consumer.

The device advertises a 16,384-frame zero-timestamp period, matching Apple's
NullAudio example and exceeding the SDK's documented 10,923-frame minimum.
The same constant controls the clock calculation. This is clock granularity,
not PCM queue capacity or a requested client buffer size. It does not eliminate
CoreAudio resynchronization under load; the isolated research probe retains
source failures and recovery losses separately from reader integrity.

The plugin's realtime callback uses a bounded SPSC queue and lock-free generation
and drop counters. The queue and recovery policy require one serialized HAL
WriteMix producer; they are not safe for concurrent producers. Control paths
advance the atomic generation rather than accessing the producer's policy state.
The ordinary CoreAudio property thread serializes reads and marshals supported
`CFData` property lists. Each read returns at most 480 frames (10 ms), in FIFO order.
Normal 512- and 1024-frame HAL blocks are preserved across successive reads,
including their final 32 or 64 frames. The caller should read again immediately
after a full 480-frame packet. Only a backlog above 1024 frames (21.33 ms) is
trimmed to the most recent 480 frames. Producer overruns invalidate all queued
audio so a stalled reader does not replay an old full queue. The owner lease also
flushes queued audio after a read stall of at least one second.

One process owns the consumer. CoreAudio supplies the actual client PID; the
plugin obtains its effective UID and process birth time together with the public
`sysctl(CTL_KERN, KERN_PROC, KERN_PROC_PID)` query. It requires a successful call,
an exact `kinfo_proc` size, a matching PID, a live non-zombie process, and a valid
birth time. Only the owner
can retrieve PCM. IO may start only for registered clients with the same UID, so
other users' audio is rejected before mixing. Unknown/inaccessible identities fail
closed. The one-second lease stops collection if the reader dies or stalls. After
expiry a replacement owner can claim while clients from its own user are running,
but cannot take a stream with another user's active clients. Same-user apps can
play to the endpoint; this is not an application whitelist or a DRM boundary.
The actual HAL sandbox's permission to query client identities must be verified
with the installed probe; rejection must remain an error, not weaken checks.

The original full `PROC_PIDTBSDINFO` lookup failed with `EPERM` in the installed
HAL helper, which runs as a different system user. The replacement uses public
headers and retains the identity checks, but its compatibility remains a
development limitation: Apple's [QA1361](https://developer.apple.com/library/archive/qa/qa1361/_index.html)
identifies `kinfo_proc` as `__APPLE_API_UNSTABLE` and recommends restricting its
use to debug builds. Exact-size checks fail closed if that ABI changes. A passing
installed-driver probe establishes behavior only on the tested macOS build;
it does not establish a stable production identity API or shipping readiness.

The transport deliberately avoids world-readable shared memory, arbitrary Unix
sockets, and client CoreAudio API calls from inside the plugin. The latter are
prohibited in the HAL host. See Apple's
[HAL sandbox and IPC requirements](https://developer.apple.com/library/archive/qa/qa1811/_index.html)
and [AudioServerPlugInDriverInterface](https://developer.apple.com/documentation/coreaudio/audioserverplugindriverinterface).

## Remaining validation

Mac CI must compile the driver and run the ring/recovery/contract tests. The native
contract test exercises `{2, 1, 1, 2}` host cycles with distinct timestamps and old
prefixes below the backlog threshold, verifies exact rejection/drop counts and
fresh surviving PCM, and checks that an owner transition accepts initial cycle
one again. Portable tests cover a late old-generation publication and a read
racing recovery, including delivered-plus-dropped frame accounting. An
installed-driver test must validate HAL discovery, IO start, custom-property marshalling, privacy
checks, reconnects, and owner exit. Real controller tests must then establish
channel mapping, Bluetooth continuity, latency, and game compatibility. These are
separate from compiling the library and must not be reported as completed by the
in-process tests.
