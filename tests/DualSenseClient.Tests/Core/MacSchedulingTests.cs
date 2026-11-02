using System.Diagnostics;
using System.Runtime.ExceptionServices;
using System.Runtime.InteropServices;
using DualSenseClient.Core.Utilities;

namespace DualSenseClient.Tests.Core;

[TestFixture]
public class MacSchedulingTests
{
    [TestCase(0UL, 1_000_000_000L, 1U, 1U, 0UL)]
    [TestCase(1UL, 1_000_000_000L, 1U, 1U, 1UL)]
    [TestCase(1UL, 1_000_000_000L, 125U, 3U, 1UL)]
    [TestCase(1000UL, 1_000_000_000L, 125U, 3U, 24UL)]
    [TestCase(1001UL, 1_000_000_000L, 125U, 3U, 25UL)]
    [TestCase(1UL, 10_000_000L, 125U, 3U, 3UL)]
    public void MachIntervalsPreserveDurationAndRoundUpFractionalTicks(ulong ticks,
        long frequency, uint numerator, uint denominator, ulong expected)
    {
        Assert.That(PrecisionWait.ConvertStopwatchIntervalToMachTicks(ticks, frequency,
            numerator, denominator), Is.EqualTo(expected));
    }

    [Test]
    public void MachConversionSaturatesRatherThanWrappingLargeIntervalsOrDeadlines()
    {
        Assert.That(PrecisionWait.ConvertStopwatchIntervalToMachTicks(ulong.MaxValue,
            1, 1, uint.MaxValue), Is.EqualTo(ulong.MaxValue));
        Assert.That(PrecisionWait.ConvertStopwatchIntervalToMachTicks(ulong.MaxValue,
            1_000_000_000, 1, 1), Is.EqualTo(ulong.MaxValue));
        Assert.That(PrecisionWait.ConvertStopwatchIntervalToMachTicks(ulong.MaxValue,
            long.MaxValue, uint.MaxValue, 1), Is.EqualTo(1UL));
        Assert.That(PrecisionWait.AddMachDeadline(ulong.MaxValue - 3, 9), Is.EqualTo(ulong.MaxValue));
        Assert.That(PrecisionWait.AddMachDeadline(40, 2), Is.EqualTo(42UL));
    }

    [Test]
    public void InvalidTimebasesAreRejectedBeforeDivision()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() =>
            PrecisionWait.ConvertStopwatchIntervalToMachTicks(1, 0, 1, 1));
        Assert.Throws<ArgumentOutOfRangeException>(() =>
            PrecisionWait.ConvertStopwatchIntervalToMachTicks(1, 1, 0, 1));
        Assert.Throws<ArgumentOutOfRangeException>(() =>
            PrecisionWait.ConvertStopwatchIntervalToMachTicks(1, 1, 1, 0));
    }

    [Test]
    public void DarwinNativeWaitLoadsAndHonorsOriginalStopwatchDeadlines()
    {
        RequireMacOS();
        // This entry point returns false on unavailable APIs/native failure,
        // so a passing test cannot silently exercise the coarse fallback.
        foreach (int microseconds in new[] { 1, 100, 1000, 10_667 })
        {
            long deadline = Stopwatch.GetTimestamp() + Math.Max(1,
                (long)((Int128)Stopwatch.Frequency * microseconds / 1_000_000));
            Assert.That(PrecisionWait.TrySleepUntilDarwin(deadline), Is.True,
                "macOS native wait must be available on the supported runner");
            Assert.That(Stopwatch.GetTimestamp(), Is.GreaterThanOrEqualTo(deadline),
                "the caller must never resume before its Stopwatch deadline");
        }
        Assert.That(PrecisionWait.TrySleepUntilDarwin(long.MinValue), Is.True);
        // No upper latency assertion: runner contention is not an API failure.
    }

    [TestCase(false)]
    [TestCase(true)]
    public void ScopedQosKeepsRequestedClassAndReleasesNestedOverrides(bool aboveNormal)
    {
        RequireMacOS();
        RunOnThread(() =>
        {
            var original = ReadRequestedQos();
            using var outer = MultimediaThread.Enter("Games");
            Assert.That(outer, Is.InstanceOf<MultimediaThread.DarwinRegistration>(),
                "macOS must accept the public pthread QoS override");
            var outerOverride = (MultimediaThread.DarwinRegistration)outer!;
            Assert.That(ReadRequestedQos(), Is.EqualTo(original));
            using var inner = MultimediaThread.Enter("Pro Audio");
            Assert.That(inner, Is.InstanceOf<MultimediaThread.DarwinRegistration>());
            var innerOverride = (MultimediaThread.DarwinRegistration)inner!;
            Assert.That(ReadRequestedQos(), Is.EqualTo(original));

            // Apple explicitly permits ending on a different thread. Verify the
            // scoped object is not dependent on the caller retaining its thread.
            Task.Run(innerOverride.Dispose).GetAwaiter().GetResult();
            Assert.That(innerOverride.ReleaseStatus, Is.Zero, "pthread override release failed");
            Assert.That(innerOverride.IsClosed, Is.True);
            Assert.That(outerOverride.IsClosed, Is.False, "nested release consumed the outer override");
            Assert.That(ReadRequestedQos(), Is.EqualTo(original));
            innerOverride.Dispose(); // double close must not double-free native state
            outerOverride.Dispose();
            Assert.That(outerOverride.ReleaseStatus, Is.Zero);
            Assert.That(ReadRequestedQos(), Is.EqualTo(original));
        }, aboveNormal);
    }

    private static (uint Class, int RelativePriority) ReadRequestedQos()
    {
        int result = PthreadGetQos(PthreadSelf(), out uint qos, out int relative);
        Assert.That(result, Is.Zero, "pthread QoS query failed");
        return (qos, relative);
    }

    private static void RunOnThread(Action action, bool aboveNormal)
    {
        ExceptionDispatchInfo? failure = null;
        var worker = new Thread(() =>
        {
            try { action(); }
            catch (Exception e) { failure = ExceptionDispatchInfo.Capture(e); }
        }) { IsBackground = true, Name = "MadSense scheduling test" };
        if (aboveNormal) worker.Priority = ThreadPriority.AboveNormal;
        worker.Start();
        Assert.That(worker.Join(TimeSpan.FromSeconds(20)), Is.True, "scheduling test worker hung");
        failure?.Throw();
    }

    private static void RequireMacOS()
    {
        if (!OperatingSystem.IsMacOS()) Assert.Ignore("Requires actual macOS libSystem and pthread scheduling.");
    }

    [DllImport("/usr/lib/libSystem.B.dylib", EntryPoint = "pthread_self")]
    private static extern IntPtr PthreadSelf();
    [DllImport("/usr/lib/libSystem.B.dylib", EntryPoint = "pthread_get_qos_class_np")]
    private static extern int PthreadGetQos(IntPtr thread, out uint qosClass, out int relativePriority);
}
