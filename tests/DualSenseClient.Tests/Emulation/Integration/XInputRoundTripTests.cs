using System.Diagnostics;
using System.Net;
using System.Runtime.InteropServices;
using DualSenseClient.Emulation;
using DualSenseClient.Emulation.Devices.Xbox360;

namespace DualSenseClient.Tests.Emulation.Integration;

/// <summary>MadSense: verifies the Windows XInput API, beyond USB attachment/driver enumeration.</summary>
[TestFixture, NonParallelizable]
public sealed class XInputRoundTripTests
{
    [Test]
    public void WindowsXInputReadsInputAndSendsRumbleToVirtualController()
    {
        if (Environment.GetEnvironmentVariable("EMULATION_INTEGRATION") != "1")
            Assert.Ignore("Set EMULATION_INTEGRATION=1 for Windows device integration.");
        var occupied = Enumerable.Range(0, 4).Where(i => XInputGetState((uint)i, out _) == 0).ToHashSet();
        Assert.That(occupied.Count, Is.LessThan(4), "A free XInput slot is required.");
        using var server = new EmulationServer(new EmulationServerOptions { ListenEndPoint = new IPEndPoint(IPAddress.Loopback, 0) });
        server.Start();
        var pad = server.AddXbox360();
        using var rumble = new ManualResetEventSlim();
        pad.OutputReportReceived += report => { if (report.LeftMotor > 0 && report.RightMotor > 0) rumble.Set(); };
        Assert.That(server.TryAttach(pad, out var error), Is.True, error);
        uint? slot = null;
        try
        {
            Assert.That(Wait(() =>
            {
                var fresh = Enumerable.Range(0, 4).Where(i => !occupied.Contains(i) && XInputGetState((uint)i, out _) == 0).ToArray();
                if (fresh.Length != 1) return false;
                slot = (uint)fresh[0]; return true;
            }), Is.True, "Exactly one new controller must be visible through XInput.");
            pad.SetInput(new Xbox360InputState { A = true, LeftTrigger = 37, RightStickX = 1234 });
            Assert.That(Wait(() => XInputGetState(slot!.Value, out var state) == 0 &&
                (state.Gamepad.Buttons & 0x1000) != 0 && state.Gamepad.LeftTrigger == 37 && state.Gamepad.RightStickX == 1234), Is.True);
            var vibration = new Vibration { LeftMotor = 4096, RightMotor = 2048 };
            Assert.That(XInputSetState(slot!.Value, ref vibration), Is.Zero);
            Assert.That(rumble.Wait(5000), Is.True, "XInput rumble must reach the virtual USB OUT callback.");
        }
        finally
        {
            pad.SetInput(new Xbox360InputState());
            if (slot.HasValue) { var off = new Vibration(); XInputSetState(slot.Value, ref off); }
            Assert.That(server.TryDetach(pad, out var detachError), Is.True, detachError);
            if (slot.HasValue) Assert.That(Wait(() => XInputGetState(slot.Value, out _) != 0), Is.True, "XInput slot must disappear after detach.");
        }
    }

    internal static bool Wait(Func<bool> predicate)
    {
        var timer = Stopwatch.StartNew();
        do { if (predicate()) return true; Thread.Sleep(50); } while (timer.Elapsed < TimeSpan.FromSeconds(10));
        return false;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct Gamepad
    {
        public ushort Buttons;
        public byte LeftTrigger, RightTrigger;
        public short LeftStickX, LeftStickY, RightStickX, RightStickY;
    }
    [StructLayout(LayoutKind.Sequential)] internal struct State { public uint Packet; public Gamepad Gamepad; }
    [StructLayout(LayoutKind.Sequential)] internal struct Vibration { public ushort LeftMotor, RightMotor; }
    [DllImport("xinput1_4.dll")] internal static extern uint XInputGetState(uint index, out State state);
    [DllImport("xinput1_4.dll")] internal static extern uint XInputSetState(uint index, ref Vibration vibration);
}
