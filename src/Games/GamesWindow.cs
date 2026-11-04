using System.Diagnostics;
using System.Drawing.Drawing2D;

namespace MadSense.Games;

/// <summary>
/// The games screen: every installed game, what it does with the DualSense on its own, and the
/// community mod MadSense can install with one click when it does not. Same paper, ink, orange
/// and acid as the main window; reads only, until the user presses Install.
/// </summary>
internal sealed class GamesWindow : Form
{
    static readonly Color Ink = Color.FromArgb(24, 24, 21), Paper = Color.FromArgb(240, 235, 220);
    static readonly Color Acid = Color.FromArgb(220, 241, 49), Hot = Color.FromArgb(249, 84, 40), Muted = Color.FromArgb(99, 96, 85);
    readonly GamesService service;
    readonly CancellationTokenSource lifetime = new();
    readonly Dictionary<(string, float, FontStyle), Font> fonts = [];
    readonly MainWindow.RoundedButton refresh = new(), install = new() { Accent = true }, remove = new(), run = new(), page = new(), wiki = new();
    readonly Label heading = new(), subtitle = new(), footer = new(), count = new(), empty = new(), badge = new();
    readonly TextBox search = new();
    readonly EntryList entries = new();
    readonly Panel detailScroll = new() { Dock = DockStyle.Fill, AutoScroll = true };
    readonly FlowLayoutPanel detail = new() { Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.TopDown, WrapContents = false };
    readonly Panel listHost = new() { Dock = DockStyle.Fill };
    readonly ProgressBar progress = new() { Dock = DockStyle.Fill, Visible = false, Style = ProgressBarStyle.Continuous };
    readonly TableLayoutPanel actions = new() { Dock = DockStyle.Fill, ColumnCount = 5, RowCount = 1, Margin = Padding.Empty };
    readonly Dictionary<string, WikiSupport?> support = new(StringComparer.Ordinal);
    readonly HashSet<string> pending = new(StringComparer.Ordinal);
    List<LibraryGame> games = [];
    CancellationTokenSource? lookups;
    bool scanning, installing, disposedResources, rebuilding;
    string? selectedKey;
    ModEntry? selectedMod;
    Uri? pageUri, wikiUri;

    sealed record Entry(LibraryGame Game, GameStanding Standing, bool Pending) { public override string ToString() => Game.Name; }

    public GamesWindow(GamesService service)
    {
        this.service = service;
        SuspendLayout();
        AutoScaleMode = AutoScaleMode.Dpi;
        AutoScaleDimensions = new SizeF(96, 96);
        ClientSize = new(1100, 740); MinimumSize = new(900, 660);
        StartPosition = FormStartPosition.CenterParent;
        BackColor = Paper; ForeColor = Ink; Font = MakeFont("Segoe UI", 10);
        Icon = MainWindow.Logo.CreateIcon(); Padding = new(28, 20, 28, 20);
        var root = new TableLayoutPanel { Dock = DockStyle.Fill, RowCount = 4, ColumnCount = 1, Margin = Padding.Empty };
        root.RowStyles.Add(new(SizeType.Absolute, 112)); root.RowStyles.Add(new(SizeType.Absolute, 56));
        root.RowStyles.Add(new(SizeType.Percent, 100)); root.RowStyles.Add(new(SizeType.Absolute, 34));
        // Header: title, count badge, one-line explanation.
        var top = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 2, Margin = Padding.Empty };
        top.ColumnStyles.Add(new(SizeType.Percent, 100)); top.ColumnStyles.Add(new(SizeType.Absolute, 190));
        top.RowStyles.Add(new(SizeType.Percent, 62)); top.RowStyles.Add(new(SizeType.Percent, 38));
        heading.Dock = DockStyle.Fill; heading.Font = MakeFont("Impact", 30); heading.TextAlign = ContentAlignment.MiddleLeft;
        badge.Dock = DockStyle.Fill; badge.BackColor = Acid; badge.ForeColor = Ink; badge.TextAlign = ContentAlignment.MiddleCenter;
        badge.Font = MakeFont("Consolas", 11, FontStyle.Bold); badge.Margin = new(0, 16, 0, 10);
        subtitle.Dock = DockStyle.Fill; subtitle.ForeColor = Muted; subtitle.Font = MakeFont("Segoe UI", 10.5f);
        top.Controls.Add(heading, 0, 0); top.Controls.Add(badge, 1, 0); top.Controls.Add(subtitle, 0, 1); top.SetColumnSpan(subtitle, 2);
        root.Controls.Add(top, 0, 0);
        // Search and refresh.
        var filters = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 3, RowCount = 1, Margin = Padding.Empty, Padding = new(0, 4, 0, 10) };
        filters.ColumnStyles.Add(new(SizeType.Percent, 100)); filters.ColumnStyles.Add(new(SizeType.Absolute, 150)); filters.ColumnStyles.Add(new(SizeType.Absolute, 150));
        search.Anchor = AnchorStyles.Left | AnchorStyles.Right; search.BackColor = Paper; search.ForeColor = Ink; search.BorderStyle = BorderStyle.FixedSingle; search.Font = MakeFont("Segoe UI", 11);
        search.TextChanged += (_, _) => Populate();
        count.Dock = DockStyle.Fill; count.TextAlign = ContentAlignment.MiddleRight; count.Font = MakeFont("Consolas", 9, FontStyle.Bold); count.Margin = new(0, 0, 12, 0);
        refresh.Dock = DockStyle.Fill; refresh.Margin = new(0, 0, 0, 6); refresh.Click += async (_, _) => await ScanAsync();
        filters.Controls.Add(search, 0, 0); filters.Controls.Add(count, 1, 0); filters.Controls.Add(refresh, 2, 0); root.Controls.Add(filters, 0, 1);
        // List on the left, detail on the right.
        var content = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, Margin = Padding.Empty };
        content.ColumnStyles.Add(new(SizeType.Percent, 42)); content.ColumnStyles.Add(new(SizeType.Percent, 58));
        listHost.Padding = new(1); listHost.Margin = new(0, 0, 20, 0); listHost.BackColor = Ink;
        entries.Dock = DockStyle.Fill; entries.BackColor = Paper; entries.ForeColor = Ink; entries.BorderStyle = BorderStyle.None;
        entries.SelectedIndexChanged += (_, _) => Selected();
        entries.ItemHeight = (int)Math.Round(84 * DeviceDpi / 96f);
        empty.Dock = DockStyle.Fill; empty.BackColor = Paper; empty.ForeColor = Muted; empty.TextAlign = ContentAlignment.MiddleCenter; empty.Padding = new(24);
        listHost.Controls.Add(entries); listHost.Controls.Add(empty);
        var right = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 3, Margin = Padding.Empty };
        right.RowStyles.Add(new(SizeType.Percent, 100)); right.RowStyles.Add(new(SizeType.Absolute, 14)); right.RowStyles.Add(new(SizeType.Absolute, 52));
        detailScroll.Controls.Add(detail); detailScroll.SizeChanged += (_, _) => FitDetail();
        for (int i = 0; i < 5; i++) actions.ColumnStyles.Add(new(SizeType.Percent, 20));
        foreach (var (button, column) in new[] { (install, 0), (run, 1), (remove, 2), (page, 3), (wiki, 4) })
        {
            button.Dock = DockStyle.Fill; button.Margin = new(column == 0 ? 0 : 6, 8, column == 4 ? 0 : 6, 0); button.Visible = false; actions.Controls.Add(button, column, 0);
        }
        install.Click += async (_, _) => await InstallAsync();
        remove.Click += (_, _) => Remove();
        run.Click += (_, _) => RunHelper();
        page.Click += (_, _) => { if (pageUri is { } uri) OpenUrl(uri); };
        wiki.Click += (_, _) => { if (wikiUri is { } uri) OpenUrl(uri); };
        progress.Margin = new(0, 4, 0, 0);
        right.Controls.Add(detailScroll, 0, 0); right.Controls.Add(progress, 0, 1); right.Controls.Add(actions, 0, 2);
        content.Controls.Add(listHost, 0, 0); content.Controls.Add(right, 1, 0); root.Controls.Add(content, 0, 2);
        footer.Dock = DockStyle.Fill; footer.TextAlign = ContentAlignment.BottomLeft; footer.ForeColor = Muted; footer.Font = MakeFont("Segoe UI", 8.5f); footer.AutoEllipsis = true;
        root.Controls.Add(footer, 0, 3);
        Controls.Add(root);
        ApplyText();
        service.LibraryChanged += LibraryChanged;
        service.Runner.HelperStarted += (_, _) => Post(Populate);
        service.Runner.HelperStopped += (_, _) => Post(Populate);
        Shown += async (_, _) => await ScanAsync();
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
        Text = "MadSense · " + L.Text("Games.Title"); heading.Text = L.Text("Games.Heading").ToUpper(L.Culture);
        subtitle.Text = L.Text("Games.Subtitle"); refresh.Text = L.Text("Games.Refresh");
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
        var rows = games.Select(g => new Entry(g, service.Standing(g, support.GetValueOrDefault(g.Key)), pending.Contains(g.Key)))
            .Where(r => r.Game.Name.Contains(search.Text.Trim(), StringComparison.CurrentCultureIgnoreCase))
            .OrderBy(r => r.Standing.Status switch { GameStatus.ModInstalled => 0, GameStatus.ModAvailable => 1, GameStatus.Native => 2, GameStatus.ModManual => 3, _ => 4 })
            .ThenBy(r => r.Game.Name, StringComparer.CurrentCultureIgnoreCase).ToList();
        rebuilding = true;
        entries.BeginUpdate(); entries.Items.Clear(); foreach (var row in rows) entries.Items.Add(row); entries.EndUpdate();
        count.Text = L.Text("Games.GameCount", games.Count);
        badge.Text = L.Text("Games.Badge", rows.Count(r => r.Standing.Status is GameStatus.Native), rows.Count(r => r.Standing.Status is GameStatus.ModAvailable or GameStatus.ModInstalled));
        empty.Text = L.Text(games.Count == 0 ? "Games.NoGames" : "Games.NoMatches"); empty.Visible = rows.Count == 0;
        footer.Text = scanning ? L.Text("Games.Scanning") : pending.Count > 0 ? L.Text("Games.Consulting", pending.Count) : L.Text("Games.Footer", ModCatalog.Version);
        var index = rows.FindIndex(r => r.Game.Key == previous);
        if (rows.Count > 0) entries.SelectedIndex = index < 0 ? 0 : index;
        rebuilding = false;
        if (rows.Count > 0) Selected(force: true); else { selectedKey = null; ResetDetail(); Block(L.Text("Games.EmptyTitle"), 24, true); Block(L.Text("Games.EmptyDescription")); }
    }

    void Selected(bool force = false)
    {
        if (rebuilding || entries.SelectedItem is not Entry entry) return;
        if (!force && entry.Game.Key == selectedKey && !installing) return;
        selectedKey = entry.Game.Key;
        ShowGame(entry);
    }

    void ResetDetail()
    {
        detail.SuspendLayout();
        foreach (var control in detail.Controls.Cast<Control>().ToArray()) control.Dispose();
        detail.Controls.Clear(); detailScroll.AutoScrollPosition = Point.Empty;
        detail.ResumeLayout();
        pageUri = wikiUri = null; selectedMod = null;
        foreach (var button in new[] { install, run, remove, page, wiki }) button.Visible = false;
    }

    Label Block(string text, float size = 10, bool title = false, bool band = false, Color? color = null)
    {
        var label = new Label
        {
            AutoSize = true, Text = text, ForeColor = color ?? (title ? Ink : Muted), Font = MakeFont(title ? "Impact" : band ? "Consolas" : "Segoe UI", size, band ? FontStyle.Bold : FontStyle.Regular),
            Margin = new(0, 0, 0, title ? 14 : band ? 14 : 10), Padding = band ? new(12, 8, 12, 8) : Padding.Empty, BackColor = band ? (color is null ? Acid : Paper) : Paper, UseMnemonic = false,
        };
        if (band && color is not null) { label.BackColor = color.Value; label.ForeColor = color == Hot ? Paper : Ink; }
        detail.Controls.Add(label); FitDetail();
        return label;
    }

    void FitDetail()
    {
        int width = Math.Max(120, detailScroll.ClientSize.Width - SystemInformation.VerticalScrollBarWidth - 4);
        detail.Width = width;
        foreach (Control label in detail.Controls) label.MaximumSize = new(width, 0);
    }

    static string StatusText(GameStatus status, bool pending) => L.Text(status switch
    {
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
        Block((game.Source + (game.SteamAppId is { } id ? " / " + id : "")).ToUpper(L.Culture), 9);
        Block(game.Name, 25, true);
        Block(StatusText(standing.Status, entry.Pending), 11, band: true, color: StatusColor(standing.Status));
        // What the game itself does with the pad, from the wiki.
        if (info is not null)
        {
            foreach (var (field, key) in new[] { ("haptics", info.Haptics), ("triggers", info.Triggers), ("speaker", info.Speaker), ("lightbar", info.Lightbar), ("gyro", info.Motion), ("prompts", info.Prompts) })
                Block(L.Text("Games.Feature." + field) + "  /  " + L.Text("Games.Value." + key), 10, color: key == WikiValue.Yes ? Ink : Muted);
            if (info.ConnectionModes.Length > 0) Block(L.Text("Games.ConnectionModes", info.ConnectionModes), 9);
            if (info.Note("dualsense adaptive trigger support") is { } note) Block(Trim(note), 9);
            Block(L.Text("Games.WikiAttribution"), 9);
            wikiUri = info.Source; wiki.Visible = true;
        }
        else Block(L.Text(entry.Pending ? "Games.LoadingWiki" : service.Wiki.Blocked ? "Games.WikiBlocked" : "Games.NoWikiData"));
        if (standing.Status == GameStatus.Native) Block(L.Text("Games.NativeHint"));
        // Community mods for this game.
        foreach (var mod in standing.Mods)
        {
            var installed = service.Installer.Find(mod.Id, game.Key);
            Block(L.Text(installed is null ? "Games.ModHeader" : "Games.ModHeaderInstalled", mod.Name), 11, band: true, color: installed is null && mod.Installable ? Hot : installed is null ? Paper : Acid);
            Block(L.Text("Games.ModBy", mod.Author, mod.License ?? L.Text("Games.NoLicense"), string.Join(", ", mod.Features.Select(f => L.Text("Games.Feature." + f)))), 9);
            Block(mod.Note(L.CurrentLanguage));
            if (installed is not null)
            {
                Block(L.Text("Games.InstalledDetail", installed.Version, installed.Installed.ToString("d", L.Culture), installed.Root), 9);
                if (installed.Exe is not null) Block(L.Text(service.Runner.IsRunning(mod.Id, game.Key) ? "Games.HelperRunning" : "Games.HelperIdle"), 9, color: Ink);
            }
            else if (mod.Installable) Block(L.Text(mod.Kind switch { ModKind.Helper => "Games.InstallHelperHint", ModKind.GameFolder => "Games.InstallGameFolderHint", _ => "Games.InstallBeamNgHint" }, service.Installer.ModsRoot), 9);
            else Block(L.Text("Games.ManualHint"), 9);
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
        if (standing.Mods.Count == 0 && standing.Status != GameStatus.Native) Block(L.Text("Games.NoModKnown"), 9);
    }

    static string Trim(string wikitext)
    {
        var text = System.Text.RegularExpressions.Regex.Replace(wikitext, @"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]|'''?|\{\{[^}]*\}\}|<[^>]+>", "$1").Trim();
        return text.Length > 360 ? text[..357] + "…" : text;
    }

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
        finally { installing = false; progress.Visible = false; progress.Style = ProgressBarStyle.Continuous; if (!IsDisposed) { Populate(); } }
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

    /// <summary>Owner-drawn rows: name, source line and the status tag in the strip's own colours.</summary>
    sealed class EntryList : ListBox
    {
        public EntryList() { DrawMode = DrawMode.OwnerDrawFixed; IntegralHeight = false; }
        protected override void OnDrawItem(DrawItemEventArgs e)
        {
            if (e.Index < 0 || Items[e.Index] is not Entry item) return;
            float s = DeviceDpi / 96f; bool selected = (e.State & DrawItemState.Selected) != 0;
            var bg = selected ? Ink : Paper; var fg = selected ? Paper : Ink;
            using var brush = new SolidBrush(bg); e.Graphics.FillRectangle(brush, e.Bounds);
            using var stripe = new SolidBrush(StatusColor(item.Standing.Status) == Paper ? (selected ? Acid : Muted) : StatusColor(item.Standing.Status));
            e.Graphics.FillRectangle(stripe, e.Bounds.X, e.Bounds.Y, 6 * s, e.Bounds.Height);
            var bounds = new Rectangle(e.Bounds.X + (int)(20 * s), e.Bounds.Y + (int)(11 * s), e.Bounds.Width - (int)(32 * s), (int)(24 * s));
            using var title = new Font("Segoe UI", 11f, FontStyle.Bold); using var small = new Font("Consolas", 8.5f, FontStyle.Bold);
            const TextFormatFlags flags = TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix;
            TextRenderer.DrawText(e.Graphics, item.Game.Name, title, bounds, fg, flags);
            bounds.Y += (int)(26 * s); bounds.Height = (int)(18 * s);
            TextRenderer.DrawText(e.Graphics, item.Game.Source.ToUpper(L.Culture) + (item.Game.SteamAppId is { } id ? " / " + id : ""), small, bounds, selected ? Acid : Muted, flags);
            bounds.Y += (int)(19 * s);
            var tag = StatusText(item.Standing.Status, item.Pending);
            var tagColor = StatusColor(item.Standing.Status);
            var size = TextRenderer.MeasureText(tag, small);
            var tagRect = new Rectangle(bounds.X, bounds.Y, size.Width + (int)(12 * s), size.Height + (int)(2 * s));
            if (tagColor != Paper) { using var fill = new SolidBrush(tagColor); e.Graphics.FillRectangle(fill, tagRect); }
            TextRenderer.DrawText(e.Graphics, tag, small, tagRect, tagColor == Paper ? (selected ? Paper : Muted) : (tagColor == Hot ? Paper : Ink), flags | TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
            using var line = new Pen(Ink); e.Graphics.DrawLine(line, e.Bounds.Left, e.Bounds.Bottom - 1, e.Bounds.Right, e.Bounds.Bottom - 1);
            if ((e.State & DrawItemState.Focus) != 0) e.DrawFocusRectangle();
        }
    }
}
