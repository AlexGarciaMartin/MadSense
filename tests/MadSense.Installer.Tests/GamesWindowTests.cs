using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using MadSense.Games;

namespace MadSense.Installer.Tests;

[TestFixture, Apartment(ApartmentState.STA), NonParallelizable]
public class GamesWindowTests
{
    const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
    string root = "";
    GamesService service = null!;
    GamesWindow window = null!;
    static T Get<T>(object target, string field) => (T)target.GetType().GetField(field, Private)!.GetValue(target)!;
    static void Set(object target, string field, object value) => target.GetType().GetField(field, Private)!.SetValue(target, value);
    void Call(string method, params object[] arguments) => typeof(GamesWindow).GetMethod(method, Private)!.Invoke(window, arguments);

    [SetUp]
    public void SetUp()
    {
        root = Path.Combine(Path.GetTempPath(), "madsense-ui-tests-" + Guid.NewGuid().ToString("N"));
        service = new GamesService(root, watchHelpers: false);
        window = new GamesWindow(service);
        _ = window.Handle;
        // Do not Show(): no scanning, HTTP, hardware, or process launches in these tests.
        Set(window, "games", new List<LibraryGame>
        {
            new("dirt", "DiRT Rally 2.0", "Test", "690790", root, null),
            new("alpha", "Alpha", "Test", "not-in-catalog", root, null),
            new("wreckfest", "Wreckfest 2", "Test", "1203190", root, null),
        });
        Call("Populate");
    }

    [TearDown]
    public void TearDown()
    {
        window.Dispose(); service.Dispose();
        if (Directory.Exists(root)) Directory.Delete(root, true);
    }

    [Test]
    public void OperationNoticeSurvivesListRefreshAndSearching()
    {
        Call("Notify", UiMessage.Raw("Install failed: keep this result visible."));
        Call("Populate");
        Get<TextBox>(window, "search").Text = "Wreckfest";
        Assert.That(Get<Label>(window, "notice").Text, Is.EqualTo("Install failed: keep this result visible."));
        Assert.That(Get<object>(window, "noticeMessage"), Is.Not.Null);
    }

    [Test]
    public void SecondModSelectionChangesActionTargetAndSurvivesRefresh()
    {
        Get<TextBox>(window, "search").Text = "DiRT";
        var selector = Get<FlowLayoutPanel>(window, "detail").Controls.OfType<ComboBox>().Single();
        Assert.That(selector.Items.Count, Is.EqualTo(2));
        selector.SelectedIndex = 1;
        Application.DoEvents();
        var chosen = Get<ModEntry>(window, "selectedMod");
        Assert.That(chosen.Id, Is.EqualTo("dr2ds"));
        Assert.That(Get<Uri>(window, "pageUri"), Is.EqualTo(chosen.PageUri));
        Get<TextBox>(window, "search").Text = "Wreckfest";
        Get<TextBox>(window, "search").Text = "DiRT";
        Assert.That(Get<ModEntry>(window, "selectedMod").Id, Is.EqualTo("dr2ds"));
    }

    [Test]
    public void CompatibilityArrivingDoesNotReorderGamesAndFiltersUseIndependentSupport()
    {
        var entries = Get<ListBox>(window, "entries");
        var before = entries.Items.Cast<object>().Select(x => x.ToString()).ToArray();
        var support = Get<Dictionary<string, WikiSupport?>>(window, "support");
        support["wreckfest"] = new WikiSupport { Fetched = DateTimeOffset.Now, Fields = new() { ["dualsense haptics support"] = "true" } };
        Call("Populate");
        Assert.That(entries.Items.Cast<object>().Select(x => x.ToString()), Is.EqualTo(before));
        Set(window, "activeFilter", "Native"); Call("Populate");
        Assert.That(entries.Items.Cast<object>().Select(x => x.ToString()), Is.EqualTo(new[] { "Wreckfest 2" }));
        Set(window, "activeFilter", "Available"); Call("Populate");
        Assert.That(entries.Items.Cast<object>().Select(x => x.ToString()), Is.EqualTo(new[] { "DiRT Rally 2.0", "Wreckfest 2" }));
    }

    [Test]
    public void FilterSwitchRepaintsBothPreviousAndNewSelection()
    {
        var buttons = Get<Dictionary<string, MainWindow.RoundedButton>>(window, "filterButtons");
        var invalidated = new HashSet<string>();
        foreach (var (key, button) in buttons)
        {
            _ = button.Handle;
            button.Invalidated += (_, _) => invalidated.Add(key);
        }
        string previous = "All";
        foreach (var next in new[] { "Native", "Available", "Installed", "All", "Available", "Native" })
        {
            invalidated.Clear();
            // Invoke the real Click handler without displaying the form or synthetic mouse events.
            // The old button must repaint even when no hover event reaches it afterwards.
            typeof(Button).GetMethod("OnClick", Private)!.Invoke(buttons[next], new object[] { EventArgs.Empty });
            Assert.Multiple(() =>
            {
                Assert.That(buttons.Where(pair => pair.Value.Selected).Select(pair => pair.Key), Is.EqualTo(new[] { next }));
                Assert.That(invalidated, Does.Contain(previous), "the old green selection must repaint");
                Assert.That(invalidated, Does.Contain(next), "the new selection must repaint");
            });
            previous = next;
        }
    }

    [Test]
    public void SelectionInvalidatesOnlyWhenItsVisualStateChanges()
    {
        using var button = new MainWindow.RoundedButton();
        _ = button.Handle;
        int invalidations = 0;
        button.Invalidated += (_, _) => invalidations++;
        button.Selected = true;
        Assert.That(invalidations, Is.GreaterThan(0));
        int afterSelection = invalidations;
        button.Selected = true;
        Assert.That(invalidations, Is.EqualTo(afterSelection), "unchanged selection needs no extra repaint");
        button.Selected = false;
        Assert.That(invalidations, Is.GreaterThan(afterSelection), "removing the green fill needs a repaint too");
    }
}
