using System.Diagnostics;

namespace MadSense.Games;

/// <summary>Opt-in exploration. No controller, Steam configuration or mod-install operations.</summary>
internal sealed class GamesWindow : Form
{
    static readonly Color Ink = Color.FromArgb(24, 24, 21), Paper = Color.FromArgb(240, 235, 220);
    static readonly Color Acid = Color.FromArgb(220, 241, 49), Hot = Color.FromArgb(249, 84, 40), Muted = Color.FromArgb(99, 96, 85);
    readonly CancellationTokenSource lifetime = new();
    readonly PcGamingWikiClient wiki = new();
    readonly ModDiscoveryClient discovery = new();
    readonly Dictionary<(string Family, float Size, FontStyle Style), Font> fonts = [];
    readonly MainWindow.RoundedButton gamesTab = new(), modsTab = new(), refresh = new(), source = new();
    readonly Label heading = new(), subtitle = new(), footer = new(), count = new(), empty = new(), badge = new();
    readonly TextBox search = new();
    readonly CheckBox installedOnly = new();
    readonly EntryList entries = new();
    readonly Panel detailScroll = new() { Dock = DockStyle.Fill, AutoScroll = true };
    readonly FlowLayoutPanel detail = new() { Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.TopDown, WrapContents = false };
    readonly Panel listHost = new() { Dock = DockStyle.Fill };
    readonly TableLayoutPanel content = new() { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, Margin = Padding.Empty };
    readonly List<InstalledSteamGame> games = [];
    IReadOnlyList<DiscoveredMod> discovered = [];
    readonly Dictionary<string, WikiSupport> support = new(StringComparer.Ordinal);
    CancellationTokenSource? selection;
    bool showMods, scanning, discovering, discoveryAttempted, discoveryFailed, resourcesDisposed, rebuilding;
    string? selectedGameId;
    int scanWarnings;
    Uri? currentSource;

    sealed record Entry(string Title, string Subtitle, string Tag, InstalledSteamGame? Game = null, ModReference? Mod = null, DiscoveredMod? Discovered = null)
    { public override string ToString() => Title; }

    public GamesWindow(bool initialMods = false)
    {
        SuspendLayout();
        showMods = initialMods;
        AutoScaleMode = AutoScaleMode.Dpi;
        AutoScaleDimensions = new SizeF(96, 96);
        ClientSize = new(1060, 720); MinimumSize = new(880, 650);
        StartPosition = FormStartPosition.CenterParent;
        BackColor = Paper; ForeColor = Ink; Font = MakeFont("Segoe UI", 10);
        Icon = MainWindow.Logo.CreateIcon(); Padding = new(28, 20, 28, 20);
        var root = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 5, ColumnCount = 1, Margin = Padding.Empty };
        root.RowStyles.Add(new(SizeType.Absolute, 106)); root.RowStyles.Add(new(SizeType.Absolute, 58));
        root.RowStyles.Add(new(SizeType.Absolute, 56)); root.RowStyles.Add(new(SizeType.Percent, 100)); root.RowStyles.Add(new(SizeType.Absolute, 38));
        var top = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 2, Margin = Padding.Empty };
        top.ColumnStyles.Add(new(SizeType.Percent, 100)); top.ColumnStyles.Add(new(SizeType.Absolute, 160));
        top.RowStyles.Add(new(SizeType.Percent, 65)); top.RowStyles.Add(new(SizeType.Percent, 35));
        heading.Dock = DockStyle.Fill; heading.Font = MakeFont("Impact", 30); heading.TextAlign = ContentAlignment.MiddleLeft;
        badge.Dock = DockStyle.Fill; badge.BackColor = Acid; badge.ForeColor = Ink; badge.TextAlign = ContentAlignment.MiddleCenter;
        badge.Font = MakeFont("Consolas", 11, FontStyle.Bold); badge.Margin = new(0, 14, 0, 8);
        subtitle.Dock = DockStyle.Fill; subtitle.ForeColor = Muted;
        top.Controls.Add(heading, 0, 0); top.Controls.Add(badge, 1, 0); top.Controls.Add(subtitle, 0, 1); top.SetColumnSpan(subtitle, 2);
        root.Controls.Add(top, 0, 0);
        var nav = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1, Margin = Padding.Empty };
        nav.ColumnStyles.Add(new(SizeType.Percent, 50)); nav.ColumnStyles.Add(new(SizeType.Percent, 50)); nav.ColumnStyles.Add(new(SizeType.Absolute, 142));
        foreach (var button in new[] { gamesTab, modsTab, refresh }) { button.Dock = DockStyle.Fill; button.Margin = new(0, 0, 12, 8); }
        refresh.Margin = new(0, 0, 0, 8);
        nav.Controls.Add(gamesTab, 0, 0); nav.Controls.Add(modsTab, 1, 0); nav.Controls.Add(refresh, 2, 0); root.Controls.Add(nav, 0, 1);
        gamesTab.Click += (_, _) => SwitchTab(false); modsTab.Click += (_, _) => SwitchTab(true);
        refresh.Click += async (_, _) => { await ScanAsync(); if (showMods) await DiscoverAsync(); };
        var filters = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1, Margin = Padding.Empty, Padding = new(0, 6, 0, 10) };
        filters.ColumnStyles.Add(new(SizeType.Percent, 50)); filters.ColumnStyles.Add(new(SizeType.Absolute, 212)); filters.ColumnStyles.Add(new(SizeType.Percent, 50));
        search.Anchor = AnchorStyles.Left | AnchorStyles.Right; search.BackColor = Paper; search.ForeColor = Ink; search.BorderStyle = BorderStyle.FixedSingle; search.Font = MakeFont("Segoe UI", 11);
        search.TextChanged += (_, _) => Populate();
        installedOnly.AutoSize = true; installedOnly.Anchor = AnchorStyles.Left; installedOnly.Margin = new(16, 0, 0, 0); installedOnly.CheckedChanged += (_, _) => Populate();
        count.Dock = DockStyle.Fill; count.TextAlign = ContentAlignment.MiddleRight; count.Font = MakeFont("Consolas", 9, FontStyle.Bold);
        filters.Controls.Add(search, 0, 0); filters.Controls.Add(installedOnly, 1, 0); filters.Controls.Add(count, 2, 0); root.Controls.Add(filters, 0, 2);
        content.ColumnStyles.Add(new(SizeType.Percent, 43)); content.ColumnStyles.Add(new(SizeType.Percent, 57));
        listHost.Padding = new(1); listHost.Margin = new(0, 0, 20, 0); listHost.BackColor = Ink;
        entries.Dock = DockStyle.Fill; entries.BackColor = Paper; entries.ForeColor = Ink; entries.BorderStyle = BorderStyle.None;
        entries.SelectedIndexChanged += async (_, _) => await SelectedAsync();
        entries.ItemHeight = (int)Math.Round(82 * DeviceDpi / 96f);
        empty.Dock = DockStyle.Fill; empty.BackColor = Paper; empty.ForeColor = Muted; empty.TextAlign = ContentAlignment.MiddleCenter; empty.Padding = new(24);
        listHost.Controls.Add(entries); listHost.Controls.Add(empty);
        var right = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 2, Margin = Padding.Empty, Padding = new(0) };
        right.RowStyles.Add(new(SizeType.Percent, 100)); right.RowStyles.Add(new(SizeType.Absolute, 52));
        detailScroll.Controls.Add(detail); detailScroll.SizeChanged += (_, _) => FitDetail();
        source.Dock = DockStyle.Fill; source.Margin = new(0, 8, 0, 0); source.Click += (_, _) => OpenSource();
        right.Controls.Add(detailScroll, 0, 0); right.Controls.Add(source, 0, 1);
        content.Controls.Add(listHost, 0, 0); content.Controls.Add(right, 1, 0); root.Controls.Add(content, 0, 3);
        footer.Dock = DockStyle.Fill; footer.TextAlign = ContentAlignment.BottomLeft; footer.ForeColor = Muted; footer.Font = MakeFont("Segoe UI", 8.5f); root.Controls.Add(footer, 0, 4);
        Controls.Add(root);
        ApplyText();
        Shown += async (_, _) => { await ScanAsync(); if (showMods) await DiscoverAsync(); };
        L.Changed += LanguageChanged;
        FormClosed += (_, _) => { if (!resourcesDisposed) { lifetime.Cancel(); selection?.Cancel(); } L.Changed -= LanguageChanged; };
        ResumeLayout(true);
    }

    Font MakeFont(string family, float size, FontStyle style = FontStyle.Regular)
    {
        var key = (family, size, style);
        if (!fonts.TryGetValue(key, out var font)) { font = new Font(family, size, style); fonts.Add(key, font); }
        return font;
    }
    void LanguageChanged() { if (IsDisposed || Disposing || resourcesDisposed) return; if (InvokeRequired) { BeginInvoke(new Action(LanguageChanged)); return; } ApplyText(); }
    void ApplyText()
    {
        Text = "MadSense · " + L.Text("Games.Title"); heading.Text = L.Text("Games.Title").ToUpper(L.Culture);
        badge.Text = L.Text("Games.Preview"); selectedGameId = null;
        subtitle.Text = L.Text("Games.Subtitle"); gamesTab.Text = L.Text("Games.GamesTab"); modsTab.Text = L.Text("Games.ModsTab");
        refresh.Text = L.Text("Games.Refresh"); search.PlaceholderText = L.Text("Games.Search"); search.AccessibleName = L.Text("Games.Search");
        installedOnly.Text = L.Text("Games.InstalledOnly"); entries.AccessibleName = L.Text("Games.Title");
        Populate();
    }
    void SwitchTab(bool mods)
    {
        if (showMods == mods) return;
        showMods = mods; search.Clear(); Populate();
        if (mods && !discoveryAttempted) _ = DiscoverAsync();
    }
    async Task ScanAsync()
    {
        if (scanning || resourcesDisposed || lifetime.IsCancellationRequested) return;
        var token = lifetime.Token;
        scanning = true; refresh.Enabled = false; footer.Text = L.Text("Games.Scanning");
        try
        {
            var result = await Task.Run(() => new SteamLibraryScanner().Scan(token), token);
            if (lifetime.IsCancellationRequested) return;
            games.Clear(); games.AddRange(result.Games.Where(g => !GameCatalog.KnownUtilities.Contains(g.AppId))); scanWarnings = result.Warnings.Count;
        }
        catch (OperationCanceledException) { }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Security.SecurityException) { scanWarnings++; }
        finally { scanning = false; if (!IsDisposed) { refresh.Enabled = !discovering; Populate(); } }
    }
    async Task DiscoverAsync()
    {
        if (discovering || lifetime.IsCancellationRequested) return;
        discoveryAttempted = discovering = true; refresh.Enabled = false; footer.Text = L.Text("Games.LoadingSources");
        try
        {
            var result = await discovery.GetAsync(lifetime.Token);
            if (lifetime.IsCancellationRequested) return;
            discoveryFailed = result is null;
            if (result is not null) discovered = result;
        }
        catch (OperationCanceledException) { }
        finally { discovering = false; if (!IsDisposed) { refresh.Enabled = !scanning; Populate(); } }
    }
    void Populate()
    {
        if (IsDisposed || Disposing || resourcesDisposed || lifetime.IsCancellationRequested) return;
        var previous = (entries.SelectedItem as Entry)?.Title;
        gamesTab.Selected = !showMods; modsTab.Selected = showMods; gamesTab.Invalidate(); modsTab.Invalidate(); installedOnly.Visible = showMods;
        IEnumerable<Entry> rows;
        if (!showMods)
            rows = games.Select(g => new Entry(g.Name, "STEAM / " + g.AppId, GameCatalog.Native.Any(n => n.AppId == g.AppId) ? L.Text("Games.Documented") : L.Text("Games.CheckFeatures"), g));
        else
        {
            var known = GameCatalog.Mods.Select(m => new Entry(m.Name, m.Games, L.Text("Games.Unverified"), Mod: m));
            var other = discovered.Where(d => !GameCatalog.Mods.Any(m => GameCatalog.SamePage(m.Page, d.Page)))
                .Select(d => new Entry(d.Name, L.Text("Games.ListedBy", d.SourceName), L.Text("Games.Unverified"), Discovered: d));
            rows = known.Concat(other);
            if (installedOnly.Checked) rows = rows.Where(r => r.Mod is { } m && games.Any(g => m.SteamIds.Contains(g.AppId)));
        }
        rows = rows.Where(r => (r.Title + " " + r.Subtitle).Contains(search.Text.Trim(), StringComparison.CurrentCultureIgnoreCase)).OrderBy(r => r.Title, StringComparer.CurrentCultureIgnoreCase);
        rebuilding = true;
        entries.BeginUpdate(); entries.Items.Clear(); foreach (var row in rows) entries.Items.Add(row); entries.EndUpdate();
        count.Text = L.Text(showMods ? "Games.ModCount" : "Games.GameCount", entries.Items.Count);
        empty.Text = L.Text(games.Count == 0 && !showMods ? "Games.NoSteamGames" : "Games.NoMatches"); empty.Visible = entries.Items.Count == 0;
        footer.Text = scanning ? L.Text("Games.Scanning") : discovering ? L.Text("Games.LoadingSources") : discoveryFailed && showMods ? L.Text("Games.OfflineSources") : scanWarnings > 0 && !showMods ? L.Text("Games.ScanWarnings", scanWarnings) : L.Text("Games.ReadOnly");
        if (entries.Items.Count > 0)
        {
            var index = entries.Items.Cast<Entry>().ToList().FindIndex(x => x.Title == previous);
            entries.SelectedIndex = index < 0 ? 0 : index;
            rebuilding = false;
            _ = SelectedAsync();
        }
        else { rebuilding = false; selection?.Cancel(); selectedGameId = null; ResetDetail(); Block(L.Text("Games.EmptyTitle"), 24, true); Block(L.Text("Games.EmptyDescription")); }
    }
    async Task SelectedAsync()
    {
        if (rebuilding || IsDisposed || Disposing || resourcesDisposed || lifetime.IsCancellationRequested) return;
        if (entries.SelectedItem is not Entry entry) return;
        if (entry.Game is { } same && same.AppId == selectedGameId) return;
        selectedGameId = entry.Game?.AppId;
        selection?.Cancel(); selection?.Dispose(); selection = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
        var token = selection.Token;
        if (entry.Game is not { } game) { ShowMod(entry); return; }
        support.TryGetValue(game.AppId, out var cached);
        ShowGame(game, cached, cached is null);
        if (cached is not null) return;
        try
        {
            var info = await wiki.GetAsync(game.AppId, token);
            if (token.IsCancellationRequested || IsDisposed) return;
            if (info is not null) support[game.AppId] = info;
            ShowGame(game, info, false);
        }
        catch (OperationCanceledException) { }
    }
    void ResetDetail()
    {
        detail.SuspendLayout();
        foreach (var control in detail.Controls.Cast<Control>().ToArray()) control.Dispose();
        detail.Controls.Clear(); currentSource = null; source.Enabled = false; detailScroll.AutoScrollPosition = Point.Empty;
        detail.ResumeLayout();
    }
    void Block(string text, float size = 10, bool title = false, bool band = false)
    {
        var label = new Label { AutoSize = true, Text = text, ForeColor = title ? Ink : Muted, Font = MakeFont(title ? "Impact" : "Segoe UI", size), Margin = new(0, 0, 0, title ? 16 : 12), Padding = band ? new(12) : Padding.Empty, BackColor = band ? Acid : Paper, UseMnemonic = false };
        detail.Controls.Add(label); FitDetail();
    }
    void FitDetail()
    {
        int width = Math.Max(120, detailScroll.ClientSize.Width - SystemInformation.VerticalScrollBarWidth - 4);
        detail.Width = width;
        foreach (Control label in detail.Controls) label.MaximumSize = new(width, 0);
    }
    void SetSource(Uri uri, string text) { currentSource = uri; source.Text = text; source.Enabled = true; }
    void ShowGame(InstalledSteamGame game, WikiSupport? info, bool loading)
    {
        ResetDetail();
        Block("STEAM / " + game.AppId, 9); Block(game.Name, 25, true);
        var native = GameCatalog.Native.FirstOrDefault(n => n.AppId == game.AppId);
        if (info is not null)
        {
            Block(L.Text("Games.WikiFeatures"), 11, band: true);
            foreach (var feature in new[] { "haptics", "triggers", "speaker", "gyro", "touchpad", "lightbar" })
            {
                var value = info.Features.TryGetValue(feature, out var state) ? state : WikiValue.Unknown;
                Block(L.Text("Games.Feature." + feature) + "  /  " + L.Text("Games.Value." + value), 10);
            }
            Block(L.Text("Games.WikiAttribution"), 9);
            var license = new LinkLabel { AutoSize = true, Text = L.Text("Games.WikiLicense"), LinkColor = Muted, ActiveLinkColor = Ink, Margin = new(0, 0, 0, 14) };
            license.LinkClicked += (_, _) => OpenUrl(new("https://www.pcgamingwiki.com/wiki/PCGamingWiki:Copyrights")); detail.Controls.Add(license);
            SetSource(info.Source, L.Text("Games.ViewWiki"));
        }
        else
        {
            Block(L.Text(native is null ? "Games.UnknownSupport" : "Games.PublisherSupport"), 11, band: true);
            Block(L.Text(loading ? "Games.LoadingWiki" : "Games.NoWikiData"));
            if (native is not null) SetSource(native.Source, L.Text("Games.ViewSource"));
            else SetSource(new("https://store.steampowered.com/app/" + game.AppId + "/"), L.Text("Games.ViewSteam"));
        }
        var mods = GameCatalog.Mods.Where(m => m.SteamIds.Contains(game.AppId)).ToArray();
        if (mods.Length > 0) Block(L.Text("Games.RelatedMods", string.Join(", ", mods.Select(m => m.Name))));
        Block(L.Text("Games.SteamUnchanged"), 9);
    }
    void ShowMod(Entry entry)
    {
        ResetDetail();
        Block(L.Text("Games.CommunityMod"), 9); Block(entry.Title, 25, true);
        Block(entry.Subtitle, 11);
        if (entry.Mod is { } mod)
        {
            Block(L.Text("Games.Author", mod.Author), 9);
            Block(L.Text(mod.DescriptionKey));
            Block(L.Text(games.Any(g => mod.SteamIds.Contains(g.AppId)) ? "Games.MatchingGame" : "Games.NoMatchingGame"), 10, band: true);
            SetSource(mod.Page, L.Text("Games.ViewMod"));
        }
        else if (entry.Discovered is { } found)
        {
            Block(L.Text("Games.DiscoveredDescription", found.SourceName));
            SetSource(found.Page, L.Text("Games.ViewMod"));
        }
        Block(L.Text("Games.NotReadyToInstall"));
        Block(L.Text("Games.CatalogExplanation"), 9);
    }
    void OpenSource() { if (currentSource is { } uri) OpenUrl(uri); }
    void OpenUrl(Uri uri)
    {
        if (uri.Scheme != Uri.UriSchemeHttps) return;
        try { Process.Start(new ProcessStartInfo(uri.AbsoluteUri) { UseShellExecute = true }); }
        catch (Exception e) when (e is System.ComponentModel.Win32Exception or InvalidOperationException) { footer.Text = L.Text("Games.OpenFailed"); }
    }
    protected override void Dispose(bool disposing)
    {
        if (disposing && !resourcesDisposed)
        {
            resourcesDisposed = true;
            lifetime.Cancel(); selection?.Cancel(); L.Changed -= LanguageChanged;
            wiki.Dispose(); discovery.Dispose(); selection?.Dispose(); lifetime.Dispose();
            base.Dispose(true);
            foreach (var font in fonts.Values) font.Dispose();
            return;
        }
        base.Dispose(disposing);
    }

    sealed class EntryList : ListBox
    {
        public EntryList() { DrawMode = DrawMode.OwnerDrawFixed; IntegralHeight = false; }
        protected override void OnDrawItem(DrawItemEventArgs e)
        {
            if (e.Index < 0 || Items[e.Index] is not Entry item) return;
            float s = DeviceDpi / 96f; bool selected = (e.State & DrawItemState.Selected) != 0;
            var bg = selected ? Ink : Paper; var fg = selected ? Paper : Ink;
            using var brush = new SolidBrush(bg); e.Graphics.FillRectangle(brush, e.Bounds);
            using var stripe = new SolidBrush(selected ? Acid : Hot); e.Graphics.FillRectangle(stripe, e.Bounds.X, e.Bounds.Y, 5 * s, e.Bounds.Height);
            var bounds = new Rectangle(e.Bounds.X + (int)(18 * s), e.Bounds.Y + (int)(12 * s), e.Bounds.Width - (int)(30 * s), (int)(23 * s));
            using var title = new Font("Segoe UI", 10.5f, FontStyle.Bold); using var small = new Font("Consolas", 8);
            const TextFormatFlags flags = TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix;
            TextRenderer.DrawText(e.Graphics, item.Title, title, bounds, fg, flags);
            bounds.Y += (int)(25 * s); bounds.Height = (int)(19 * s);
            TextRenderer.DrawText(e.Graphics, item.Subtitle, small, bounds, selected ? Acid : Muted, flags);
            bounds.Y += (int)(18 * s);
            TextRenderer.DrawText(e.Graphics, item.Tag, small, bounds, selected ? Paper : Muted, flags);
            using var line = new Pen(Ink); e.Graphics.DrawLine(line, e.Bounds.Left, e.Bounds.Bottom - 1, e.Bounds.Right, e.Bounds.Bottom - 1);
            if ((e.State & DrawItemState.Focus) != 0) e.DrawFocusRectangle();
        }
    }
}
