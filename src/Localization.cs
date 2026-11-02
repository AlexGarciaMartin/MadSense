using System.Globalization;
using System.Text.Json;

namespace MadSense;

internal sealed record LanguageOption(string Code, string NativeName);

/// <summary>Small embedded catalogs: no network, resource files or controller work on a language change.</summary>
internal static class L
{
    public static IReadOnlyList<LanguageOption> Languages { get; } = Array.AsReadOnly(new[]
    {
        new LanguageOption("en", "English"), new LanguageOption("es", "Español"),
        new LanguageOption("fr", "Français"), new LanguageOption("de", "Deutsch"),
        new LanguageOption("it", "Italiano"), new LanguageOption("pt-BR", "Português Brasileiro")
    });

    sealed record Selection(string Preference, string Code, CultureInfo Culture);
    static readonly IReadOnlyDictionary<string, IReadOnlyDictionary<string, string>> catalogs = LoadCatalogs();
    // Capture the user's UI culture at startup, not the OS installation language.
    static readonly CultureInfo systemCulture = CultureInfo.CurrentUICulture;
    static Selection current = Select("auto", systemCulture);
    public static event Action? Changed;
    public static string CurrentLanguage => Volatile.Read(ref current).Code;
    public static string Preference => Volatile.Read(ref current).Preference;
    public static CultureInfo Culture => Volatile.Read(ref current).Culture;

    public static bool IsSupportedPreference(string? preference) => preference == "auto" || Languages.Any(l => l.Code == preference);

    public static string ResolveLanguage(string? preference, CultureInfo systemCulture)
    {
        var name = string.IsNullOrWhiteSpace(preference) || preference.Equals("auto", StringComparison.OrdinalIgnoreCase)
            ? systemCulture.Name : preference;
        if (name.Equals("pt", StringComparison.OrdinalIgnoreCase) || name.StartsWith("pt-", StringComparison.OrdinalIgnoreCase)) return "pt-BR";
        var primary = name.Split('-')[0];
        return Languages.FirstOrDefault(l => l.Code.Equals(primary, StringComparison.OrdinalIgnoreCase))?.Code ?? "en";
    }

    static Selection Select(string preference, CultureInfo systemCulture)
    {
        var code = ResolveLanguage(preference, systemCulture);
        return new Selection(preference, code, CultureInfo.GetCultureInfo(code));
    }

    public static void Use(string preference)
    {
        var normalized = IsSupportedPreference(preference) ? preference : ResolveLanguage(preference, systemCulture);
        var selection = Select(normalized, systemCulture);
        var previous = Interlocked.Exchange(ref current, selection);
        if (previous != selection) Changed?.Invoke();
    }

    public static UiMessage Message(string key, params object?[] args) => new(key, args);

    public static string Text(string key, params object?[] args)
    {
        var selected = Volatile.Read(ref current);
        var catalog = catalogs[selected.Code];
        if (!catalog.TryGetValue(key, out var value) && !catalogs["en"].TryGetValue(key, out value)) return key;
        if (args.Length == 0) return value;
        var formattedArgs = args.Select(a => a is UiMessage message ? message.Render() : a).ToArray();
        return string.Format(selected.Culture, value, formattedArgs);
    }

    internal static IReadOnlyDictionary<string, string> GetCatalog(string language) => catalogs[language];

    static IReadOnlyDictionary<string, IReadOnlyDictionary<string, string>> LoadCatalogs()
    {
        var assembly = typeof(L).Assembly;
        var result = new Dictionary<string, IReadOnlyDictionary<string, string>>(StringComparer.Ordinal);
        foreach (var language in Languages)
        {
            var entries = new Dictionary<string, string>(StringComparer.Ordinal);
            var suffix = "." + language.Code + ".json";
            foreach (var name in assembly.GetManifestResourceNames().Where(n => n.StartsWith("MadSense.Localization.", StringComparison.Ordinal) && n.EndsWith(suffix, StringComparison.Ordinal)))
            {
                using var stream = assembly.GetManifestResourceStream(name)!;
                var part = JsonSerializer.Deserialize<Dictionary<string, string>>(stream) ?? throw new InvalidDataException("Empty language resource: " + name);
                foreach (var entry in part)
                    if (!entries.TryAdd(entry.Key, entry.Value)) throw new InvalidDataException("Duplicate language key: " + entry.Key);
            }
            result.Add(language.Code, new System.Collections.ObjectModel.ReadOnlyDictionary<string, string>(entries));
        }
        return result;
    }
}

/// <summary>Retains message identity so status and activity change language without reconnecting.</summary>
internal sealed class UiMessage
{
    readonly string? key;
    readonly string? raw;
    readonly object?[] args;
    internal UiMessage(string key, object?[] args) { this.key = key; this.args = (object?[])args.Clone(); }
    UiMessage(string raw) { this.raw = raw; args = []; }
    public static UiMessage Raw(string text) => new(text);
    public static UiMessage FromException(Exception exception) => exception is LocalizedException localized ? localized.LocalizedMessage : Raw(exception.Message);
    public string Render() => key is null ? raw ?? "" : L.Text(key, args);
    public override string ToString() => Render();
}

internal sealed class LocalizedException(UiMessage message) : InvalidOperationException
{
    public UiMessage LocalizedMessage { get; } = message;
    public override string Message => LocalizedMessage.Render();
}
