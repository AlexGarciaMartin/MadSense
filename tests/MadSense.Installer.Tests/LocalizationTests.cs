using System.Globalization;
using System.Text;
using System.Text.RegularExpressions;
using MadSense;

namespace MadSense.Installer.Tests;

[TestFixture, NonParallelizable]
public sealed class LocalizationTests
{
    static readonly string[] LanguageCodes = ["en", "es", "fr", "de", "it", "pt-BR"];
    string originalPreference = "auto";

    [SetUp]
    public void RememberLanguage() => originalPreference = L.Preference;

    [TearDown]
    public void RestoreLanguage() => L.Use(originalPreference);

    [Test]
    public void LanguageMenuHasTheSixSupportedLanguagesAndNativeNames()
    {
        Assert.That(L.Languages.Select(language => language.Code), Is.EquivalentTo(LanguageCodes));
        Assert.That(L.Languages.Select(language => language.NativeName), Is.Unique);
        Assert.That(L.Languages.All(language => !string.IsNullOrWhiteSpace(language.NativeName)), Is.True);
    }

    [TestCaseSource(nameof(LanguageCodes))]
    public void EveryCatalogHasTheCompleteEnglishKeySet(string code)
    {
        var english = L.GetCatalog("en");
        var catalog = L.GetCatalog(code);
        Assert.That(english, Is.Not.Empty, "Embedded resources must actually be present in the built assembly.");
        Assert.That(catalog.Keys, Is.EquivalentTo(english.Keys), $"Missing or unexpected keys in {code}");
        Assert.That(catalog.Where(entry => string.IsNullOrWhiteSpace(entry.Value)).Select(entry => entry.Key),
            Is.Empty, $"Empty translations in {code}");
    }

    [TestCaseSource(nameof(LanguageCodes))]
    public void EveryTranslationHasValidFormatsAndKeepsAllArguments(string code)
    {
        var english = L.GetCatalog("en");
        foreach (var entry in L.GetCatalog(code))
        {
            var baseline = FormatArguments(english[entry.Key], CultureInfo.GetCultureInfo("en"));
            var translated = FormatArguments(entry.Value, CultureInfo.GetCultureInfo(code));
            Assert.That(translated, Is.EqualTo(baseline), $"Changed placeholder arguments: {code}/{entry.Key}");
        }
    }

    // Formatting with real IFormattable arguments exercises alignment, numeric format suffixes,
    // escaped braces and repeated/reordered placeholders without assuming a particular language.
    static string[] FormatArguments(string text, CultureInfo culture)
    {
        var format = CompositeFormat.Parse(text);
        var arguments = Enumerable.Range(0, format.MinimumArgumentCount)
            .Select(index => (object)new ArgumentSentinel(index)).ToArray();
        var rendered = string.Format(culture, format, arguments);
        return Regex.Matches(rendered, @"\[\[ARG:(\d+)\]\]")
            .Select(match => match.Groups[1].Value).OrderBy(value => value, StringComparer.Ordinal).ToArray();
    }

    sealed record ArgumentSentinel(int Index) : IFormattable
    {
        public string ToString(string? format, IFormatProvider? formatProvider) => $"[[ARG:{Index}]]";
        public override string ToString() => ToString(null, null);
    }

    [TestCase("auto", "en-GB", "en")]
    [TestCase("auto", "es-MX", "es")]
    [TestCase(null, "fr-CA", "fr")]
    [TestCase("", "de-AT", "de")]
    [TestCase("auto", "it-CH", "it")]
    [TestCase("auto", "pt-PT", "pt-BR")]
    [TestCase("auto", "pt-BR", "pt-BR")]
    [TestCase("auto", "ja-JP", "en")]
    [TestCase("auto", "", "en")]
    [TestCase("fr", "es-ES", "fr")]
    [TestCase("ES-mx", "en-US", "es")]
    [TestCase("PT-pt", "en-US", "pt-BR")]
    [TestCase("unknown", "es-ES", "en")]
    public void LanguageResolutionHandlesRegionsAndFallsBackToEnglish(string? preference, string systemCulture, string expected)
    {
        Assert.That(L.ResolveLanguage(preference, CultureInfo.GetCultureInfo(systemCulture)), Is.EqualTo(expected));
    }

    [Test]
    public void ExplicitLanguageIsRetainedAndAutomaticRemainsAutomatic()
    {
        L.Use("pt-BR");
        Assert.That(L.Preference, Is.EqualTo("pt-BR"));
        Assert.That(L.CurrentLanguage, Is.EqualTo("pt-BR"));
        L.Use("auto");
        Assert.That(L.Preference, Is.EqualTo("auto"));
        Assert.That(LanguageCodes, Does.Contain(L.CurrentLanguage));
    }

    [Test]
    public void SelectingTheActiveLanguageDoesNotRequestAnotherUiRefresh()
    {
        L.Use("en");
        var notifications = 0;
        void OnChanged() => notifications++;
        L.Changed += OnChanged;
        try
        {
            L.Use("es");
            L.Use("es");
            Assert.That(notifications, Is.EqualTo(1));
        }
        finally { L.Changed -= OnChanged; }
    }

    [Test]
    public void ExistingStatusAndLocalizedExceptionRenderInTheNewLanguage()
    {
        L.Use("en");
        var message = L.Message("Ui.SpeakerHigh");
        var exception = new LocalizedException(message);
        var statusFromException = UiMessage.FromException(exception);
        var english = message.Render();

        L.Use("es");
        var spanish = L.GetCatalog("es")["Ui.SpeakerHigh"];
        Assert.Multiple(() =>
        {
            Assert.That(spanish, Is.Not.EqualTo(english));
            Assert.That(message.Render(), Is.EqualTo(spanish));
            Assert.That(exception.Message, Is.EqualTo(spanish));
            Assert.That(statusFromException.Render(), Is.EqualTo(spanish));
        });
    }

    [Test]
    public void NestedMessagesAndCapturedArgumentsSurviveLanguageChanges()
    {
        object?[] arguments = [L.Message("Ui.SpeakerHigh")];
        var message = L.Message("Ui.SpeakerLevel", arguments);
        arguments[0] = "caller changed its array";
        foreach (var language in LanguageCodes)
        {
            L.Use(language);
            var catalog = L.GetCatalog(language);
            var expected = string.Format(CultureInfo.GetCultureInfo(language),
                catalog["Ui.SpeakerLevel"], catalog["Ui.SpeakerHigh"]);
            Assert.That(message.Render(), Is.EqualTo(expected));
        }
    }

    [Test]
    public void RawDiagnosticsRemainReadableAcrossLanguageChanges()
    {
        const string diagnostic = "HID error 0x05: C:\\Controller\\device.json {0}";
        var raw = UiMessage.Raw(diagnostic);
        var exceptionMessage = UiMessage.FromException(new IOException(diagnostic));
        foreach (var language in LanguageCodes)
        {
            L.Use(language);
            Assert.That(raw.Render(), Is.EqualTo(diagnostic));
            Assert.That(exceptionMessage.Render(), Is.EqualTo(diagnostic));
        }
    }

    [Test]
    public void MissingKeyIsVisibleInsteadOfThrowing()
    {
        L.Use("fr");
        Assert.That(L.Text("Tests.Nonexistent.Key", 42), Is.EqualTo("Tests.Nonexistent.Key"));
    }
}
