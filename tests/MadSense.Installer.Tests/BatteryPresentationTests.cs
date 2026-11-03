using DualSenseClient.Controllers.DualSense.Input;
using MadSense;

namespace MadSense.Installer.Tests;

[TestFixture, NonParallelizable]
public class BatteryPresentationTests
{
    [TestCase(0x10, "Cargando")]
    [TestCase(0x11, "Cargando")]
    [TestCase(0x18, "Cargando")]
    [TestCase(0x20, "Carga completa")]
    [TestCase(0x05, "Batería 55 %")]
    [TestCase(0x0B, "Batería ? %")]
    public void ConnectionUsesPowerStateBeforePercentage(int raw, string expected)
    {
        var original = L.Preference;
        try { L.Use("es"); Assert.That(BatteryPresentation.Connection(new BatteryState((byte)raw)).Render(), Does.EndWith(expected)); }
        finally { L.Use(original); }
    }

    [TestCase(0, true, " · Cargando")]
    [TestCase(-1, true, " · Cargando")]
    [TestCase(100, true, " · Carga completa")]
    [TestCase(0, false, " · Batería 0 %")]
    [TestCase(-1, false, "")]
    public void TrayRetainsDischargingAndUnknownStates(int percentage, bool charging, string expected)
    {
        var original = L.Preference;
        try { L.Use("es"); Assert.That(BatteryPresentation.Tray(percentage, charging), Is.EqualTo(expected)); }
        finally { L.Use(original); }
    }
}
