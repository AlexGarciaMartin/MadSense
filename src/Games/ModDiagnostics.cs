namespace MadSense.Games;

/// <summary>A small immutable sample from the existing controller worker; no extra polling.</summary>
sealed record ModDiagnostics(bool ControllerConnected, bool Listening, bool PortBusy, bool Receiving, long ValidPackets, long RejectedPackets)
{
    public static readonly ModDiagnostics Unavailable = new(false, false, false, false, 0, 0);
}
