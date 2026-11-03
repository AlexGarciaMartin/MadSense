using System.Net.Sockets;
using System.Text;

namespace MadSense;

sealed class SingleInstance : IDisposable
{
    readonly FileStream gate;
    readonly Socket server;
    readonly CancellationTokenSource lifetime = new();
    readonly string socketPath;
    readonly Task listening;
    readonly Lock requests = new();
    Action<bool>? handler;
    bool pendingShow, pendingStop;
    public void SetHandler(Action<bool> callback)
    {
        bool show, stop;
        lock (requests) { handler = callback; show = pendingShow; stop = pendingStop; pendingShow = pendingStop = false; }
        if (show || stop) callback(stop);
    }
    void Deliver(bool stop)
    {
        Action<bool>? callback;
        lock (requests)
        {
            callback = handler;
            if (callback == null) { if (stop) pendingStop = true; else pendingShow = true; return; }
        }
        callback(stop);
    }
    SingleInstance(FileStream gate, string path)
    {
        this.gate = gate; socketPath = path;
        if (File.Exists(path)) File.Delete(path); // Owned directory, stale socket after a crash.
        server = new(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
        server.Bind(new UnixDomainSocketEndPoint(path)); server.Listen(4);
        if (OperatingSystem.IsLinux()) File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
        listening = Task.Run(Listen);
    }
    static string DirectoryPath()
    {
        var runtime = Environment.GetEnvironmentVariable("XDG_RUNTIME_DIR");
        var path = !string.IsNullOrEmpty(runtime) && Path.IsPathFullyQualified(runtime)
            ? Path.Combine(runtime, "madsense") : Path.Combine(AppPaths.State, "run");
        Directory.CreateDirectory(path);
        if (new DirectoryInfo(path).LinkTarget != null) throw new IOException("MadSense runtime directory must not be a symbolic link.");
        if (OperatingSystem.IsLinux()) File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        return path;
    }
    public static SingleInstance? Open(bool stop)
    {
        var directory = DirectoryPath(); var path = Path.Combine(directory, "control.sock");
        FileStream? gate = null;
        try { gate = new FileStream(Path.Combine(directory, "instance.lock"), FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
        catch (IOException)
        {
            Send(path, stop).GetAwaiter().GetResult(); return null;
        }
        if (stop) { gate.Dispose(); return null; }
        try { return new(gate, path); } catch { gate.Dispose(); throw; }
    }
    static async Task Send(string path, bool stop)
    {
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
        // The first process may hold its lock a moment before binding the socket.
        while (true)
        {
            using var client = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
            try
            {
                await client.ConnectAsync(new UnixDomainSocketEndPoint(path), timeout.Token);
                var bytes = Encoding.ASCII.GetBytes(stop ? "stop\n" : "show\n");
                var sent = 0;
                while (sent < bytes.Length)
                {
                    var count = await client.SendAsync(bytes.AsMemory(sent), SocketFlags.None, timeout.Token);
                    if (count == 0) throw new IOException("The running MadSense instance closed its control socket.");
                    sent += count;
                }
                return;
            }
            catch (SocketException) when (!timeout.IsCancellationRequested) { await Task.Delay(40, timeout.Token); }
        }
    }
    async Task Listen()
    {
        try
        {
            while (!lifetime.IsCancellationRequested)
            {
                using var client = await server.AcceptAsync(lifetime.Token);
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
                timeout.CancelAfter(TimeSpan.FromSeconds(2));
                var bytes = new byte[16]; var length = 0;
                try
                {
                    while (length < bytes.Length && (length == 0 || bytes[length - 1] != '\n'))
                    {
                        var read = await client.ReceiveAsync(bytes.AsMemory(length), SocketFlags.None, timeout.Token);
                        if (read == 0) break; length += read;
                    }
                    var command = Encoding.ASCII.GetString(bytes, 0, length);
                    if (command is "stop\n" or "show\n") Deliver(command == "stop\n");
                }
                catch (Exception error) when (error is OperationCanceledException or SocketException) { }
            }
        }
        catch (Exception error) when (error is OperationCanceledException or SocketException or ObjectDisposedException) { }
    }
    public void Dispose()
    {
        lifetime.Cancel(); server.Dispose(); listening.GetAwaiter().GetResult();
        try { File.Delete(socketPath); } finally { gate.Dispose(); lifetime.Dispose(); }
    }
}
