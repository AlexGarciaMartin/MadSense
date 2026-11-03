using System.Text.Json;
using DualSenseClient.Controllers.DualSense.Output;
using DualSenseClient.Controllers.DualSense.Triggers;

namespace MadSense.Platform.Tests;

public sealed class NeutralReportTests
{
    // The helper's fixed zero-output packets are checked against the shared
    // encoder used by DualSenseDevice.ResetOutputs, including all sequence tags.
    // This validates framing, flags and CRC; it cannot prove physical timing.
    public static IEnumerable<TestCaseData> Reports()
    {
        using var document = JsonDocument.Parse(File.ReadAllText(
            Path.Combine(TestContext.CurrentContext.TestDirectory, "neutral-reports.json")));
        foreach (var item in document.RootElement.EnumerateArray())
            yield return new TestCaseData(item.GetProperty("kind").GetString()!,
                item.GetProperty("sequence").GetByte(), item.GetProperty("hex").GetString()!);
    }

    [TestCaseSource(nameof(Reports))]
    public void EmergencyNeutralFrame_MatchesSharedEncoder(string kind, byte sequence, string expected)
    {
        var state = kind switch
        {
            "rumble_legacy" => new SetStateData
            {
                ValidFlag0 = ValidFlags.UseRumbleNotHaptics | ValidFlags.EnableRumbleEmulation,
            },
            "rumble_v2" => new SetStateData
            {
                ValidFlag0 = ValidFlags.UseRumbleNotHaptics,
                ValidFlag2 = ValidFlags.EnableImprovedRumbleEmu,
            },
            "triggers_off" => new SetStateData
            {
                ValidFlag0 = ValidFlags.AllowLeftTriggerFfb | ValidFlags.AllowRightTriggerFfb,
                L2TriggerEffect = TriggerEffectBuilder.Off(),
                R2TriggerEffect = TriggerEffectBuilder.Off(),
            },
            _ => throw new ArgumentException("Unknown neutral fixture")
        };
        Assert.That(OutputReport.ForBluetooth(state, sequence).Raw,
            Is.EqualTo(Convert.FromHexString(expected)));
    }
}
