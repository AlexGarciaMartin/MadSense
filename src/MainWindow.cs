using System.Diagnostics;
using System.Drawing.Drawing2D;
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
    const int WM_DEVICECHANGE = 0x0219, DBT_DEVNODES_CHANGED = 0x0007;
    const int LowBatteryPercent = 15;
    readonly Channel<Action<Bridge>> commands = Channel.CreateUnbounded<Action<Bridge>>(new() { SingleReader = true });
    readonly SemaphoreSlim wake = new(0, 1);
    readonly CancellationTokenSource lifetime = new();
    readonly EventWaitHandle showSignal = new(false, EventResetMode.AutoReset, "Local\\MadSense-Show");
    readonly EventWaitHandle stopSignal = new(false, EventResetMode.AutoReset, "Local\\MadSense-Stop");
    readonly System.Windows.Forms.Timer animation = new() { Interval = 250 };
    readonly NotifyIcon tray;
    readonly ToolStripMenuItem autostart;
    readonly HeroPanel hero = new() { Dock = DockStyle.Fill };
    readonly RoundedButton pause = new() { Text = "Pausar", Accent = true, Width = 144, Height = 40 };
    readonly Label modeHint = new() { Dock = DockStyle.Fill, ForeColor = Muted, TextAlign = ContentAlignment.MiddleLeft, AutoEllipsis = true };
    readonly Label activity = new() { Dock = DockStyle.Fill, ForeColor = Muted, TextAlign = ContentAlignment.MiddleLeft, AutoEllipsis = true };
    readonly Dictionary<string, RoundedButton> modeButtons = new();
    readonly Dictionary<string, RoundedButton> speakerButtons = new();
    readonly Dictionary<string, ToolStripMenuItem> speakerItems = new();
    readonly Task worker;
    bool paused, quitting, hasHiddenTip, allowVisible, lowBatteryNotified;
    float phase;
    string lastMessage = "Arrancando…";

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
        if (m.Msg == WM_DEVICECHANGE && (int)m.WParam == DBT_DEVNODES_CHANGED && !quitting)
        {
            commands.Writer.TryWrite(b => b.RequestRetry());
            Nudge();
        }
        base.WndProc(ref m);
    }

    public MainWindow(bool startHidden = false)
    {
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
        var header = new HeaderPanel { Dock = DockStyle.Fill, Margin = Padding.Empty };
        layout.Controls.Add(header, 0, 0);
        layout.Controls.Add(hero, 0, 1);
        var section = new Label { Text = "MODO  /  CÁMBIALO ANTES DE ABRIR EL JUEGO", Dock = DockStyle.Fill, TextAlign = ContentAlignment.BottomLeft, ForeColor = Ink, Font = new Font("Consolas", 8, FontStyle.Bold), Padding = new Padding(0, 0, 0, 5) };
        layout.Controls.Add(section, 0, 2);
        var modes = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 2, Margin = Padding.Empty };
        for (int i = 0; i < 2; i++) modes.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
        modes.RowStyles.Add(new RowStyle(SizeType.Absolute, 54)); modes.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        AddMode("Auto", "01 / DUALSENSE · RECOMENDADO", 0); AddMode("Xbox", "02 / MANDO XBOX (XINPUT)", 1);
        modes.Controls.Add(modeHint, 0, 1); modes.SetColumnSpan(modeHint, 2);
        layout.Controls.Add(modes, 0, 3);
        var speakerSection = new Label { Text = "ALTAVOZ DEL MANDO  /  SE APLICA AL INSTANTE, TAMBIÉN DESDE LA BANDEJA", Dock = DockStyle.Fill, TextAlign = ContentAlignment.BottomLeft, ForeColor = Ink, Font = new Font("Consolas", 8, FontStyle.Bold), Padding = new Padding(0, 0, 0, 5) };
        layout.Controls.Add(speakerSection, 0, 4);
        var speaker = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1, Margin = Padding.Empty };
        for (int i = 0; i < 3; i++) speaker.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f / 3));
        AddSpeaker("Alto", "ALTO", 0); AddSpeaker("Bajo", "BAJO", 1); AddSpeaker("Off", "APAGADO", 2);
        layout.Controls.Add(speaker, 0, 5);
        var footer = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1, Margin = Padding.Empty, Padding = new Padding(0, 6, 0, 0) };
        footer.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); footer.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 105)); footer.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 144));
        activity.Font = new Font(Font.FontFamily, 8);
        footer.Controls.Add(activity, 0, 0);
        var details = new RoundedButton { Text = "Registros", Dock = DockStyle.Fill, Margin = new Padding(0, 0, 10, 0), AccessibleDescription = "Abrir la carpeta local de registros de MadSense" };
        details.Click += (_, _) => { try { Process.Start(new ProcessStartInfo("explorer.exe", AppContext.BaseDirectory) { UseShellExecute = true }); } catch (Exception e) { activity.Text = e.Message; } };
        footer.Controls.Add(details, 1, 0);
        pause.Dock = DockStyle.Fill; pause.Margin = Padding.Empty;
        pause.Click += (_, _) => TogglePause();
        footer.Controls.Add(pause, 2, 0);
        layout.Controls.Add(footer, 0, 6);
        Controls.Add(layout);

        var menu = new ContextMenuStrip();
        menu.Items.Add("Abrir MadSense", null, (_, _) => RestoreWindow());
        menu.Items.Add("Pausar / reanudar", null, (_, _) => TogglePause());
        var speakerMenu = new ToolStripMenuItem("Altavoz del mando");
        foreach (var level in SpeakerLevels.All)
        {
            var item = new ToolStripMenuItem(level == "Off" ? "Apagado" : level);
            item.Click += (_, _) => Send(b => b.SetSpeaker(level));
            speakerItems[level] = item; speakerMenu.DropDownItems.Add(item);
        }
        menu.Items.Add(speakerMenu);
        autostart = new ToolStripMenuItem("Iniciar con Windows, en la bandeja") { CheckOnClick = true, Checked = Preferences.StartsWithWindows };
        autostart.CheckedChanged += (_, _) =>
        {
            try { Preferences.StartsWithWindows = autostart.Checked; }
            catch (Exception e) { activity.Text = "No se pudo cambiar el inicio con Windows: " + e.Message; autostart.Checked = Preferences.StartsWithWindows; }
        };
        menu.Items.Add(autostart);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Salir y liberar el mando", null, async (_, _) => await QuitAsync());
        tray = new NotifyIcon { Icon = Icon, Text = "MadSense · Esperando al mando", Visible = true, ContextMenuStrip = menu };
        tray.DoubleClick += (_, _) => RestoreWindow();
        animation.Tick += async (_, _) =>
        {
            if (stopSignal.WaitOne(0)) { await QuitAsync(); return; }
            if (showSignal.WaitOne(0)) RestoreWindow();
            // No drawing or high-frequency timer wakeups when the window is hidden.
            if (Visible && WindowState != FormWindowState.Minimized) { phase += .2f; hero.Phase = phase; hero.Invalidate(); }
        };
        VisibleChanged += (_, _) => AdjustAnimation();
        Resize += (_, _) => { if (WindowState == FormWindowState.Minimized) Hide(); AdjustAnimation(); };
        FormClosing += (_, e) =>
        {
            if (quitting) return;
            if (e.CloseReason is CloseReason.WindowsShutDown or CloseReason.TaskManagerClosing)
            {
                quitting = true; animation.Stop(); lifetime.Cancel(); tray.Visible = false;
                try { worker?.Wait(TimeSpan.FromSeconds(3)); } catch { }
                return;
            }
            e.Cancel = true; Hide();
            if (!hasHiddenTip) { tray.ShowBalloonTip(2500, "MadSense sigue en marcha", "Queda en la bandeja. Para cerrar del todo: clic derecho en el icono → Salir y liberar el mando.", ToolTipIcon.Info); hasHiddenTip = true; }
        };
        ResumeLayout(true);
        animation.Start();
        worker = Task.Run(BridgeLoop);

        void AddMode(string mode, string text, int column)
        {
            var button = new RoundedButton { Text = text, Dock = DockStyle.Fill, Margin = new Padding(column == 0 ? 0 : 6, 0, column == 1 ? 0 : 6, 0), Selected = mode == "Auto", AccessibleDescription = mode switch { "Auto" => "DualSense nativo con Steam Input configurado por juego para ampliar compatibilidad", _ => "Mando Xbox para juegos XInput; sin funciones exclusivas de DualSense" } };
            button.Click += (_, _) => Send(b => b.SetMode(mode));
            modeButtons[mode] = button; modes.Controls.Add(button, column, 0);
        }

        void AddSpeaker(string level, string text, int column)
        {
            var button = new RoundedButton { Text = text, Dock = DockStyle.Fill, Margin = new Padding(column == 0 ? 0 : 6, 0, column == 2 ? 0 : 6, 0), Selected = level == "Alto", AccessibleDescription = "Nivel del altavoz del mando: " + SpeakerLevels.Label(level) };
            button.Click += (_, _) => Send(b => b.SetSpeaker(level));
            speakerButtons[level] = button; speaker.Controls.Add(button, column, 0);
        }
    }

    void AdjustAnimation() => animation.Interval = Visible && WindowState != FormWindowState.Minimized ? 250 : 1000;
    void RestoreWindow() { if (quitting) return; allowVisible = true; Show(); WindowState = FormWindowState.Normal; Activate(); }
    void TogglePause()
    {
        if (quitting) return;
        paused = !paused; pause.Text = paused ? "Reanudar" : "Pausar";
        var shouldPause = paused;
        Send(b => { if (shouldPause) { b.Stop(); b.Log("En pausa · mando liberado."); } else b.Start(); });
    }

    /// <summary>Queues a bridge command and wakes the worker so it runs now, not on the next second.</summary>
    void Send(Action<Bridge> command)
    {
        if (quitting) return;
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
            Post(() => { activity.Text = "No se pudo iniciar el puente: " + e.Message; hero.Status = "No se pudo iniciar el puente. Consulta los registros."; hero.Connected = false; hero.Invalidate(); });
            try { File.AppendAllText(Path.Combine(AppContext.BaseDirectory, "ui-error.log"), $"{DateTimeOffset.Now:O} {e}\n"); } catch { }
        }
    }

    async Task RunBridge()
    {
        using var bridge = new Bridge();
        bridge.Message += message => { lastMessage = $"{DateTime.Now:HH:mm:ss} · {message}"; };
        bridge.WakeRequested += Nudge;
        try { bridge.Start(); } catch (Exception e) { lastMessage = e.Message; }
        try
        {
            while (!lifetime.IsCancellationRequested)
            {
                while (commands.Reader.TryRead(out var command))
                    try { command(bridge); } catch (Exception e) { lastMessage = e.Message; }
                try { bridge.Update(); } catch (Exception e) { lastMessage = e.Message; }
                var running = bridge.Ready;
                var status = bridge.DisplayStatus;
                var mode = bridge.Mode;
                var speakerLevel = bridge.Speaker;
                var message = lastMessage;
                var signals = bridge.Signals;
                var notifyLowBattery = CheckLowBattery(signals);
                Post(() =>
                {
                    hero.Connected = running;
                    hero.Paused = paused;
                    hero.Status = paused ? "Conexión en pausa" : status;
                    hero.Signals = running ? signals : null;
                    hero.Xbox = mode == "Xbox";
                    hero.Invalidate();
                    foreach (var pair in modeButtons) { pair.Value.Selected = pair.Key == mode || pair.Key == "Auto" && mode == "Native"; pair.Value.Invalidate(); }
                    // The speaker only exists for the DualSense persona; an Xbox pad has none.
                    foreach (var pair in speakerButtons) { pair.Value.Selected = pair.Key == speakerLevel; pair.Value.Enabled = mode != "Xbox"; pair.Value.Invalidate(); }
                    foreach (var pair in speakerItems) { pair.Value.Checked = pair.Key == speakerLevel; pair.Value.Enabled = mode != "Xbox"; }
                    modeHint.Text = mode == "Xbox"
                        ? "El juego ve un mando de Xbox: botones, sticks y vibración normal. Sin hápticos ni gatillos adaptativos.\nPara juegos fuera de Steam que solo aceptan Xbox."
                        : "El juego ve un DualSense por USB, con hápticos, gatillos adaptativos y altavoz si los soporta.\nSi el juego lo soporta en nativo, desactiva Steam Input para ese juego. MadSense no toca Steam.";
                    activity.Text = message.Split('\n')[0];
                    var battery = running && signals.Battery >= 0 ? $" · Batería {signals.Battery} %" : "";
                    tray.Text = Truncate(running ? "MadSense · Listo para jugar" + battery : paused ? "MadSense · En pausa" : "MadSense · Esperando al mando", 63);
                    if (notifyLowBattery) tray.ShowBalloonTip(6000, "Batería baja", $"Al DualSense le queda un {signals.Battery} %.", ToolTipIcon.Warning);
                });
                // Sleep until a command, a device event or the bridge's own schedule needs us.
                try { await wake.WaitAsync(Math.Min(1000, bridge.MillisUntilDue), lifetime.Token).ConfigureAwait(false); }
                catch (OperationCanceledException) { break; }
            }
        }
        catch (OperationCanceledException) { }
        catch (Exception e) { Post(() => { activity.Text = "Error del puente: " + e.Message; }); }
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
        quitting = true; pause.Enabled = false; activity.Text = "Liberando el mando…";
        animation.Stop(); commands.Writer.TryComplete(); lifetime.Cancel();
        try { await worker; } catch (Exception e) { Debug.WriteLine(e); }
        tray.Visible = false; tray.Dispose(); animation.Dispose();
        showSignal.Dispose(); stopSignal.Dispose(); lifetime.Dispose(); wake.Dispose();
        Close();
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
            using var title = new Font("Impact", 57, FontStyle.Regular, GraphicsUnit.Pixel);
            using var small = new Font("Consolas", 10.5f, FontStyle.Bold, GraphicsUnit.Pixel);
            using var ink = new SolidBrush(Ink);
            g.DrawString("MADSENSE", title, ink, 78, 0);
            g.DrawString("DUALSENSE POR BLUETOOTH: HÁPTICOS, GATILLOS Y ALTAVOZ", small, ink, 82, 65);
            using var line = new Pen(Ink, 3);
            g.DrawLine(line, 0, 94, w, 94);
            if (w > 650)
            {
                float x = w - 146;
                g.FillRectangle(ink, x, 12, 146, 63);
                using var paper = new SolidBrush(Paper);
                using var tag = new Font("Consolas", 13, FontStyle.Bold, GraphicsUnit.Pixel);
                g.DrawString("BLUETOOTH / PC", small, paper, x + 11, 20);
                using var acid = new SolidBrush(Acid); g.FillRectangle(acid, x + 10, 42, 126, 25);
                g.DrawString("VERSIÓN 0.4", tag, ink, x + 16, 45);
                for (int i = 0; i < 12; i++)
                    g.FillRectangle(ink, w - 145 + i * 12, 84, i % 3 == 0 ? 6 : 2, 6);
            }
        }
    }

    sealed class HeroPanel : Panel
    {
        public bool Connected, Paused, Xbox;
        public string Status = "Pulsa PS en el mando. Tiene que estar emparejado por Bluetooth en Windows.";
        public float Phase;
        /// <summary>Live feedback lamps; <c>null</c> hides the row (not connected).</summary>
        public BridgeSignals? Signals;
        public HeroPanel() { DoubleBuffered = true; Margin = Padding.Empty; AccessibleName = "Estado del mando"; }
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
            using var headline = new Font("Impact", 55, FontStyle.Regular, GraphicsUnit.Pixel);
            using var second = new Font("Impact", 35, FontStyle.Regular, GraphicsUnit.Pixel);
            g.DrawString(Xbox ? "MODO XBOX" : "MODO DUALSENSE", tiny, paper, 23, 20);
            string top = Paused ? "EN PAUSA." : Connected ? "LISTO." : "PULSA PS";
            string bottom = Paused ? "MANDO LIBRE." : Connected ? "ABRE EL JUEGO." : "EN EL MANDO.";
            g.DrawString(top, headline, paper, 20, 45);
            g.DrawString(bottom, second, paper, 23, 106);
            float labelY = Math.Max(164, baseY - 45);
            var badge = new RectangleF(25, labelY, 212, 28);
            g.FillPolygon(Connected ? acid : paper, new PointF[] { new(badge.X, badge.Y), new(badge.Right + 6, badge.Y - 3), new(badge.Right, badge.Bottom), new(badge.X - 3, badge.Bottom + 2) });
            string state = Paused ? "// EN PAUSA" : Connected ? "// LISTO PARA JUGAR" : "// ESPERANDO AL MANDO";
            g.DrawString(state, tiny, ink, badge.X + 12, badge.Y + 7);
            if (!Paused && (int)(Phase * 2) % 2 == 0)
                g.FillRectangle(ink, badge.Right - 18, badge.Y + 10, 6, 6);
            g.Restore(save);
            // Functional status is printed on a calm strip, separated from the artwork.
            g.FillRectangle(paper, 2, baseY, w - 4, h - baseY - 2);
            using var outline = new Pen(Ink, 3); g.DrawRectangle(outline, 1.5f, 1.5f, w - 3, h - 3);
            using var normal = new Font("Segoe UI", 13, FontStyle.Regular, GraphicsUnit.Pixel);
            using var index = new Font("Consolas", 11, FontStyle.Bold, GraphicsUnit.Pixel);
            g.DrawString("LINK", index, ink, 17, baseY + 23);
            g.DrawLine(outline, 64, baseY + 13, 64, h - 15);
            // Live lamps on the right of the strip: what the game is really driving on the pad.
            float lampsWidth = Signals is not null && w > 640 ? DrawLamps(g, w - 18, baseY + 12, h - baseY - 24, tiny, ink, paper, acid) : 0;
            g.DrawString(Status, normal, ink, new RectangleF(79, baseY + 12, w - 96 - lampsWidth, 48));
            AccessibleDescription = Status + (Signals is { } sig ? $" Hápticos {(sig.Haptics ? "activos" : "en reposo")}, gatillos {(sig.Triggers ? "con efecto" : "libres")}, audio {(sig.Audio ? "en curso" : "en reposo")}." : "");
        }

        /// <summary>Draws the lamp row right-aligned at <paramref name="right"/> and returns its width.</summary>
        float DrawLamps(Graphics g, float right, float top, float height, Font font, Brush ink, Brush paper, Brush acid)
        {
            var sig = Signals!;
            // Xbox mode has no native haptics or trigger lane: show what that persona can carry.
            (string Label, bool On)[] lamps = Xbox
                ? [("VIBRACIÓN", sig.Rumble)]
                : [("HÁPTICOS", sig.Haptics || sig.Rumble), ("GATILLOS", sig.Triggers), ("AUDIO", sig.Audio)];
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
            TextRenderer.DrawText(g, Text, Font, Rectangle.Round(r), Enabled ? Ink : Muted, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis);
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
