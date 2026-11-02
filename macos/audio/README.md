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
executables. The ring test is also portable C++17. The plugin contract test invokes
the real plugin's COM/HAL interface through an in-process host; it does not prove
the operating system will load the driver. `madsense-audio-installed-probe` is an
additional, opt-in end-to-end test against the installed HAL driver. It sends a
small test signal only to this virtual endpoint and needs no controller.

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

Call `ms_audio_open` once, then `ms_audio_read` on a normal worker about every 5 ms.
Supply room for at least 480 frames × four float32 samples. Close with
`ms_audio_close` after joining the reader worker. The functions are not safe to
call concurrently on the same handle. The backend must separately forward the
four channels into its Bluetooth audio pipeline; the consumer does not synthesize
haptics from speaker channels.

The plugin's realtime callback only copies into a lock-free bounded SPSC queue.
The ordinary CoreAudio property thread serializes reads and marshals supported
`CFData` property lists. A read returns at most the most recent 10 ms in the queue;
producer overruns invalidate queued audio so stalls do not play old effects.

One process owns the consumer. CoreAudio supplies the actual client PID; the
plugin verifies its UID and process birth time with `proc_pidinfo`. Only the owner
can retrieve PCM. IO may start only for registered clients with the same UID, so
other users' audio is rejected before mixing. Unknown/inaccessible identities fail
closed. The one-second lease stops collection if the reader dies or stalls. After
expiry a replacement owner can claim while clients from its own user are running,
but cannot take a stream with another user's active clients. Same-user apps can
play to the endpoint; this is not an application whitelist or a DRM boundary.
The actual HAL sandbox's permission to query client identities must be verified
with the installed probe; rejection must remain an error, not weaken checks.

The transport deliberately avoids world-readable shared memory, arbitrary Unix
sockets, and client CoreAudio API calls from inside the plugin. The latter are
prohibited in the HAL host. See Apple's
[HAL sandbox and IPC requirements](https://developer.apple.com/library/archive/qa/qa1811/_index.html)
and [AudioServerPlugInDriverInterface](https://developer.apple.com/documentation/coreaudio/audioserverplugindriverinterface).

## Remaining validation

Mac CI must compile the driver and run the ring/contract tests. An installed-driver
test must validate HAL discovery, IO start, custom-property marshalling, privacy
checks, reconnects, and owner exit. Real controller tests must then establish
channel mapping, Bluetooth continuity, latency, and game compatibility. These are
separate from compiling the library and must not be reported as completed by the
in-process tests.
