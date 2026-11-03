using System.Diagnostics;
using Tmds.DBus;

namespace MadSense;

[DBusInterface("org.freedesktop.DBus")]
interface ISessionBus : IDBusObject
{
    Task<bool> NameHasOwnerAsync(string name);
    Task<IDisposable> WatchNameOwnerChangedAsync(Action<(string Name, string OldOwner, string NewOwner)> changed);
}

sealed class TrayAvailability : IDisposable
{
    const string Watcher = "org.kde.StatusNotifierWatcher";
    Connection? connection;
    IDisposable? watch;
    int revision, disposed;
    public bool Available { get; private set; }
    public event Action<bool>? Changed;
    public async Task Start()
    {
        if (!OperatingSystem.IsLinux()) return;
        try
        {
            connection = new Connection(Address.Session!); await connection.ConnectAsync().WaitAsync(TimeSpan.FromSeconds(3));
            var bus = connection.CreateProxy<ISessionBus>("org.freedesktop.DBus", "/org/freedesktop/DBus");
            watch = await bus.WatchNameOwnerChangedAsync(change =>
            { if (change.Name == Watcher) { Interlocked.Increment(ref revision); Set(change.NewOwner.Length != 0); } }).WaitAsync(TimeSpan.FromSeconds(3));
            var observed = Volatile.Read(ref revision);
            var available = await bus.NameHasOwnerAsync(Watcher).WaitAsync(TimeSpan.FromSeconds(3));
            if (observed == Volatile.Read(ref revision)) Set(available);
        }
        catch (Exception error) { LinuxLog.Append("Tray detection: " + error.Message); connection?.Dispose(); Set(false); }
    }
    void Set(bool available) { if (Volatile.Read(ref disposed) != 0) return; Available = available; Changed?.Invoke(available); }
    public void Dispose() { Interlocked.Exchange(ref disposed, 1); watch?.Dispose(); connection?.Dispose(); }
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
