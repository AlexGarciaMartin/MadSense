using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Themes.Simple;

namespace MadSense;

static class Program
{
    internal static string[] Arguments = [];
    internal static string? Option(string name)
    {
        var index = Array.IndexOf(Arguments, name);
        return index >= 0 && index + 1 < Arguments.Length ? Arguments[index + 1] : null;
    }
    [STAThread]
    public static int Main(string[] args)
    {
        Arguments = args;
        return AppBuilder.Configure<MadSenseApplication>().UsePlatformDetect()
            .With(new X11PlatformOptions { WmClass = "madsense", RenderingMode = [X11RenderingMode.Glx, X11RenderingMode.Software] })
            .StartWithClassicDesktopLifetime(args);
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
