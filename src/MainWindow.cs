using System.Diagnostics;
using System.Drawing.Drawing2D;
using System.Globalization;
using System.Threading.Channels;
using DualSenseClient.Controllers;

namespace MadSense;

/// <summary>A small native UI. Every bridge operation runs on one serialized worker.</summary>
sealed class MainWindow : Form
{
    static readonly Color Ink = Color.FromArgb(24, 24, 21);
    static readonly Color Paper = Color.FromArgb(240, 235, 220);
    static readonly Color Acid = Color.FromArgb(220, 241, 49);
    static readonly Color Hot = Color.FromArgb(249, 84, 40);
    static readonly Color Muted = Color.FromArgb(99, 96, 85);
    static readonly string DisplayVersion = typeof(MainWindow).Assembly.GetName().Version is { } version
        ? $"{version.Major}.{version.Minor}.{Math.Max(0, version.Build)}" : "0.4";
    const int WM_DEVICECHANGE = 0x0219, DBT_DEVNODES_CHANGED = 0x0007;
    const int LowBatteryPercent = 15;
    readonly Channel<Action<Bridge>> commands = Channel.CreateUnbounded<Action<Bridge>>(new() { SingleReader = true });
    readonly SemaphoreSlim wake = new(0, 1);
    readonly CancellationTokenSource lifetime = new();
    readonly EventWaitHandle? showSignal;
    readonly EventWaitHandle? stopSignal;
    readonly EventWaitHandle? installerStopSignal;
    readonly System.Windows.Forms.Timer animation = new() { Interval = 250 };
    readonly NotifyIcon tray;
    readonly ToolStripMenuItem autostart;
    readonly ToolStripMenuItem openItem = new(), pauseItem = new(), speakerMenu = new(), languageMenu = new(), quitItem = new();
    readonly Dictionary<string, ToolStripMenuItem> languageItems = new();
    readonly HeaderPanel header = new() { Dock = DockStyle.Fill, Margin = Padding.Empty };
    readonly Label section = CreateSection(), speakerSection = CreateSection();
    readonly RoundedButton details = new() { Dock = DockStyle.Fill, Margin = new Padding(0, 0, 10, 0) };
    readonly HeroPanel hero = new() { Dock = DockStyle.Fill };
    readonly RoundedButton pause = new() { Accent = true, Width = 144, Height = 40 };
    readonly Label modeHint = new() { Dock = DockStyle.Fill, ForeColor = Muted, TextAlign = ContentAlignment.MiddleLeft };
    readonly Label activity = new SingleLineLabel { Dock = DockStyle.Fill, ForeColor = Muted, TextAlign = ContentAlignment.MiddleLeft };
    readonly Dictionary<string, RoundedButton> modeButtons = new();
    readonly Dictionary<string, RoundedButton> speakerButtons = new();
    readonly Dictionary<string, ToolStripMenuItem> speakerItems = new();
    readonly Dictionary<Label, Font> fittedFonts = new();
    readonly Task worker;
    readonly bool previewOnly;
    bool paused, quitting, hasHiddenTip, allowVisible, lowBatteryNotified;
    float phase;
    ActivityEntry lastMessage = new(DateTime.Now, L.Message("Ui.Starting"));
    ActivityEntry displayedMessage = new(DateTime.Now, L.Message("Ui.Starting"));
    sealed record ActivityEntry(DateTime Time, UiMessage Message)
    {
        public string Render() => $"{Time:HH:mm:ss} · {Message.Render().Split('\n')[0]}";
    }

    static Label CreateSection() => new() { Dock = DockStyle.Fill, TextAlign = ContentAlignment.BottomLeft, ForeColor = Ink, Font = new Font("Consolas", 8, FontStyle.Bold), Padding = new Padding(0, 0, 0, 5) };

    [System.Runtime.InteropServices.DllImport("dwmapi.dll")]
    static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);
    protected override void OnHandleCreated(EventArgs e)
    {
        base.OnHandleCreated(e);
        int enabled = 1;
        DwmSetWindowAttribute(Handle, 20, ref enabled, sizeof(int));
    }

    /// <summary>Started with --tray (autostart): the window stays hidden until the tray icon opens it.</summary>
    protected override void SetVisibleCore(bool value)
    {
        if (!allowVisible)
        {
            value = false;
            if (!IsHandleCreated) CreateHandle();
        }
        base.SetVisibleCore(value);
    }

    /// <summary>
    /// Windows broadcasts a device-tree change when the pad connects or drops. Ask the worker to
    /// retry right away so "pulsa PS" turns into "listo" in about a second instead of up to three.
    /// </summary>
    protected override void WndProc(ref Message m)
    {
        if (!previewOnly && m.Msg == WM_DEVICECHANGE && (int)m.WParam == DBT_DEVNODES_CHANGED && !quitting)
        {
            commands.Writer.TryWrite(b => b.RequestRetry());
            Nudge();
        }
        base.WndProc(ref m);
    }

    public MainWindow(bool startHidden = false, bool previewOnly = false)
    {
        this.previewOnly = previewOnly;
        if (!previewOnly)
        {
            showSignal = new(false, EventResetMode.AutoReset, "Local\\MadSense-Show");
            stopSignal = new(false, EventResetMode.AutoReset, "Local\\MadSense-Stop");
            installerStopSignal = InstallerStopSignal.Create();
        }
        allowVisible = !startHidden;
        SuspendLayout();
        AutoScaleMode = AutoScaleMode.Dpi;
        AutoScaleDimensions = new SizeF(96, 96);
        Text = "MadSense";
        ClientSize = new Size(900, 730);
        MinimumSize = new Size(800, 730);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Paper; ForeColor = Ink;
        Font = new Font("Segoe UI", 10);
        DoubleBuffered = true;
        Icon = Logo.CreateIcon();
        Padding = new Padding(24, 12, 24, 16);

        var layout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 7, Margin = Padding.Empty };
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 106));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 30));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 119));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 30));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 50));
        layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 48));
        layout.Controls.Add(header, 0, 0);
        layout.Controls.Add(hero, 0, 1);
        layout.Controls.Add(section, 0, 2);
        var modes = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 2, Margin = Padding.Empty };
        for (int i = 0; i < 2; i++) modes.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
        modes.RowStyles.Add(new RowStyle(SizeType.Absolute, 54)); modes.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        AddMode("Auto", 0); AddMode("Xbox", 1);
        modes.Controls.Add(modeHint, 0, 1); modes.SetColumnSpan(modeHint, 2);
        layout.Controls.Add(modes, 0, 3);
        layout.Controls.Add(speakerSection, 0, 4);
        var speaker = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1, Margin = Padding.Empty };
        for (int i = 0; i < 3; i++) speaker.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f / 3));
        AddSpeaker("Alto", 0); AddSpeaker("Bajo", 1); AddSpeaker("Off", 2);
        layout.Controls.Add(speaker, 0, 5);
        var footer = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1, Margin = Padding.Empty, Padding = new Padding(0, 6, 0, 0) };
        footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); footer.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 105)); footer.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 144));
        activity.Font = new Font(Font.FontFamily, 8);
        footer.Controls.Add(activity, 0, 0);
        details.Click += (_, _) => { if (previewOnly) return; try { Process.Start(new ProcessStartInfo("explorer.exe", AppContext.BaseDirectory) { UseShellExecute = true }); } catch (Exception e) { ShowActivity(UiMessage.FromException(e)); } };
        footer.Controls.Add(details, 1, 0);
        pause.Dock = DockStyle.Fill; pause.Margin = Padding.Empty;
        pause.Click += (_, _) => TogglePause();
        footer.Controls.Add(pause, 2, 0);
        layout.Controls.Add(footer, 0, 6);
        Controls.Add(layout);

        var menu = new ContextMenuStrip();
        openItem.Click += (_, _) => RestoreWindow(); menu.Items.Add(openItem);
        pauseItem.Click += (_, _) => TogglePause(); menu.Items.Add(pauseItem);
        foreach (var level in SpeakerLevels.All)
        {
            var item = new ToolStripMenuItem();
            item.Click += (_, _) => Send(b => b.SetSpeaker(level));
            speakerItems[level] = item; speakerMenu.DropDownItems.Add(item);
        }
        menu.Items.Add(speakerMenu);
        foreach (var code in new[] { "auto" }.Concat(L.Languages.Select(option => option.Code)))
        {
            var item = new ToolStripMenuItem();
            item.Click += (_, _) => ChangeLanguage(code);
            languageItems[code] = item; languageMenu.DropDownItems.Add(item);
        }
        menu.Items.Add(languageMenu);
        autostart = new ToolStripMenuItem { CheckOnClick = true, Checked = !previewOnly && Preferences.StartsWithWindows };
        autostart.CheckedChanged += (_, _) =>
        {
            if (previewOnly) return;
            try { Preferences.StartsWithWindows = autostart.Checked; }
            catch (Exception e) { ShowActivity(L.Message("Ui.AutostartError", UiMessage.FromException(e))); autostart.Checked = Preferences.StartsWithWindows; }
        };
        menu.Items.Add(autostart);
        menu.Items.Add(new ToolStripSeparator());
        quitItem.Click += async (_, _) => await QuitAsync(); menu.Items.Add(quitItem);
        tray = new NotifyIcon { Icon = Icon, Text = "MadSense", Visible = !previewOnly, ContextMenuStrip = menu };
        tray.DoubleClick += (_, _) => RestoreWindow();
        animation.Tick += async (_, _) =>
        {
            if (stopSignal?.WaitOne(0) == true || installerStopSignal?.WaitOne(0) == true) { await QuitAsync(); return; }
            if (showSignal?.WaitOne(0) == true) RestoreWindow();
            // No drawing or high-frequency timer wakeups when the window is hidden.
            if (Visible && WindowState != FormWindowState.Minimized) { phase += .2f; hero.Phase = phase; hero.Invalidate(); }
        };
        VisibleChanged += (_, _) => AdjustAnimation();
        Resize += (_, _) => { if (WindowState == FormWindowState.Minimized && !previewOnly) Hide(); AdjustAnimation(); FitLabels(); };
        FormClosing += (_, e) =>
        {
            if (quitting || previewOnly) return;
            if (e.CloseReason is CloseReason.WindowsShutDown or CloseReason.TaskManagerClosing)
            {
                quitting = true; animation.Stop(); lifetime.Cancel(); tray.Visible = false;
                try { worker?.Wait(TimeSpan.FromSeconds(3)); } catch { }
                return;
            }
            e.Cancel = true; Hide();
            if (!hasHiddenTip) { tray.ShowBalloonTip(2500, L.Text("Ui.HiddenTitle"), L.Text("Ui.HiddenBody"), ToolTipIcon.Info); hasHiddenTip = true; }
        };
        FormClosed += (_, _) => L.Changed -= LanguageChanged;
        L.Changed += LanguageChanged;
        ApplyLanguage();
        ResumeLayout(true);
        FitLabels();
        animation.Start();
        worker = previewOnly ? Task.CompletedTask : Task.Run(BridgeLoop);

        void AddMode(string mode, int column)
        {
            var button = new RoundedButton { Dock = DockStyle.Fill, Margin = new Padding(column == 0 ? 0 : 6, 0, column == 1 ? 0 : 6, 0), Selected = mode == "Auto" };
            button.Click += (_, _) => Send(b => b.SetMode(mode));
            modeButtons[mode] = button; modes.Controls.Add(button, column, 0);
        }

        void AddSpeaker(string level, int column)
        {
            var button = new RoundedButton { Dock = DockStyle.Fill, Margin = new Padding(column == 0 ? 0 : 6, 0, column == 2 ? 0 : 6, 0), Selected = level == "Alto" };
            button.Click += (_, _) => Send(b => b.SetSpeaker(level));
            speakerButtons[level] = button; speaker.Controls.Add(button, column, 0);
        }
    }

    void ChangeLanguage(string preference)
    {
        try { Preferences.SaveLanguage(preference); L.Use(preference); }
        catch (Exception e) { ShowActivity(L.Message("Ui.LanguageError", UiMessage.FromException(e))); }
    }

    void LanguageChanged()
    {
        if (IsDisposed || Disposing || quitting) return;
        if (InvokeRequired) { Post(() => { ApplyLanguage(); if (!previewOnly) Nudge(); }); return; }
        ApplyLanguage();
        if (!previewOnly) Nudge();
    }

    void ApplyLanguage()
    {
        var culture = CultureInfo.GetCultureInfo(L.CurrentLanguage);
        section.Text = L.Text("Ui.ModeSection"); speakerSection.Text = L.Text("Ui.SpeakerSection");
        details.Text = L.Text("Ui.Logs"); details.AccessibleDescription = L.Text("Ui.LogsDescription");
        pause.Text = L.Text(paused ? "Ui.Resume" : "Ui.Pause");
        modeButtons["Auto"].Text = L.Text("Ui.NativeMode"); modeButtons["Xbox"].Text = L.Text("Ui.XboxMode");
        modeButtons["Auto"].AccessibleDescription = L.Text("Ui.NativeDescription");
        modeButtons["Xbox"].AccessibleDescription = L.Text("Ui.XboxDescription");
        foreach (var pair in speakerButtons)
        {
            var label = SpeakerLevels.Label(pair.Key);
            pair.Value.Text = label.ToUpper(culture);
            pair.Value.AccessibleDescription = L.Text("Ui.SpeakerLevel", label);
            speakerItems[pair.Key].Text = culture.TextInfo.ToTitleCase(label);
        }
        openItem.Text = L.Text("Ui.Open"); pauseItem.Text = L.Text("Ui.TogglePause");
        speakerMenu.Text = L.Text("Ui.SpeakerMenu"); languageMenu.Text = L.Text("Ui.Language");
        autostart.Text = L.Text("Ui.Autostart"); quitItem.Text = L.Text("Ui.Quit");
        foreach (var (code, item) in languageItems)
        {
            item.Text = code == "auto" ? L.Text("Ui.LanguageAuto") : L.Languages.First(option => option.Code == code).NativeName;
            item.Checked = code == L.Preference;
        }
        hero.AccessibleName = L.Text("Ui.ControllerState");
        RefreshStatusText();
        header.Invalidate(); hero.Invalidate();
        foreach (var button in modeButtons.Values.Concat(speakerButtons.Values)) button.Invalidate();
        FitLabels();
    }

    void RefreshStatusText()
    {
        var hint = L.Text(hero.Xbox ? "Ui.XboxHint" : "Ui.NativeHint");
        if (modeHint.Text != hint) { modeHint.Text = hint; FitLabel(modeHint, 10, 8.5f, true); }
        activity.Text = displayedMessage.Render();
        activity.AccessibleDescription = displayedMessage.Message.Render();
        var battery = hero.Connected && hero.Signals is { Battery: >= 0 } sig ? L.Text("Ui.TrayBattery", sig.Battery) : "";
        tray.Text = Truncate(L.Text(hero.Connected ? "Ui.TrayReady" : paused ? "Ui.TrayPaused" : "Ui.TrayWaiting") + battery, 63);
    }

    void ShowActivity(UiMessage message)
    {
        displayedMessage = new(DateTime.Now, message);
        Interlocked.Exchange(ref lastMessage, displayedMessage);
        activity.Text = displayedMessage.Render();
        activity.AccessibleDescription = message.Render();
    }

    void FitLabels()
    {
        FitLabel(modeHint, 10, 8.5f, true);
        FitLabel(section, 8, 7.5f, false);
        FitLabel(speakerSection, 8, 7.5f, false);
    }

    void FitLabel(Label label, float largest, float smallest, bool wrap)
    {
        if (label.ClientSize.Width < 1 || label.ClientSize.Height < 1) return;
        var available = new Size(Math.Max(1, label.ClientSize.Width - label.Padding.Horizontal), Math.Max(1, label.ClientSize.Height - label.Padding.Vertical));
        var flags = TextFormatFlags.NoPadding | (wrap ? TextFormatFlags.WordBreak : TextFormatFlags.SingleLine);
        Font? chosen = null;
        for (float size = largest; size >= smallest; size -= .25f)
        {
            var candidate = new Font(label.Font.FontFamily, size, label.Font.Style);
            var measured = TextRenderer.MeasureText(label.Text, candidate, available, flags);
            if ((measured.Width <= available.Width && measured.Height <= available.Height) || size <= smallest)
            { chosen = candidate; break; }
            candidate.Dispose();
        }
        if (chosen is null) return;
        if (Math.Abs(label.Font.Size - chosen.Size) < .01f) chosen.Dispose();
        else
        {
            fittedFonts.Remove(label, out var old);
            label.Font = chosen; fittedFonts[label] = chosen; old?.Dispose();
        }
    }

    /// <summary>Visual QA fixture. It never starts a bridge or creates global control events.</summary>
    internal void SetPreviewState(bool connected, bool paused, bool xbox, BridgeSignals? signals, UiMessage? status = null)
    {
        if (!previewOnly) throw new InvalidOperationException("Preview state requires previewOnly.");
        this.paused = paused; hero.Connected = connected; hero.Paused = paused; hero.Xbox = xbox;
        hero.Signals = signals; hero.Status = status ?? L.Message("Ui.WaitingInstructions");
        foreach (var pair in modeButtons) pair.Value.Selected = xbox ? pair.Key == "Xbox" : pair.Key == "Auto";
        foreach (var pair in speakerButtons) pair.Value.Enabled = !xbox;
        foreach (var pair in speakerItems) pair.Value.Enabled = !xbox;
        ApplyLanguage();
    }

    void AdjustAnimation() => animation.Interval = Visible && WindowState != FormWindowState.Minimized ? 250 : 1000;
    void RestoreWindow() { if (quitting) return; allowVisible = true; Show(); WindowState = FormWindowState.Normal; Activate(); }
    void TogglePause()
    {
        if (quitting) return;
        paused = !paused; pause.Text = L.Text(paused ? "Ui.Resume" : "Ui.Pause");
        if (previewOnly) { hero.Paused = paused; RefreshStatusText(); hero.Invalidate(); return; }
        var shouldPause = paused;
        Send(b => { if (shouldPause) { b.Stop(); b.Log(L.Message("Ui.PausedMessage")); } else b.Start(); });
    }

    /// <summary>Queues a bridge command and wakes the worker so it runs now, not on the next second.</summary>
    void Send(Action<Bridge> command)
    {
        if (quitting || previewOnly) return;
        commands.Writer.TryWrite(command);
        Nudge();
    }

    /// <summary>Wakes the worker; extra nudges while one is pending are coalesced.</summary>
    void Nudge()
    {
        try { wake.Release(); } catch (SemaphoreFullException) { } catch (ObjectDisposedException) { }
    }

    async Task BridgeLoop()
    {
        try { await RunBridge().ConfigureAwait(false); }
        catch (Exception e)
        {
            Post(() => { ShowActivity(L.Message("Ui.BridgeStartError", UiMessage.FromException(e))); hero.Status = L.Message("Ui.BridgeStartFailed"); hero.Connected = false; hero.Invalidate(); });
            try { File.AppendAllText(Path.Combine(AppContext.BaseDirectory, "ui-error.log"), $"{DateTimeOffset.Now:O} {e}\n"); } catch { }
        }
    }

    async Task RunBridge()
    {
        using var bridge = new Bridge();
        bridge.Message += message => Interlocked.Exchange(ref lastMessage, new ActivityEntry(DateTime.Now, message));
        bridge.WakeRequested += Nudge;
        try { bridge.Start(); } catch (Exception e) { Interlocked.Exchange(ref lastMessage, new ActivityEntry(DateTime.Now, UiMessage.FromException(e))); }
        try
        {
            while (!lifetime.IsCancellationRequested)
            {
                while (commands.Reader.TryRead(out var command))
                    try { command(bridge); } catch (Exception e) { Interlocked.Exchange(ref lastMessage, new ActivityEntry(DateTime.Now, UiMessage.FromException(e))); }
                try { bridge.Update(); } catch (Exception e) { Interlocked.Exchange(ref lastMessage, new ActivityEntry(DateTime.Now, UiMessage.FromException(e))); }
                var running = bridge.Ready;
                var status = bridge.DisplayStatus;
                var mode = bridge.Mode;
                var speakerLevel = bridge.Speaker;
                var message = Volatile.Read(ref lastMessage);
                var signals = bridge.Signals;
                var notifyLowBattery = CheckLowBattery(signals);
                Post(() =>
                {
                    hero.Connected = running;
                    hero.Paused = paused;
                    hero.Status = status;
                    hero.Signals = running ? signals : null;
                    hero.Xbox = mode == "Xbox";
                    hero.Invalidate();
                    foreach (var pair in modeButtons) { pair.Value.Selected = pair.Key == mode || pair.Key == "Auto" && mode == "Native"; pair.Value.Invalidate(); }
                    // The speaker only exists for the DualSense persona; an Xbox pad has none.
                    foreach (var pair in speakerButtons) { pair.Value.Selected = pair.Key == speakerLevel; pair.Value.Enabled = mode != "Xbox"; pair.Value.Invalidate(); }
                    foreach (var pair in speakerItems) { pair.Value.Checked = pair.Key == speakerLevel; pair.Value.Enabled = mode != "Xbox"; }
                    displayedMessage = message;
                    RefreshStatusText();
                    if (notifyLowBattery) tray.ShowBalloonTip(6000, L.Text("Ui.LowBatteryTitle"), L.Text("Ui.LowBatteryBody", signals.Battery), ToolTipIcon.Warning);
                });
                // Sleep until a command, a device event or the bridge's own schedule needs us.
                try { await wake.WaitAsync(Math.Min(1000, bridge.MillisUntilDue), lifetime.Token).ConfigureAwait(false); }
                catch (OperationCanceledException) { break; }
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception e) { Post(() => ShowActivity(L.Message("Ui.BridgeError", UiMessage.FromException(e)))); }
        finally { bridge.WakeRequested -= Nudge; }
        // Dispose completes on this same worker, never concurrently with Update.
    }

    /// <summary>One warning per discharge cycle; re-armed once the level recovers past the hysteresis.</summary>
    bool CheckLowBattery(BridgeSignals signals)
    {
        if (signals.Battery < 0) return false;
        if (signals.Charging || BatteryNotificationPolicy.ShouldResetLowNotification(signals.Battery, LowBatteryPercent)) { lowBatteryNotified = false; return false; }
        if (!BatteryNotificationPolicy.ShouldNotifyLowBattery(signals.Battery, LowBatteryPercent, lowBatteryNotified)) return false;
        lowBatteryNotified = true;
        return true;
    }

    static string Truncate(string text, int max) => text.Length <= max ? text : text[..max];

    void Post(Action action)
    {
        if (IsDisposed || Disposing || !IsHandleCreated) return;
        try { BeginInvoke(() => { if (!IsDisposed && !quitting) action(); }); } catch (InvalidOperationException) { }
    }

    async Task QuitAsync()
    {
        if (quitting) return;
        quitting = true; pause.Enabled = false; activity.Text = L.Text("Ui.Releasing");
        animation.Stop(); commands.Writer.TryComplete(); lifetime.Cancel();
        try { await worker; } catch (Exception e) { Debug.WriteLine(e); }
        tray.Visible = false; tray.Dispose(); animation.Dispose();
        showSignal?.Dispose(); stopSignal?.Dispose(); installerStopSignal?.Dispose();
        if (!previewOnly) { lifetime.Dispose(); wake.Dispose(); }
        Close();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            L.Changed -= LanguageChanged;
            if (previewOnly)
            {
                animation.Dispose(); tray?.ContextMenuStrip?.Dispose(); tray?.Dispose();
                lifetime.Dispose(); wake.Dispose();
            }
            foreach (var font in fittedFonts.Values) font.Dispose();
            fittedFonts.Clear();
        }
        base.Dispose(disposing);
    }

    // Use a bounded font fit for translated artwork; the full text also remains accessible.
    static void DrawFitted(Graphics graphics, string text, string family, float largest, float smallest, FontStyle style, Brush brush, RectangleF bounds, bool wrap = false)
    {
        using var format = new StringFormat(StringFormat.GenericDefault)
        {
            FormatFlags = wrap ? 0 : StringFormatFlags.NoWrap,
            Trimming = StringTrimming.None,
            LineAlignment = StringAlignment.Near
        };
        for (float size = largest; size >= smallest; size -= .5f)
        {
            using var font = new Font(family, size, style, GraphicsUnit.Pixel);
            var measured = graphics.MeasureString(text, font, wrap ? new SizeF(bounds.Width, 1000) : new SizeF(10000, bounds.Height), format);
            if ((measured.Width <= bounds.Width + .5f && measured.Height <= bounds.Height + .5f) || size <= smallest)
            { graphics.DrawString(text, font, brush, bounds, format); return; }
        }
    }

    // All artwork is native vector drawing: no textures, browser or animation framework.
    sealed class HeaderPanel : Panel
    {
        public HeaderPanel() { DoubleBuffered = true; }
        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e); var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias;
            float s = DeviceDpi / 96f; g.ScaleTransform(s, s);
            float w = Width / s;
            var save = g.Save(); g.TranslateTransform(5, 10); g.RotateTransform(-6);
            using (var orange = new SolidBrush(Hot)) g.FillRectangle(orange, 1, 1, 68, 68);
            Logo.Draw(g, new RectangleF(5, 4, 58, 58)); g.Restore(save);
            using var small = new Font("Consolas", 10.5f, FontStyle.Bold, GraphicsUnit.Pixel);
            using var ink = new SolidBrush(Ink);
            float textRight = w > 650 ? w - 161 : w;
            DrawFitted(g, "MADSENSE", "Impact", 57, 40, FontStyle.Regular, ink, new RectangleF(78, 0, textRight - 78, 70));
            DrawFitted(g, L.Text("Ui.HeaderTagline"), "Consolas", 10.5f, 8, FontStyle.Bold, ink, new RectangleF(82, 65, textRight - 82, 26), true);
            using var line = new Pen(Ink, 3);
            g.DrawLine(line, 0, 94, w, 94);
            if (w > 650)
            {
                float x = w - 146;
                g.FillRectangle(ink, x, 12, 146, 63);
                using var paper = new SolidBrush(Paper);
                g.DrawString("BLUETOOTH / PC", small, paper, x + 11, 20);
                using var acid = new SolidBrush(Acid); g.FillRectangle(acid, x + 10, 42, 126, 25);
                DrawFitted(g, L.Text("Ui.Version", DisplayVersion), "Consolas", 13, 10, FontStyle.Bold, ink, new RectangleF(x + 16, 45, 114, 22));
                for (int i = 0; i < 12; i++)
                    g.FillRectangle(ink, w - 145 + i * 12, 84, i % 3 == 0 ? 6 : 2, 6);
            }
        }
    }

    sealed class HeroPanel : Panel
    {
        public bool Connected, Paused, Xbox;
        public UiMessage Status = L.Message("Ui.WaitingInstructions");
        public float Phase;
        /// <summary>Live feedback lamps; <c>null</c> hides the row (not connected).</summary>
        public BridgeSignals? Signals;
        public HeroPanel() { DoubleBuffered = true; Margin = Padding.Empty; }
        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e); var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias;
            float s = DeviceDpi / 96f; g.ScaleTransform(s, s);
            float w = Width / s, h = Height / s;
            if (w < 20 || h < 20) return;
            using var ink = new SolidBrush(Ink); using var paper = new SolidBrush(Paper);
            using var orange = new SolidBrush(Hot); using var acid = new SolidBrush(Acid);
            float baseY = h - 70;
            g.FillRectangle(ink, 0, 0, w, h);
            var save = g.Save(); g.SetClip(new RectangleF(2, 2, w - 4, baseY - 2));
            // Offset orange printing plate, diagonally cut against the black headline area.
            g.FillPolygon(orange, new PointF[] { new(w * .62f, -5), new(w, -5), new(w, baseY + 2), new(w * .49f, baseY + 2) });
            using var dots = new SolidBrush(Color.FromArgb(75, Ink));
            for (float y = 9; y < baseY; y += 11)
                for (float x = w * .62f; x < w; x += 11)
                    g.FillEllipse(dots, x + ((int)y % 2 == 0 ? 3 : 0), y, 3, 3);
            using var streak = new Pen(Paper, 2.2f);
            for (int i = 0; i < 5; i++)
            {
                float y = 45 + i * 22;
                g.DrawLine(streak, w * .65f + i * 5, y, w * .9f + i * 5, y - 15);
            }
            float cw = Math.Clamp(w * .34f, 220, 290);
            var tilted = g.Save(); g.TranslateTransform(w * .76f, baseY * .51f); g.RotateTransform(-11);
            DrawController(g, new RectangleF(-cw / 2, -cw * .3f, cw, cw * .6f), Connected);
            g.Restore(tilted);
            // Sharp speech-label, distinct state text even without color perception.
            using var tiny = new Font("Consolas", 10.5f, FontStyle.Bold, GraphicsUnit.Pixel);
            float headlineWidth = w * .51f - 34;
            DrawFitted(g, L.Text(Xbox ? "Ui.HeroXbox" : "Ui.HeroDualSense"), "Consolas", 10.5f, 9, FontStyle.Bold, paper, new RectangleF(23, 20, headlineWidth, 20));
            string top = L.Text(Paused ? "Ui.HeroPaused" : Connected ? "Ui.HeroReady" : "Ui.HeroPressPs");
            string bottom = L.Text(Paused ? "Ui.HeroReleased" : Connected ? "Ui.HeroPlay" : "Ui.HeroOnController");
            DrawFitted(g, top, "Impact", 55, 28, FontStyle.Regular, paper, new RectangleF(20, 45, headlineWidth, 68));
            DrawFitted(g, bottom, "Impact", 35, 22, FontStyle.Regular, paper, new RectangleF(23, 112, headlineWidth, 45));
            float labelY = Math.Max(164, baseY - 45);
            string state = L.Text(Paused ? "Ui.BadgePaused" : Connected ? "Ui.BadgeReady" : "Ui.BadgeWaiting");
            float badgeWidth = Math.Min(headlineWidth - 12, Math.Max(212, g.MeasureString(state, tiny).Width + 40));
            var badge = new RectangleF(25, labelY, badgeWidth, 28);
            g.FillPolygon(Connected ? acid : paper, new PointF[] { new(badge.X, badge.Y), new(badge.Right + 6, badge.Y - 3), new(badge.Right, badge.Bottom), new(badge.X - 3, badge.Bottom + 2) });
            DrawFitted(g, state, "Consolas", 10.5f, 9, FontStyle.Bold, ink, new RectangleF(badge.X + 12, badge.Y + 7, badge.Width - 38, 18));
            if (!Paused && (int)(Phase * 2) % 2 == 0)
                g.FillRectangle(ink, badge.Right - 18, badge.Y + 10, 6, 6);
            g.Restore(save);
            // Functional status is printed on a calm strip, separated from the artwork.
            g.FillRectangle(paper, 2, baseY, w - 4, h - baseY - 2);
            using var outline = new Pen(Ink, 3); g.DrawRectangle(outline, 1.5f, 1.5f, w - 3, h - 3);
            using var index = new Font("Consolas", 11, FontStyle.Bold, GraphicsUnit.Pixel);
            g.DrawString("LINK", index, ink, 17, baseY + 23);
            g.DrawLine(outline, 64, baseY + 13, 64, h - 15);
            // Live lamps on the right of the strip: what the game is really driving on the pad.
            float lampsWidth = Signals is not null && w > 640 ? DrawLamps(g, w - 18, baseY + 12, h - baseY - 24, tiny, ink, paper, acid) : 0;
            string status = Paused ? L.Text("Ui.ConnectionPaused") : Status.Render();
            DrawFitted(g, status, "Segoe UI", 13, 10.5f, FontStyle.Regular, ink, new RectangleF(79, baseY + 10, w - 96 - lampsWidth, 52), true);
            AccessibleDescription = status + (Signals is { } sig ? Xbox
                ? L.Text("Ui.RumbleState", L.Text(sig.Rumble ? "Ui.Active" : "Ui.Idle"))
                : L.Text("Ui.FeedbackState", L.Text(sig.Haptics || sig.Rumble ? "Ui.Active" : "Ui.Idle"), L.Text(sig.Triggers ? "Ui.Active" : "Ui.Idle"), L.Text(sig.Audio ? "Ui.Active" : "Ui.Idle")) : "");
        }

        /// <summary>Draws the lamp row right-aligned at <paramref name="right"/> and returns its width.</summary>
        float DrawLamps(Graphics g, float right, float top, float height, Font font, Brush ink, Brush paper, Brush acid)
        {
            var sig = Signals!;
            // Xbox mode has no native haptics or trigger lane: show what that persona can carry.
            (string Label, bool On)[] lamps = Xbox
                ? [(L.Text("Ui.Rumble"), sig.Rumble)]
                : [(L.Text("Ui.Haptics"), sig.Haptics || sig.Rumble), (L.Text("Ui.Triggers"), sig.Triggers), (L.Text("Ui.Audio"), sig.Audio)];
            const float gap = 14, box = 9;
            float x = right;
            using var outline = new Pen(Ink, 1.5f);
            using var muted = new SolidBrush(Muted);
            float y = top + height / 2;
            for (int i = lamps.Length - 1; i >= 0; i--)
            {
                var size = g.MeasureString(lamps[i].Label, font);
                x -= size.Width;
                g.DrawString(lamps[i].Label, font, lamps[i].On ? ink : muted, x, y - size.Height / 2);
                x -= box + 5;
                var rect = new RectangleF(x, y - box / 2, box, box);
                if (lamps[i].On) g.FillRectangle(acid, rect);
                g.DrawRectangle(outline, rect.X, rect.Y, rect.Width, rect.Height);
                x -= gap;
            }
            return right - x + 8;
        }

        static void DrawController(Graphics g, RectangleF r, bool connected)
        {
            var save = g.Save(); g.TranslateTransform(r.X, r.Y); g.ScaleTransform(r.Width / 216, r.Height / 130);
            using var body = new GraphicsPath();
            // The bridge between the grips sits at y=103 so both sticks (bottom at y=91) stay inside the body.
            body.StartFigure(); body.AddBezier(36, 16, 12, 23, 0, 104, 14, 117); body.AddBezier(14, 117, 30, 133, 50, 114, 62, 103);
            body.AddLine(62, 103, 154, 103); body.AddBezier(154, 103, 166, 114, 186, 133, 202, 117); body.AddBezier(202, 117, 218, 103, 205, 23, 181, 16); body.AddBezier(181, 16, 150, 6, 65, 6, 36, 16); body.CloseFigure();
            using var ink = new SolidBrush(Ink); using var paper = new SolidBrush(Paper);
            var shadow = g.Save(); g.TranslateTransform(5, 7); g.FillPath(ink, body); g.Restore(shadow);
            using var outline = new Pen(Ink, 3); g.FillPath(paper, body); g.DrawPath(outline, body);
            g.FillRectangle(ink, 67, 20, 82, 36);
            using var link = new Pen(connected ? Hot : Ink, 3); g.DrawLine(link, 62, 23, 62, 48); g.DrawLine(link, 154, 23, 154, 48);
            g.FillRectangle(ink, 35, 36, 9, 30); g.FillRectangle(ink, 25, 46, 29, 9);
            g.FillEllipse(ink, 68, 64, 24, 24); g.FillEllipse(ink, 124, 64, 24, 24);
            g.DrawEllipse(outline, 65, 61, 30, 30); g.DrawEllipse(outline, 121, 61, 30, 30);
            g.FillEllipse(ink, 173, 33, 10, 10); g.FillEllipse(ink, 185, 46, 10, 10); g.FillEllipse(ink, 173, 59, 10, 10); g.FillEllipse(ink, 161, 46, 10, 10);
            using var hatch = new Pen(Ink, 1.2f);
            for (int i = 0; i < 5; i++) { g.DrawLine(hatch, 20 + i * 4, 88, 16 + i * 4, 107); g.DrawLine(hatch, 174 + i * 4, 88, 178 + i * 4, 107); }
            g.Restore(save);
        }
    }

    // The activity footer has one line at every width. Label.AutoEllipsis can still wrap;
    // custom painting keeps the full Text available to accessibility without clipped rows.
    sealed class SingleLineLabel : Label
    {
        public SingleLineLabel()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.AllPaintingInWmPaint, true);
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var bounds = new Rectangle(Padding.Left, Padding.Top,
                Math.Max(0, ClientSize.Width - Padding.Horizontal), Math.Max(0, ClientSize.Height - Padding.Vertical));
            TextRenderer.DrawText(e.Graphics, Text, Font, bounds, ForeColor,
                TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis | TextFormatFlags.VerticalCenter |
                TextFormatFlags.NoPrefix | TextFormatFlags.NoPadding | TextFormatFlags.PreserveGraphicsClipping);
        }
    }

    // Kept as native Button controls for keyboard focus, UI Automation and accessibility.
    internal sealed class RoundedButton : Button
    {
        public bool Selected, Accent;
        bool hovered;
        public RoundedButton()
        {
            FlatStyle = FlatStyle.Flat; FlatAppearance.BorderSize = 0; Cursor = Cursors.Hand;
            BackColor = Paper; ForeColor = Ink; Font = new Font("Consolas", 9, FontStyle.Bold);
            SetStyle(ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.AllPaintingInWmPaint, true);
        }
        protected override void OnMouseEnter(EventArgs e) { hovered = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hovered = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics; float s = DeviceDpi / 96f; g.SmoothingMode = SmoothingMode.AntiAlias;
            var r = new RectangleF(1, 1, Width - 6 * s, Height - 6 * s);
            using var ink = new SolidBrush(Ink);
            g.FillRectangle(ink, r.X + 4 * s, r.Y + 4 * s, r.Width, r.Height);
            using var fill = new SolidBrush(!Enabled ? Color.FromArgb(212, 209, 198) : Selected ? Acid : Accent ? Hot : hovered ? Color.White : Paper);
            g.FillRectangle(fill, r);
            using var outline = new Pen(Ink, 2 * s); g.DrawRectangle(outline, r.X, r.Y, r.Width, r.Height);
            var textBounds = Rectangle.Inflate(Rectangle.Round(r), -(int)(10 * s), -(int)(4 * s));
            const TextFormatFlags flags = TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding | TextFormatFlags.SingleLine;
            for (float size = Font.Size; size >= 7; size -= .25f)
            {
                using var candidate = new Font(Font.FontFamily, size, Font.Style);
                var measured = TextRenderer.MeasureText(g, Text, candidate, Size.Empty, flags);
                if ((measured.Width <= textBounds.Width && measured.Height <= textBounds.Height) || size <= 7)
                { TextRenderer.DrawText(g, Text, candidate, textBounds, Enabled ? Ink : Muted, flags); break; }
            }
            if (Focused && ShowFocusCues) ControlPaint.DrawFocusRectangle(g, Rectangle.Inflate(Rectangle.Round(r), -(int)(6 * s), -(int)(6 * s)), Ink, Paper);
        }
    }

    internal static class Logo
    {
        [System.Runtime.InteropServices.DllImport("user32.dll")] static extern bool DestroyIcon(IntPtr handle);
        public static void Draw(Graphics g, RectangleF r)
        {
            var save = g.Save(); g.TranslateTransform(r.X, r.Y); g.ScaleTransform(r.Width / 64, r.Height / 64);
            using var ink = new SolidBrush(Ink); using var acid = new SolidBrush(Acid);
            PointF[] shape = [new(3, 51), new(13, 10), new(27, 26), new(36, 8), new(47, 24), new(58, 11), new(59, 52), new(46, 52), new(46, 34), new(36, 48), new(25, 35), new(17, 54)];
            g.FillPolygon(ink, shape);
            g.FillPolygon(acid, new PointF[] { new(23, 23), new(34, 15), new(29, 31), new(40, 28), new(29, 47), new(31, 34), new(20, 37) });
            g.Restore(save);
        }
        public static Icon CreateIcon()
        {
            using var bitmap = new Bitmap(64, 64);
            using (var graphics = Graphics.FromImage(bitmap))
            {
                graphics.SmoothingMode = SmoothingMode.AntiAlias;
                graphics.Clear(Hot); Draw(graphics, new RectangleF(2, 2, 60, 60));
            }
            var handle = bitmap.GetHicon();
            try { using var borrowed = Icon.FromHandle(handle); return (Icon)borrowed.Clone(); }
            finally { DestroyIcon(handle); }
        }
    }
}
