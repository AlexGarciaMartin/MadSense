using System.Diagnostics;
using System.Text.Json;

namespace DualSenseClient.Tests.Emulation.Integration;

// Explicit lab-only benchmark: never picked by the normal validation filter.
// The bridge under observation must already be running, with no game open.
[TestFixture, NonParallelizable]
public class BridgeComparisonAudioTests
{
    [Test]
    public void ObserveHostTransportWhileRenderingNativeFourChannelAudio()
    {
        var exe = Environment.GetEnvironmentVariable("MADSENSE_PROBE");
        var output = Environment.GetEnvironmentVariable("COMPARISON_OUTPUT");
        if (string.IsNullOrEmpty(exe) || string.IsNullOrEmpty(output)) { Assert.Ignore("Opt-in comparison lab only."); return; }
        var endpoints = WasapiAudioEndpoint.CaptureActiveEndpoints().RenderEndpoints
            .Where(e => e.FriendlyName.Contains("DualSense", StringComparison.OrdinalIgnoreCase) || e.FriendlyName.Contains("Wireless Controller", StringComparison.OrdinalIgnoreCase)).ToArray();
        Assert.That(endpoints.Length, Is.EqualTo(1), "Exactly one controller audio endpoint must be active.");
        using var render = WasapiAudioEndpoint.OpenRenderEndpoint(endpoints[0].Id, new WasapiAudioFormat(4, 48000, 16, false));
        Assert.That(render.Format.Channels, Is.EqualTo(4), "Stereo fallback cannot test the rear native haptic channels.");
        File.WriteAllText(output + ".audio.json", JsonSerializer.Serialize(new { Endpoint = endpoints[0], Format = render.Format, ToneHz = 120, Amplitude = 0.08, ToneSeconds = 17, Meaning = "Synthetic four-channel source, not game or perceptual latency." }, new JsonSerializerOptions { WriteIndented = true }));
        var start = new ProcessStartInfo(exe) { UseShellExecute = false, CreateNoWindow = true };
        foreach (var arg in new[] { "--seconds", "15", "--echo", "--trigger-probe", "--label", Environment.GetEnvironmentVariable("COMPARISON_LABEL") ?? "audio", "--out", output }) start.ArgumentList.Add(arg);
        using var probe = Process.Start(start)!;
        try
        {
            render.PlayTone(120, TimeSpan.FromSeconds(17), 0.08);
            Assert.That(probe.WaitForExit(10000), Is.True);
            Assert.That(probe.ExitCode, Is.Zero);
        }
        finally { if (!probe.HasExited) probe.Kill(); }
    }
}
