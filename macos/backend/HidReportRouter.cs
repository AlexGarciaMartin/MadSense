using DualSenseClient.Emulation.Core;
using DualSenseClient.Emulation.Devices.DualSense;

namespace MadSense.Mac;

/// <summary>Converts IOKit report types to the existing, tested DualSense persona.
/// IOKit uses zero-based types and includes report IDs in numbered reports.</summary>
internal sealed class HidReportRouter(DualSenseDevice persona)
{
    public const int Success = 0;
    public const int Unsupported = unchecked((int)0xe00002c7);
    public const int BadArgument = unchecked((int)0xe00002c2);
    public const int Error = unchecked((int)0xe00002bc);

    public int Get(uint type, uint id, Span<byte> destination, out int count)
    {
        count = 0;
        if (type is not (0 or 2) || id > 255) return Unsupported;
        // Persona builders may require a full report buffer even for a short host read.
        Span<byte> full = stackalloc byte[4096];
        int result = persona.HandleControlRequest(new UsbSetupPacket
        {
            RequestType = 0xa1, Request = 1, Value = (ushort)(((type + 1) << 8) | id),
            Index = 3, Length = (ushort)full.Length
        }, [], full);
        if (result < 0) return Unsupported;
        if (result > 0 && full[0] != id) return Unsupported;
        count = Math.Min(result, destination.Length);
        full[..count].CopyTo(destination);
        return Success;
    }

    public int Set(uint type, uint id, ReadOnlySpan<byte> report)
    {
        if (type is not (1 or 2) || id > 255) return Unsupported;
        if (report.Length is < 1 or > 4096 || report[0] != id) return BadArgument;
        if (type == 1)
        {
            if (!DualSenseOutputReport.TryParse(report, out _)) return BadArgument;
            persona.OnOutputReport(report);
            return Success;
        }
        // The USB class handler expects report ID in wValue, outside the payload.
        int result = persona.HandleControlRequest(new UsbSetupPacket
        {
            RequestType = 0x21, Request = 9, Value = (ushort)(0x300 | id),
            Index = 3, Length = (ushort)(report.Length - 1)
        }, report[1..], []);
        return result < 0 ? Unsupported : Success;
    }
}
