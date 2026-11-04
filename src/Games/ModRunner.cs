using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace MadSense.Games;

/// <summary>
/// Starts a mod's helper program when its game runs and stops it when the game quits, so a mod
/// installed from the catalog needs no clicks per session. The watch runs only while a helper is
/// installed: every two seconds it takes one process snapshot and asks the path of each process
/// it has not seen before, so a quiet desktop costs no handle at all and a game is matched the
/// tick it appears. Nothing is launched twice and nothing outlives its game; MadSense's own exit
/// stops every helper it started.
/// </summary>
sealed class ModRunner : IDisposable
{
    [DllImport("kernel32.dll", SetLastError = true)] static extern nint OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool QueryFullProcessImageNameW(nint handle, int flags, StringBuilder name, ref int size);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(nint handle);

    sealed record Running(string ModId, string GameKey, Process Helper, int GamePid);
    sealed record Watched(InstalledMod Mod, string GameDir);

    readonly ModInstaller installer;
    readonly Func<IReadOnlyList<LibraryGame>> library;
    readonly System.Threading.Timer timer;
    readonly bool watch;
    readonly object gate = new();
    readonly List<Running> running = [];
    HashSet<int> seen = [];
    int busy;

    public event Action<string, string>? HelperStarted;
    public event Action<string, string>? HelperStopped;
    public event Action<string, string>? HelperFailed;

    public ModRunner(ModInstaller installer, Func<IReadOnlyList<LibraryGame>> library, bool watch = true)
    {
        this.installer = installer;
        this.library = library;
        this.watch = watch;
        timer = new System.Threading.Timer(_ => Tick(), null, Timeout.Infinite, Timeout.Infinite);
        installer.Changed += Arm;
        Arm();
    }

    /// <summary>True while the watch ticks: some helper is installed.</summary>
    public bool Watching { get; private set; }

    /// <summary>Helpers MadSense started that are still alive (mod id, game key).</summary>
    public IReadOnlyList<(string ModId, string GameKey)> Active
    {
        get { lock (gate) return running.Where(r => !r.Helper.HasExited).Select(r => (r.ModId, r.GameKey)).ToList(); }
    }

    public bool IsRunning(string modId, string gameKey) { lock (gate) return running.Any(r => r.ModId == modId && r.GameKey == gameKey && !r.Helper.HasExited); }

    /// <summary>Starts the helper now, without waiting for the game (a "try it" button).</summary>
    public bool Start(string modId, string gameKey) => Launch(modId, gameKey, 0);

    public void Stop(string modId, string gameKey)
    {
        Running? entry;
        lock (gate) { entry = running.FirstOrDefault(r => r.ModId == modId && r.GameKey == gameKey); if (entry != null) running.Remove(entry); }
        if (entry != null) Kill(entry);
    }

    /// <summary>Ticks only while a helper is installed; a fresh arm looks at every process once.</summary>
    void Arm()
    {
        bool any = watch && installer.Installed.Any(m => m.Exe != null);
        Watching = any;
        if (!any) seen = [];
        try { timer.Change(any ? 2000 : Timeout.Infinite, any ? 2000 : Timeout.Infinite); } catch (ObjectDisposedException) { }
    }

    void Tick()
    {
        if (Interlocked.Exchange(ref busy, 1) == 1) return;
        try
        {
            // Helpers whose game went away, or that died on their own.
            List<Running> finished;
            lock (gate)
            {
                finished = running.Where(r => r.Helper.HasExited || (r.GamePid != 0 && !ProcessAlive(r.GamePid))).ToList();
                foreach (var entry in finished) running.Remove(entry);
            }
            foreach (var entry in finished) Kill(entry);
            // Games that just appeared with a helper installed and not yet running.
            var watched = new List<Watched>();
            IReadOnlyList<LibraryGame>? games = null;
            foreach (var mod in installer.Installed.Where(m => m.Exe != null))
            {
                var dir = mod.GameDir ?? (games ??= library()).FirstOrDefault(g => g.Key == mod.GameKey)?.InstallDir;
                if (dir != null) watched.Add(new Watched(mod, dir.TrimEnd('\\') + "\\"));
            }
            if (watched.Count == 0) { seen = []; return; }
            var current = new HashSet<int>();
            foreach (var process in Process.GetProcesses())
            {
                using (process)
                {
                    int pid = process.Id;
                    current.Add(pid);
                    if (pid == Environment.ProcessId || seen.Contains(pid)) continue;
                    var exe = PathOf(pid);
                    if (exe == null) continue;
                    foreach (var entry in watched)
                        if (exe.StartsWith(entry.GameDir, StringComparison.OrdinalIgnoreCase) && !IsRunning(entry.Mod.Id, entry.Mod.GameKey))
                            Launch(entry.Mod.Id, entry.Mod.GameKey, pid);
                }
            }
            seen = current;
        }
        catch (Exception e) when (e is InvalidOperationException or Win32Exception or IOException) { }
        finally { Volatile.Write(ref busy, 0); }
    }

    bool Launch(string modId, string gameKey, int gamePid)
    {
        var record = installer.Find(modId, gameKey);
        var mod = ModCatalog.ById(modId);
        if (record?.Exe is null || mod is null) return false;
        var exe = Path.Combine(record.Root, record.Exe);
        if (!File.Exists(exe)) { HelperFailed?.Invoke(modId, gameKey); return false; }
        var start = new ProcessStartInfo(exe, mod.Run?.Args ?? "")
        {
            WorkingDirectory = Path.GetDirectoryName(exe)!,
            UseShellExecute = true,
            WindowStyle = mod.Run?.Window switch { "hidden" => ProcessWindowStyle.Hidden, "normal" => ProcessWindowStyle.Normal, _ => ProcessWindowStyle.Minimized },
        };
        try
        {
            var helper = Process.Start(start);
            if (helper == null) { HelperFailed?.Invoke(modId, gameKey); return false; }
            lock (gate) running.Add(new Running(modId, gameKey, helper, gamePid));
            HelperStarted?.Invoke(modId, gameKey);
            return true;
        }
        catch (Exception e) when (e is Win32Exception or InvalidOperationException or IOException) { HelperFailed?.Invoke(modId, gameKey); return false; }
    }

    void Kill(Running entry)
    {
        try { if (!entry.Helper.HasExited) { entry.Helper.CloseMainWindow(); if (!entry.Helper.WaitForExit(1500)) entry.Helper.Kill(true); } }
        catch (Exception e) when (e is Win32Exception or InvalidOperationException) { }
        finally { entry.Helper.Dispose(); HelperStopped?.Invoke(entry.ModId, entry.GameKey); }
    }

    static bool ProcessAlive(int pid)
    {
        try { using var process = Process.GetProcessById(pid); return !process.HasExited; }
        catch (ArgumentException) { return false; }
        catch (InvalidOperationException) { return false; }
    }

    internal static string? PathOf(int pid)
    {
        var handle = OpenProcess(0x1000, false, pid);
        if (handle == 0) return null;
        try
        {
            var builder = new StringBuilder(1024);
            var size = builder.Capacity;
            return QueryFullProcessImageNameW(handle, 0, builder, ref size) ? builder.ToString() : null;
        }
        finally { CloseHandle(handle); }
    }

    public void Dispose()
    {
        installer.Changed -= Arm;
        timer.Dispose();
        List<Running> all;
        lock (gate) { all = running.ToList(); running.Clear(); }
        foreach (var entry in all) Kill(entry);
    }
}
