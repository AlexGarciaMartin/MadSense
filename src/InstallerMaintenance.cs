using System.Diagnostics;
using System.Text;

namespace MadSense;

/// <summary>
/// Installer-only commands run before the app mutex, UI, logging or controller bridge.
/// The new payload can prepare an older installation without executing the older app.
/// </summary>
internal static class InstallerMaintenance
{
    internal const int Ready = 0, Busy = 2, RecoveryFailed = 3, InvalidArguments = 4;

    internal static bool TryRun(string[] args, out int exitCode)
    {
        exitCode = Ready;
        var commands = args.Where(a => a is "--prepare-update" or "--prepare-uninstall" or "--installer-check-running").ToArray();
        if (commands.Length == 0) return false;

        string? resultPath = null;
        MaintenanceResult result;
        try
        {
            if (commands.Length != 1) throw new ArgumentException("Indica una única operación de mantenimiento.");
            var options = new Dictionary<string, string>(StringComparer.Ordinal);
            for (var i = 0; i < args.Length; i++)
            {
                if (args[i] == commands[0]) continue;
                if (args[i] is not ("--install-path" or "--result-path") || i + 1 >= args.Length || args[i + 1].StartsWith("--", StringComparison.Ordinal))
                    throw new ArgumentException("Argumentos de mantenimiento no válidos.");
                if (!options.TryAdd(args[i], args[++i])) throw new ArgumentException("Opción de mantenimiento duplicada.");
            }
            if (options.TryGetValue("--result-path", out var requestedResultPath))
            {
                if (!Path.IsPathFullyQualified(requestedResultPath))
                    throw new ArgumentException("La ruta del resultado debe ser absoluta.");
                resultPath = requestedResultPath;
            }

            var processes = new WindowsInstallationProcesses();
            if (commands[0] == "--installer-check-running")
            {
                result = processes.Find().Any(p => p.Id != processes.CurrentId)
                    ? new(Busy, "MadSense ya está abierta. La instalación no abrirá otra copia.")
                    : new(Ready, "No hay otra copia de MadSense abierta.");
            }
            else
            {
                if (!options.TryGetValue("--install-path", out var installPath))
                    throw new ArgumentException("Falta --install-path con la ruta absoluta de MadSense.exe.");
                result = Prepare(installPath, processes,
                    directory => File.Exists(Path.Combine(directory, HiddenControllerRecovery.JournalName)),
                    directory => HiddenControllerRecovery.Recover(directory, () => new HidHideRecoveryState()));
            }
        }
        catch (ArgumentException e) { result = new(InvalidArguments, e.Message); }
        catch (Exception e) { result = new(RecoveryFailed, "No se pudo preparar MadSense: " + e.Message); }

        if (resultPath != null)
        {
            try { File.WriteAllText(resultPath, result.Message, new UTF8Encoding(false)); }
            catch { exitCode = InvalidArguments; return true; }
        }
        exitCode = result.ExitCode;
        return true;
    }

    internal static MaintenanceResult Prepare(string executablePath, IInstallationProcesses processes,
        Func<string, bool> hasJournal, Action<string> recover)
    {
        if (!Path.IsPathFullyQualified(executablePath) ||
            !string.Equals(Path.GetFileName(executablePath), "MadSense.exe", StringComparison.OrdinalIgnoreCase))
            return new(InvalidArguments, "La ruta de instalación debe ser absoluta y terminar en MadSense.exe.");
        var target = Path.GetFullPath(executablePath);
        var directory = Path.GetDirectoryName(target)!;
        var snapshots = processes.Find().Where(p => p.Id != processes.CurrentId).ToArray();
        if (snapshots.Any(p => p.ExecutablePath == null))
            return new(Busy, "Hay una copia de MadSense cuya ubicación no se puede comprobar. Ciérrala desde la bandeja y vuelve a intentarlo.");

        var owned = snapshots.Where(p => SamePath(p.ExecutablePath!, target)).ToArray();
        if (owned.Any(p => p.SessionId != processes.CurrentSessionId))
            return new(Busy, "Esta instalación de MadSense está abierta en otra sesión de Windows. Ciérrala antes de continuar.");
        foreach (var process in owned)
        {
            // Never use the old global stop event: it can belong to a development copy.
            if (!processes.RequestStop(process))
                return new(Busy, "Cierra esta copia de MadSense desde la bandeja: «Salir y liberar el mando». Después vuelve a intentarlo.");
        }
        foreach (var process in owned)
            if (!processes.WaitForExit(process, TimeSpan.FromSeconds(12)))
                return new(Busy, "MadSense sigue liberando el mando. Espera a que termine de cerrar y vuelve a intentarlo.");

        var remaining = processes.Find().Where(p => p.Id != processes.CurrentId).ToArray();
        if (remaining.Any(p => p.ExecutablePath == null || SamePath(p.ExecutablePath, target)))
            return new(Busy, "La copia que se va a modificar sigue abierta. No se han forzado cierres ni borrado sus datos.");
        var recoveryPending = hasJournal(directory);
        if (recoveryPending && remaining.Length != 0)
            return new(Busy, "Hay una recuperación del mando pendiente y otra copia de MadSense abierta. Cierra esa copia antes de continuar.");
        using var recoveryGuard = recoveryPending ? processes.TryGuardRecovery() : null;
        if (recoveryPending && recoveryGuard == null)
            return new(Busy, "Otra copia de MadSense se ha abierto durante la comprobación. Ciérrala y vuelve a intentarlo.");
        try { recover(directory); }
        catch (Exception e)
        {
            return new(RecoveryFailed, "No se pudo restaurar el estado del mando. Se conservan sus datos de recuperación. " + e.Message);
        }
        return new(Ready, "MadSense está preparada. El mando y sus datos de recuperación se han comprobado.");
    }

    internal static bool SamePath(string left, string right) =>
        string.Equals(Path.GetFullPath(left), Path.GetFullPath(right), StringComparison.OrdinalIgnoreCase);
}

internal sealed record MaintenanceResult(int ExitCode, string Message);
internal sealed record InstalledProcess(int Id, int SessionId, long StartUtcTicks, string? ExecutablePath);

internal interface IInstallationProcesses
{
    int CurrentId { get; }
    int CurrentSessionId { get; }
    IReadOnlyList<InstalledProcess> Find();
    bool RequestStop(InstalledProcess process);
    bool WaitForExit(InstalledProcess process, TimeSpan timeout);
    IDisposable? TryGuardRecovery();
}

internal sealed class WindowsInstallationProcesses : IInstallationProcesses
{
    public int CurrentId => Environment.ProcessId;
    public int CurrentSessionId { get; } = ReadSessionId();
    private static int ReadSessionId() { using var current = Process.GetCurrentProcess(); return current.SessionId; }

    public IReadOnlyList<InstalledProcess> Find()
    {
        var found = new List<InstalledProcess>();
        foreach (var process in Process.GetProcessesByName("MadSense"))
        {
            using (process)
            {
                try
                {
                    if (process.HasExited) continue;
                    found.Add(new(process.Id, process.SessionId, process.StartTime.ToUniversalTime().Ticks, process.MainModule?.FileName));
                }
                catch (InvalidOperationException) { /* It exited during enumeration. */ }
                catch (System.ComponentModel.Win32Exception)
                {
                    found.Add(new(process.Id, -1, 0, null));
                }
            }
        }
        return found;
    }

    public bool RequestStop(InstalledProcess process)
    {
        try
        {
            using var live = Process.GetProcessById(process.Id);
            if (live.HasExited || live.StartTime.ToUniversalTime().Ticks != process.StartUtcTicks) return true;
            if (!EventWaitHandle.TryOpenExisting(InstallerStopSignal.Name(process.Id, process.StartUtcTicks), out var stop)) return false;
            using (stop) return stop.Set();
        }
        catch (ArgumentException) { return true; }
        catch (InvalidOperationException) { return true; }
        catch (System.ComponentModel.Win32Exception) { return false; }
        catch (UnauthorizedAccessException) { return false; }
    }

    public bool WaitForExit(InstalledProcess process, TimeSpan timeout)
    {
        try
        {
            using var live = Process.GetProcessById(process.Id);
            if (live.StartTime.ToUniversalTime().Ticks != process.StartUtcTicks) return true;
            return live.WaitForExit((int)timeout.TotalMilliseconds);
        }
        catch (ArgumentException) { return true; }
        catch (InvalidOperationException) { return true; }
        catch (System.ComponentModel.Win32Exception) { return false; }
    }

    public IDisposable? TryGuardRecovery()
    {
        // Normal startup tests whether this object already exists. Holding it while
        // restoring HidHide prevents a new bridge in this session racing recovery.
        var mutex = new Mutex(true, "Local\\MadSense-0.1", out var created);
        if (created) return mutex;
        mutex.Dispose();
        return null;
    }
}

internal static class InstallerStopSignal
{
    internal static string Name(int processId, long startUtcTicks) => $"Local\\MadSense-Installer-Stop-{processId}-{startUtcTicks:x}";
    internal static EventWaitHandle Create()
    {
        using var current = Process.GetCurrentProcess();
        return new(false, EventResetMode.AutoReset, Name(current.Id, current.StartTime.ToUniversalTime().Ticks));
    }
}
