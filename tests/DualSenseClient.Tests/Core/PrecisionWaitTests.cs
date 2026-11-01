using System.Diagnostics;
using DualSenseClient.Core.Utilities;

namespace DualSenseClient.Tests.Core;

[TestFixture]
public class PrecisionWaitTests
{
    [Test]
    public void SleepUntil_ReturnsImmediately_WhenDeadlinePassed()
    {
        long before = Stopwatch.GetTimestamp();
        PrecisionWait.SleepUntil(before - Stopwatch.Frequency);
        double elapsedMs = (Stopwatch.GetTimestamp() - before) * 1000.0 / Stopwatch.Frequency;
        Assert.That(elapsedMs, Is.LessThan(1.0));
    }

    [Test]
    public void SleepUntil_NeverWakesEarly_AndKeepsTheAudioCadenceTight()
    {
        TimerResolution.AddRef();
        try
        {
            const int ticks = 60;
            long period = (long)(Stopwatch.Frequency * 512 / 48000.0); // one Bluetooth audio tick, 10.667 ms
            double worstLateMs = 0, sumLateMs = 0;
            long next = Stopwatch.GetTimestamp();
            for (int i = 0; i < ticks; i++)
            {
                next += period;
                PrecisionWait.SleepUntil(next);
                long now = Stopwatch.GetTimestamp();
                Assert.That(now, Is.GreaterThanOrEqualTo(next), "woke before the deadline");
                double lateMs = (now - next) * 1000.0 / Stopwatch.Frequency;
                worstLateMs = Math.Max(worstLateMs, lateMs);
                sumLateMs += lateMs;
                next = Math.Max(next, now);
            }

            TestContext.Out.WriteLine($"mean late {sumLateMs / ticks:F3} ms, worst {worstLateMs:F3} ms");
            // The previous Sleep+Yield loop was accurate to the timer tick; the waitable timer must not be
            // worse. A loaded test host may add an occasional scheduling hiccup, hence the loose worst case.
            Assert.That(sumLateMs / ticks, Is.LessThan(1.0), "mean overshoot");
            Assert.That(worstLateMs, Is.LessThan(6.0), "worst overshoot");
        }
        finally
        {
            TimerResolution.Release();
        }
    }
}
