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
    readonly Func<string, string, bool> autoStartEnabled;
    readonly System.Threading.Timer timer;
    readonly bool watch;
    readonly object gate = new();
    readonly List<Running> running = [];
    readonly Dictionary<int, string?> paths = [];
    readonly ModSessionPolicy sessions = new();
    readonly Dictionary<(string, string), string> failures = [];
    readonly HashSet<(string Mod, string Game)> attempted = [];
    bool disposed;
    int busy;

    public event Action<string, string>? HelperStarted;
    public event Action<string, string>? HelperStopped;
    public event Action<string, string>? HelperFailed;

    public ModRunner(ModInstaller installer, Func<IReadOnlyList<LibraryGame>> library, bool watch = true, Func<string, string, bool>? autoStartEnabled = null)
    {
        this.installer = installer;
        this.library = library;
        this.watch = watch;
        this.autoStartEnabled = autoStartEnabled ?? ((_, _) => true);
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
    public bool StoppedForSession(string modId, string gameKey) { lock (gate) return sessions.IsStopped(modId, gameKey); }
    public string? Failure(string modId, string gameKey) { lock (gate) return failures.GetValueOrDefault((modId, gameKey)); }

    /// <summary>Explicit start overrides a session stop; receiving effects is a separate diagnostic.</summary>
    public bool Start(string modId, string gameKey)
    {
        lock (gate) sessions.Resume(modId, gameKey);
        return Launch(modId, gameKey, 0);
    }

    public void Stop(string modId, string gameKey)
    {
        Running? entry;
        lock (gate)
        {
            sessions.Stop(modId, gameKey);
            entry = running.FirstOrDefault(r => r.ModId == modId && r.GameKey == gameKey);
            if (entry != null) running.Remove(entry);
        }
        if (entry != null) Kill(entry);
    }

    public void RefreshPreferences() => Arm();

    /// <summary>No process polling while every helper is disabled and none is running.</summary>
    void Arm()
    {
        lock (gate)
        {
            if (disposed) return;
            var installed = installer.Installed;
            attempted.RemoveWhere(key => !installed.Any(m => m.Id == key.Mod && m.GameKey == key.Game));
            bool any = watch && (running.Count > 0 || installed.Any(m => m.Exe != null && autoStartEnabled(m.Id, m.GameKey)));
            Watching = any;
            if (!any) paths.Clear();
            try { timer.Change(any ? 2000 : Timeout.Infinite, any ? 2000 : Timeout.Infinite); } catch (ObjectDisposedException) { }
        }
    }

    void Tick()
    {
        if (Interlocked.Exchange(ref busy, 1) == 1) return;
        try
        {
            // Resolve each process path once. All matching processes form a game session, so
            // a launcher exiting does not stop a helper while the real game keeps running.
            var watched = new List<Watched>();
            IReadOnlyList<LibraryGame>? games = null;
            foreach (var mod in installer.Installed.Where(m => m.Exe != null))
            {
                var dir = mod.GameDir ?? (games ??= library()).FirstOrDefault(g => g.Key == mod.GameKey)?.InstallDir;
                if (dir != null) watched.Add(new Watched(mod, dir.TrimEnd('\\') + "\\"));
            }
            var current = new HashSet<int>();
            var activeGames = new Dictionary<string, int>();
            foreach (var process in Process.GetProcesses())
            {
                using (process)
                {
                    int pid = process.Id;
                    current.Add(pid);
                    if (pid == Environment.ProcessId) continue;
                    string? exe;
                    lock (gate)
                    {
                        if (disposed) return;
                        if (!paths.TryGetValue(pid, out exe)) paths[pid] = exe = PathOf(pid);
                        // A helper located under a game directory cannot keep its own session alive.
                        if (running.Any(r => r.Helper.Id == pid)) continue;
                    }
                    if (exe == null) continue;
                    foreach (var entry in watched)
                        if (exe.StartsWith(entry.GameDir, StringComparison.OrdinalIgnoreCase)) activeGames.TryAdd(entry.Mod.GameKey, pid);
                }
            }
            List<Running> finished;
            lock (gate)
            {
                foreach (var pid in paths.Keys.Where(pid => !current.Contains(pid)).ToList()) paths.Remove(pid);
                var keys = activeGames.Keys.ToHashSet();
                sessions.ObserveGames(keys);
                attempted.RemoveWhere(key => !keys.Contains(key.Game));
                for (int i = 0; i < running.Count; i++)
                    if (running[i].GamePid == 0 && activeGames.TryGetValue(running[i].GameKey, out var gamePid))
                        running[i] = running[i] with { GamePid = gamePid };
                finished = running.Where(r => r.Helper.HasExited || (r.GamePid != 0 && !keys.Contains(r.GameKey))).ToList();
                foreach (var entry in finished) running.Remove(entry);
            }
            foreach (var entry in finished) Kill(entry);
            foreach (var entry in watched)
            {
                var mod = entry.Mod;
                if (!activeGames.TryGetValue(mod.GameKey, out var pid) || !autoStartEnabled(mod.Id, mod.GameKey)) continue;
                lock (gate)
                {
                    if (disposed || sessions.IsStopped(mod.Id, mod.GameKey) || IsRunning(mod.Id, mod.GameKey) || !attempted.Add((mod.Id, mod.GameKey))) continue;
                }
                Launch(mod.Id, mod.GameKey, pid);
            }
            Arm();
        }
        catch (Exception e) when (e is InvalidOperationException or Win32Exception or IOException) { }
        finally { Volatile.Write(ref busy, 0); }
    }

    bool Launch(string modId, string gameKey, int gamePid)
    {
        bool started = false;
        lock (gate)
        {
            if (disposed) return false;
            if (gamePid != 0 && (sessions.IsStopped(modId, gameKey) || !autoStartEnabled(modId, gameKey))) return false;
            if (IsRunning(modId, gameKey)) return true;
            var record = installer.Find(modId, gameKey);
            var mod = ModCatalog.ById(modId);
            if (record?.Exe is null || mod is null) return false;
            var exe = Path.Combine(record.Root, record.Exe);
            var start = new ProcessStartInfo(exe, mod.Run?.Args ?? "")
            {
                WorkingDirectory = Path.GetDirectoryName(exe)!,
                UseShellExecute = true,
                WindowStyle = mod.Run?.Window switch { "hidden" => ProcessWindowStyle.Hidden, "normal" => ProcessWindowStyle.Normal, _ => ProcessWindowStyle.Minimized },
            };
            try
            {
                if (!File.Exists(exe)) throw new FileNotFoundException(L.Text("Games.NoExecutable") + " " + exe, exe);
                var helper = Process.Start(start);
                if (helper == null) throw new InvalidOperationException(L.Text("Games.HelperFailed", mod.Name));
                running.Add(new Running(modId, gameKey, helper, gamePid));
                failures.Remove((modId, gameKey));
                started = true;
            }
            catch (Exception e) when (e is Win32Exception or InvalidOperationException or IOException) { failures[(modId, gameKey)] = e.Message; }
        }
        if (started) { Arm(); HelperStarted?.Invoke(modId, gameKey); }
        else HelperFailed?.Invoke(modId, gameKey);
        return started;
    }

    void Kill(Running entry)
    {
        try { if (!entry.Helper.HasExited) { entry.Helper.CloseMainWindow(); if (!entry.Helper.WaitForExit(1500)) entry.Helper.Kill(true); } }
        catch (Exception e) when (e is Win32Exception or InvalidOperationException) { }
        finally { entry.Helper.Dispose(); HelperStopped?.Invoke(entry.ModId, entry.GameKey); }
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
        lock (gate) { disposed = true; all = running.ToList(); running.Clear(); }
        foreach (var entry in all) Kill(entry);
    }
}
