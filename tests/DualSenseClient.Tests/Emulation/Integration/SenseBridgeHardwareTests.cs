using System.Diagnostics;
using System.Text.RegularExpressions;

namespace DualSenseClient.Tests.Emulation.Integration;

[TestFixture, NonParallelizable]
public class MadSenseHardwareTests
{
    [Test]
    public void WindowsXInputRumbleReachesPhysicalBluetooth()
    {
        var exe = Environment.GetEnvironmentVariable("MADSENSE_EXE");
        if (string.IsNullOrEmpty(exe)) { Assert.Ignore("Set MADSENSE_EXE to enable real controller test."); return; }
        var log = Path.Combine(Path.GetDirectoryName(exe)!, "madsense.log");
        var firstLine = File.Exists(log) ? File.ReadLines(log).Count() : 0;
        var occupied = Enumerable.Range(0, 4).Where(i => XInputRoundTripTests.XInputGetState((uint)i, out _) == 0).ToHashSet();
        Assert.That(occupied.Count, Is.LessThan(4), "Need a free XInput slot.");
        using var app = Process.Start(new ProcessStartInfo(exe, "--soak --xbox") { UseShellExecute = false, CreateNoWindow = true })!;
        uint? slot = null;
        string? LastStatus() => File.Exists(log) ? File.ReadLines(log).Skip(firstLine).LastOrDefault(x => x.Contains("TX 31:")) : null;
        try
        {
            Assert.That(XInputRoundTripTests.Wait(() =>
            {
                var fresh = Enumerable.Range(0, 4).Where(i => !occupied.Contains(i) && XInputRoundTripTests.XInputGetState((uint)i, out _) == 0).ToArray();
                if (fresh.Length != 1) return false;
                slot = (uint)fresh[0]; return true;
            }), Is.True, "Bridge must expose exactly one new Windows XInput controller.");
            Assert.That(XInputRoundTripTests.Wait(() => LastStatus() != null), Is.True);
            var initial = Regex.Match(LastStatus()!, @"TX 31:(\d+)");
            Assert.That(initial.Success, Is.True);
            int before = int.Parse(initial.Groups[1].Value);
            var vibration = new XInputRoundTripTests.Vibration { LeftMotor = 4096, RightMotor = 2048 };
            Assert.That(XInputRoundTripTests.XInputSetState(slot!.Value, ref vibration), Is.Zero);
            Assert.That(XInputRoundTripTests.Wait(() =>
            {
                var status = LastStatus();
                if (status == null) return false;
                var match = Regex.Match(status, @"TX 31:(\d+).*err:(\d+)");
                return match.Success && int.Parse(match.Groups[1].Value) > before && int.Parse(match.Groups[2].Value) == 0;
            }), Is.True, "XInput rumble must cause a successful Bluetooth state write.");
            TestContext.Out.WriteLine(LastStatus());
        }
        finally
        {
            if (slot.HasValue) { var off = new XInputRoundTripTests.Vibration(); XInputRoundTripTests.XInputSetState(slot.Value, ref off); }
            using var stop = Process.Start(new ProcessStartInfo(exe, "--stop") { UseShellExecute = false, CreateNoWindow = true })!;
            stop.WaitForExit(5000);
            Assert.That(app.WaitForExit(10000), Is.True);
            if (slot.HasValue) Assert.That(XInputRoundTripTests.Wait(() => XInputRoundTripTests.XInputGetState(slot.Value, out _) != 0), Is.True);
        }
    }

    [Test]
    public void VirtualTriggerEffectReachesPhysicalBluetooth()
    {
        var exe = Environment.GetEnvironmentVariable("MADSENSE_EXE");
        if (string.IsNullOrEmpty(exe)) { Assert.Ignore("Set MADSENSE_EXE to enable real controller test."); return; }
        var baseline = WasapiAudioEndpoint.CaptureActiveEndpoints();
        using var app = Process.Start(new ProcessStartInfo(exe, "--exercise") { UseShellExecute = false, CreateNoWindow = true })!;
        var endpoints = WasapiAudioEndpoint.WaitForNewEndpoints(baseline, TimeSpan.FromSeconds(12));
        try
        {
            Assert.That(app.WaitForExit(30000), Is.True, "Trigger test must finish in 30 seconds.");
            Assert.That(app.ExitCode, Is.Zero, "The bridge must observe an active trigger write and no Bluetooth errors.");
        }
        finally
        {
            if (!app.HasExited)
            {
                using var stop = Process.Start(new ProcessStartInfo(exe, "--stop") { UseShellExecute = false, CreateNoWindow = true })!;
                stop.WaitForExit(5000); app.WaitForExit(10000);
            }
            Assert.That(WasapiAudioEndpoint.WaitForEndpointRemoval(endpoints.NewRenderEndpoints.Select(x => x.Id).ToArray(), TimeSpan.FromSeconds(10)), Is.True);
        }
    }

    [Test]
    public void WindowsFourChannelAudioReachesPhysicalBluetooth()
    {
        var exe = Environment.GetEnvironmentVariable("MADSENSE_EXE");
        if (string.IsNullOrEmpty(exe)) { Assert.Ignore("Set MADSENSE_EXE to enable real controller test."); return; }
        var log = Path.Combine(Path.GetDirectoryName(exe)!, "madsense.log");
        var baseline = WasapiAudioEndpoint.CaptureActiveEndpoints();
        using var app = Process.Start(new ProcessStartInfo(exe, "--soak --exercise-stream") { UseShellExecute = false, CreateNoWindow = true })!;
        try
        {
            var endpoints = WasapiAudioEndpoint.WaitForNewEndpoints(baseline, TimeSpan.FromSeconds(12));
            Assert.That(endpoints.NewRenderEndpoints.Count, Is.EqualTo(1), "Exactly one new render endpoint must belong to this bridge.");
            using (var render = WasapiAudioEndpoint.OpenRenderEndpoint(endpoints.NewRenderEndpoints[0].Id, new WasapiAudioFormat(4, 48000, 16, false)))
            {
                render.PlayTone(120, TimeSpan.FromSeconds(6), 0.08);
            }
            Thread.Sleep(2000);
            var last = File.ReadLines(log).Last(x => x.Contains("TX 31:"));
            TestContext.Out.WriteLine(last);
            var counts = Regex.Match(last, @"32:(\d+) 35:(\d+) 36:(\d+) err:(\d+)");
            Assert.That(counts.Success, Is.True);
            Assert.That(int.Parse(counts.Groups[1].Value) + int.Parse(counts.Groups[3].Value), Is.GreaterThan(20), "Haptic reports must reach the real Bluetooth HID handle.");
            Assert.That(int.Parse(counts.Groups[2].Value) + int.Parse(counts.Groups[3].Value), Is.GreaterThan(20), "Encoded speaker reports must reach the real Bluetooth HID handle.");
            Assert.That(int.Parse(counts.Groups[4].Value), Is.Zero, "No short or failed Bluetooth writes.");
            var native = Regex.Match(last, @"hápticos:(\d+)");
            Assert.That(native.Success && int.Parse(native.Groups[1].Value) > 20, Is.True, "Native nonzero samples must reach the physical controller.");
            Assert.That(Regex.IsMatch(last, @"gatillos:[1-9]\d*"), Is.True, "Adaptive trigger state must reach Bluetooth during the audio stream.");
        }
        finally
        {
            using var stop = Process.Start(new ProcessStartInfo(exe, "--stop") { UseShellExecute = false, CreateNoWindow = true })!;
            stop.WaitForExit(5000);
            Assert.That(app.WaitForExit(10000), Is.True, "Bridge must stop and clean up gracefully.");
        }
    }
}

