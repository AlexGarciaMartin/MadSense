using Avalonia;
using Avalonia.Automation;
using Avalonia.Controls;
using Avalonia.Media;
using System.Globalization;
using System.Text.Json;
using Avalonia.Platform;

namespace MadSense;

static class Brand
{
    public static readonly IBrush Ink = Brush.Parse("#181815"), Paper = Brush.Parse("#F0EBDC"),
        Hot = Brush.Parse("#F95428"), Acid = Brush.Parse("#DCF131"), Muted = Brush.Parse("#636055");
    public static readonly FontFamily Display = new("avares://MadSense.Linux/Assets#Anton");
    public static readonly FontFamily Mono = new(OperatingSystem.IsWindows() ? "Consolas" : "DejaVu Sans Mono");
    sealed record Outline(Geometry Geometry, double Width, double Height, double Em);
    static readonly Dictionary<string, Outline> HeadlineArtwork = LoadHeadlineArtwork();
    static Dictionary<string, Outline> LoadHeadlineArtwork()
    {
        using var source = AssetLoader.Open(new Uri("avares://MadSense.Linux/Assets/brand-headlines.json"));
        using var json = JsonDocument.Parse(source);
        return json.RootElement.EnumerateObject().ToDictionary(item => item.Name, item => new Outline(
            Geometry.Parse(item.Value.GetProperty("path").GetString()!), item.Value.GetProperty("width").GetDouble(),
            item.Value.GetProperty("height").GetDouble(), item.Value.GetProperty("em").GetDouble()));
    }
    public static void Rect(DrawingContext c, IBrush fill, Rect rect, double border = 0) =>
        c.DrawRectangle(fill, border > 0 ? new Pen(Ink, border) : null, rect);
    public static void Path(DrawingContext c, string data, IBrush fill, double border = 0) =>
        c.DrawGeometry(fill, border > 0 ? new Pen(Ink, border) : null, Geometry.Parse(data));
    public static void Text(DrawingContext c, string value, Rect rect, IBrush color, double largest, double smallest = 9, bool display = false, bool centered = false)
    {
        if (rect.Width <= 0 || rect.Height <= 0) return;
        if (display && HeadlineArtwork.TryGetValue(value, out var artwork))
        {
            var top = largest * .17;
            var scale = Math.Min(largest / artwork.Em, Math.Min(rect.Width / artwork.Width, (rect.Height - top) / artwork.Height));
            using (c.PushClip(rect))
            using (c.PushTransform(Matrix.CreateScale(scale, scale) * Matrix.CreateTranslation(rect.X, rect.Y + top)))
                c.DrawGeometry(color, null, artwork.Geometry);
            return;
        }
        FormattedText text;
        var size = largest;
        do
        {
            text = new(value, CultureInfo.CurrentUICulture, FlowDirection.LeftToRight,
                new Typeface(display ? Display : Mono, FontStyle.Normal, FontWeight.Bold), size, color);
            if ((text.Width <= rect.Width && text.Height <= rect.Height) || size <= smallest) break;
            size -= .5;
        } while (true);
        var origin = centered ? new Point(rect.X + Math.Max(0, (rect.Width - text.Width) / 2), rect.Y + Math.Max(0, (rect.Height - text.Height) / 2)) : rect.TopLeft;
        using (c.PushClip(rect)) c.DrawText(text, origin);
    }
    public static void Logo(DrawingContext c, Rect rect)
    {
        using (c.PushTransform(Matrix.CreateScale(rect.Width / 64, rect.Height / 64) * Matrix.CreateTranslation(rect.X, rect.Y)))
        {
            Path(c, "M3 51 13 10 27 26 36 8 47 24 58 11 59 52 46 52 46 34 36 48 25 35 17 54Z", Ink);
            Path(c, "M23 23 34 15 29 31 40 28 29 47 31 34 20 37Z", Acid);
        }
    }
    public static void Controller(DrawingContext c, double width, bool connected)
    {
        using (c.PushTransform(Matrix.CreateScale(width / 216, width / 216)))
        {
            const string body = "M36 16 C12 23 0 104 14 117 C30 133 50 114 62 103 L154 103 C166 114 186 133 202 117 C218 103 205 23 181 16 C150 6 65 6 36 16Z";
            using (c.PushTransform(Matrix.CreateTranslation(5, 7))) Path(c, body, Ink);
            Path(c, body, Paper, 3);
            Rect(c, Ink, new(67, 20, 82, 36));
            foreach (var x in new[] { 62, 154 }) c.DrawLine(new Pen(connected ? Hot : Ink, 3), new(x, 23), new(x, 48));
            Rect(c, Ink, new(35, 36, 9, 30)); Rect(c, Ink, new(25, 46, 29, 9));
            foreach (var x in new[] { 80, 136 }) { c.DrawEllipse(Ink, null, new Point(x, 76), 12, 12); c.DrawEllipse(null, new Pen(Ink, 3), new Point(x, 76), 15, 15); }
            foreach (var p in new[] { new Point(178, 38), new Point(190, 51), new Point(178, 64), new Point(166, 51) }) c.DrawEllipse(Ink, null, p, 5, 5);
            for (int i = 0; i < 5; i++)
            {
                c.DrawLine(new Pen(Ink, 1.2), new(20 + i * 4, 88), new(16 + i * 4, 107));
                c.DrawLine(new Pen(Ink, 1.2), new(174 + i * 4, 88), new(178 + i * 4, 107));
            }
        }
    }
}

sealed class BrandHeader : Control
{
    public BrandHeader() { Height = 106; AutomationProperties.SetName(this, "MadSense"); }
    public override void Render(DrawingContext c)
    {
        var w = Bounds.Width;
        Brand.Rect(c, Brand.Paper, new(Bounds.Size));
        using (c.PushTransform(Matrix.CreateRotation(-Math.PI / 30) * Matrix.CreateTranslation(5, 10)))
        { Brand.Rect(c, Brand.Hot, new(1, 1, 68, 68)); Brand.Logo(c, new(5, 4, 58, 58)); }
        var right = w > 650 ? w - 161 : w;
        Brand.Text(c, "MADSENSE", new(96, 0, right - 96, 70), Brand.Ink, 57, 40, true);
        Brand.Text(c, L.Text("Ui.HeaderTagline"), new(100, 68, right - 100, 22), Brand.Ink, 10, 7);
        c.DrawLine(new Pen(Brand.Ink, 3), new(0, 94), new(w, 94));
        if (w <= 650) return;
        var x = w - 146;
        Brand.Rect(c, Brand.Ink, new(x, 12, 146, 76));
        Brand.Text(c, "BLUETOOTH / LINUX", new(x + 11, 20, 125, 18), Brand.Paper, 10.5);
        Brand.Rect(c, Brand.Acid, new(x + 10, 42, 126, 25));
        Brand.Text(c, L.Text("Ui.Version", "0.5.0"), new(x + 16, 45, 114, 22), Brand.Ink, 13);
        Brand.Rect(c, Brand.Paper, new(x + 10, 72, 126, 11));
        for (var i = 0; i < 12; i++) Brand.Rect(c, Brand.Ink, new(x + 15 + i * 10, 74, i % 3 == 0 ? 6 : 2, 6));
    }
}

sealed class BrandHero : Control
{
    public bool Paused { get; set; } = true;
    public bool Ready { get; set; }
    public bool Xbox { get; set; }
    public string Headline => L.Text(Paused ? "Ui.HeroPaused" : Ready ? "Ui.HeroReady" : "Ui.HeroPressPs");
    public string Subheadline => L.Text(Paused ? "Ui.HeroReleased" : Ready ? "Ui.HeroPlay" : "Ui.HeroOnController");
    public string Badge => L.Text(Paused ? "Ui.BadgePaused" : Ready ? "Ui.BadgeReady" : "Ui.BadgeWaiting");
    public void Refresh()
    {
        AutomationProperties.SetName(this, string.Join(". ", Headline, Subheadline, Badge));
        InvalidateVisual();
    }
    public override void Render(DrawingContext c)
    {
        var w = Bounds.Width; var h = Bounds.Height;
        Brand.Rect(c, Brand.Ink, new(Bounds.Size));
        using (c.PushClip(new Rect(Bounds.Size)))
        {
            Brand.Path(c, FormattableString.Invariant($"M{w * .62} -5 L{w} -5 {w} {h + 2} {w * .49} {h + 2}Z"), Brand.Hot);
            for (var y = 9; y < h; y += 11)
                for (var x = w * .62; x < w; x += 11) c.DrawEllipse(new SolidColorBrush(Color.FromArgb(75, 24, 24, 21)), null, new Point(x, y), 1.5, 1.5);
            for (var i = 0; i < 5; i++) c.DrawLine(new Pen(Brand.Paper, 2.2), new(w * .65 + i * 5, 45 + i * 22), new(w * .9 + i * 5, 30 + i * 22));
            var width = Math.Clamp(w * .34, 220, 290);
            using (c.PushTransform(Matrix.CreateTranslation(-width / 2, -width * .3) * Matrix.CreateRotation(-11 * Math.PI / 180) * Matrix.CreateTranslation(w * .76, h * .51)))
                Brand.Controller(c, width, Ready);
            var textWidth = w * .51 - 34;
            Brand.Text(c, L.Text(Xbox ? "Ui.HeroXbox" : "Ui.HeroDualSense"), new(23, 20, textWidth, 20), Brand.Paper, 10.5);
            Brand.Text(c, Headline, new(20, 45, textWidth, 68), Brand.Paper, 55, 28, true);
            Brand.Text(c, Subheadline, new(23, 112, textWidth, 45), Brand.Paper, 35, 22, true);
            var yBadge = Math.Min(h - 40, Math.Max(164, h - 45));
            var badgeWidth = Math.Min(textWidth - 12, 270);
            Brand.Path(c, FormattableString.Invariant($"M25 {yBadge} L{31 + badgeWidth} {yBadge - 3} {25 + badgeWidth} {yBadge + 28} 22 {yBadge + 30}Z"), Ready ? Brand.Acid : Brand.Paper);
            Brand.Text(c, Badge, new(37, yBadge + 7, badgeWidth - 38, 18), Brand.Ink, 10.5);
            Brand.Rect(c, Brand.Ink, new(25 + badgeWidth - 18, yBadge + 10, 6, 6));
        }
    }
}

// Inherits real Button keyboard, pointer, focus and accessibility behavior.
sealed class BrandButton : Button
{
    public bool Selected { get; set; }
    public bool Accent { get; set; }
    public BrandButton(string text)
    {
        Content = text; MinHeight = 42; FontFamily = Brand.Mono; FontSize = 12;
        Template = new Avalonia.Controls.Templates.FuncControlTemplate<BrandButton>((_, _) => new Panel());
        HorizontalContentAlignment = Avalonia.Layout.HorizontalAlignment.Center;
    }
    public override void Render(DrawingContext c)
    {
        var face = new Rect(1, 1, Math.Max(0, Bounds.Width - 6), Math.Max(0, Bounds.Height - 6));
        Brand.Rect(c, Brand.Ink, face.Translate(new Vector(4, 4)));
        if (IsPressed) face = face.Translate(new Vector(2, 2));
        Brand.Rect(c, Selected ? Brand.Acid : Accent ? Brand.Hot : IsPointerOver ? Brushes.White : Brand.Paper, face, 2);
        var label = Content?.ToString() ?? "";
        Brand.Text(c, label, face.Deflate(new Thickness(12, 8)), IsEnabled ? Brand.Ink : Brand.Muted, 12, centered: true);
        if (IsKeyboardFocusWithin) c.DrawRectangle(null, new Pen(Brand.Ink, 1, DashStyle.Dash), face.Deflate(5));
    }
}
