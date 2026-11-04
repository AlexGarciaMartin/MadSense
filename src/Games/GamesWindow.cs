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
    readonly bool previewOnly;
    readonly CancellationTokenSource lifetime = new();
    readonly Dictionary<(string, float, FontStyle), Font> fonts = [];
    readonly BandPanel band = new() { Dock = DockStyle.Fill, Margin = Padding.Empty };
    readonly MainWindow.RoundedButton refresh = new(), folder = new(), install = new() { Accent = true }, remove = new(), run = new(), page = new(), wiki = new(), forget = new();
    readonly Label footer = new(), count = new(), empty = new(), section = new();
    readonly TextBox search = new();
    readonly EntryList entries = new();
    readonly PlatePanel listPlate = new(), detailPlate = new();
    readonly Panel detailScroll = new() { Dock = DockStyle.Fill, AutoScroll = true };
    readonly FlowLayoutPanel detail = new() { Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.TopDown, WrapContents = false };
    readonly ProgressBar progress = new() { Dock = DockStyle.Fill, Visible = false, Style = ProgressBarStyle.Continuous };
    readonly FlowLayoutPanel actions = new() { Dock = DockStyle.Fill, AutoSize = true, WrapContents = true, Margin = Padding.Empty, Padding = new(0, 6, 0, 0) };
    readonly FlowLayoutPanel filters = new() { Dock = DockStyle.Fill, Margin = Padding.Empty };
    readonly Dictionary<string, MainWindow.RoundedButton> filterButtons = [];
    readonly TableLayoutPanel noticePanel = new() { Dock = DockStyle.Fill, AutoSize = true, ColumnCount = 2, Margin = new(0, 4, 0, 4), BackColor = Ink, Padding = new(10, 6, 6, 6), Visible = false };
    readonly Label notice = new() { AutoSize = true, ForeColor = Paper, Dock = DockStyle.Fill, Margin = new(0, 3, 8, 3), UseMnemonic = false };
    readonly MainWindow.RoundedButton dismiss = new(), cancel = new();
    readonly Dictionary<string, string> chosenMods = [];
    readonly Dictionary<string, bool> modViews = [];
    readonly Dictionary<string, int> guideSteps = [];
    readonly HashSet<string> expandedDetails = [];
    string activeFilter = "All";
    CancellationTokenSource? installation;
    UiMessage? noticeMessage;
    ModDiagnostics? diagnostics;
    Label? diagnosticText;
    public event Action? RecheckRequested;
    public event Action? ReturnRequested;
    public void SetConnectionSummary(string text) { if (band.Connection.Text != text) band.Connection.Text = text; }
    public void SetDiagnostics(ModDiagnostics snapshot)
    {
        if (diagnostics == snapshot) return;
        diagnostics = snapshot;
        RefreshDiagnostics();
    }
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

    public GamesWindow(GamesService service, bool previewOnly = false)
    {
        this.service = service;
        band.Navigation.Click += (_, _) => ReturnRequested?.Invoke();
        this.previewOnly = previewOnly;
        SuspendLayout();
        AutoScaleMode = AutoScaleMode.Dpi;
        AutoScaleDimensions = new SizeF(96, 96);
        ClientSize = new(1100, 760); MinimumSize = new(920, 680);
        StartPosition = FormStartPosition.CenterParent;
        BackColor = Paper; ForeColor = Ink; Font = MakeFont("Segoe UI", 10);
        Icon = MainWindow.Logo.CreateIcon(); Padding = new(24, 12, 24, 16);
        var root = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 8, ColumnCount = 1, Margin = Padding.Empty };
        root.RowStyles.Add(new(SizeType.Absolute, 96));
        root.RowStyles.Add(new(SizeType.Absolute, 30));
        root.RowStyles.Add(new(SizeType.Absolute, 56));
        root.RowStyles.Add(new(SizeType.Absolute, 38));
        root.RowStyles.Add(new(SizeType.AutoSize));
        root.RowStyles.Add(new(SizeType.Percent, 100));
        root.RowStyles.Add(new(SizeType.AutoSize));
        root.RowStyles.Add(new(SizeType.Absolute, 30));
        root.Controls.Add(band, 0, 0);
        // Section line, as in the main window: mono kicker with a slash.
        section.Dock = DockStyle.Fill; section.TextAlign = ContentAlignment.BottomLeft; section.ForeColor = Ink; section.Font = MakeFont("Consolas", 8, FontStyle.Bold); section.Padding = new(0, 0, 0, 5);
        root.Controls.Add(section, 0, 1);
        // Toolbar: search field, count, a folder of the user's own, refresh. Equal-height search and actions, with breathing room before the filter row.
        var toolbar = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 4, RowCount = 1, Margin = Padding.Empty };
        toolbar.RowStyles.Add(new(SizeType.Percent, 100));
        toolbar.ColumnStyles.Add(new(SizeType.Percent, 100)); toolbar.ColumnStyles.Add(new(SizeType.Absolute, 140)); toolbar.ColumnStyles.Add(new(SizeType.Absolute, 156)); toolbar.ColumnStyles.Add(new(SizeType.Absolute, 144));
        var searchPlate = new PlatePanel { Dock = DockStyle.Fill, Margin = new(0, 0, 12, 8), Padding = new(10, 0, 10, 0) };
        search.Anchor = AnchorStyles.Left | AnchorStyles.Right; search.BackColor = Paper; search.ForeColor = Ink; search.BorderStyle = BorderStyle.None; search.Font = MakeFont("Consolas", 11, FontStyle.Bold);
        search.TextChanged += (_, _) => Populate();
        searchPlate.Controls.Add(search);
        searchPlate.Layout += (_, _) => { var area = searchPlate.DisplayRectangle; search.SetBounds(area.X, area.Y + Math.Max(0, (area.Height - search.Height) / 2), area.Width, search.Height); };
        count.Dock = DockStyle.Fill; count.TextAlign = ContentAlignment.MiddleRight; count.Font = MakeFont("Consolas", 9, FontStyle.Bold); count.Margin = new(0, 0, 12, 8);
        folder.Dock = DockStyle.Fill; folder.Margin = new(0, 0, 12, 8); folder.Click += async (_, _) => await AddFolderAsync();
        refresh.Dock = DockStyle.Fill; refresh.Margin = new(0, 0, 0, 8); refresh.Click += async (_, _) => await ScanAsync();
        toolbar.Controls.Add(searchPlate, 0, 0); toolbar.Controls.Add(count, 1, 0); toolbar.Controls.Add(folder, 2, 0); toolbar.Controls.Add(refresh, 3, 0);
        root.Controls.Add(toolbar, 0, 2);
        foreach (var key in new[] { "All", "Native", "Available", "Installed" })
        {
            var button = new MainWindow.RoundedButton { Width = key is "Available" or "Installed" ? 160 : 110, Height = 32, Margin = new(0, 2, 8, 2) };
            button.Click += (_, _) => { activeFilter = key; Populate(); };
            filterButtons[key] = button; filters.Controls.Add(button);
        }
        root.Controls.Add(filters, 0, 3);
        noticePanel.ColumnStyles.Add(new(SizeType.Percent, 100)); noticePanel.ColumnStyles.Add(new(SizeType.Absolute, 116));
        noticePanel.Controls.Add(notice, 0, 0);
        dismiss.Dock = DockStyle.Fill; dismiss.Height = 32; dismiss.Click += (_, _) => { noticeMessage = null; noticePanel.Visible = false; };
        noticePanel.Controls.Add(dismiss, 1, 0);
        noticePanel.SizeChanged += (_, _) => notice.MaximumSize = new(Math.Max(120, noticePanel.ClientSize.Width - 150), 0);
        root.Controls.Add(noticePanel, 0, 4);
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
        content.Controls.Add(listPlate, 0, 0); content.Controls.Add(detailPlate, 1, 0); root.Controls.Add(content, 0, 5);
        foreach (var button in new[] { install, run, page, wiki, remove, forget, cancel })
        {
            button.Width = 146; button.Height = 38; button.Margin = new(0, 0, 8, 4); button.Visible = false; actions.Controls.Add(button);
        }
        cancel.Click += (_, _) => { cancel.Enabled = false; installation?.Cancel(); };
        install.Click += async (_, _) => await InstallAsync();
        remove.Click += (_, _) => Remove();
        run.Click += (_, _) => RunHelper();
        forget.Click += async (_, _) => await ForgetFolderAsync();
        page.Click += (_, _) => { if (pageUri is { } uri) OpenUrl(uri); };
        wiki.Click += (_, _) => { if (wikiUri is { } uri) OpenUrl(uri); };
        root.Controls.Add(actions, 0, 6);
        var bottom = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, Margin = Padding.Empty };
        bottom.RowStyles.Add(new(SizeType.Percent, 100));
        bottom.ColumnStyles.Add(new(SizeType.Percent, 100)); bottom.ColumnStyles.Add(new(SizeType.Absolute, 220));
        footer.Dock = DockStyle.Fill; footer.TextAlign = ContentAlignment.MiddleLeft; footer.ForeColor = Muted; footer.Font = MakeFont("Segoe UI", 8); footer.AutoEllipsis = true;
        progress.Margin = new(12, 10, 0, 10);
        bottom.Controls.Add(footer, 0, 0); bottom.Controls.Add(progress, 1, 0);
        root.Controls.Add(bottom, 0, 7);
        Controls.Add(root);
        ApplyText();
        service.LibraryChanged += LibraryChanged;
        service.Runner.HelperStarted += HelperChanged;
        service.Runner.HelperStopped += HelperChanged;
        service.Runner.HelperFailed += HelperFailed;
        Shown += async (_, _) => { ActiveControl = entries; if (!previewOnly) await ScanAsync(); };
        L.Changed += LanguageChanged;
        FormClosed += (_, _) => { if (!disposedResources) { lifetime.Cancel(); lookups?.Cancel(); } };
        ResumeLayout(true);
    }

    /// <summary>Deterministic visual QA; never scans the host or consults the network.</summary>
    internal void SetPreviewLibrary(IReadOnlyList<LibraryGame> library, IReadOnlyDictionary<string, WikiSupport?> wiki)
    {
        if (!previewOnly) throw new InvalidOperationException("Preview data requires previewOnly.");
        games = library.ToList(); support.Clear(); pending.Clear();
        foreach (var pair in wiki) support[pair.Key] = pair.Value;
        Populate();
    }

    Font MakeFont(string family, float size, FontStyle style = FontStyle.Regular)
    {
        var key = (family, size, style);
        if (!fonts.TryGetValue(key, out var font)) { font = new Font(family, size, style); fonts.Add(key, font); }
        return font;
    }

    void Post(Action action) { if (IsDisposed || Disposing || disposedResources) return; if (InvokeRequired) BeginInvoke(action); else action(); }
    void HelperChanged(string mod, string game) => Post(Populate);
    void HelperFailed(string mod, string game) => Post(() => Notify(L.Message("Games.HelperFailed", ModCatalog.ById(mod)?.Name ?? mod)));
    void Notify(UiMessage message)
    {
        if (IsDisposed || Disposing) return;
        noticeMessage = message; notice.Text = message.Render(); noticePanel.Visible = true;
    }
    void LanguageChanged() => Post(ApplyText);
    void LibraryChanged() => Post(() => { games = service.Library.ToList(); Populate(); _ = LookupAllAsync(); });

    void ApplyText()
    {
        Text = "MadSense · " + L.Text("Games.Title");
        band.Kicker = L.Text("Games.Kicker"); band.Headline = L.Text("Games.Heading"); band.Tagline = L.Text("Games.Tagline");
        section.Text = L.Text("Games.Section");
        band.Navigation.Text = "←  " + L.Text("Games.NavController").ToUpper(L.Culture);
        band.Navigation.AccessibleName = L.Text("Games.NavController");
        refresh.Text = L.Text("Games.Refresh").ToUpper(L.Culture); folder.Text = L.Text("Games.AddFolder").ToUpper(L.Culture); forget.Text = L.Text("Games.ForgetFolder").ToUpper(L.Culture);
        search.PlaceholderText = L.Text("Games.Search").ToUpper(L.Culture); search.AccessibleName = L.Text("Games.Search"); entries.AccessibleName = L.Text("Games.Title");
        install.Text = L.Text("Games.Install").ToUpper(L.Culture); remove.Text = L.Text("Games.Remove").ToUpper(L.Culture); run.Text = L.Text("Games.RunNow").ToUpper(L.Culture);
        page.Text = L.Text("Games.ModPage").ToUpper(L.Culture); wiki.Text = L.Text("Games.WikiPage").ToUpper(L.Culture);
        foreach (var (key, button) in filterButtons) button.Text = L.Text("Games.Filter." + key).ToUpper(L.Culture);
        dismiss.Text = L.Text("Games.Dismiss").ToUpper(L.Culture); cancel.Text = L.Text("Games.Cancel").ToUpper(L.Culture);
        if (noticeMessage is not null) notice.Text = noticeMessage.Render();
        shownSignature = "";
        Populate();
    }

    async Task ScanAsync()
    {
        if (previewOnly) return;
        if (scanning || disposedResources || lifetime.IsCancellationRequested) return;
        scanning = true; refresh.Enabled = false; footer.Text = L.Text("Games.Scanning");
        try { games = (await service.ScanAsync(lifetime.Token)).ToList(); }
        catch (OperationCanceledException) { }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Security.SecurityException) { Notify(UiMessage.FromException(e)); }
        finally { scanning = false; if (!IsDisposed) { refresh.Enabled = true; Populate(); } }
    }

    /// <summary>Resolves each game's wiki record in the background, one at a time, cached answers first.</summary>
    async Task LookupAllAsync()
    {
        if (previewOnly) return;
        lookups?.Cancel(); lookups?.Dispose();
        lookups = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
        var token = lookups.Token;
        pending.Clear();
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
            if (service.Wiki.Blocked) { pending.Clear(); Populate(); Notify(L.Message("Games.WikiBlocked")); return; }
        }
    }

    void Populate()
    {
        if (IsDisposed || Disposing || disposedResources) return;
        var previous = (entries.SelectedItem as Entry)?.Game.Key ?? selectedKey;
        var all = games.Select(g => new Entry(g, service.Standing(g, support.GetValueOrDefault(g.Key)), pending.Contains(g.Key))).ToList();
        var filter = search.Text.Trim();
        var rows = all.Where(r => r.Game.Name.Contains(filter, StringComparison.CurrentCultureIgnoreCase))
            .Where(r => activeFilter switch { "Native" => r.Standing.Wiki?.NativeDualSense == true, "Available" => r.Standing.Mods.Any(m => !service.Installer.IsInstalled(m.Id, r.Game.Key)), "Installed" => r.Standing.Installed.Count > 0, _ => true })
            .OrderBy(r => r.Game.Name, StringComparer.CurrentCultureIgnoreCase).ToList();
        foreach (var (key, button) in filterButtons) button.Selected = key == activeFilter;
        count.Text = L.Text("Games.Results", rows.Count, games.Count).ToUpper(L.Culture);
        if (!installing) foreach (var control in new Control[] { entries, search, filters, folder, refresh }) control.Enabled = !scanning;
        band.Badge = L.Text("Games.Badge", all.Count(r => r.Standing.Wiki?.NativeDualSense == true), all.Count(r => r.Standing.Mods.Count > 0));
        band.Invalidate();
        empty.Text = L.Text(games.Count == 0 ? "Games.NoGames" : "Games.NoMatches"); empty.Visible = rows.Count == 0;
        if (!installing) footer.Text = scanning ? L.Text("Games.Scanning") : pending.Count > 0 ? L.Text("Games.Consulting", pending.Count) : L.Text("Games.Footer", ModCatalog.Version);
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
        if (rows.Count > 0) Selected(); else { selectedKey = null; shownSignature = ""; ResetDetail(); Kicker(L.Text(games.Count == 0 ? "Games.EmptyTitle" : "Games.EmptyFiltered")); Body(L.Text(games.Count == 0 ? "Games.EmptyDescription" : "Games.EmptyFilteredHint")); }
    }

    /// <summary>
    /// Shows the selected game, but only when what there is to show has changed: the same game with
    /// the same data is left alone, and the same game with new data keeps its scroll position.
    /// </summary>
    void Selected()
    {
        if (rebuilding || entries.SelectedItem is not Entry entry) return;
        var standing = entry.Standing;
        var signature = string.Join("|", entry.Game.Key, standing.Status, entry.Pending, standing.Wiki?.Fetched.Ticks, installing, L.CurrentLanguage, chosenMods.GetValueOrDefault(entry.Game.Key),
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
        pageUri = wikiUri = null; selectedMod = null; diagnosticText = null;
        foreach (var button in new[] { install, run, remove, forget, page, wiki }) button.Visible = false;
    }

    Control Add(Control control) { detail.Controls.Add(control); FitDetail(); return control; }

    /// <summary>Mono uppercase line, the kicker of the main window.</summary>
    Label Kicker(string text, Color? color = null) => (Label)Add(new Label { AutoSize = true, Text = text.ToUpper(L.Culture), ForeColor = color ?? Ink, Font = MakeFont("Consolas", 8.5f, FontStyle.Bold), Margin = new(0, 2, 0, 6), UseMnemonic = false });

    /// <summary>Body copy in the strip's own voice.</summary>
    Label Body(string text, bool muted = true) => (Label)Add(new Label { AutoSize = true, Text = text, ForeColor = muted ? Muted : Ink, Font = MakeFont("Segoe UI", 9.75f), Margin = new(0, 0, 0, 10), UseMnemonic = false });

    /// <summary>The game's name as the hero prints its headline: Impact, fitted to the pane.</summary>
    void Headline(string text) => Add(new FittedTitle { Text = text, Height = (int)(44 * DeviceDpi / 96f), Margin = new(0, 0, 0, 6) });

    /// <summary>The slanted badge of the hero, in acid, orange or paper.</summary>
    void Badge(string text, Color fill) => Add(new SlantBadge { Text = text.ToUpper(L.Culture), Fill = fill, Height = (int)(30 * DeviceDpi / 96f), Margin = new(0, 2, 0, 12) });

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

    void RedrawDetail() { shownSignature = ""; Selected(); detailScroll.AutoScrollPosition = Point.Empty; }

    void DetailAction(string key, Action click)
    {
        int height = (int)Math.Round(38 * DeviceDpi / 96f);
        var button = new MainWindow.RoundedButton { Text = L.Text(key).ToUpper(L.Culture), Height = height,
            MinimumSize = new(0, height), Margin = new(0, (int)(4 * DeviceDpi / 96f), 0, (int)(8 * DeviceDpi / 96f)), Enabled = !installing };
        button.Click += (_, _) => click(); Add(button);
    }

    void ShowGame(Entry entry)
    {
        ResetDetail(); detail.SuspendLayout();
        var game = entry.Game; var standing = entry.Standing; var info = standing.Wiki;
        bool native = info?.NativeDualSense == true;
        bool mods = standing.Mods.Count > 0;
        bool showMod = mods && (!native || modViews.GetValueOrDefault(game.Key));
        bool details = expandedDetails.Contains(game.Key);
        Kicker(game.Source, Muted); Headline(game.Name);
        if (showMod)
        {
            if (native) DetailAction("Games.Flow.NativeView", () => { modViews[game.Key] = false; RedrawDetail(); });
            selectedMod = standing.Mods.FirstOrDefault(m => m.Id == chosenMods.GetValueOrDefault(game.Key)) ?? standing.Mods[0];
            if (standing.Mods.Count > 1)
            {
                Kicker(L.Text("Games.SelectMod"));
                var selector = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, BackColor = Paper, ForeColor = Ink,
                    Font = MakeFont("Segoe UI", 10), AccessibleName = L.Text("Games.SelectMod"), Margin = new(0, 0, 0, 10), Enabled = !installing };
                selector.Items.AddRange(standing.Mods.Select(m => m.Name).ToArray());
                selector.SelectedIndex = standing.Mods.ToList().IndexOf(selectedMod);
                selector.SelectedIndexChanged += (_, _) => { chosenMods[game.Key] = standing.Mods[selector.SelectedIndex].Id; BeginInvoke(RedrawDetail); };
                Add(selector);
            }
            var mod = selectedMod;
            pageUri = mod.PageUri;
            var installed = service.Installer.Find(mod.Id, game.Key);
            if (!mod.Available)
            {
                Body(mod.Note(L.CurrentLanguage), muted: false);
                remove.Visible = installed is not null && !installing;
                page.Visible = true;
                detail.ResumeLayout(true);
                return;
            }
            string guideKey = game.Key + ":" + mod.Id;
            int step = guideSteps.GetValueOrDefault(guideKey, installed is null ? 0 : service.IsSetupComplete(mod.Id, game.Key) ? 2 : 1);
            Badge(L.Text("Games.ModFeatures", string.Join(" · ", mod.Features.Select(f => L.Text("Games.Feature." + f)))), Paper);
            Kicker((step + 1) + " / 3  ·  " + L.Text(step == 0 ? "Games.Flow.Install" : step == 1 ? "Games.Setup" : "Games.Flow.Play"));
            if (step == 0)
            {
                Body(L.Text(mod.Installable ? "Games.Flow.InstallIntro" : "Games.Flow.Manual"), muted: false);
                if (mod.InstallNotice(L.CurrentLanguage) is { Length: > 0 } notice) Body(notice, muted: false);
                install.Visible = mod.Installable && installed is null && !installing;
                if (!mod.Installable) { pageUri = mod.PageUri; page.Visible = true; }
                if (installed is not null || !mod.Installable)
                    DetailAction(installed is null ? "Games.Flow.ManualDone" : "Games.Flow.Next", () => { guideSteps[guideKey] = 1; RedrawDetail(); });
            }
            else if (step == 1)
            {
                var preparation = mod.Preparation(game, L.CurrentLanguage);
                Body(preparation.Length > 0 ? preparation : L.Text("Games.Flow.Manual"), muted: false);
                page.Visible = true;
                DetailAction("Games.Flow.Next", () => {
                    if (installed is not null)
                    {
                        try { service.SetSetupComplete(mod.Id, game.Key, true); }
                        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { Notify(L.Message("Games.SettingsFailed", e.Message)); return; }
                    }
                    guideSteps[guideKey] = 2; RecheckRequested?.Invoke(); RedrawDetail();
                });
            }
            else
            {
                Body(L.Text(mod.Kind == ModKind.Helper ? "Games.Flow.HelperLaunch" : "Games.Flow.GameLaunch"), muted: false);
                if (installed?.Exe is not null)
                {
                    AddToggle("Games.AutoStart", service.IsAutoStartEnabled(mod.Id, game.Key), value => service.SetAutoStartEnabled(mod.Id, game.Key, value));
                    if (service.Runner.Failure(mod.Id, game.Key) is not null) Body(L.Text("Games.HelperFailed", mod.Name), muted: false);
                    run.Visible = !installing;
                    run.Text = L.Text(service.Runner.IsRunning(mod.Id, game.Key) ? "Games.StopNow" : "Games.RunNow").ToUpper(L.Culture);
                }
                if (!mod.Installable) Body(L.Text("Games.Flow.Unverified"));
                diagnosticText = Body("", muted: false); RefreshDiagnostics();
                Body(L.Text("Games.Flow.TryEffects"));
            }
            if (step > 0) DetailAction("Games.Flow.Back", () => { guideSteps[guideKey] = step - 1; RedrawDetail(); });
            DetailAction(details ? "Games.Flow.HideDetails" : "Games.Flow.Details", () => { if (!expandedDetails.Add(game.Key)) expandedDetails.Remove(game.Key); RedrawDetail(); });
            if (details)
            {
                Kicker(mod.Name);
                Body(mod.Note(L.CurrentLanguage));
                Body(L.Text("Games.ModBy", mod.Author, mod.License ?? L.Text("Games.NoLicense"), string.Join(", ", mod.Features.Select(f => L.Text("Games.Feature." + f)))));
                pageUri = mod.PageUri; page.Visible = true;
                remove.Visible = installed is not null && !installing;
                if (native) Body(L.Text("Games.ModPriority"));
            }
        }
        else
        {
            Badge(native ? L.Text(standing.Installed.Count == 0 ? "Games.Flow.Native" : "Games.NativeSummary") : StatusText(standing, entry.Pending), native ? Acid : Paper);
            if (native)
            {
                Body(L.Text(standing.Installed.Count == 0 ? "Games.Flow.NativePlay" : "Games.StartStep"), muted: false);
                if (game.SteamAppId is not null) Body(L.Text("Games.Flow.Steam"));
                if (standing.Installed.Count > 0) Body(L.Text("Games.Flow.InstalledWarning"));
            }
            if (info is not null)
            {
                (string Label, WikiValue Value)[] features = [(L.Text("Games.Feature.haptics"), info.Haptics), (L.Text("Games.Feature.triggers"), info.Triggers),
                    (L.Text("Games.Feature.speaker"), info.Speaker), (L.Text("Games.Feature.lightbar"), info.Lightbar),
                    (L.Text("Games.Feature.gyro"), info.Motion), (L.Text("Games.Feature.prompts"), info.Prompts)];
                Lamps(native && !details ? features.Where(f => f.Value is WikiValue.Yes or WikiValue.Limited).ToArray() : features);
            }
            else Body(L.Text(entry.Pending ? "Games.LoadingWiki" : "Games.NoWikiData"));
            if (mods) DetailAction("Games.Flow.ModView", () => { modViews[game.Key] = true; RedrawDetail(); });
            DetailAction(details ? "Games.Flow.HideDetails" : "Games.Flow.Details", () => { if (!expandedDetails.Add(game.Key)) expandedDetails.Remove(game.Key); RedrawDetail(); });
            if (details && info is not null)
            {
                Body(L.Text("Games.WikiAttribution")); wikiUri = info.Source; wiki.Visible = true;
            }
        }
        if (details && service.IsCustomFolder(game.Source)) forget.Visible = !installing;
        detail.ResumeLayout(true);
    }

    void AddToggle(string key, bool value, Action<bool> save)
    {
        var toggle = new CheckBox { Text = L.Text(key), Checked = value, AutoSize = true, Enabled = !installing, Margin = new(0, 2, 0, 8), UseMnemonic = false };
        toggle.CheckedChanged += (_, _) =>
        {
            try { save(toggle.Checked); BeginInvoke(() => { shownSignature = ""; Selected(); }); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { Notify(L.Message("Games.SettingsFailed", e.Message)); BeginInvoke(() => { shownSignature = ""; Selected(); }); }
        };
        Add(toggle);
    }

    void RefreshDiagnostics()
    {
        if (diagnosticText is null || diagnosticText.IsDisposed) return;
        var key = diagnostics switch
        {
            null => "Games.Flow.Connect",
            { ControllerConnected: false } => "Games.Flow.Connect",
            { PortBusy: true } => "Games.Flow.Busy",
            { Listening: false } => "Games.Flow.Offline",
            { Receiving: true } => "Games.Flow.Receiving",
            _ => "Games.Flow.Waiting"
        };
        diagnosticText.Text = L.Text(key);
    }

    static string Trim(string wikitext)
    {
        var text = System.Text.RegularExpressions.Regex.Replace(wikitext, @"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]|'''?|\{\{[^}]*\}\}|<[^>]+>", "$1").Trim();
        return text.Length > 360 ? text[..357] + "…" : text;
    }

    // ── actions ───────────────────────────────────────────────────────────────────────────────
    /// <summary>Lets the user point at a folder where games live; it is remembered and read with every refresh.</summary>
    async Task AddFolderAsync()
    {
        if (previewOnly) return;
        if (scanning) return;
        string path;
        using (var dialog = new FolderBrowserDialog { Description = L.Text("Games.AddFolderTitle"), UseDescriptionForTitle = true, ShowNewFolderButton = false })
        {
            if (dialog.ShowDialog(this) != DialogResult.OK || string.IsNullOrWhiteSpace(dialog.SelectedPath)) return;
            path = dialog.SelectedPath;
        }
        if (!service.AddFolder(path)) { Notify(L.Message("Games.FolderKnown", path)); return; }
        Notify(L.Message("Games.FolderAdded", path));
        await ScanAsync();
    }

    async Task ForgetFolderAsync()
    {
        if (previewOnly) return;
        if (scanning || entries.SelectedItem is not Entry entry || !service.RemoveFolder(entry.Game.Source)) return;
        Notify(L.Message("Games.FolderForgotten", entry.Game.Source));
        await ScanAsync();
    }

    async Task InstallAsync()
    {
        if (previewOnly) return;
        if (installing || entries.SelectedItem is not Entry entry || selectedMod is not { } mod) return;
        foreach (var control in new Control[] { entries, search, filters, folder, refresh }) control.Enabled = false;
        installing = true; installation = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token); cancel.Visible = true; cancel.Enabled = true; progress.Visible = true; progress.Value = 0; Populate();
        footer.Text = L.Text("Games.Downloading", mod.Name);
        var reporter = new Progress<InstallProgress>(p => { if (IsDisposed || !installing) return; if (p.Total > 0 && p.Stage == "download") progress.Value = (int)Math.Clamp(p.Done * 100 / p.Total, 0, 100); if (p.Stage == "install") { progress.Style = ProgressBarStyle.Marquee; footer.Text = L.Text("Games.Installing", mod.Name); } });
        try
        {
            service.SetSetupComplete(mod.Id, entry.Game.Key, false);
            await service.Installer.InstallAsync(mod, entry.Game, reporter, installation.Token);
            guideSteps[entry.Game.Key + ":" + mod.Id] = 1;
            Notify(L.Message("Games.Installed", mod.Name));
        }
        catch (OperationCanceledException) { Notify(L.Message("Games.Cancelled")); }
        catch (Exception e) when (e is InvalidOperationException or IOException or UnauthorizedAccessException or System.Net.Http.HttpRequestException or FormatException)
        {
            Notify(L.Message("Games.InstallFailed", mod.Name, e.Message));
        }
        finally { installing = false; installation?.Dispose(); installation = null; if (!IsDisposed) { cancel.Visible = false; progress.Visible = false; progress.Style = ProgressBarStyle.Continuous; Populate(); } }
    }

    void Remove()
    {
        if (previewOnly) return;
        if (installing || entries.SelectedItem is not Entry entry || selectedMod is not { } mod) return;
        try
        {
            service.Runner.Stop(mod.Id, entry.Game.Key);
            service.Installer.Uninstall(mod.Id, entry.Game.Key);
            guideSteps.Remove(entry.Game.Key + ":" + mod.Id);
            Notify(L.Message("Games.Removed", mod.Name));
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or InvalidOperationException) { Notify(L.Message("Games.RemoveFailed", mod.Name, e.Message)); }
        Populate();
    }

    void RunHelper()
    {
        if (previewOnly) return;
        if (entries.SelectedItem is not Entry entry || selectedMod is not { } mod) return;
        if (service.Runner.IsRunning(mod.Id, entry.Game.Key)) { service.Runner.Stop(mod.Id, entry.Game.Key); Notify(L.Message("Games.ModStopped", mod.Name)); }
        else Notify(L.Message(service.Runner.Start(mod.Id, entry.Game.Key) ? "Games.HelperStarted" : "Games.HelperFailed", mod.Name));
        Populate();
    }

    void OpenUrl(Uri uri)
    {
        if (uri.Scheme != Uri.UriSchemeHttps) return;
        try { Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); }
        catch (Exception e) when (e is System.ComponentModel.Win32Exception or InvalidOperationException) { Notify(L.Message("Games.OpenFailed")); }
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing && !disposedResources)
        {
            disposedResources = true;
            lifetime.Cancel(); lookups?.Cancel();
            service.LibraryChanged -= LibraryChanged; L.Changed -= LanguageChanged;
            service.Runner.HelperStarted -= HelperChanged; service.Runner.HelperStopped -= HelperChanged; service.Runner.HelperFailed -= HelperFailed;
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
        public readonly MainWindow.HeaderNavigationButton Navigation = new();
        public readonly Label Connection = new() { ForeColor = Paper, BackColor = Ink, TextAlign = ContentAlignment.MiddleLeft, UseMnemonic = false };
        public BandPanel()
        {
            DoubleBuffered = true; SetStyle(ControlStyles.ResizeRedraw, true);
            Connection.Font = new Font("Consolas", 8, FontStyle.Bold);
            Controls.Add(Navigation); Controls.Add(Connection);
        }
        protected override void OnLayout(LayoutEventArgs e)
        {
            base.OnLayout(e);
            float s = DeviceDpi / 96f;
            int x = Width - (int)(158 * s);
            Navigation.SetBounds(x, (int)(12 * s), (int)(146 * s), (int)(30 * s));
            Connection.SetBounds(x, (int)(42 * s), (int)(146 * s), (int)(34 * s));
            Connection.Padding = new((int)(11 * s), 0, (int)(5 * s), 0);
        }
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
                var card = new RectangleF(w - 300 - i * 7, 14 + i * 7, 105, 52);
                g.FillRectangle(ink, card.X + 5, card.Y + 5, card.Width, card.Height);
                g.FillRectangle(paper, card);
                using var outline = new Pen(Ink, 2.5f); g.DrawRectangle(outline, card.X, card.Y, card.Width, card.Height);
                g.FillRectangle(i == 0 ? acid : ink, card.X + 12, card.Y + 14, 9, 9);
                g.FillRectangle(ink, card.X + 30, card.Y + 15, 70, 6);
                g.FillRectangle(ink, card.X + 12, card.Y + 36, 80, 5);
            }
            g.Restore(save);
            using var tiny = new Font("Consolas", 10.5f, FontStyle.Bold, GraphicsUnit.Pixel);
            float textWidth = Math.Min(w * .56f - 40, w - 340);
            MainWindow.DrawFitted(g, Kicker, "Consolas", 10.5f, 9, FontStyle.Bold, paper, new RectangleF(20, 8, textWidth, 16));
            MainWindow.DrawFitted(g, Headline.ToUpper(L.Culture), "Impact", 34, 24, FontStyle.Regular, paper, new RectangleF(18, 24, textWidth, 38));
            float badgeWidth = Math.Min(textWidth - 12, Math.Max(180, g.MeasureString(Badge, tiny).Width + 40));
            var badge = new RectangleF(22, h - 29, badgeWidth, 21);
            g.FillPolygon(acid, new PointF[] { new(badge.X, badge.Y), new(badge.Right + 6, badge.Y - 3), new(badge.Right, badge.Bottom), new(badge.X - 3, badge.Bottom + 2) });
            MainWindow.DrawFitted(g, Badge, "Consolas", 10.5f, 9, FontStyle.Bold, ink, new RectangleF(badge.X + 12, badge.Y + 2, badge.Width - 24, 18));
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
            using var title = new Font("Segoe UI Semibold", 11.5f); using var small = new Font("Consolas", 8.5f, FontStyle.Bold);
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
