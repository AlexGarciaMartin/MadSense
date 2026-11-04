extern alias MadSenseApp;
using MadSenseApp::MadSense;
using MadSenseApp::MadSense.Games;
using System.Text.Json;

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
        if (options.View == "games")
        {
            using var games = new MadSenseApp::MadSense.Games.GamesService(Path.Combine(options.DataDirectory, "games"));
            using var library = new MadSenseApp::MadSense.Games.GamesWindow(games);
            Application.Run(library);
            return;
        }
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
        if (options.View == "mods") ConfigureModFixture(window, options.DataDirectory);
        if (options.UpdateVersion is { } version)
            window.SetPreviewUpdate(new AppUpdate(version,
                new Uri($"https://github.com/AlexGarciaMartin/MadSense/releases/download/v{version}/MadSense-{version}-Setup.exe"),
                new Uri($"https://github.com/AlexGarciaMartin/MadSense/releases/tag/v{version}")));
        Application.Run(window);
    }

    // Deliberately synthetic fixture. No installed game paths, executables, sockets or web calls.
    static void ConfigureModFixture(MainWindow window, string directory)
    {
        var fixtureRoot = Path.Combine(directory, "mod-fixture");
        var modsRoot = Path.Combine(fixtureRoot, "mods");
        Directory.CreateDirectory(modsRoot);
        var dirt = new LibraryGame("fixture:dirt", "DiRT Rally 2.0", "Steam · PREVIEW", "690790", Path.Combine(fixtureRoot, "DiRT"), null);
        var beam = new LibraryGame("fixture:beam", "BeamNG.drive", "Steam · PREVIEW", "284160", Path.Combine(fixtureRoot, "BeamNG"), null);
        var cyberpunk = new LibraryGame("fixture:cyberpunk", "Cyberpunk 2077", "Steam · PREVIEW", "1091500", Path.Combine(fixtureRoot, "Cyberpunk"), null);
        var native = new LibraryGame("fixture:native", "Native support · PREVIEW", "PREVIEW", null, Path.Combine(fixtureRoot, "Native"), null);
        var unknown = new LibraryGame("fixture:unknown", "Unknown support · PREVIEW", "PREVIEW", null, Path.Combine(fixtureRoot, "Unknown"), null);
        var installed = new InstalledMod
        {
            Id = "racing-dsx", GameKey = dirt.Key, Version = "V0.6.6", Installed = new DateTimeOffset(2026, 11, 4, 12, 0, 0, TimeSpan.Zero),
            Root = Path.Combine(modsRoot, "preview-only"), Exe = "nonexistent-preview.exe", GameDir = dirt.InstallDir,
        };
        File.WriteAllText(Path.Combine(modsRoot, "installed.json"), JsonSerializer.Serialize(new[] { installed }));
        var service = new GamesService(fixtureRoot, watchHelpers: false);
        var wiki = new Dictionary<string, WikiSupport?>
        {
            [dirt.Key] = new WikiSupport { Title = dirt.Name, Page = "DiRT Rally 2.0", Fetched = DateTimeOffset.UtcNow,
                Fields = new() { ["playstation controllers"] = "true", ["dualsense haptics support"] = "false", ["dualsense adaptive trigger support"] = "false" } },
            [beam.Key] = new WikiSupport { Title = beam.Name, Page = "PREVIEW", Fetched = DateTimeOffset.UtcNow, Fields = new() { ["dualsense haptics support"] = "true" } },
            [native.Key] = new WikiSupport { Title = native.Name, Page = "PREVIEW", Fetched = DateTimeOffset.UtcNow,
                Fields = new() { ["dualsense haptics support"] = "true", ["dualsense adaptive trigger support"] = "true", ["light bar support"] = "true", ["playstation speaker"] = "false" } },
        };
        window.ShowPreviewGames(service, new[] { dirt, beam, cyberpunk, native, unknown }, wiki,
            new ModDiagnostics(true, true, false, false, 0, 0));
    }

    static Options ParseOptions(string[] args)
    {
        const string usage = "Usage: MadSense.UiPreview --data-dir <absolute isolated folder> [--language en|es|fr|de|it|pt-BR|auto] [--state waiting|native|xbox|paused] [--update-version 0.4.6] [--view main|games|mods]";
        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        for (var index = 0; index < args.Length; index += 2)
        {
            if (index + 1 >= args.Length || args[index] is not ("--data-dir" or "--language" or "--state" or "--update-version" or "--view") || !values.TryAdd(args[index], args[index + 1]))
                throw new ArgumentException(usage);
        }
        if (!values.TryGetValue("--data-dir", out var directory) || !Path.IsPathFullyQualified(directory))
            throw new ArgumentException(usage);
        var language = values.GetValueOrDefault("--language", "en");
        if (!L.IsSupportedPreference(language)) throw new ArgumentException(usage);
        var state = values.GetValueOrDefault("--state", "waiting");
        if (state is not ("waiting" or "native" or "xbox" or "paused")) throw new ArgumentException(usage);
        var updateVersion = values.GetValueOrDefault("--update-version");
        if (updateVersion is not null && !Version.TryParse(updateVersion, out _)) throw new ArgumentException(usage);
        var view = values.GetValueOrDefault("--view", "main");
        if (view is not ("main" or "games" or "mods")) throw new ArgumentException(usage);
        return new Options(Path.GetFullPath(directory), language, state, updateVersion, view);
    }

    sealed record Options(string DataDirectory, string Language, string State, string? UpdateVersion, string View);
}
