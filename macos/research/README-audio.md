# Audio path research

This opt-in executable measures the existing four-channel HAL output endpoint
and its authenticated custom-property reader. It is not shipped in the app.
Neither the production plugin, its queue limits, its authentication nor the
native consumer ABI is changed. It uses no controller, microphone, input
IOProc, process tap, system-default audio selection or physical speaker.

Run the dispatch-only **macOS audio path research** workflow for disposable
macOS 15 ARM and Intel VMs. It builds and installs the current HAL plugin using
the existing CI-only installer, verifies its existing integration tests, and
then saves JSONL results and CoreAudio diagnostics. Its installation guard
refuses ordinary developer machines and self-hosted runners.

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
wait against the backend's scoped user-interactive QoS and requested 1 ms
wait. Pair order alternates across three trials, with two seconds of warmup
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

This is a software path experiment on shared CI VMs. It does not measure
Bluetooth transmission, Opus/forwarder buffering, USB transfers, controller
firmware, actuator response, physical output latency, or game compatibility.
No fixed timing threshold determines success: data integrity and cleanup are
checked, while scheduler stalls and drops remain visible in the artifact.
