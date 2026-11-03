# Broker progress with a blocked thread pool

This standalone probe links the production Linux platform client. It uses a
synthetic protocol peer over real loopback TCP, without privileged operations,
devices, helper installation or changes to the production socket path.

Each operation runs in its own child process, first with the ordinary pool and
then with one maximum worker. The parent never changes its pool. Acquisition
finishes before the clamp; one worker then calls the actual synchronous HID
GetFeatureReport, Read, Write, SendFeatureReport, TryAttach or TryDetach API.
Detach establishes its own attachment before the pool clamp. A dedicated peer thread
receives and checks the request. The supervisor confirms that the caller is
waiting before allowing an input event and the exact reply. For Read, this
barrier ensures the queue was empty when the synchronous call began.

The supervisor and peer are synchronous threads outside the pool. An input event
precedes each RPC reply, so received-report counts distinguish a stalled IPC
reader from a stalled caller continuation. The two-second deadline is a
deterministic liveness bound, **not a latency target or benchmark**. The parent
also has a 15-second process watchdog and only kills the child it created.

After the observation, the original pool limits are restored in that child.
Every operation must unwind with exact payload/result values and complete the
normal release/ACK. The parent exits nonzero if any restricted case did not
finish, input was not received, payload verification failed or cleanup was not
acknowledged. The JSON includes every control and restricted result. No ThreadPool
limits leak into NUnit or another test process.

The restricted cases also force a release race: after the peer receives release,
an independent worker occupies the sole pool slot before the peer sends its ACK
and closes. The dedicated reader must process both before the worker is freed.
The ACK's queued continuation can then race synchronous lifetime cancellation;
the already validated reply must survive. `releaseEofWhileWorkerHeld=true`
records this barrier. An EOF without an ACK is covered separately and must never
set cleanup confirmed.

```sh
dotnet run --project linux/platform/tests/starvation/StarvationProbe.csproj \
  --configuration Release > starvation.json
```

On Windows, the unmodified `a71108f` client reproduced all six restricted
failures: the peer sent input and reply, but received/read counts stayed zero
until the pool clamp was removed. All six ordinary-pool controls completed, and
all twelve cases then returned exact values and acknowledged release. The client
with a dedicated blocking reader, synchronous RPC waits and a pool-independent
synchronous input wait passes all twelve cases while the clamp is still active.
This proves
the shared-pool dependency of that client, not the timing of the native VM's
separate `client_stalled` failure. The first native run (`8743326`, run
41619490187, both Linux architectures) exposed the ACK/EOF race during cleanup.
The additional barrier reproduces that failure on Windows too. After preserving
validated replies across lifetime cancellation, all twelve cases pass locally;
the native rerun remains pending.

For a controlled before/after comparison, `PlatformSourceDirectory` can point to
a separate directory containing the exact platform `.cs` files from a recorded
commit. This changes linked test sources only; no old implementation is copied
into the repository or production build.
