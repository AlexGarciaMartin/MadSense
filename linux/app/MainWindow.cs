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
    readonly TrayAvailability trayAvailability = new();
    TrayIcon? tray;
    string traySignature = "";
    LinuxBridgeState? lastResumeRequest;
    LinuxUpdate? availableUpdate;
    bool checkingUpdates;
    string transientMessage = "";
    long transientUntil;
    string mode, speaker;
    bool paused, closing, quitting;
    public MainWindow(IClassicDesktopStyleApplicationLifetime lifetime)
    {
        this.lifetime = lifetime;
        preferences = new(preview); preferences.Load();
        L.Use(Program.Option("--language") ?? preferences.Language);
        mode = preferences.Mode; speaker = preferences.Speaker;
        paused = preview || Program.Arguments.Contains("--start-paused");
        bridge = new(preview, mode, speaker);
        Title = "MadSense"; Width = 900; Height = 730; MinWidth = 800; MinHeight = 730;
        Background = Brand.Paper; Foreground = Brand.Ink; FontFamily = Brand.Mono;
        WindowStartupLocation = WindowStartupLocation.CenterScreen;
        var grid = new Grid { Margin = new Thickness(24, 12, 24, 16), RowDefinitions = new("106,*,62,30,54,60,30,50,48") };
        Content = grid;
        Add(header, 0); Add(hero, 1);
        var state = new Grid { ColumnDefinitions = new("55,*,230"), Background = Brand.Paper };
        state.Children.Add(new TextBlock { Text = "LINK", VerticalAlignment = VerticalAlignment.Center, FontWeight = FontWeight.Bold, FontSize = 11 });
        link.FontSize = 12; link.TextWrapping = TextWrapping.Wrap; link.VerticalAlignment = VerticalAlignment.Center;
        Grid.SetColumn(link, 1); state.Children.Add(link);
        telemetry.FontSize = 11; telemetry.TextWrapping = TextWrapping.Wrap; telemetry.VerticalAlignment = VerticalAlignment.Center; telemetry.Margin = new(12, 0, 0, 0);
        Grid.SetColumn(telemetry, 2); state.Children.Add(telemetry); Add(state, 2);
        Add(Section(modeSection), 3);
        var modes = new Grid { ColumnDefinitions = new("*,*") };
        native.Margin = new(0, 0, 8, 0); xbox.Margin = new(0); modes.Children.Add(native); Grid.SetColumn(xbox, 1); modes.Children.Add(xbox); Add(modes, 4);
        hint.FontSize = 12; hint.Foreground = Brand.Muted; hint.TextWrapping = TextWrapping.Wrap; hint.VerticalAlignment = VerticalAlignment.Center; Add(hint, 5);
        Add(Section(speakerSection), 6);
        var speakers = new Grid { ColumnDefinitions = new("*,*,*") };
        var buttons = new[] { high, low, off }; for (var i = 0; i < buttons.Length; i++) { buttons[i].Margin = new(0, 0, i == 2 ? 0 : 8, 0); Grid.SetColumn(buttons[i], i); speakers.Children.Add(buttons[i]); } Add(speakers, 7);
        var footer = new Grid { ColumnDefinitions = new("*,150,120,144"), Margin = new(0, 6, 0, 0) };
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
        pause.Click += (_, _) => TogglePause();
        logs.Click += (_, _) => ShowDiagnostics();
        poll.Tick += (_, _) => Refresh();
        Opened += async (_, _) =>
        {
            bridge.SetPaused(paused); poll.Start();
            if (Program.Option("--ui-smoke") is string directory)
                DispatcherTimer.RunOnce(() => CaptureSmoke(directory), TimeSpan.FromSeconds(2));
            if (!preview)
            {
                CreateTray(); await trayAvailability.Start();
                if (Program.Arguments.Contains("--tray") && trayAvailability.Available) Hide();
            }
        };
        Closing += async (_, e) =>
        {
            if (closing) return;
            if (!preview && !quitting && trayAvailability.Available && e.CloseReason == WindowCloseReason.WindowClosing)
            { e.Cancel = true; Hide(); return; }
            e.Cancel = true; closing = true; poll.Stop(); pause.IsEnabled = false;
            activity.Text = L.Text("Ui.Releasing");
            try { await bridge.DisposeAsync(); }
            finally { trayAvailability.Dispose(); tray?.Dispose(); lifetime.Shutdown(); }
        };
        Program.Instance?.SetHandler(stop => Dispatcher.UIThread.Post(() => { if (stop) RequestQuit(); else RestoreWindow(); }));
        if (!preview) Program.RequestExit = () => Dispatcher.UIThread.Post(RequestQuit);
        trayAvailability.Changed += available => Dispatcher.UIThread.Post(() => { if (!available && !closing) RestoreWindow(); });
        Refresh();
        void Add(Control control, int row) { Grid.SetRow(control, row); grid.Children.Add(control); }
    }
    static TextBlock Section(TextBlock block)
    { block.FontSize = 10; block.FontWeight = FontWeight.Bold; block.VerticalAlignment = VerticalAlignment.Bottom; block.Margin = new(0, 0, 0, 5); return block; }
    void ChangeMode(string value) { mode = value; preferences.Save("mode", value); bridge.SetMode(value); Refresh(); }
    void ChangeSpeaker(string value) { speaker = value; preferences.Save("speaker", value); bridge.SetSpeaker(value); Refresh(); }
    void TogglePause() { paused = !paused; bridge.SetPaused(paused); Refresh(); }
    public void RequestQuit() { quitting = true; Close(); }
    void RestoreWindow() { Show(); if (WindowState == WindowState.Minimized) WindowState = WindowState.Normal; Activate(); }
    void Refresh()
    {
        var state = bridge.State;
        if (state.RequiresResume && !ReferenceEquals(state, lastResumeRequest))
        { paused = true; lastResumeRequest = state; }
        hero.Paused = paused && state.Paused; hero.Releasing = state.Releasing;
        hero.Ready = state.Ready; hero.Xbox = mode == "Xbox"; hero.Refresh(); header.InvalidateVisual();
        modeSection.Text = L.Text("Ui.ModeSection"); speakerSection.Text = L.Text("Ui.SpeakerSection");
        native.Content = L.Text("Ui.NativeMode"); xbox.Content = L.Text("Ui.XboxMode");
        high.Content = L.Text("Ui.SpeakerHigh").ToUpper(L.Culture); low.Content = L.Text("Ui.SpeakerLow").ToUpper(L.Culture); off.Content = L.Text("Ui.SpeakerOff").ToUpper(L.Culture);
        logs.Content = L.Text("Ui.Logs"); pause.Content = L.Text(paused ? "Ui.Resume" : "Ui.Pause");
        native.Selected = mode != "Xbox"; xbox.Selected = mode == "Xbox"; high.Selected = speaker == "Alto"; low.Selected = speaker == "Bajo"; off.Selected = speaker == "Off";
        foreach (var button in new[] { native, xbox, high, low, off, logs, pause }) { AutomationProperties.SetName(button, button.Content?.ToString() ?? ""); button.InvalidateVisual(); }
        hint.Text = L.Text(mode == "Xbox" ? "Ui.XboxHint" : "Ui.NativeHint");
        link.Text = state.Message;
        if (state.Ready && state.Battery >= 0) link.Text += "\n" + L.Text("Bridge.Battery", state.Battery) + (state.Headset ? L.Text("Bridge.HeadsetSuffix") : "");
        telemetry.Text = mode == "Xbox" ? L.Text("Ui.Rumble") + "  " + L.Text(state.Rumble ? "Ui.Active" : "Ui.Idle") :
            string.Join("\n", Lamp("Ui.Haptics", state.Haptics), Lamp("Ui.Triggers", state.Triggers), Lamp("Ui.Audio", state.Audio));
        activity.Text = Environment.TickCount64 < transientUntil ? transientMessage : preview ? LinuxText.Text("Linux.Preview") : state.Message;
        RefreshTray(state);
        static string Lamp(string key, bool active) => L.Text(key) + "  " + (active ? "●" : "○");
    }
    void CreateTray()
    {
        var logo = new LogoControl { Width = 64, Height = 64 };
        logo.Measure(new Size(64, 64)); logo.Arrange(new Rect(0, 0, 64, 64));
        using var bitmap = new RenderTargetBitmap(new PixelSize(64, 64), new Vector(96, 96)); bitmap.Render(logo);
        Icon = new WindowIcon(bitmap);
        tray = new TrayIcon { Icon = Icon, ToolTipText = "MadSense", IsVisible = true };
        tray.Clicked += (_, _) => RestoreWindow();
        TrayIcon.SetIcons(Application.Current!, new TrayIcons { tray });
        RefreshTray(bridge.State);
    }
    void RefreshTray(LinuxBridgeState state)
    {
        if (tray == null) return;
        var signature = $"{L.CurrentLanguage}:{paused}:{state.Paused}:{state.Releasing}:{mode}:{speaker}:{state.Ready}:{state.Battery}:{Autostart.Enabled}:{checkingUpdates}:{availableUpdate?.Version}";
        if (signature == traySignature) return; traySignature = signature;
        tray.ToolTipText = L.Text(state.Releasing ? "Ui.Releasing" : state.Paused ? "Ui.TrayPaused" : state.Ready ? "Ui.TrayReady" : "Ui.TrayWaiting") + (state.Battery >= 0 ? L.Text("Ui.TrayBattery", state.Battery) : "");
        var menu = new NativeMenu();
        menu.Items.Add(Item(L.Text("Ui.Open"), RestoreWindow));
        menu.Items.Add(Item(L.Text(paused ? "Ui.Resume" : "Ui.Pause"), TogglePause));
        var modeMenu = new NativeMenu();
        modeMenu.Items.Add(Item(L.Text("Ui.NativeMode"), () => ChangeMode("Auto"), mode != "Xbox"));
        modeMenu.Items.Add(Item(L.Text("Ui.XboxMode"), () => ChangeMode("Xbox"), mode == "Xbox"));
        menu.Items.Add(new NativeMenuItem { Header = L.Text("Ui.ModeSection"), Menu = modeMenu });
        var speakerMenu = new NativeMenu();
        foreach (var (level, key) in new[] { ("Alto", "Ui.SpeakerHigh"), ("Bajo", "Ui.SpeakerLow"), ("Off", "Ui.SpeakerOff") })
            speakerMenu.Items.Add(Item(L.Text(key), () => ChangeSpeaker(level), speaker == level));
        menu.Items.Add(new NativeMenuItem { Header = L.Text("Ui.SpeakerMenu"), Menu = speakerMenu });
        var languageMenu = new NativeMenu();
        languageMenu.Items.Add(Item(LinuxText.Text("Ui.LanguageAuto"), () => languages.SelectedIndex = 0, L.Preference == "auto"));
        for (var i = 0; i < L.Languages.Count; i++)
        { var index = i; var language = L.Languages[i]; languageMenu.Items.Add(Item(language.NativeName, () => languages.SelectedIndex = index + 1, L.Preference == language.Code)); }
        menu.Items.Add(new NativeMenuItem { Header = L.Text("Ui.Language"), Menu = languageMenu });
        menu.Items.Add(Item(LinuxText.Text("Ui.Autostart"), () =>
        { try { Autostart.Set(!Autostart.Enabled); RefreshTray(bridge.State); } catch (Exception error) { activity.Text = error.Message; } }, Autostart.Enabled));
        menu.Items.Add(Item(L.Text("Ui.Logs"), ShowDiagnostics));
        var update = Item(L.Text(checkingUpdates ? "Ui.CheckingUpdates" : "Ui.CheckUpdates"), () => CheckUpdates());
        update.IsEnabled = !checkingUpdates; menu.Items.Add(update);
        if (availableUpdate != null) menu.Items.Add(Item(LinuxText.Text("Linux.DownloadUpdate", availableUpdate.Version), OpenUpdate));
        menu.Items.Add(new NativeMenuItemSeparator()); menu.Items.Add(Item(L.Text("Ui.Quit"), RequestQuit)); tray.Menu = menu;
        static NativeMenuItem Item(string name, Action action, bool? selected = null)
        {
            var item = new NativeMenuItem { Header = name };
            if (selected is { } value) { item.ToggleType = MenuItemToggleType.CheckBox; item.IsChecked = value; }
            item.Click += (_, _) => action(); return item;
        }
    }
    async void ShowDiagnostics()
    {
        var text = new TextBox { Text = bridge.DiagnosticText, IsReadOnly = true, AcceptsReturn = true, TextWrapping = TextWrapping.Wrap, FontFamily = Brand.Mono, FontSize = 12 };
        var close = new BrandButton(LinuxText.Text("Linux.Close"));
        var dialog = new Window { Title = "MadSense · " + LinuxText.Text("Linux.Diagnostics"), Width = 760, Height = 480, MinWidth = 600, MinHeight = 360, Background = Brand.Paper };
        var settings = new Grid { ColumnDefinitions = new("*,210") };
        var startup = new CheckBox { Content = LinuxText.Text("Ui.Autostart"), IsChecked = !preview && Autostart.Enabled, IsEnabled = !preview, FontSize = 12 };
        startup.IsCheckedChanged += (_, _) =>
        { try { Autostart.Set(startup.IsChecked == true); RefreshTray(bridge.State); } catch (Exception error) { text.Text = error.Message; } };
        settings.Children.Add(startup);
        var update = new BrandButton(L.Text("Ui.CheckUpdates")) { IsEnabled = !preview };
        update.Click += (_, _) => { if (availableUpdate != null) OpenUpdate(); else CheckUpdates(message => { text.Text = message + "\n\n" + bridge.DiagnosticText; if (availableUpdate != null) update.Content = LinuxText.Text("Linux.DownloadUpdate", availableUpdate.Version); update.InvalidateVisual(); }); };
        Grid.SetColumn(update, 1); settings.Children.Add(update);
        var grid = new Grid { Margin = new Thickness(20), RowDefinitions = new("48,*,48") }; grid.Children.Add(settings); Grid.SetRow(text, 1); grid.Children.Add(text); Grid.SetRow(close, 2); grid.Children.Add(close); dialog.Content = grid;
        close.Click += (_, _) => dialog.Close(); await dialog.ShowDialog(this);
    }
    async void CheckUpdates(Action<string>? notify = null)
    {
        if (preview || checkingUpdates) return;
        checkingUpdates = true; RefreshTray(bridge.State);
        var result = await LinuxUpdates.Check();
        checkingUpdates = false; availableUpdate = result.Update;
        var message = !result.Available ? L.Text("Ui.UpdateCheckFailed") : !result.HasLinuxRelease ? LinuxText.Text("Linux.NoRelease") : result.Update == null ? L.Text("Ui.UpToDate") : LinuxText.Text("Linux.DownloadUpdate", result.Update.Version);
        transientMessage = message; transientUntil = Environment.TickCount64 + 10000;
        notify?.Invoke(message); Refresh();
    }
    void OpenUpdate()
    {
        if (availableUpdate == null || preview) return;
        try { Process.Start(new ProcessStartInfo(availableUpdate.Download.AbsoluteUri) { UseShellExecute = true }); }
        catch (Exception error) { transientMessage = error.Message; transientUntil = Environment.TickCount64 + 10000; }
    }
    void CaptureSmoke(string directory)
    {
        Directory.CreateDirectory(directory);
        using var bitmap = new RenderTargetBitmap(new PixelSize((int)ClientSize.Width, (int)ClientSize.Height), new Vector(96, 96));
        bitmap.Render(this); bitmap.Save(Path.Combine(directory, "ui-" + L.CurrentLanguage + ".png"), PngBitmapEncoderOptions.Default);
        File.WriteAllText(Path.Combine(directory, "ui-" + L.CurrentLanguage + ".json"), JsonSerializer.Serialize(new
        { language = L.CurrentLanguage, platform = Environment.OSVersion.ToString(), preview, physical_device_opened = false, ready = hero.Ready, headline = hero.Headline, subheadline = hero.Subheadline, tagline = L.Text("Ui.HeaderTagline"), client_width = ClientSize.Width, client_height = ClientSize.Height }));
        RequestQuit();
    }
}

sealed class LogoControl : Control
{
    public override void Render(DrawingContext context)
    { Brand.Rect(context, Brand.Hot, new Rect(Bounds.Size)); Brand.Logo(context, new Rect(Bounds.Size)); }
}
