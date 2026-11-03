# Audio path research

This opt-in executable measures the existing four-channel HAL output endpoint
and its authenticated custom-property reader. It is not shipped in the app.
The production plugin, queue limits, authentication and native consumer ABI
remain unchanged. The research workflow builds an instrumented source copy
with the ingress observer described below. It uses no controller, microphone, input
IOProc, process tap, system-default audio selection or physical speaker.

The **macOS audio path research** workflow runs on changes to this experiment
on `feature/macos`, and supports manual dispatch on disposable macOS 15 ARM
and Intel VMs. It builds and installs the current HAL plugin using
the existing CI-only installer, verifies its existing integration tests, and
then saves JSONL results and CoreAudio diagnostics. Its installation guard
refuses ordinary developer machines and self-hosted runners.

The current research matrix compares timestamp periods of **256 and 16384
frames** on both architectures. The actual runner's Apple SDK documents a
minimum of 10923 frames for `kAudioDevicePropertyZeroTimeStampPeriod`; 256 is
retained only as the known noncompliant control. The generated variants differ
in one constant shared by the property and `GetZeroTimeStamp` calculation.
Production uses the compliant 16384 constant; the generator overrides its three
uses only in the isolated source copy to preserve the explicit 256 control.
Queue capacity, drop thresholds, nominal sample rate and reader behavior do
not change, and no client buffer size is requested. HAL may choose a different
buffer size, which the probe records. No discontinuity-discard policy is added.
Artifacts include the chosen period, source digests, SDK version/header digest
and extracted minimum instead of redistributing the full SDK header. Separate
VMs run each variant, so between-host variation still limits timing comparisons.

On a development Mac where the endpoint is already installed and unused:

```sh
bash macos/research/build_audio_probe.sh .build/audio-research
.build/audio-research/audio-path-probe --seconds 30 --trials 3 --warmup 2 --control-iterations 5000
```

The controls measure a self-process `KERN_PROC_PID` identity snapshot, an
authenticated custom `CFBoolean` owner query, and an authenticated empty
`CFData` read. Each runs up to 5,000 calls or 20 seconds. The standalone sysctl
control runs in the app process; it does **not** isolate sysctl's cost inside
the sandboxed audio host. Empty and owner RPCs include HAL IPC, scheduling and
authentication. Differences between their distributions are comparisons, not
exact subtraction of component CPU costs. Full 480-frame RPCs are measured
separately during streaming. The driver never exposes an unauthenticated
benchmark operation.

Each streaming pair compares default scheduling with a requested 5 ms idle
wait against scoped user-interactive QoS and a requested 1 ms wait. These are
the backend's QoS/requested-idle settings, but this C++ executable waits with
`std::this_thread::sleep_for`; the managed backend uses a cancellable
`WaitHandle.WaitOne`. This comparison does not measure the managed reader,
GC, forwarding or backend performance directly. Pair order alternates across three trials, with two seconds of warmup
before each 30-second measurement. No requested audio buffer size is imposed;
the reported size and actual callback sizes are recorded.

The output IOProc writes exact binary fraction markers containing a callback
identifier, frame offset and integrity check. It records callback entry/end,
the supplied presentation timestamp and sequence origin in preallocated
immutable records, published atomically. It does no allocation, lock, logging
or IPC. Every returned non-silent frame must decode correctly. Unsupported
buffer layouts, mixed/scaled samples, duplicate or reordered frames fail the
probe. Silence and sequence gaps are counted separately. Sequence gaps omit
the unobserved prefix/tail of a measurement; they need not equal the driver's
drop counter, which has different boundaries and can include silent frames.

JSON statistics report count, minimum, p50, p95, p99 and maximum in milliseconds:

- `callback_entry_to_read_return_ms`: frame-weighted elapsed time from the
  source callback's entry to the reader's return. Every frame generated in a
  callback shares its entry time. This includes the source callback, HAL
  transport/mixing, plugin queue, reader scheduling and custom-property RPC.
- `read_return_minus_scheduled_frame_presentation_ms`: signed offset from each
  frame's planned CoreAudio presentation. Negative values are valid: the frame
  reached the reader before that scheduled time. This is **not** callback age.
- `read_rpc_ms`, `full480_rpc_ms`, `empty_rpc_ms`: synchronous reader-call wall
  duration, including any descheduling. Long wall time does not prove CPU cost.
- `actual_idle_ms`, `between_reads_ms`, `callback_gap_ms`, `callback_body_ms`:
  observed scheduling and callback timing; the requested sleep is not assumed
  to equal the actual wait.
- `reader_thread_cpu_ms`: CPU time of the reader thread;
  `probe_process_cpu_ms`: all benchmark-process threads, including the source
  IOProc, marker validation and measurements. Neither includes the separate
  CoreAudio host. Per-frame decoding and statistics collection contribute to
  the reader cost; both policies use the same instrumentation.

`data_integrity` requires nonempty verified PCM with no duplicate/reordered
frames or overlapping source callbacks. A lock-free guard detects unexpected
concurrent IOProc invocation, leaves that callback's HAL-zeroed output alone,
and invalidates the trial instead of allowing metadata races.
`stream_discontinuity` records a generation change or a read with no
active output client. `measurement_valid` additionally requires no such
discontinuity. These flags do not hide the counters or claim zero frame loss:
drops and gaps remain separate, and age statistics describe received frames.
`first_nonincreasing_frames` retains up to 32 violations with the prior highest
and current callback/offset/sequence, reader call/frame index, native drop and
generation counters, and raw mach timestamps. These timestamps can be compared
with CoreAudio overload logs on the same host. Later trial pairs still run
after a continuity failure; the final process exit remains unsuccessful if
any trial was invalid. RPC/layout/corrupt-marker errors still abort immediately.

The workflow additionally builds a **research-only ingress observer** before
installing its disposable HAL bundle. `instrument_audio_plugin.py` generates
a source copy under `.build`, requiring every injection site to match exactly
once. It records production/copy SHA-256 digests; no production source, normal
CMake target or shipped bundle enables this observer. The generated copy
retains the original `ring.push` and observes its unchanged input after the
normal lease/running checks. It stores up to 32 anomalous WriteMix blocks,
their preceding block, first eight and last bad markers, frame counts,
client/operation, cycle ordinal, and current/input/output sample and host
timestamps. The timestamp flags indicate which fields CoreAudio supplied.
Per-generation ordering/counters reset when the stream restarts. An atomic
try-guard counts overlapping plugin callbacks; it never waits, allocates or
logs on the realtime thread and does not skip the original audio write.
Any overlap invalidates subsequent diagnostic trials.

After StopIO, the same authenticated owner can read the bounded trace through
a research-only CFData property. Reads during IO, anonymous/foreign callers,
and snapshots racing a lingering observer fail. Previous-generation storage
is scrubbed and changing owner invalidates the prior trace. The native fake
host test checks this access contract. `audio_ingress_trace` JSON rows include
warmup, unlike the measured reader window, and retain failures even when the
ring subsequently drops those frames. They distinguish repeated markers
already present at plugin ingress from failures introduced after ingress.
The observer adds per-frame work to the host: these instrumented runs diagnose
integrity, and their timing must not be presented as unchanged-runtime costs.
`--require-ingress 1` fails if the expected research property is absent; without
that option the executable still supports an ordinary installed endpoint.

This is a software path experiment on shared CI VMs. It does not measure
Bluetooth transmission, Opus/forwarder buffering, USB transfers, controller
firmware, actuator response, physical output latency, or game compatibility.
No fixed timing threshold determines success: data integrity and cleanup are
checked, while scheduler stalls and drops remain visible in the artifact.
