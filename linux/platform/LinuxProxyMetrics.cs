using System.Diagnostics;

namespace MadSense.Platform;

public sealed record LinuxProxyTiming(long Count, int RetainedSamples, double P50Ms, double P95Ms, double P99Ms, double MaxMs);
public sealed record LinuxProxyMetrics(long ReceivedReports, long ReadReports, long InitializationDiscardedReports, long InputOverflows, int QueuedReports,
    double OldestQueuedAgeMs, long LastReceivedTimestamp, long LastDeliveredTimestamp, long TimestampFrequency,
    long LastBrokerReadMonotonicNs, LinuxProxyTiming ClientQueueAge, LinuxProxyTiming HidRpcRoundTrip);

// Bounded recent software timings. They exclude radio, firmware and physical latency.
internal sealed class ProxyTiming
{
    readonly object gate = new();
    readonly double[] recent = new double[1024];
    long count;
    public void Add(long elapsedTicks)
    {
        lock (gate) recent[count++ % recent.Length] = elapsedTicks * 1000.0 / Stopwatch.Frequency;
    }
    public LinuxProxyTiming Snapshot()
    {
        lock (gate)
        {
            int length = (int)Math.Min(count, recent.Length);
            if (length == 0) return new(count, 0, 0, 0, 0, 0);
            double[] values = recent.AsSpan(0, length).ToArray(); Array.Sort(values);
            double At(double percentile) => values[Math.Clamp((int)Math.Ceiling(percentile * length) - 1, 0, length - 1)];
            return new(count, length, At(.50), At(.95), At(.99), values[^1]);
        }
    }
}
