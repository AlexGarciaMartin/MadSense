using System.Text.Json;
using DualSenseClient.Controllers.DualSense.Input;

namespace MadSense.Mac.Tests;

[NonParallelizable]
public class BatteryStatusTests
{
    [Test]
    public void MissingPhysicalInputHasUnknownChargingState() =>
        Assert.That(BridgeSession.ChargingFromReport(null), Is.Null);

    [TestCase(0x00, false)]
    [TestCase(0x0a, false)]
    [TestCase(0x10, true)]
    [TestCase(0x1a, true)]
    [TestCase(0x20, true)]
    [TestCase(0xa5, false)]
    [TestCase(0xb5, false)]
    [TestCase(0xf5, false)]
    public void ChargingUsesReportedPowerStateInsteadOfBatteryLevel(int rawBattery, bool expected)
    {
        byte[] bluetooth = new byte[78];
        bluetooth[0] = 0x31;
        bluetooth[54] = (byte)rawBattery;
        var report = new InputReport(bluetooth, 2);
        Assert.That(BridgeSession.ChargingFromReport(report), Is.EqualTo(expected));
    }

    [TestCase(null)]
    [TestCase(false)]
    [TestCase(true)]
    public void StatusJsonPreservesUnknownAndKnownChargingStates(bool? charging)
    {
        TextWriter previous = Console.Out;
        using var output = new StringWriter();
        try
        {
            Console.SetOut(output);
            Program.WriteStatus("test", "Battery status.", charging: charging);
        }
        finally { Console.SetOut(previous); }

        using var json = JsonDocument.Parse(output.ToString());
        var value = json.RootElement.GetProperty("charging");
        if (charging is null) Assert.That(value.ValueKind, Is.EqualTo(JsonValueKind.Null));
        else Assert.That(value.GetBoolean(), Is.EqualTo(charging));
        var activity = json.RootElement.GetProperty("activity");
        foreach (string name in new[] { "haptics", "triggers", "audio", "rumble" })
            Assert.That(activity.GetProperty(name).GetBoolean(), Is.False, "Missing session has no delivered activity.");
    }
}
