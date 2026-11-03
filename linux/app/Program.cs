using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Themes.Simple;
using System.Runtime.InteropServices;

namespace MadSense;

static class Program
{
    internal static string[] Arguments = [];
    internal static SingleInstance? Instance;
    internal static Action? RequestExit;
    internal static string? Option(string name)
    {
        var index = Array.IndexOf(Arguments, name);
        return index >= 0 && index + 1 < Arguments.Length ? Arguments[index + 1] : null;
    }
    [STAThread]
    public static int Main(string[] args)
    {
        Arguments = args;
        var preview = args.Contains("--preview") || Option("--ui-smoke") != null;
        if (!preview && OperatingSystem.IsLinux())
        {
            Instance = SingleInstance.Open(args.Contains("--stop"));
            if (Instance == null) return 0;
        }
        var signals = new List<PosixSignalRegistration>();
        if (OperatingSystem.IsLinux() && !preview)
        {
            foreach (var signal in new[] { PosixSignal.SIGTERM, PosixSignal.SIGINT, PosixSignal.SIGHUP })
                signals.Add(PosixSignalRegistration.Create(signal, context =>
                {
                    var exit = Volatile.Read(ref RequestExit);
                    if (exit != null) { context.Cancel = true; exit(); }
                }));
        }
        try
        {
            return AppBuilder.Configure<MadSenseApplication>().UsePlatformDetect()
                .With(new X11PlatformOptions { WmClass = "madsense", RenderingMode = [X11RenderingMode.Glx, X11RenderingMode.Software] })
                .StartWithClassicDesktopLifetime(args);
        }
        finally { foreach (var signal in signals) signal.Dispose(); Instance?.Dispose(); }
    }
}

sealed class MadSenseApplication : Application
{
    public override void Initialize() => Styles.Add(new SimpleTheme());
    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            desktop.ShutdownMode = Avalonia.Controls.ShutdownMode.OnExplicitShutdown;
            desktop.MainWindow = new MainWindow(desktop);
        }
        base.OnFrameworkInitializationCompleted();
    }
}
