extern alias MadSenseApp;
using MadSenseApp::MadSense;

namespace MadSense.UiPreview;

internal static class Program
{
    // Manual visual QA only. The preview-only form never creates a controller bridge.
    // An explicit data directory isolates its preferences from the installed/development app.
    [STAThread]
    static void Main(string[] args)
    {
        var options = ParseOptions(args);
        Directory.CreateDirectory(options.DataDirectory);
        Directory.SetCurrentDirectory(options.DataDirectory);
        ApplicationConfiguration.Initialize();
        L.Use(options.Language);
        using var window = new MainWindow(previewOnly: true);
        window.Text = "MadSense · UI PREVIEW";
        var signals = options.State switch
        {
            "native" => new BridgeSignals(true, true, true, false, 85, false, false),
            "xbox" => new BridgeSignals(false, false, false, true, 45, false, false),
            _ => null
        };
        var status = options.State is "native" or "xbox"
            ? L.Message("Bridge.ConnectionDetails", L.Message("Bridge.Battery", signals!.Battery),
                UiMessage.Raw(""), L.Message(options.State == "xbox" ? "Bridge.ConnectedXbox" : "Bridge.ConnectedNative"))
            : L.Message(options.State == "paused" ? "Ui.ConnectionPaused" : "Ui.WaitingInstructions");
        window.SetPreviewState(
            connected: options.State is "native" or "xbox",
            paused: options.State == "paused",
            xbox: options.State == "xbox",
            signals: signals,
            status: status);
        Application.Run(window);
    }

    static Options ParseOptions(string[] args)
    {
        const string usage = "Usage: MadSense.UiPreview --data-dir <absolute isolated folder> [--language en|es|fr|de|it|pt-BR|auto] [--state waiting|native|xbox|paused]";
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        for (var index = 0; index < args.Length; index += 2)
        {
            if (index + 1 >= args.Length || args[index] is not ("--data-dir" or "--language" or "--state") || !values.TryAdd(args[index], args[index + 1]))
                throw new ArgumentException(usage);
        }
        if (!values.TryGetValue("--data-dir", out var directory) || !Path.IsPathFullyQualified(directory))
            throw new ArgumentException(usage);
        var language = values.GetValueOrDefault("--language", "en");
        if (!L.IsSupportedPreference(language)) throw new ArgumentException(usage);
        var state = values.GetValueOrDefault("--state", "waiting");
        if (state is not ("waiting" or "native" or "xbox" or "paused")) throw new ArgumentException(usage);
        return new Options(Path.GetFullPath(directory), language, state);
    }

    sealed record Options(string DataDirectory, string Language, string State);
}
