using System.Text;
using MadSense.Mods;

namespace MadSense.Installer.Tests;

[TestFixture]
public class ModDiagnosticsTests
{
    [Test]
    public void MalformedAndIncompleteTrafficNeverReportsAConnectedMod()
    {
        using var receiver = new ModReceiver(new ModOverlay(), () => new ModPadStatus(false, 0, null, true), 0);
        int connected = 0;
        receiver.Connected += () => connected++;
        Assert.That(receiver.Accept(Encoding.UTF8.GetBytes("not JSON")), Is.False);
        Assert.That(receiver.Accept(Encoding.UTF8.GetBytes("""{"instructions":[{"type":null}]}""")), Is.False);
        Assert.That(receiver.Accept(Encoding.UTF8.GetBytes("""{"instructions":[]}""")), Is.False);
        Assert.That(receiver.Accept(Encoding.UTF8.GetBytes("""{"instructions":[{"type":1,"parameters":[0]}]}""")), Is.False);
        Assert.Multiple(() =>
        {
            Assert.That(receiver.Active, Is.False);
            Assert.That(receiver.ValidPackets, Is.Zero);
            Assert.That(receiver.Rejected, Is.EqualTo(2));
            Assert.That(connected, Is.Zero);
        });
    }

    [Test]
    public void AcceptedInstructionsReportDataEvenWithoutAConnectedController()
    {
        using var receiver = new ModReceiver(new ModOverlay(), () => new ModPadStatus(false, 0, null, true), 0);
        int connected = 0;
        receiver.Connected += () => connected++;
        Assert.That(receiver.Accept(Encoding.UTF8.GetBytes("""{"instructions":[{"type":2,"parameters":[0,200,40,0]}]}""")), Is.True);
        Assert.That(receiver.Accept(Encoding.UTF8.GetBytes("""{"instructions":[{"type":0,"parameters":[0]}]}""")), Is.True);
        Assert.Multiple(() =>
        {
            Assert.That(receiver.Active, Is.True);
            Assert.That(receiver.ValidPackets, Is.EqualTo(2));
            Assert.That(connected, Is.EqualTo(1));
        });
        receiver.Stop();
        Assert.That(receiver.Active, Is.False, "stopping clears the current session's activity");
    }
}
