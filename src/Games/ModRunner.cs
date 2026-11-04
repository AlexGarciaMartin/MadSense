using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;

namespace MadSense.Games;

/// <summary>
/// Starts a mod's helper program when its game runs and stops it when the game quits, so a mod
/// installed from the catalog needs no clicks per session. Every two seconds it looks at the
/// processes that own a window, finds the ones living inside an installed game's folder, and
/// matches them against the installed helpers. Nothing is launched twice and nothing outlives
/// its game; MadSense's own exit stops every helper it started.
/// </summary>
sealed class ModRunner : IDisposable
{
    [DllImport("kernel32.dll", SetLastError = true)] static extern nint OpenProcess(uint access, bool inherit, int pid);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] static extern bool QueryFullProcessImageNameW(nint handle, int flags, StringBuilder name, ref int size);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(nint handle);

    sealed record Running(string ModId, string GameKey, Process Helper, int GamePid);

    readonly ModInstaller installer;
    readonly Func<IReadOnlyList<LibraryGame>> library;
    readonly System.Threading.Timer timer;
    readonly object gate = new();
    readonly List<Running> running = [];
    int busy;

    public event Action<string, string>? HelperStarted;
    public event Action<string, string>? HelperStopped;
    public event Action<string, string>? HelperFailed;

    public ModRunner(ModInstaller installer, Func<IReadOnlyList<LibraryGame>> library, bool watch = true)
    {
        this.installer = installer;
        this.library = library;
        timer = new System.Threading.Timer(_ => Tick(), null, watch ? 2000 : Timeout.Infinite, 2000);
    }

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
            var installed = installer.Installed.Where(m => m.Exe != null).ToList();
            if (installed.Count == 0) return;
            var games = library();
            foreach (var process in Process.GetProcesses())
            {
                using (process)
                {
                    int pid;
                    try { if (process.Id == Environment.ProcessId || process.MainWindowHandle == 0) continue; pid = process.Id; }
                    catch (Exception e) when (e is InvalidOperationException or Win32Exception) { continue; }
                    var exe = PathOf(pid);
                    if (exe == null) continue;
                    foreach (var game in games)
                    {
                        if (!game.Contains(exe)) continue;
                        foreach (var mod in installed.Where(m => m.GameKey == game.Key))
                            if (!IsRunning(mod.Id, game.Key)) Launch(mod.Id, game.Key, pid);
                    }
                }
            }
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
        timer.Dispose();
        List<Running> all;
        lock (gate) { all = running.ToList(); running.Clear(); }
        foreach (var entry in all) Kill(entry);
    }
}
