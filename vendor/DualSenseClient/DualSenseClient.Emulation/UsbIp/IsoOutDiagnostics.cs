using System.Globalization;
using DualSenseClient.Emulation.Core;

namespace DualSenseClient.Emulation.UsbIp;

// MadSense diagnostic-only counters. Absent in normal sessions: no clock reads,
// string formatting, allocations or diagnostic locks on the production audio path.
internal sealed class IsoOutDiagnostics(TimeProvider clock, IEmulationLogger logger)
{
    readonly object gate = new();
    bool started;
    long origin, previousDelivery, submissions, submittedPackets, delivered;
    int maxQueued, maxPackets;
    long sentinelFrames, zeroFrames;
    uint lastStartFrame;
    double totalAge, maxAge, maxGap;

    public long RecordSubmit(int packets, int queued, uint startFrame)
    {
        long now = clock.GetTimestamp();
        lock(gate)
        {
            if(!started) { started=true; origin=now; }
            submissions++; submittedPackets+=packets;
            maxPackets=Math.Max(maxPackets,packets); maxQueued=Math.Max(maxQueued,queued);
            if(startFrame==uint.MaxValue) sentinelFrames++;
            if(startFrame==0) zeroFrames++;
            lastStartFrame=startFrame;
        }
        return now;
    }

    public void RecordDelivery(long submitted, byte endpoint)
    {
        long now = clock.GetTimestamp();
        string? summary=null;
        lock(gate)
        {
            double age=clock.GetElapsedTime(submitted,now).TotalMilliseconds;
            totalAge+=age; maxAge=Math.Max(maxAge,age); delivered++;
            if(previousDelivery!=0) maxGap=Math.Max(maxGap,clock.GetElapsedTime(previousDelivery,now).TotalMilliseconds);
            previousDelivery=now;
            double seconds=clock.GetElapsedTime(origin,now).TotalSeconds;
            if(seconds<2) return;
            summary=string.Create(CultureInfo.InvariantCulture,$"ISO OUT diagnostic: ep=0x{endpoint:X2} window_s={seconds:F3} urbs={submissions} packets_per_urb_mean={(submissions==0?0:submittedPackets/(double)submissions):F2} packets_per_urb_max={maxPackets} queued_urbs_max={maxQueued} delivered_packets={delivered} packets_per_s={delivered/seconds:F1} received_to_delivery_mean_ms={totalAge/delivered:F3} received_to_delivery_max_ms={maxAge:F3} delivery_gap_max_ms={maxGap:F3} start_frame_last={lastStartFrame} start_frame_zero={zeroFrames} start_frame_sentinel={sentinelFrames}; excludes Windows/game buffers before USB/IP receipt and physical Bluetooth latency");
            origin=now; submissions=submittedPackets=delivered=sentinelFrames=zeroFrames=0;
            maxPackets=maxQueued=0; totalAge=maxAge=maxGap=0;
        }
        logger.Log(EmulationLogLevel.Information,summary);
    }
}
