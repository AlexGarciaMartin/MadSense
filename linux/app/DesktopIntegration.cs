using System.Diagnostics;
using Tmds.DBus;

namespace MadSense;

[DBusInterface("org.freedesktop.DBus")]
interface ISessionBus : IDBusObject
{
    Task<bool> NameHasOwnerAsync(string name);
    Task<IDisposable> WatchNameOwnerChangedAsync(Action<(string Name, string OldOwner, string NewOwner)> changed,
        Action<Exception>? onError = null);
}

[DBusInterface("org.kde.StatusNotifierWatcher")]
interface IStatusNotifierWatcher : IDBusObject
{
    Task<T> GetAsync<T>(string property);
    Task<IDisposable> WatchStatusNotifierHostRegisteredAsync(Action changed);
    Task<IDisposable> WatchStatusNotifierHostUnregisteredAsync(Action changed);
}

sealed class TrayAvailability : IDisposable
{
    const string Watcher = "org.kde.StatusNotifierWatcher";
    readonly SemaphoreSlim refresh = new(1, 1);
    readonly Lock resources = new();
    readonly List<IDisposable> hostWatches = [];
    Connection? connection;
    IDisposable? watch;
    ISessionBus? bus;
    Func<IStatusNotifierWatcher>? createWatcher;
    int available, disposed, revision;
    public bool Available => Volatile.Read(ref available) != 0;
    public event Action<bool>? Changed;
    public async Task Start()
    {
        if (!OperatingSystem.IsLinux() || Volatile.Read(ref disposed) != 0) return;
        try
        {
            connection = new Connection(Address.Session!); await connection.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(3));
            await Start(connection.CreateProxy<ISessionBus>("org.freedesktop.DBus", "/org/freedesktop/DBus"),
                () => connection.CreateProxy<IStatusNotifierWatcher>(Watcher, "/StatusNotifierWatcher"));
        }
        catch (Exception error) { LinuxLog.Append("Tray detection: " + error.Message); connection?.Dispose(); Set(false); }
    }
    internal async Task Start(ISessionBus sessionBus, Func<IStatusNotifierWatcher> factory)
    {
        bus = sessionBus; createWatcher = factory;
        var subscription = await WaitSubscription(bus.WatchNameOwnerChangedAsync(change =>
        {
            if (change.Name != Watcher) return;
            Interlocked.Increment(ref revision); Set(false); _ = RefreshHost(reset: true);
        }, _ => { Interlocked.Increment(ref revision); Set(false); }));
        Keep(subscription, host: false);
        await RefreshHost(reset: true);
    }
    async Task RefreshHost(bool reset)
    {
        await refresh.WaitAsync();
        try
        {
            if (Volatile.Read(ref disposed) != 0) return;
            if (reset) ClearHostWatches();
            if (!await bus!.NameHasOwnerAsync(Watcher).WaitAsync(TimeSpan.FromSeconds(3)))
            { ClearHostWatches(); Set(false); return; }
            var watcher = createWatcher!();
            if (hostWatches.Count == 0)
            {
                Keep(await WaitSubscription(watcher.WatchStatusNotifierHostRegisteredAsync(() =>
                    { Interlocked.Increment(ref revision); _ = RefreshHost(false); })), host: true);
                Keep(await WaitSubscription(watcher.WatchStatusNotifierHostUnregisteredAsync(() =>
                    { Interlocked.Increment(ref revision); Set(false); _ = RefreshHost(false); })), host: true);
            }
            // A watcher can run without any panel hosting its icons. Only the
            // registered host property permits hiding the application's window.
            var observed = Volatile.Read(ref revision);
            var registered = await watcher.GetAsync<bool>("IsStatusNotifierHostRegistered").WaitAsync(TimeSpan.FromSeconds(3));
            if (observed == Volatile.Read(ref revision)) Set(registered);
        }
        catch (Exception error) { LinuxLog.Append("Tray host detection: " + error.Message); ClearHostWatches(); Set(false); }
        finally { refresh.Release(); }
    }
    static async Task<IDisposable> WaitSubscription(Task<IDisposable> task)
    {
        try { return await task.WaitAsync(TimeSpan.FromSeconds(3)); }
        catch
        {
            _ = task.ContinueWith(completed => { if (completed.IsCompletedSuccessfully) completed.Result.Dispose(); },
                CancellationToken.None, TaskContinuationOptions.ExecuteSynchronously, TaskScheduler.Default);
            throw;
        }
    }
    void Keep(IDisposable subscription, bool host)
    {
        lock (resources)
        {
            if (Volatile.Read(ref disposed) != 0) subscription.Dispose();
            else if (host) hostWatches.Add(subscription);
            else watch = subscription;
        }
    }
    void ClearHostWatches()
    {
        IDisposable[] subscriptions;
        lock (resources) { subscriptions = hostWatches.ToArray(); hostWatches.Clear(); }
        foreach (var subscription in subscriptions) subscription.Dispose();
    }
    void Set(bool value)
    {
        if (Volatile.Read(ref disposed) != 0) return;
        if (Interlocked.Exchange(ref available, value ? 1 : 0) != (value ? 1 : 0)) Changed?.Invoke(value);
    }
    public void Dispose()
    {
        if (Interlocked.Exchange(ref disposed, 1) != 0) return;
        Interlocked.Exchange(ref available, 0);
        lock (resources) { watch?.Dispose(); watch = null; }
        ClearHostWatches(); connection?.Dispose();
    }
}

static class Autostart
{
    static string Entry => Path.Combine(Path.GetDirectoryName(AppPaths.Config)!, "autostart", "madsense.desktop");
    public static bool Enabled => File.Exists(Entry);
    internal static string Quote(string value)
    {
        if (value.IndexOfAny(['\r', '\n', '\0', '\t', '=']) >= 0) throw new IOException("Invalid executable path for desktop autostart.");
        // Exec unquoting follows generic Desktop Entry string unescaping. Encode
        // both layers: a literal backslash needs four, a dollar sign needs two.
        var quoted = value.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("`", "\\`").Replace("$", "\\$").Replace("%", "%%");
        return "\"" + quoted.Replace("\\", "\\\\") + "\"";
    }
    public static void Set(bool enabled)
    {
        if (!enabled) { File.Delete(Entry); return; }
        var executable = Environment.ProcessPath ?? throw new IOException("Cannot resolve MadSense executable.");
        if (!Path.GetFileName(executable).Equals("MadSense.Linux", StringComparison.Ordinal))
            throw new IOException("Autostart requires the installed MadSense Linux app.");
        Directory.CreateDirectory(Path.GetDirectoryName(Entry)!);
        var content = "[Desktop Entry]\nType=Application\nName=MadSense\nExec=" + Quote(executable) + " --tray\nIcon=madsense\nTerminal=false\nCategories=Game;Utility;\n";
        var pending = Entry + ".tmp";
        try { File.WriteAllText(pending, content); File.Move(pending, Entry, true); }
        finally { if (File.Exists(pending)) File.Delete(pending); }
    }
}
