using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Text.Json;
using MadSense.Platform;

// The parent never modifies its pool. Each child uses real loopback sockets and
// the linked production client; only the protocol peer is synthetic. The peer,
// supervisor and deadline are synchronous threads outside that child's pool.
static class Program
{
    static readonly byte[] Input = [0x31, 0x12, 0x34, 0x56];
    static readonly byte[] Feature = [0x05, 0x76, 0x54, 0x32];
    static readonly string[] Operations = ["get_feature", "read", "write", "send_feature", "attach", "detach"];

    static int Main(string[] args)
    {
        try
        {
            if (args.Length == 3 && args[0] == "--child") return Child(args[1], args[2] == "bounded");
            if (args.Length != 0) throw new ArgumentException("Use no arguments (suite), or --child OP bounded|control.");
            var cases = new List<JsonElement>();
            foreach (string operation in Operations)
            foreach (string pool in new[] { "control", "bounded" })
            {
                string executable = Environment.ProcessPath ?? throw new IOException("Missing executable path.");
                var start = new ProcessStartInfo(executable)
                { UseShellExecute = false, RedirectStandardOutput = true, RedirectStandardError = true };
                if (Path.GetFileNameWithoutExtension(executable).Equals("dotnet", StringComparison.OrdinalIgnoreCase))
                    start.ArgumentList.Add(Assembly.GetExecutingAssembly().Location);
                foreach (string argument in new[] { "--child", operation, pool }) start.ArgumentList.Add(argument);
                using var child = Process.Start(start) ?? throw new IOException("Child did not start.");
                // Child emits one bounded JSON record, so pipe capacity cannot
                // turn this external 15-second kill into a hidden task-pool wait.
                if (!child.WaitForExit(15000))
                {
                    child.Kill(entireProcessTree: true); child.WaitForExit();
                    throw new TimeoutException($"Owned child watchdog expired: {operation}/{pool}");
                }
                string stdout = child.StandardOutput.ReadToEnd(), stderr = child.StandardError.ReadToEnd();
                if (child.ExitCode != 0) throw new IOException($"Child failed: {operation}/{pool}: {stdout} {stderr}");
                using var document = JsonDocument.Parse(stdout);
                cases.Add(document.RootElement.Clone());
            }
            bool passed = cases.All(value => value.GetProperty("completedWhilePoolRestricted").GetBoolean()
                && value.GetProperty("exactResult").GetBoolean()
                && value.GetProperty("receivedReportsAtDeadline").GetInt64() == 1
                && value.GetProperty("cleanupConfirmed").GetBoolean());
            Console.WriteLine(JsonSerializer.Serialize(new { result = passed ? "passed" : "blocked", cases }));
            return passed ? 0 : 1;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error); return 2;
        }
    }

    static int Child(string operation, bool bounded)
    {
        if (!Operations.Contains(operation)) throw new ArgumentException("Unknown operation.");
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        using var operationEntered = new ManualResetEventSlim();
        using var requestReceived = new ManualResetEventSlim();
        using var allowReply = new ManualResetEventSlim();
        using var replySent = new ManualResetEventSlim();
        using var operationCompleted = new ManualResetEventSlim();
        using var peerCompleted = new ManualResetEventSlim();
        Exception? peerError = null, operationError = null;
        Thread? worker = null;
        object? actual = null;
        var peer = new Thread(() =>
        {
            try
            {
                using TcpClient accepted = listener.AcceptTcpClient();
                accepted.ReceiveTimeout = accepted.SendTimeout = 10000;
                using NetworkStream stream = accepted.GetStream();
                using var reader = new StreamReader(stream, Encoding.UTF8, leaveOpen: true);
                using var writer = new StreamWriter(stream, new UTF8Encoding(false), leaveOpen: true) { AutoFlush = true };
                JsonElement ReadRequest()
                {
                    using var value = JsonDocument.Parse(reader.ReadLine() ?? throw new IOException("Unexpected client EOF."));
                    return value.RootElement.Clone();
                }
                void Send(object value) => writer.WriteLine(JsonSerializer.Serialize(value));
                JsonElement acquire = ReadRequest();
                if (acquire.GetProperty("op").GetString() != "acquire") throw new IOException("Expected acquisition.");
                Send(new { id = acquire.GetProperty("id").GetInt64(), ok = true,
                    result = new { isolationVerified = true, path = "/dev/synthetic-hidraw", vendorId = 0x054c,
                        productId = 0x0ce6, productName = "Synthetic starvation peer" } });
                if (operation == "detach")
                {
                    JsonElement setup = ReadRequest();
                    if (setup.GetProperty("op").GetString() != "attach" || setup.GetProperty("busId").GetString() != "1-1")
                        throw new IOException("Detach must first establish its own attachment.");
                    Send(new { id = setup.GetProperty("id").GetInt64(), ok = true, result = new { attached = true } });
                }
                JsonElement request = default;
                if (operation != "read")
                {
                    request = ReadRequest();
                    if (request.GetProperty("op").GetString() != operation) throw new IOException("Unexpected HID request.");
                    if (operation == "get_feature" && (request.GetProperty("reportId").GetInt32() != 5 ||
                        request.GetProperty("size").GetInt32() != 4)) throw new IOException("Wrong feature arguments.");
                    if (operation is "write" or "send_feature" &&
                        !Convert.FromBase64String(request.GetProperty("data").GetString()!).SequenceEqual(Input))
                        throw new IOException("Wrong output bytes.");
                    if (operation is "attach" or "detach" && request.GetProperty("busId").GetString() != "1-1")
                        throw new IOException("Wrong owned bus id.");
                    if (operation == "attach" && (request.GetProperty("host").GetString() != "127.0.0.1" ||
                        request.GetProperty("port").GetInt32() != 3241)) throw new IOException("Wrong export endpoint.");
                    requestReceived.Set();
                }
                if (!allowReply.Wait(5000)) throw new TimeoutException("Supervisor did not release reply.");
                // An input event before the RPC reply distinguishes IPC-reader
                // progress from completion of the blocked synchronous caller.
                Send(new { @event = "input", data = Convert.ToBase64String(Input), brokerReadMonotonicNs = 123456789L });
                if (operation != "read")
                {
                    object result = operation switch
                    {
                        "get_feature" => new { data = Convert.ToBase64String(Feature) },
                        "write" => new { written = Input.Length },
                        _ => new { sent = true }
                    };
                    Send(new { id = request.GetProperty("id").GetInt64(), ok = true, result });
                }
                replySent.Set();
                JsonElement release = ReadRequest();
                if (release.GetProperty("op").GetString() != "release") throw new IOException("Expected release.");
                Send(new { id = release.GetProperty("id").GetInt64(), ok = true, result = new { released = true } });
            }
            catch (Exception error) { peerError = error; }
            finally { peerCompleted.Set(); }
        }) { IsBackground = true, Name = "starvation-synthetic-peer" };
        peer.Start();
        using var socket = new TcpClient();
        socket.Connect((IPEndPoint)listener.LocalEndpoint);
        var client = new LinuxBrokerClient(socket.GetStream());
        LinuxPhysicalSession session = client.AcquirePhysicalAsync("/dev/synthetic-hidraw").GetAwaiter().GetResult();
        if (operation == "detach" && !session.AttachStrategy.TryAttach("127.0.0.1", 3241, "1-1", out string? setupError))
            throw new IOException("Detach setup failed: " + setupError);
        ThreadPool.GetMaxThreads(out int maxWorkers, out int maxIo);
        ThreadPool.GetMinThreads(out int minWorkers, out int minIo);
        if (bounded && (!ThreadPool.SetMinThreads(1, minIo) || !ThreadPool.SetMaxThreads(1, maxIo)))
            throw new InvalidOperationException("Cannot establish isolated one-worker pool.");
        bool completed = false;
        long receivedAtDeadline = 0, readAtDeadline = 0;
        double elapsed = 0;
        try
        {
            ThreadPool.QueueUserWorkItem(_ =>
            {
                worker = Thread.CurrentThread;
                operationEntered.Set();
                try
                {
                    switch (operation)
                    {
                        case "get_feature": actual = session.Device.GetFeatureReport(5, 4); break;
                        case "write": actual = session.Device.Write(Input, 0, Input.Length); break;
                        case "send_feature": session.Device.SendFeatureReport(Input, 0, Input.Length); actual = true; break;
                        case "attach":
                            actual = session.AttachStrategy.TryAttach("127.0.0.1", 3241, "1-1", out string? attachError);
                            if (actual is false) throw new IOException(attachError); break;
                        case "detach":
                            actual = session.AttachStrategy.TryDetach("1-1", out string? detachError);
                            if (actual is false) throw new IOException(detachError); break;
                        case "read":
                            byte[] buffer = new byte[Input.Length];
                            int count = session.Device.Read(buffer, 0, buffer.Length, 10000);
                            actual = count == buffer.Length ? buffer : Array.Empty<byte>(); break;
                    }
                }
                catch (Exception error) { operationError = error; }
                finally { operationCompleted.Set(); }
            });
            if (!operationEntered.Wait(3000)) throw new TimeoutException("Worker never entered HID call.");
            if (operation != "read" && !requestReceived.Wait(3000))
                throw new TimeoutException("HID request did not reach the independent peer.");
            // No input is queued until the actual synchronous caller waits.
            // This avoids accidentally exercising Read's already-buffered path.
            if (!SpinWait.SpinUntil(() => (worker!.ThreadState & System.Threading.ThreadState.WaitSleepJoin) != 0, 3000))
                throw new TimeoutException("Caller did not block before the peer reply.");
            var clock = Stopwatch.StartNew();
            allowReply.Set();
            if (!replySent.Wait(3000)) throw new TimeoutException("Independent peer did not send reply.");
            completed = operationCompleted.Wait(2000);
            elapsed = clock.Elapsed.TotalMilliseconds;
            receivedAtDeadline = session.Metrics.ReceivedReports;
            readAtDeadline = session.Metrics.ReadReports;
        }
        finally
        {
            // Releasing the clamp lets an old client unwind without relying on
            // cancellation timers or async disposal while the pool is blocked.
            if (bounded)
            {
                ThreadPool.SetMaxThreads(maxWorkers, maxIo);
                ThreadPool.SetMinThreads(minWorkers, minIo);
            }
            allowReply.Set();
        }
        if (!operationCompleted.Wait(7000)) throw new TimeoutException("Operation did not unwind after pool restoration.");
        bool exact = operationError is null && operation switch
        {
            "get_feature" => actual is byte[] feature && feature.SequenceEqual(Feature),
            "read" => actual is byte[] input && input.SequenceEqual(Input),
            "write" => actual is int written && written == Input.Length,
            _ => actual is true
        };
        session.DisposeAsync().AsTask().GetAwaiter().GetResult();
        if (!peerCompleted.Wait(3000)) throw new TimeoutException("Peer did not finish ordered release.");
        if (peerError is not null) throw new IOException("Synthetic peer failed.", peerError);
        Console.WriteLine(JsonSerializer.Serialize(new { operation, pool = bounded ? "one-worker" : "control",
            completedWhilePoolRestricted = completed, exactResult = exact, receivedReportsAtDeadline = receivedAtDeadline,
            readReportsAtDeadline = readAtDeadline, replyToDeadlineMilliseconds = elapsed,
            error = operationError?.ToString(), cleanupConfirmed = session.CleanupConfirmed,
            scope = "synthetic TCP IPC; no helper, UHID, radio or latency claim" }));
        return 0;
    }
}
