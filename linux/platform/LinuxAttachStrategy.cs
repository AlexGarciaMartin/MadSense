using System.Globalization;
using DualSenseClient.Emulation.Windows;

namespace MadSense.Platform;

/// <summary>Attaches only through the live physical lease; never runs a privileged CLI.</summary>
public sealed class LinuxAttachStrategy : IUsbIpAttachStrategy
{
    readonly LinuxPhysicalSession session;
    readonly LinuxBrokerClient broker;
    readonly object gate = new();
    readonly HashSet<string> owned = new(StringComparer.Ordinal);
    internal LinuxAttachStrategy(LinuxPhysicalSession session, LinuxBrokerClient broker)
    { this.session = session; this.broker = broker; }

    public bool TryAttach(string host, int port, string busId, out string? error)
    {
        lock (gate)
        {
            if (!session.IsolationVerified) { error = "Physical isolation must be verified before USB/IP attachment."; return false; }
            if (host != "127.0.0.1" || port is < 1024 or > 65535 || !ValidBusId(busId))
            { error = "Only a local MadSense USB/IP export with a valid port and bus id can be attached."; return false; }
            if (owned.Contains(busId)) { error = "This USB/IP export is already attached by the session."; return false; }
            try
            {
                broker.RequestAsync("attach", new() { ["host"] = host, ["port"] = port, ["busId"] = busId },
                    deadline: TimeSpan.FromSeconds(20)).GetAwaiter().GetResult();
                if (!session.IsolationVerified) { error = "Physical isolation ended during USB/IP attachment."; return false; }
                owned.Add(busId); error = null; return true;
            }
            catch (Exception failure) { error = failure.Message; return false; }
        }
    }

    public bool TryDetach(string busId, out string? error)
    {
        lock (gate)
        {
            if (!owned.Contains(busId)) { error = "This session does not own that USB/IP attachment."; return false; }
            try
            {
                broker.RequestAsync("detach", new() { ["busId"] = busId }).GetAwaiter().GetResult();
                owned.Remove(busId); error = null; return true;
            }
            catch (Exception failure) { error = failure.Message; return false; }
        }
    }

    internal static bool ValidBusId(string? busId)
    {
        if (busId is null || !busId.EndsWith("-1", StringComparison.Ordinal) || busId.Length is < 3 or > 7) return false;
        string number = busId[..^2];
        return number[0] != '0' && number.All(c => c is >= '0' and <= '9') &&
            ushort.TryParse(number, NumberStyles.None, CultureInfo.InvariantCulture, out ushort bus) && bus > 0;
    }
}
