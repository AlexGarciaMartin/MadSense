using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Layout;
using Avalonia.Media;
using Avalonia.Media.Imaging;
using Avalonia.Threading;
using System.Diagnostics;
using System.Text.Json;

namespace MadSense;

sealed class MainWindow : Window
{
    readonly IClassicDesktopStyleApplicationLifetime lifetime;
    readonly bool preview = Program.Arguments.Contains("--preview") || Program.Option("--ui-smoke") != null;
    readonly LinuxPreferences preferences;
    readonly BrandHeader header = new();
    readonly BrandHero hero = new();
    readonly TextBlock link = new(), telemetry = new(), modeSection = new(), speakerSection = new(), hint = new(), activity = new();
    readonly BrandButton native = new(""), xbox = new(""), high = new(""), low = new(""), off = new(""), pause = new("") { Accent = true }, logs = new("");
    readonly ComboBox languages = new() { Width = 150, FontFamily = Brand.Mono, FontSize = 11 };
    readonly DispatcherTimer poll = new() { Interval = TimeSpan.FromMilliseconds(250) };
    readonly LinuxBridgeController bridge;
    string mode, speaker;
    bool paused, closing;
    public MainWindow(IClassicDesktopStyleApplicationLifetime lifetime)
    {
        this.lifetime = lifetime;
        preferences = new(preview); preferences.Load();
        L.Use(Program.Option("--language") ?? preferences.Language);
        mode = preferences.Mode; speaker = preferences.Speaker;
        paused = preview || Program.Arguments.Contains("--start-paused");
        bridge = new(preview, mode, speaker);
        Title = "MadSense"; Width = 900; Height = 770; MinWidth = 800; MinHeight = 770;
        Background = Brand.Paper; Foreground = Brand.Ink; FontFamily = Brand.Mono;
        WindowStartupLocation = WindowStartupLocation.CenterScreen;
        var grid = new Grid { Margin = new Thickness(24, 12, 24, 16), RowDefinitions = new("106,*,62,30,54,60,30,50,48") };
        Content = grid;
        Add(header, 0); Add(hero, 1);
        var state = new Grid { ColumnDefinitions = new("55,*,230"), Background = Brand.Paper };
        state.Children.Add(new TextBlock { Text = "LINK", VerticalAlignment = VerticalAlignment.Center, FontWeight = FontWeight.Bold, FontSize = 11 });
        link.FontSize = 11; link.TextWrapping = TextWrapping.Wrap; link.VerticalAlignment = VerticalAlignment.Center;
        Grid.SetColumn(link, 1); state.Children.Add(link);
        telemetry.FontSize = 10; telemetry.TextWrapping = TextWrapping.Wrap; telemetry.VerticalAlignment = VerticalAlignment.Center; telemetry.Margin = new(12, 0, 0, 0);
        Grid.SetColumn(telemetry, 2); state.Children.Add(telemetry); Add(state, 2);
        Add(Section(modeSection), 3);
        var modes = new Grid { ColumnDefinitions = new("*,*") };
        native.Margin = new(0, 0, 8, 0); xbox.Margin = new(0); modes.Children.Add(native); Grid.SetColumn(xbox, 1); modes.Children.Add(xbox); Add(modes, 4);
        hint.FontSize = 11; hint.Foreground = Brand.Muted; hint.TextWrapping = TextWrapping.Wrap; hint.VerticalAlignment = VerticalAlignment.Center; Add(hint, 5);
        Add(Section(speakerSection), 6);
        var speakers = new Grid { ColumnDefinitions = new("*,*,*") };
        var buttons = new[] { high, low, off }; for (var i = 0; i < buttons.Length; i++) { buttons[i].Margin = new(0, 0, i == 2 ? 0 : 8, 0); Grid.SetColumn(buttons[i], i); speakers.Children.Add(buttons[i]); } Add(speakers, 7);
        var footer = new Grid { ColumnDefinitions = new("*,150,96,144"), Margin = new(0, 6, 0, 0) };
        activity.FontSize = 10; activity.Foreground = Brand.Muted; activity.TextTrimming = TextTrimming.CharacterEllipsis; activity.VerticalAlignment = VerticalAlignment.Center; footer.Children.Add(activity);
        Grid.SetColumn(languages, 1); footer.Children.Add(languages); logs.Margin = new(8, 0, 8, 0); Grid.SetColumn(logs, 2); footer.Children.Add(logs); Grid.SetColumn(pause, 3); footer.Children.Add(pause); Add(footer, 8);
        languages.ItemsSource = new[] { LinuxText.Text("Ui.LanguageAuto") }.Concat(L.Languages.Select(l => l.NativeName)).ToArray();
        languages.SelectedIndex = L.Preference == "auto" ? 0 : L.Languages.ToList().FindIndex(l => l.Code == L.CurrentLanguage) + 1;
        AutomationProperties.SetName(languages, L.Text("Ui.Language"));
        languages.SelectionChanged += (_, _) =>
        {
            var index = languages.SelectedIndex;
            if (index < 0) return;
            var language = index == 0 ? "auto" : L.Languages[index - 1].Code;
            L.Use(language); preferences.Save("language", language); Refresh();
        };
        native.Click += (_, _) => ChangeMode("Auto"); xbox.Click += (_, _) => ChangeMode("Xbox");
        high.Click += (_, _) => ChangeSpeaker("Alto"); low.Click += (_, _) => ChangeSpeaker("Bajo"); off.Click += (_, _) => ChangeSpeaker("Off");
        pause.Click += (_, _) => { paused = !paused; bridge.SetPaused(paused); Refresh(); };
        logs.Click += (_, _) => ShowDiagnostics();
        poll.Tick += (_, _) => Refresh();
        Opened += (_, _) =>
        {
            bridge.SetPaused(paused); poll.Start();
            if (Program.Option("--ui-smoke") is string directory)
                DispatcherTimer.RunOnce(() => CaptureSmoke(directory), TimeSpan.FromSeconds(2));
        };
        Closing += async (_, e) =>
        {
            if (closing) return;
            e.Cancel = true; closing = true; poll.Stop(); pause.IsEnabled = false;
            activity.Text = L.Text("Ui.Releasing");
            await bridge.DisposeAsync(); lifetime.Shutdown();
        };
        Refresh();
        void Add(Control control, int row) { Grid.SetRow(control, row); grid.Children.Add(control); }
    }
    static TextBlock Section(TextBlock block)
    { block.FontSize = 10; block.FontWeight = FontWeight.Bold; block.VerticalAlignment = VerticalAlignment.Bottom; block.Margin = new(0, 0, 0, 5); return block; }
    void ChangeMode(string value) { mode = value; preferences.Save("mode", value); bridge.SetMode(value); Refresh(); }
    void ChangeSpeaker(string value) { speaker = value; preferences.Save("speaker", value); bridge.SetSpeaker(value); Refresh(); }
    void Refresh()
    {
        var state = bridge.State;
        hero.Paused = paused; hero.Ready = state.Ready; hero.Xbox = mode == "Xbox"; hero.Refresh(); header.InvalidateVisual();
        modeSection.Text = L.Text("Ui.ModeSection"); speakerSection.Text = L.Text("Ui.SpeakerSection");
        native.Content = L.Text("Ui.NativeMode"); xbox.Content = L.Text("Ui.XboxMode");
        high.Content = L.Text("Ui.SpeakerHigh"); low.Content = L.Text("Ui.SpeakerLow"); off.Content = L.Text("Ui.SpeakerOff");
        logs.Content = L.Text("Ui.Logs"); pause.Content = L.Text(paused ? "Ui.Resume" : "Ui.Pause");
        native.Selected = mode != "Xbox"; xbox.Selected = mode == "Xbox"; high.Selected = speaker == "Alto"; low.Selected = speaker == "Bajo"; off.Selected = speaker == "Off";
        foreach (var button in new[] { native, xbox, high, low, off, logs, pause }) { AutomationProperties.SetName(button, button.Content?.ToString() ?? ""); button.InvalidateVisual(); }
        hint.Text = L.Text(mode == "Xbox" ? "Ui.XboxHint" : "Ui.NativeHint");
        link.Text = paused ? L.Text("Ui.PausedMessage") : state.Message;
        telemetry.Text = mode == "Xbox" ? L.Text("Ui.Rumble") + "  " + L.Text(state.Rumble ? "Ui.Active" : "Ui.Idle") :
            string.Join("\n", Lamp("Ui.Haptics", state.Haptics), Lamp("Ui.Triggers", state.Triggers), Lamp("Ui.Audio", state.Audio));
        activity.Text = preview ? LinuxText.Text("Linux.Preview") : state.Message;
        static string Lamp(string key, bool active) => L.Text(key) + "  " + (active ? "●" : "○");
    }
    async void ShowDiagnostics()
    {
        var text = new TextBox { Text = bridge.DiagnosticText, IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, FontFamily = Brand.Mono, FontSize = 12 };
        var close = new BrandButton(LinuxText.Text("Linux.Close"));
        var dialog = new Window { Title = "MadSense · " + LinuxText.Text("Linux.Diagnostics"), Width = 760, Height = 480, MinWidth = 600, MinHeight = 360, Background = Brand.Paper };
        var grid = new Grid { Margin = new Thickness(20), RowDefinitions = new("*,48") }; grid.Children.Add(text); Grid.SetRow(close, 1); grid.Children.Add(close); dialog.Content = grid;
        close.Click += (_, _) => dialog.Close(); await dialog.ShowDialog(this);
    }
    void CaptureSmoke(string directory)
    {
        Directory.CreateDirectory(directory);
        using var bitmap = new RenderTargetBitmap(new PixelSize((int)ClientSize.Width, (int)ClientSize.Height), new Vector(96, 96));
        bitmap.Render(this); bitmap.Save(Path.Combine(directory, "ui-" + L.CurrentLanguage + ".png"), PngBitmapEncoderOptions.Default);
        File.WriteAllText(Path.Combine(directory, "ui-" + L.CurrentLanguage + ".json"), JsonSerializer.Serialize(new
        { language = L.CurrentLanguage, platform = Environment.OSVersion.ToString(), preview, physical_device_opened = false, ready = hero.Ready, headline = hero.Headline, subheadline = hero.Subheadline, tagline = L.Text("Ui.HeaderTagline"), client_width = ClientSize.Width, client_height = ClientSize.Height }));
        Close();
    }
}
