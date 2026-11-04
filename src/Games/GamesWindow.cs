using System.Diagnostics;
using System.Drawing.Drawing2D;

namespace MadSense.Games;

/// <summary>
/// The games screen: every installed game, what it does with the DualSense on its own, and the
/// community mod MadSense can install with one click when it does not. Built from the main
/// window's own pieces: the ink band with the orange plate, mono kickers, Impact headlines, slanted
/// badges, lamp rows and the hard-shadow buttons. Reads only, until the user presses Install.
/// </summary>
internal sealed class GamesWindow : Form
{
    static readonly Color Ink = Color.FromArgb(24, 24, 21), Paper = Color.FromArgb(240, 235, 220);
    static readonly Color Acid = Color.FromArgb(220, 241, 49), Hot = Color.FromArgb(249, 84, 40), Muted = Color.FromArgb(99, 96, 85);
    readonly GamesService service;
    readonly CancellationTokenSource lifetime = new();
    readonly Dictionary<(string, float, FontStyle), Font> fonts = [];
    readonly BandPanel band = new() { Dock = DockStyle.Fill, Margin = Padding.Empty };
    readonly MainWindow.RoundedButton refresh = new(), install = new() { Accent = true }, remove = new(), run = new(), page = new(), wiki = new();
    readonly Label footer = new(), count = new(), empty = new(), section = new();
    readonly TextBox search = new();
    readonly EntryList entries = new();
    readonly PlatePanel listPlate = new(), detailPlate = new();
    readonly Panel detailScroll = new() { Dock = DockStyle.Fill, AutoScroll = true };
    readonly FlowLayoutPanel detail = new() { Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.TopDown, WrapContents = false };
    readonly ProgressBar progress = new() { Dock = DockStyle.Fill, Visible = false, Style = ProgressBarStyle.Continuous };
    readonly TableLayoutPanel actions = new() { Dock = DockStyle.Fill, ColumnCount = 5, RowCount = 1, Margin = Padding.Empty, Padding = new(0, 6, 0, 0) };
    readonly Dictionary<string, WikiSupport?> support = new(StringComparer.Ordinal);
    readonly HashSet<string> pending = new(StringComparer.Ordinal);
    List<LibraryGame> games = [];
    CancellationTokenSource? lookups;
    bool scanning, installing, disposedResources, rebuilding;
    string? selectedKey;
    string listSignature = "", shownSignature = "";
    ModEntry? selectedMod;
    Uri? pageUri, wikiUri;

    sealed class Entry(LibraryGame game, GameStanding standing, bool pending)
    {
        public LibraryGame Game { get; } = game;
        public GameStanding Standing { get; set; } = standing;
        public bool Pending { get; set; } = pending;
        public override string ToString() => Game.Name;
    }

    public GamesWindow(GamesService service)
    {
        this.service = service;
        SuspendLayout();
        AutoScaleMode = AutoScaleMode.Dpi;
        AutoScaleDimensions = new SizeF(96, 96);
        ClientSize = new(1100, 760); MinimumSize = new(920, 680);
        StartPosition = FormStartPosition.CenterParent;
        BackColor = Paper; ForeColor = Ink; Font = MakeFont("Segoe UI", 10);
        Icon = MainWindow.Logo.CreateIcon(); Padding = new(24, 12, 24, 16);
        var root = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 6, ColumnCount = 1, Margin = Padding.Empty };
        root.RowStyles.Add(new(SizeType.Absolute, 176));
        root.RowStyles.Add(new(SizeType.Absolute, 30));
        root.RowStyles.Add(new(SizeType.Absolute, 48));
        root.RowStyles.Add(new(SizeType.Percent, 100));
        root.RowStyles.Add(new(SizeType.Absolute, 54));
        root.RowStyles.Add(new(SizeType.Absolute, 30));
        root.Controls.Add(band, 0, 0);
        // Section line, as in the main window: mono kicker with a slash.
        section.Dock = DockStyle.Fill; section.TextAlign = ContentAlignment.BottomLeft; section.ForeColor = Ink; section.Font = MakeFont("Consolas", 8, FontStyle.Bold); section.Padding = new(0, 0, 0, 5);
        root.Controls.Add(section, 0, 1);
        // Toolbar: search field, count, refresh. Same 48 px row and button geometry as the main footer.
        var toolbar = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1, Margin = Padding.Empty };
        toolbar.RowStyles.Add(new(SizeType.Percent, 100));
        toolbar.ColumnStyles.Add(new(SizeType.Percent, 100)); toolbar.ColumnStyles.Add(new(SizeType.Absolute, 150)); toolbar.ColumnStyles.Add(new(SizeType.Absolute, 144));
        var searchPlate = new PlatePanel { Dock = DockStyle.Fill, Margin = new(0, 0, 12, 6), Padding = new(10, 0, 10, 0) };
        search.Anchor = AnchorStyles.Left | AnchorStyles.Right; search.BackColor = Paper; search.ForeColor = Ink; search.BorderStyle = BorderStyle.None; search.Font = MakeFont("Consolas", 11, FontStyle.Bold);
        search.TextChanged += (_, _) => Populate();
        searchPlate.Controls.Add(search);
        searchPlate.Layout += (_, _) => { var area = searchPlate.DisplayRectangle; search.SetBounds(area.X, area.Y + Math.Max(0, (area.Height - search.Height) / 2), area.Width, search.Height); };
        count.Dock = DockStyle.Fill; count.TextAlign = ContentAlignment.MiddleRight; count.Font = MakeFont("Consolas", 9, FontStyle.Bold); count.Margin = new(0, 0, 12, 6);
        refresh.Dock = DockStyle.Fill; refresh.Margin = Padding.Empty; refresh.Click += async (_, _) => await ScanAsync();
        toolbar.Controls.Add(searchPlate, 0, 0); toolbar.Controls.Add(count, 1, 0); toolbar.Controls.Add(refresh, 2, 0);
        root.Controls.Add(toolbar, 0, 2);
        // Plates: list on the left, detail on the right, both with the hard shadow of the buttons.
        var content = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, Margin = Padding.Empty };
        content.RowStyles.Add(new(SizeType.Percent, 100));
        content.ColumnStyles.Add(new(SizeType.Percent, 42)); content.ColumnStyles.Add(new(SizeType.Percent, 58));
        listPlate.Dock = DockStyle.Fill; listPlate.Margin = new(0, 6, 18, 4);
        entries.Dock = DockStyle.Fill; entries.BackColor = Paper; entries.ForeColor = Ink; entries.BorderStyle = BorderStyle.None;
        entries.SelectedIndexChanged += (_, _) => Selected();
        entries.ItemHeight = (int)Math.Round(78 * DeviceDpi / 96f);
        empty.Dock = DockStyle.Fill; empty.BackColor = Paper; empty.ForeColor = Muted; empty.TextAlign = ContentAlignment.MiddleCenter; empty.Padding = new(24);
        listPlate.Controls.Add(entries); listPlate.Controls.Add(empty);
        detailPlate.Dock = DockStyle.Fill; detailPlate.Margin = new(0, 6, 0, 4); detailPlate.Padding = new(18, 14, 18, 10);
        detailScroll.Controls.Add(detail); detailScroll.SizeChanged += (_, _) => FitDetail();
        detailPlate.Controls.Add(detailScroll);
        content.Controls.Add(listPlate, 0, 0); content.Controls.Add(detailPlate, 1, 0); root.Controls.Add(content, 0, 3);
        actions.RowStyles.Add(new(SizeType.Percent, 100));
        for (int i = 0; i < 5; i++) actions.ColumnStyles.Add(new(SizeType.Percent, 20));
        foreach (var (button, column) in new[] { (install, 0), (run, 1), (remove, 2), (page, 3), (wiki, 4) })
        {
            button.Dock = DockStyle.Fill; button.Margin = new(column == 0 ? 0 : 6, 0, column == 4 ? 0 : 6, 0); button.Visible = false; actions.Controls.Add(button, column, 0);
        }
        install.Click += async (_, _) => await InstallAsync();
        remove.Click += (_, _) => Remove();
        run.Click += (_, _) => RunHelper();
        page.Click += (_, _) => { if (pageUri is { } uri) OpenUrl(uri); };
        wiki.Click += (_, _) => { if (wikiUri is { } uri) OpenUrl(uri); };
        root.Controls.Add(actions, 0, 4);
        var bottom = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, Margin = Padding.Empty };
        bottom.RowStyles.Add(new(SizeType.Percent, 100));
        bottom.ColumnStyles.Add(new(SizeType.Percent, 100)); bottom.ColumnStyles.Add(new(SizeType.Absolute, 220));
        footer.Dock = DockStyle.Fill; footer.TextAlign = ContentAlignment.MiddleLeft; footer.ForeColor = Muted; footer.Font = MakeFont("Segoe UI", 8); footer.AutoEllipsis = true;
        progress.Margin = new(12, 10, 0, 10);
        bottom.Controls.Add(footer, 0, 0); bottom.Controls.Add(progress, 1, 0);
        root.Controls.Add(bottom, 0, 5);
        Controls.Add(root);
        ApplyText();
        service.LibraryChanged += LibraryChanged;
        service.Runner.HelperStarted += (_, _) => Post(Populate);
        service.Runner.HelperStopped += (_, _) => Post(Populate);
        Shown += async (_, _) => { ActiveControl = entries; await ScanAsync(); };
        L.Changed += LanguageChanged;
        FormClosed += (_, _) => { if (!disposedResources) { lifetime.Cancel(); lookups?.Cancel(); } };
        ResumeLayout(true);
    }

    Font MakeFont(string family, float size, FontStyle style = FontStyle.Regular)
    {
        var key = (family, size, style);
        if (!fonts.TryGetValue(key, out var font)) { font = new Font(family, size, style); fonts.Add(key, font); }
        return font;
    }

    void Post(Action action) { if (IsDisposed || Disposing || disposedResources) return; if (InvokeRequired) BeginInvoke(action); else action(); }
    void LanguageChanged() => Post(ApplyText);
    void LibraryChanged() => Post(() => { games = service.Library.ToList(); Populate(); _ = LookupAllAsync(); });

    void ApplyText()
    {
        Text = "MadSense · " + L.Text("Games.Title");
        band.Kicker = L.Text("Games.Kicker"); band.Headline = L.Text("Games.Heading"); band.Tagline = L.Text("Games.Tagline");
        section.Text = L.Text("Games.Section");
        refresh.Text = L.Text("Games.Refresh");
        search.PlaceholderText = L.Text("Games.Search"); search.AccessibleName = L.Text("Games.Search"); entries.AccessibleName = L.Text("Games.Title");
        install.Text = L.Text("Games.Install"); remove.Text = L.Text("Games.Remove"); run.Text = L.Text("Games.RunNow");
        page.Text = L.Text("Games.ModPage"); wiki.Text = L.Text("Games.WikiPage");
        selectedKey = null;
        Populate();
    }

    async Task ScanAsync()
    {
        if (scanning || disposedResources || lifetime.IsCancellationRequested) return;
        scanning = true; refresh.Enabled = false; footer.Text = L.Text("Games.Scanning");
        try { games = (await service.ScanAsync(lifetime.Token)).ToList(); }
        catch (OperationCanceledException) { }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Security.SecurityException) { footer.Text = e.Message; }
        finally { scanning = false; if (!IsDisposed) { refresh.Enabled = true; Populate(); _ = LookupAllAsync(); } }
    }

    /// <summary>Resolves each game's wiki record in the background, one at a time, cached answers first.</summary>
    async Task LookupAllAsync()
    {
        lookups?.Cancel(); lookups?.Dispose();
        lookups = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
        var token = lookups.Token;
        foreach (var game in games)
        {
            if (service.Wiki.Cached(game) is { } cached && DateTimeOffset.UtcNow - cached.Fetched < TimeSpan.FromDays(30)) { support[game.Key] = cached; continue; }
            pending.Add(game.Key);
        }
        Populate();
        foreach (var game in games.Where(g => pending.Contains(g.Key)).ToList())
        {
            if (token.IsCancellationRequested) return;
            WikiSupport? info = null;
            try { info = await service.Wiki.GetAsync(game, token); }
            catch (OperationCanceledException) { return; }
            if (token.IsCancellationRequested) return;
            support[game.Key] = info; pending.Remove(game.Key);
            Populate();
            if (service.Wiki.Blocked) { footer.Text = L.Text("Games.WikiBlocked"); return; }
        }
    }

    void Populate()
    {
        if (IsDisposed || Disposing || disposedResources) return;
        var previous = (entries.SelectedItem as Entry)?.Game.Key ?? selectedKey;
        var all = games.Select(g => new Entry(g, service.Standing(g, support.GetValueOrDefault(g.Key)), pending.Contains(g.Key))).ToList();
        var filter = search.Text.Trim();
        var rows = all.Where(r => r.Game.Name.Contains(filter, StringComparison.CurrentCultureIgnoreCase))
            .OrderBy(r => r.Standing.Status switch { GameStatus.ModInstalled => 0, GameStatus.ModAvailable => 1, GameStatus.Native => 2, GameStatus.ModManual => 3, _ => 4 })
            .ThenBy(r => r.Game.Name, StringComparer.CurrentCultureIgnoreCase).ToList();
        count.Text = L.Text("Games.GameCount", games.Count);
        band.Badge = L.Text("Games.Badge", all.Count(r => r.Standing.Status == GameStatus.Native), all.Count(r => r.Standing.Mods.Count > 0));
        band.Invalidate();
        empty.Text = L.Text(games.Count == 0 ? "Games.NoGames" : "Games.NoMatches"); empty.Visible = rows.Count == 0;
        footer.Text = scanning ? L.Text("Games.Scanning") : pending.Count > 0 ? L.Text("Games.Consulting", pending.Count) : L.Text("Games.Footer", ModCatalog.Version);
        // The list is rebuilt only when a row appears, leaves or changes state. Otherwise the rows
        // behind it are refreshed in place and repainted, so the reader keeps their place while the
        // wiki answers trickle in.
        var signature = string.Join("\n", rows.Select(r => r.Game.Key + "|" + r.Standing.Status + "|" + r.Pending)) + "\n" + L.CurrentLanguage;
        rebuilding = true;
        if (signature != listSignature)
        {
            listSignature = signature;
            int top = entries.TopIndex;
            entries.BeginUpdate();
            entries.Items.Clear();
            foreach (var row in rows) entries.Items.Add(row);
            var index = rows.FindIndex(r => r.Game.Key == previous);
            if (rows.Count > 0) entries.SelectedIndex = index < 0 ? 0 : index;
            if (top > 0 && top < rows.Count) entries.TopIndex = top;
            entries.EndUpdate();
        }
        else
        {
            for (int i = 0; i < rows.Count; i++) if (entries.Items[i] is Entry item) { item.Standing = rows[i].Standing; item.Pending = rows[i].Pending; }
            entries.Invalidate();
        }
        rebuilding = false;
        if (rows.Count > 0) Selected(); else { selectedKey = null; shownSignature = ""; ResetDetail(); Kicker(L.Text("Games.EmptyTitle")); Body(L.Text("Games.EmptyDescription")); }
    }

    /// <summary>
    /// Shows the selected game, but only when what there is to show has changed: the same game with
    /// the same data is left alone, and the same game with new data keeps its scroll position.
    /// </summary>
    void Selected()
    {
        if (rebuilding || entries.SelectedItem is not Entry entry) return;
        var standing = entry.Standing;
        var signature = string.Join("|", entry.Game.Key, standing.Status, entry.Pending, standing.Wiki?.Fetched.Ticks, installing, L.CurrentLanguage,
            string.Join(",", standing.Installed.Select(m => m.Id + ":" + service.Installer.Find(m.Id, entry.Game.Key)?.Version)),
            string.Join(",", standing.Mods.Select(m => service.Runner.IsRunning(m.Id, entry.Game.Key))));
        if (signature == shownSignature) return;
        bool sameGame = entry.Game.Key == selectedKey;
        var scroll = detailScroll.AutoScrollPosition;
        selectedKey = entry.Game.Key; shownSignature = signature;
        ShowGame(entry);
        if (sameGame) detailScroll.AutoScrollPosition = new Point(-scroll.X, -scroll.Y);
    }

    // ── detail pane pieces ────────────────────────────────────────────────────────────────────
    void ResetDetail()
    {
        detail.SuspendLayout();
        foreach (var control in detail.Controls.Cast<Control>().ToArray()) control.Dispose();
        detail.Controls.Clear(); detailScroll.AutoScrollPosition = Point.Empty;
        detail.ResumeLayout();
        pageUri = wikiUri = null; selectedMod = null;
        foreach (var button in new[] { install, run, remove, page, wiki }) button.Visible = false;
    }

    Control Add(Control control) { detail.Controls.Add(control); FitDetail(); return control; }

    /// <summary>Mono uppercase line, the kicker of the main window.</summary>
    Label Kicker(string text, Color? color = null) => (Label)Add(new Label { AutoSize = true, Text = text.ToUpper(L.Culture), ForeColor = color ?? Ink, Font = MakeFont("Consolas", 8.5f, FontStyle.Bold), Margin = new(0, 2, 0, 6), UseMnemonic = false });

    /// <summary>Body copy in the strip's own voice.</summary>
    Label Body(string text, bool muted = true) => (Label)Add(new Label { AutoSize = true, Text = text, ForeColor = muted ? Muted : Ink, Font = MakeFont("Segoe UI", 9.75f), Margin = new(0, 0, 0, 10), UseMnemonic = false });

    /// <summary>The game's name as the hero prints its headline: Impact, fitted to the pane.</summary>
    void Headline(string text) => Add(new FittedTitle { Text = text, Height = (int)(44 * DeviceDpi / 96f), Margin = new(0, 0, 0, 6) });

    /// <summary>The slanted badge of the hero, in acid, orange or paper.</summary>
    void Badge(string text, Color fill) => Add(new SlantBadge { Text = text, Fill = fill, Height = (int)(30 * DeviceDpi / 96f), Margin = new(0, 2, 0, 12) });

    /// <summary>The lamp rows of the LINK strip, one per DualSense feature.</summary>
    void Lamps(IReadOnlyList<(string Label, WikiValue Value)> items) => Add(new LampRows { Items = items, Height = (int)(items.Count * 22 * DeviceDpi / 96f), Margin = new(0, 2, 0, 10) });

    void FitDetail()
    {
        int width = Math.Max(120, detailScroll.ClientSize.Width - SystemInformation.VerticalScrollBarWidth - 4);
        detail.Width = width;
        foreach (Control control in detail.Controls)
        {
            if (control is Label label) label.MaximumSize = new(width, 0);
            else control.Width = width;
        }
    }

    static string StatusText(GameStanding standing, bool pending) => L.Text(standing.Status switch
    {
        GameStatus.Native when standing.Mods.Count > 0 => "Games.Status.NativeWithMod",
        GameStatus.Native => "Games.Status.Native",
        GameStatus.ModInstalled => "Games.Status.ModInstalled",
        GameStatus.ModAvailable => "Games.Status.ModAvailable",
        GameStatus.ModManual => "Games.Status.ModManual",
        GameStatus.PlayStationOnly => "Games.Status.PlayStationOnly",
        GameStatus.None => "Games.Status.None",
        _ => pending ? "Games.Status.Pending" : "Games.Status.Unknown",
    });

    static Color StatusColor(GameStatus status) => status switch
    {
        GameStatus.Native or GameStatus.ModInstalled => Acid,
        GameStatus.ModAvailable => Hot,
        _ => Paper,
    };

    void ShowGame(Entry entry)
    {
        ResetDetail();
        var game = entry.Game; var standing = entry.Standing; var info = standing.Wiki;
        Kicker(game.Source + (game.SteamAppId is { } id ? "  /  STEAM " + id : ""), Muted);
        Headline(game.Name);
        Badge(StatusText(standing, entry.Pending), StatusColor(standing.Status));
        if (info is not null)
        {
            Lamps([
                (L.Text("Games.Feature.haptics"), info.Haptics), (L.Text("Games.Feature.triggers"), info.Triggers), (L.Text("Games.Feature.speaker"), info.Speaker),
                (L.Text("Games.Feature.lightbar"), info.Lightbar), (L.Text("Games.Feature.gyro"), info.Motion), (L.Text("Games.Feature.prompts"), info.Prompts)]);
            if (info.ConnectionModes.Length > 0) Body(L.Text("Games.ConnectionModes", info.ConnectionModes));
            if (info.Note("dualsense adaptive trigger support") is { } note) Body(Trim(note));
            Body(L.Text("Games.WikiAttribution"));
            wikiUri = info.Source; wiki.Visible = true;
        }
        else Body(L.Text(entry.Pending ? "Games.LoadingWiki" : service.Wiki.Blocked ? "Games.WikiBlocked" : "Games.NoWikiData"));
        if (standing.Status == GameStatus.Native) Body(L.Text(standing.Mods.Count > 0 ? "Games.NativeModHint" : "Games.NativeHint"), muted: false);
        foreach (var mod in standing.Mods)
        {
            var installed = service.Installer.Find(mod.Id, game.Key);
            Badge(L.Text(installed is null ? "Games.ModHeader" : "Games.ModHeaderInstalled", mod.Name), installed is null && mod.Installable ? Hot : installed is null ? Paper : Acid);
            Kicker(L.Text("Games.ModBy", mod.Author, mod.License ?? L.Text("Games.NoLicense"), string.Join(", ", mod.Features.Select(f => L.Text("Games.Feature." + f)))), Muted);
            Body(mod.Note(L.CurrentLanguage), muted: false);
            if (installed is not null)
            {
                Body(L.Text("Games.InstalledDetail", installed.Version, installed.Installed.ToString("d", L.Culture), installed.Root));
                if (installed.Exe is not null) Body(L.Text(service.Runner.IsRunning(mod.Id, game.Key) ? "Games.HelperRunning" : "Games.HelperIdle"), muted: false);
            }
            else if (mod.Installable) Body(L.Text(mod.Kind switch { ModKind.Helper => "Games.InstallHelperHint", ModKind.GameFolder => "Games.InstallGameFolderHint", _ => "Games.InstallBeamNgHint" }, service.Installer.ModsRoot));
            else Body(L.Text("Games.ManualHint"));
            if (selectedMod is null) { selectedMod = mod; pageUri = mod.PageUri; }
        }
        if (selectedMod is { } selected)
        {
            var record = service.Installer.Find(selected.Id, game.Key);
            page.Visible = true;
            install.Visible = record is null && selected.Installable && !installing;
            remove.Visible = record is not null && !installing;
            run.Visible = record?.Exe is not null && !installing;
            run.Text = L.Text(service.Runner.IsRunning(selected.Id, game.Key) ? "Games.StopNow" : "Games.RunNow");
        }
        if (standing.Mods.Count == 0 && standing.Status != GameStatus.Native) Body(L.Text("Games.NoModKnown"));
    }

    static string Trim(string wikitext)
    {
        var text = System.Text.RegularExpressions.Regex.Replace(wikitext, @"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]|'''?|\{\{[^}]*\}\}|<[^>]+>", "$1").Trim();
        return text.Length > 360 ? text[..357] + "…" : text;
    }

    // ── actions ───────────────────────────────────────────────────────────────────────────────
    async Task InstallAsync()
    {
        if (installing || entries.SelectedItem is not Entry entry || selectedMod is not { } mod) return;
        installing = true; install.Visible = false; progress.Visible = true; progress.Value = 0;
        footer.Text = L.Text("Games.Downloading", mod.Name);
        var reporter = new Progress<InstallProgress>(p => { if (p.Total > 0 && p.Stage == "download") progress.Value = (int)Math.Clamp(p.Done * 100 / p.Total, 0, 100); if (p.Stage == "install") { progress.Style = ProgressBarStyle.Marquee; footer.Text = L.Text("Games.Installing", mod.Name); } });
        try
        {
            await service.Installer.InstallAsync(mod, entry.Game, reporter, lifetime.Token);
            footer.Text = L.Text("Games.Installed", mod.Name);
        }
        catch (OperationCanceledException) { }
        catch (Exception e) when (e is InvalidOperationException or IOException or UnauthorizedAccessException or System.Net.Http.HttpRequestException or FormatException)
        {
            footer.Text = L.Text("Games.InstallFailed", mod.Name, e.Message);
        }
        finally { installing = false; progress.Visible = false; progress.Style = ProgressBarStyle.Continuous; if (!IsDisposed) Populate(); }
    }

    void Remove()
    {
        if (installing || entries.SelectedItem is not Entry entry || selectedMod is not { } mod) return;
        service.Runner.Stop(mod.Id, entry.Game.Key);
        service.Installer.Uninstall(mod.Id, entry.Game.Key);
        footer.Text = L.Text("Games.Removed", mod.Name);
        Populate();
    }

    void RunHelper()
    {
        if (entries.SelectedItem is not Entry entry || selectedMod is not { } mod) return;
        if (service.Runner.IsRunning(mod.Id, entry.Game.Key)) service.Runner.Stop(mod.Id, entry.Game.Key);
        else if (!service.Runner.Start(mod.Id, entry.Game.Key)) footer.Text = L.Text("Games.HelperFailed", mod.Name);
        Populate();
    }

    void OpenUrl(Uri uri)
    {
        if (uri.Scheme != Uri.UriSchemeHttps) return;
        try { Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); }
        catch (Exception e) when (e is System.ComponentModel.Win32Exception or InvalidOperationException) { footer.Text = L.Text("Games.OpenFailed"); }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing && !disposedResources)
        {
            disposedResources = true;
            lifetime.Cancel(); lookups?.Cancel();
            service.LibraryChanged -= LibraryChanged; L.Changed -= LanguageChanged;
            base.Dispose(true);
            lookups?.Dispose(); lifetime.Dispose();
            foreach (var font in fonts.Values) font.Dispose();
            return;
        }
        base.Dispose(disposing);
    }

    // ── drawn pieces, the main window's visual language ───────────────────────────────────────
    /// <summary>The ink band of the hero: orange plate with the dot screen, mono kicker, Impact headline, slanted badge.</summary>
    sealed class BandPanel : Panel
    {
        public string Kicker = "", Headline = "", Tagline = "", Badge = "";
        public BandPanel() { DoubleBuffered = true; SetStyle(ControlStyles.ResizeRedraw, true); }
        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e); var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias;
            float s = DeviceDpi / 96f; g.ScaleTransform(s, s);
            float w = Width / s, h = Height / s;
            if (w < 20 || h < 20) return;
            using var ink = new SolidBrush(Ink); using var paper = new SolidBrush(Paper); using var orange = new SolidBrush(Hot); using var acid = new SolidBrush(Acid);
            g.FillRectangle(ink, 0, 0, w, h);
            var save = g.Save(); g.SetClip(new RectangleF(2, 2, w - 4, h - 4));
            g.FillPolygon(orange, new PointF[] { new(w * .66f, -5), new(w, -5), new(w, h + 2), new(w * .56f, h + 2) });
            using var dots = new SolidBrush(Color.FromArgb(75, Ink));
            for (float y = 9; y < h; y += 11)
                for (float x = w * .66f; x < w; x += 11)
                    g.FillEllipse(dots, x + ((int)y % 2 == 0 ? 3 : 0), y, 3, 3);
            // A stack of three cards on the plate: the library, drawn in the same ink and paper.
            for (int i = 2; i >= 0; i--)
            {
                var card = new RectangleF(w * .78f - i * 12, 26 + i * 14, 150, 62);
                g.FillRectangle(ink, card.X + 5, card.Y + 5, card.Width, card.Height);
                g.FillRectangle(paper, card);
                using var outline = new Pen(Ink, 2.5f); g.DrawRectangle(outline, card.X, card.Y, card.Width, card.Height);
                g.FillRectangle(i == 0 ? acid : ink, card.X + 12, card.Y + 14, 9, 9);
                g.FillRectangle(ink, card.X + 30, card.Y + 15, 70, 6);
                g.FillRectangle(ink, card.X + 12, card.Y + 36, 100, 5);
            }
            g.Restore(save);
            using var tiny = new Font("Consolas", 10.5f, FontStyle.Bold, GraphicsUnit.Pixel);
            float textWidth = w * .56f - 40;
            MainWindow.DrawFitted(g, Kicker, "Consolas", 10.5f, 9, FontStyle.Bold, paper, new RectangleF(23, 18, textWidth, 18));
            MainWindow.DrawFitted(g, Headline.ToUpper(L.Culture), "Impact", 54, 28, FontStyle.Regular, paper, new RectangleF(20, 36, textWidth, 60));
            MainWindow.DrawFitted(g, Tagline, "Impact", 22, 16, FontStyle.Regular, paper, new RectangleF(23, 98, textWidth, 28));
            float badgeWidth = Math.Min(textWidth - 12, Math.Max(180, g.MeasureString(Badge, tiny).Width + 40));
            var badge = new RectangleF(25, h - 40, badgeWidth, 26);
            g.FillPolygon(acid, new PointF[] { new(badge.X, badge.Y), new(badge.Right + 6, badge.Y - 3), new(badge.Right, badge.Bottom), new(badge.X - 3, badge.Bottom + 2) });
            MainWindow.DrawFitted(g, Badge, "Consolas", 10.5f, 9, FontStyle.Bold, ink, new RectangleF(badge.X + 12, badge.Y + 6, badge.Width - 24, 18));
            using var frame = new Pen(Ink, 3); g.DrawRectangle(frame, 1.5f, 1.5f, w - 3, h - 3);
        }
    }

    /// <summary>A paper plate with the ink outline and hard offset shadow the buttons use; children sit inside the padding.</summary>
    sealed class PlatePanel : Panel
    {
        public PlatePanel() { DoubleBuffered = true; BackColor = Paper; SetStyle(ControlStyles.ResizeRedraw, true); Padding = new(3); }
        protected override void OnPaintBackground(PaintEventArgs e)
        {
            var g = e.Graphics; float s = DeviceDpi / 96f; g.SmoothingMode = SmoothingMode.AntiAlias;
            using var paper = new SolidBrush(Paper); g.FillRectangle(paper, ClientRectangle);
            var r = new RectangleF(1, 1, Width - 6 * s, Height - 6 * s);
            using var ink = new SolidBrush(Ink); g.FillRectangle(ink, r.X + 4 * s, r.Y + 4 * s, r.Width, r.Height);
            g.FillRectangle(paper, r);
            using var outline = new Pen(Ink, 2 * s); g.DrawRectangle(outline, r.X, r.Y, r.Width, r.Height);
        }
        public override Rectangle DisplayRectangle
        {
            get
            {
                float s = DeviceDpi / 96f;
                return new Rectangle((int)(2 * s) + Padding.Left, (int)(2 * s) + Padding.Top, Width - (int)(8 * s) - Padding.Horizontal, Height - (int)(8 * s) - Padding.Vertical);
            }
        }
    }

    sealed class FittedTitle : Control
    {
        public FittedTitle() { DoubleBuffered = true; SetStyle(ControlStyles.ResizeRedraw | ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint, true); BackColor = Paper; }
        protected override void OnPaint(PaintEventArgs e)
        {
            float s = DeviceDpi / 96f; var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias; g.ScaleTransform(s, s);
            using var ink = new SolidBrush(Ink);
            MainWindow.DrawFitted(g, Text.ToUpper(L.Culture), "Impact", 36, 20, FontStyle.Regular, ink, new RectangleF(0, 0, Width / s, Height / s));
        }
    }

    sealed class SlantBadge : Control
    {
        public Color Fill = Acid;
        public SlantBadge() { DoubleBuffered = true; SetStyle(ControlStyles.ResizeRedraw | ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint, true); BackColor = Paper; }
        protected override void OnPaint(PaintEventArgs e)
        {
            float s = DeviceDpi / 96f; var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias; g.ScaleTransform(s, s);
            using var tiny = new Font("Consolas", 10.5f, FontStyle.Bold, GraphicsUnit.Pixel);
            float width = Math.Min(Width / s - 10, g.MeasureString(Text, tiny).Width + 36);
            var badge = new RectangleF(3, 3, width, Height / s - 8);
            using var fill = new SolidBrush(Fill); using var ink = new SolidBrush(Ink); using var paper = new SolidBrush(Paper);
            var shape = new PointF[] { new(badge.X, badge.Y), new(badge.Right + 6, badge.Y - 3), new(badge.Right, badge.Bottom), new(badge.X - 3, badge.Bottom + 2) };
            if (Fill == Paper) { using var outline = new Pen(Ink, 1.5f); g.FillPolygon(paper, shape); g.DrawPolygon(outline, shape); }
            else g.FillPolygon(fill, shape);
            MainWindow.DrawFitted(g, Text, "Consolas", 10.5f, 9, FontStyle.Bold, Fill == Hot ? paper : ink, new RectangleF(badge.X + 12, badge.Y + (badge.Height - 12) / 2, badge.Width - 20, 16));
        }
    }

    /// <summary>Lamp rows: a square lit in acid when the wiki says yes, outlined otherwise, with the mono label and value.</summary>
    sealed class LampRows : Control
    {
        public IReadOnlyList<(string Label, WikiValue Value)> Items = [];
        public LampRows() { DoubleBuffered = true; SetStyle(ControlStyles.ResizeRedraw | ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint, true); BackColor = Paper; }
        protected override void OnPaint(PaintEventArgs e)
        {
            float s = DeviceDpi / 96f; var g = e.Graphics; g.SmoothingMode = SmoothingMode.AntiAlias; g.ScaleTransform(s, s);
            using var tiny = new Font("Consolas", 10.5f, FontStyle.Bold, GraphicsUnit.Pixel);
            using var ink = new SolidBrush(Ink); using var muted = new SolidBrush(Muted); using var acid = new SolidBrush(Acid); using var hot = new SolidBrush(Hot);
            using var outline = new Pen(Ink, 1.5f);
            float y = 4;
            foreach (var (label, value) in Items)
            {
                var box = new RectangleF(2, y + 2, 9, 9);
                if (value == WikiValue.Yes) g.FillRectangle(acid, box);
                else if (value is WikiValue.Limited or WikiValue.ModRequired) g.FillRectangle(hot, box);
                g.DrawRectangle(outline, box.X, box.Y, box.Width, box.Height);
                string line = label.ToUpper(L.Culture) + "  /  " + L.Text("Games.Value." + value).ToUpper(L.Culture);
                g.DrawString(line, tiny, value == WikiValue.Yes ? ink : muted, 20, y);
                y += 22;
            }
        }
    }

    /// <summary>Owner-drawn rows: name, mono source line and the status tag in the strip's own colours.</summary>
    sealed class EntryList : ListBox
    {
        public EntryList() { DrawMode = DrawMode.OwnerDrawFixed; IntegralHeight = false; }
        protected override void OnDrawItem(DrawItemEventArgs e)
        {
            if (e.Index < 0 || Items[e.Index] is not Entry item) return;
            float s = DeviceDpi / 96f; bool selected = (e.State & DrawItemState.Selected) != 0;
            var bg = selected ? Ink : Paper; var fg = selected ? Paper : Ink;
            using var brush = new SolidBrush(bg); e.Graphics.FillRectangle(brush, e.Bounds);
            var stripeColor = StatusColor(item.Standing.Status);
            using var stripe = new SolidBrush(stripeColor == Paper ? (selected ? Acid : Muted) : stripeColor);
            e.Graphics.FillRectangle(stripe, e.Bounds.X, e.Bounds.Y, 6 * s, e.Bounds.Height);
            var bounds = new Rectangle(e.Bounds.X + (int)(20 * s), e.Bounds.Y + (int)(12 * s), e.Bounds.Width - (int)(32 * s), (int)(22 * s));
            using var title = new Font("Segoe UI", 11.5f); using var small = new Font("Consolas", 8.5f, FontStyle.Bold);
            const TextFormatFlags flags = TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix;
            TextRenderer.DrawText(e.Graphics, item.Game.Name, title, bounds, fg, flags);
            bounds.Y += (int)(26 * s); bounds.Height = (int)(18 * s);
            var tag = StatusText(item.Standing, item.Pending);
            var tagSize = TextRenderer.MeasureText(tag, small);
            var tagRect = new Rectangle(bounds.X, bounds.Y, tagSize.Width + (int)(12 * s), tagSize.Height + (int)(2 * s));
            if (stripeColor != Paper) { using var fill = new SolidBrush(stripeColor); e.Graphics.FillRectangle(fill, tagRect); }
            else { using var pen = new Pen(selected ? Paper : Muted, 1 * s); e.Graphics.DrawRectangle(pen, tagRect); }
            TextRenderer.DrawText(e.Graphics, tag, small, tagRect, stripeColor == Paper ? (selected ? Paper : Muted) : (stripeColor == Hot ? Paper : Ink), flags | TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
            var sourceRect = new Rectangle(tagRect.Right + (int)(10 * s), bounds.Y, bounds.Width - tagRect.Width - (int)(10 * s), tagRect.Height);
            TextRenderer.DrawText(e.Graphics, item.Game.Source.ToUpper(L.Culture) + (item.Game.SteamAppId is { } id ? " / " + id : ""), small, sourceRect, selected ? Acid : Muted, flags | TextFormatFlags.VerticalCenter);
            using var line = new Pen(Ink); e.Graphics.DrawLine(line, e.Bounds.Left, e.Bounds.Bottom - 1, e.Bounds.Right, e.Bounds.Bottom - 1);
            if ((e.State & DrawItemState.Focus) != 0) e.DrawFocusRectangle();
        }
    }
}
