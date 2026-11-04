using System.Text;
using DualSenseClient.Controllers.DualSense.Output;
using DualSenseClient.Controllers.DualSense.Triggers;
using MadSense.Mods;

namespace MadSense.Installer.Tests;

[TestFixture]
public class ModReceiverTests
{
    static ModReceiver Receiver(ModOverlay overlay) => new(overlay, () => new ModPadStatus(true, 85, "D4:2F:4B:00:00:00", true), 0);
    static void Feed(ModReceiver receiver, string json) => receiver.Handle(Encoding.UTF8.GetBytes(json));
    static byte[] Bytes(TriggerEffectBlock block) { var raw = new byte[11]; block.CopyTo(raw, 0); return raw; }
    static byte[] Raw(SetStateData state) { var raw = new byte[SetStateData.PayloadSize]; state.CopyTo(raw, 0); return raw; }

    [Test]
    public void TriggerUpdateOwnsOneTriggerAndRidesEveryReport()
    {
        var overlay = new ModOverlay();
        using var receiver = Receiver(overlay);
        // DSX v2 example packet: controller 0, right trigger, Resistance from zone 3 strength 6.
        Feed(receiver, """{"instructions":[{"type":1,"parameters":[0,2,13,3,6]}]}""");
        Assert.That(overlay.OwnsTriggers, Is.True);
        var game = new SetStateData { ValidFlag0 = ValidFlags.AllowRightTriggerFfb | ValidFlags.AllowLeftTriggerFfb, R2TriggerEffect = TriggerEffectBuilder.Feedback(0, 8), L2TriggerEffect = TriggerEffectBuilder.Feedback(0, 8) };
        var merged = overlay.Apply(game);
        Assert.Multiple(() =>
        {
            Assert.That(Bytes(merged.R2TriggerEffect), Is.EqualTo(Bytes(TriggerEffectBuilder.Feedback(3, 6))), "the mod's effect replaces the game's on the owned trigger");
            Assert.That(Bytes(merged.L2TriggerEffect), Is.EqualTo(Bytes(TriggerEffectBuilder.Feedback(0, 8))), "the other trigger stays the game's");
            Assert.That(merged.ValidFlag0.HasFlag(ValidFlags.AllowRightTriggerFfb), Is.True);
            Assert.That(Bytes(game.R2TriggerEffect), Is.EqualTo(Bytes(TriggerEffectBuilder.Feedback(0, 8))), "the caller's state is not mutated");
        });
    }

    [Test]
    public void StandaloneAndReleaseCarryOnlyTheOwnedChannels()
    {
        var overlay = new ModOverlay();
        using var receiver = Receiver(overlay);
        Feed(receiver, """{"Instructions":[{"Type":2,"Parameters":[0,249,84,40]},{"Type":6,"Parameters":[0,1]},{"Type":5,"Parameters":[0,1]}]}""");
        var alone = overlay.Standalone();
        Assert.Multiple(() =>
        {
            Assert.That(alone.ValidFlag1.HasFlag(ValidFlags.AllowLedColor), Is.True);
            Assert.That((alone.LedRed, alone.LedGreen, alone.LedBlue), Is.EqualTo(((byte)249, (byte)84, (byte)40)));
            Assert.That(alone.ValidFlag1.HasFlag(ValidFlags.AllowPlayerIndicators), Is.True);
            Assert.That(alone.PlayerLeds, Is.EqualTo((PlayerLedMask)0x0A), "player two pattern");
            Assert.That(alone.ValidFlag1.HasFlag(ValidFlags.AllowMuteLight), Is.True);
            Assert.That(alone.MuteLedMode, Is.EqualTo((byte)2), "MicLED 1 = pulse");
            Assert.That(alone.ValidFlag0.HasFlag(ValidFlags.AllowRightTriggerFfb), Is.False, "no trigger owned, no trigger bit");
        });
        var release = ModOverlay.Release();
        Assert.That(release.ValidFlag0, Is.EqualTo(ValidFlags.AllowLeftTriggerFfb | ValidFlags.AllowRightTriggerFfb));
        Assert.That(Bytes(release.R2TriggerEffect)[0], Is.EqualTo((byte)TriggerEffectType.Off));
    }

    [Test]
    public void ResetToUserSettingsReleasesEverything()
    {
        var overlay = new ModOverlay();
        using var receiver = Receiver(overlay);
        int changes = 0;
        overlay.Changed += () => changes++;
        Feed(receiver, """{"instructions":[{"type":1,"parameters":[0,1,22,2,6,8]}]}""");
        Feed(receiver, """{"instructions":[{"type":7,"parameters":[0]}]}""");
        Assert.Multiple(() =>
        {
            Assert.That(overlay.OwnsAnything, Is.False);
            Assert.That(changes, Is.EqualTo(2));
            Assert.That(Raw(overlay.Apply(new SetStateData())), Is.EqualTo(new byte[SetStateData.PayloadSize]), "nothing owned: the state passes through untouched");
        });
    }

    [TestCase(13, new[] { 3, 6 }, 0x21)]
    [TestCase(22, new[] { 2, 6, 8 }, 0x25)]
    [TestCase(17, new[] { 2, 8, 20 }, 0x26)]
    [TestCase(14, new[] { 1, 6, 4, 8 }, 0x22)]
    [TestCase(15, new[] { 1, 6, 2, 5, 10 }, 0x23)]
    [TestCase(18, new[] { 1, 6, 2, 5, 10, 3 }, 0x27)]
    [TestCase(24, new[] { 1, 8, 2, 8 }, 0x21)]
    [TestCase(0, new int[0], 0x05)]
    [TestCase(20, new int[0], 0x05)]
    public void ModesMapToThePadsEffectTypes(int mode, int[] parameters, int expectedType)
    {
        Assert.That(Bytes(DsxProtocol.Effect((DsxProtocol.Mode)mode, parameters))[0], Is.EqualTo((byte)expectedType));
    }

    [Test]
    public void OutOfRangeParametersAreClampedNotThrown()
    {
        Assert.Multiple(() =>
        {
            // Weapon with a start past its end: the pad's rule is end > start; the end is lifted, nothing throws.
            Assert.That(Bytes(DsxProtocol.Effect(DsxProtocol.Mode.Weapon, [7, 1, 99]))[0], Is.EqualTo((byte)0x25));
            Assert.That(Bytes(DsxProtocol.Effect(DsxProtocol.Mode.Resistance, [40, 40]))[0], Is.EqualTo((byte)0x21));
            Assert.That(Bytes(DsxProtocol.Effect(DsxProtocol.Mode.CustomTriggerValue, [8, 1, 2, 3, 4, 5, 6, 7]))[0], Is.EqualTo((byte)0x26), "PulseAB maps to vibration");
            Assert.That(Bytes(DsxProtocol.Effect(DsxProtocol.Mode.CustomTriggerValue, [99]))[0], Is.EqualTo((byte)0));
            Assert.That(Bytes(DsxProtocol.Effect((DsxProtocol.Mode)200, [1, 2]))[0], Is.EqualTo((byte)0x05), "unknown mode is off");
        });
    }

    [Test]
    public void MalformedPacketsAreRejectedWithoutChangingTheOverlay()
    {
        var overlay = new ModOverlay();
        using var receiver = Receiver(overlay);
        Assert.Throws<FormatException>(() => Feed(receiver, """{"hello":1}"""));
        Assert.Catch<System.Text.Json.JsonException>(() => Feed(receiver, "not json"));
        Assert.Throws<FormatException>(() => Feed(receiver, """{"instructions":[{"type":99,"parameters":[0]}]}"""));
        // Too few parameters: ignored, not an error (DSX ignores them too).
        Feed(receiver, """{"instructions":[{"type":1,"parameters":[0]}]}""");
        Assert.That(overlay.OwnsAnything, Is.False);
    }

    [Test]
    public void NamesAndStringsAreAcceptedWhereModsSendThem()
    {
        var overlay = new ModOverlay();
        using var receiver = Receiver(overlay);
        Feed(receiver, """{"instructions":[{"type":"TriggerUpdate","parameters":[0,"Left","Soft"]}]}""");
        var merged = overlay.Apply(new SetStateData());
        Assert.That(Bytes(merged.L2TriggerEffect), Is.EqualTo(Bytes(TriggerEffectBuilder.Feedback(0, 3))));
        Feed(receiver, """{"instructions":[{"type":8,"parameters":[0]}]}""");
        Feed(receiver, """{"instructions":[{"type":0,"parameters":[0]}]}""");
    }

    [Test]
    public void ListensOnLoopbackAndAnswersWithTheStatusModsExpect()
    {
        var overlay = new ModOverlay();
        var port = 40000 + Random.Shared.Next(20000);
        using var receiver = new ModReceiver(overlay, () => new ModPadStatus(true, 63, "D4:2F:4B:00:00:00", true), port);
        Assert.That(receiver.Start(), Is.True);
        using var client = new System.Net.Sockets.UdpClient();
        client.Client.ReceiveTimeout = 3000;
        var target = new System.Net.IPEndPoint(System.Net.IPAddress.Loopback, port);
        var packet = Encoding.UTF8.GetBytes("""{"instructions":[{"type":1,"parameters":[0,2,13,3,6]}]}""");
        client.Send(packet, packet.Length, target);
        var from = new System.Net.IPEndPoint(System.Net.IPAddress.Any, 0);
        var reply = Encoding.UTF8.GetString(client.Receive(ref from));
        using var json = System.Text.Json.JsonDocument.Parse(reply);
        Assert.Multiple(() =>
        {
            Assert.That(json.RootElement.GetProperty("Status").GetString(), Is.EqualTo("DSX Received UDP Instructions"));
            Assert.That(json.RootElement.GetProperty("isControllerConnected").GetBoolean(), Is.True);
            Assert.That(json.RootElement.GetProperty("BatteryLevel").GetInt32(), Is.EqualTo(63));
            Assert.That(json.RootElement.GetProperty("Devices").GetArrayLength(), Is.EqualTo(1));
            Assert.That(receiver.Active, Is.True);
            Assert.That(receiver.Packets, Is.EqualTo(1));
            Assert.That(overlay.OwnsTriggers, Is.True);
        });
        using var second = new ModReceiver(new ModOverlay(), () => new ModPadStatus(false, 0, null, true), port);
        Assert.That(second.Start(), Is.False, "the port is taken");
        Assert.That(second.PortBusy, Is.True);
    }
}
