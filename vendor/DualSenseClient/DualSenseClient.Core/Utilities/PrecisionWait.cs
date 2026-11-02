// MadSense modification, 2026-10-31. GPL-3.0, like the containing project.
using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

[assembly: InternalsVisibleTo("DualSenseClient.Tests")]

namespace DualSenseClient.Core.Utilities;

/// <summary>
/// Blocks the calling thread until a Stopwatch deadline with sub-millisecond accuracy and
/// without spinning. On Windows 10 1803+ a high-resolution waitable timer (the same facility
/// the media stack uses) wakes the thread within tens of microseconds of the deadline. macOS
/// uses mach_wait_until with the platform's timebase, without a final spin. Other platforms
/// fall back to whole-millisecond sleeps plus a short final yield. The Windows timer handle
/// is created once per thread and reused for every wait.
/// </summary>
public static class PrecisionWait
{
    private const uint CreateWaitableTimerHighResolution = 0x2;
    private const uint TimerAllAccess = 0x1F0003;
    private const uint WaitObject0 = 0;

    /// <summary>Below this remaining time the thread spins instead of arming the timer.</summary>
    private static readonly long SpinThresholdTicks = Stopwatch.Frequency / 20000; // 50 µs

    [ThreadStatic]
    private static SafeWaitHandle? _timer;

    [ThreadStatic]
    private static bool _timerProbed;

    private const int KernAborted = 14;
    private static readonly Lazy<MachTimebaseInfo?> MachTimebase = new(ReadMachTimebase);

    /// <summary>
    /// Sleeps until <paramref name="deadlineTimestamp"/> (a <see cref="Stopwatch.GetTimestamp"/>
    /// value). Returns at once when the deadline has passed.
    /// </summary>
    public static void SleepUntil(long deadlineTimestamp)
    {
        long now = Stopwatch.GetTimestamp();
        if (now >= deadlineTimestamp)
        {
            return;
        }

        if (OperatingSystem.IsMacOS())
        {
            if (!TrySleepUntilDarwin(deadlineTimestamp))
                SleepWithoutSpinning(deadlineTimestamp);
            return;
        }

        SafeWaitHandle? timer = GetTimer();
        if (timer is not null)
        {
            long remaining = deadlineTimestamp - now;
            if (remaining > SpinThresholdTicks)
            {
                // Relative due time in 100 ns units (negative = relative).
                long due = -(remaining * 10_000_000L / Stopwatch.Frequency);
                if (NativeMethods.SetWaitableTimer(timer, ref due, 0, IntPtr.Zero, IntPtr.Zero, false))
                {
                    NativeMethods.WaitForSingleObject(timer, uint.MaxValue);
                }
            }

            // The timer resolves to the scheduler's clock edge; close the last microseconds exactly.
            while (Stopwatch.GetTimestamp() < deadlineTimestamp)
            {
                Thread.SpinWait(20);
            }

            return;
        }

        // Fallback (pre-1803 / other platforms): coarse sleep, then yield the final millisecond.
        while (Stopwatch.GetTimestamp() < deadlineTimestamp)
        {
            double remainingMs = (deadlineTimestamp - Stopwatch.GetTimestamp()) * 1000.0 / Stopwatch.Frequency;
            if (remainingMs > 1.0)
            {
                Thread.Sleep(Math.Max(1, (int)(remainingMs - 1.0)));
            }
            else
            {
                Thread.Yield();
            }
        }
    }

    /// <summary>
    /// Uses an absolute Mach deadline, recalculated after interrupted waits. The
    /// clocks need not share an epoch: only a remaining Stopwatch interval is
    /// converted. Sampling Mach after Stopwatch can add sampling overhead but
    /// cannot make the deadline earlier. The original Stopwatch deadline is
    /// checked again after every wake, including successful ones.
    /// </summary>
    internal static bool TrySleepUntilDarwin(long deadlineTimestamp)
    {
        if (!OperatingSystem.IsMacOS() || MachTimebase.Value is not { } timebase)
            return false;

        try
        {
            while (true)
            {
                long now = Stopwatch.GetTimestamp();
                if (now >= deadlineTimestamp) return true;
                // Int128 handles the entire signed timestamp domain before the
                // known-positive difference is converted to an unsigned span.
                ulong remaining = (ulong)((Int128)deadlineTimestamp - now);
                ulong interval = ConvertStopwatchIntervalToMachTicks(remaining,
                    Stopwatch.Frequency, timebase.Numerator, timebase.Denominator);
                ulong nativeDeadline = AddMachDeadline(DarwinMethods.MachAbsoluteTime(), interval);
                int result = DarwinMethods.MachWaitUntil(nativeDeadline);
                if (result is not (0 or KernAborted)) return false;
            }
        }
        catch (Exception e) when (e is DllNotFoundException or EntryPointNotFoundException or BadImageFormatException)
        {
            return false;
        }
    }

    // A Mach tick is numerator/denominator nanoseconds. Ceiling prevents
    // fractional ticks from shortening a requested wait. The largest product
    // here is less than 2^126; UInt128 cannot overflow for these argument types.
    internal static ulong ConvertStopwatchIntervalToMachTicks(ulong ticks, long frequency,
        uint numerator, uint denominator)
    {
        ArgumentOutOfRangeException.ThrowIfNegativeOrZero(frequency);
        ArgumentOutOfRangeException.ThrowIfZero(numerator);
        ArgumentOutOfRangeException.ThrowIfZero(denominator);
        UInt128 dividend = (UInt128)ticks * 1_000_000_000UL * denominator;
        UInt128 divisor = (UInt128)(ulong)frequency * numerator;
        UInt128 result = (dividend + divisor - 1) / divisor;
        return result > ulong.MaxValue ? ulong.MaxValue : (ulong)result;
    }

    internal static ulong AddMachDeadline(ulong now, ulong interval) =>
        interval > ulong.MaxValue - now ? ulong.MaxValue : now + interval;

    private static MachTimebaseInfo? ReadMachTimebase()
    {
        if (!OperatingSystem.IsMacOS()) return null;
        try
        {
            return DarwinMethods.MachTimebaseInfo(out MachTimebaseInfo info) == 0 &&
                info.Numerator != 0 && info.Denominator != 0 ? info : null;
        }
        catch (Exception e) when (e is DllNotFoundException or EntryPointNotFoundException or BadImageFormatException)
        {
            return null;
        }
    }

    // A native failure may lower timer precision, but must not turn the audio
    // worker into a yield/spin loop. Recheck after a rounded-up blocking sleep.
    private static void SleepWithoutSpinning(long deadlineTimestamp)
    {
        while (true)
        {
            long now = Stopwatch.GetTimestamp();
            if (now >= deadlineTimestamp) return;
            UInt128 remaining = (UInt128)((Int128)deadlineTimestamp - now);
            UInt128 milliseconds = (remaining * 1000 + (ulong)Stopwatch.Frequency - 1) /
                (ulong)Stopwatch.Frequency;
            Thread.Sleep(milliseconds > int.MaxValue ? int.MaxValue : Math.Max(1, (int)milliseconds));
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct MachTimebaseInfo
    {
        public uint Numerator;
        public uint Denominator;
    }

    private static class DarwinMethods
    {
        // Apple public declarations: xnu/osfmk/mach/mach_time.h.
        // All supported macOS architectures export these through libSystem.
        private const string Library = "/usr/lib/libSystem.B.dylib";

        [DllImport(Library, EntryPoint = "mach_timebase_info")]
        internal static extern int MachTimebaseInfo(out MachTimebaseInfo info);
        [DllImport(Library, EntryPoint = "mach_absolute_time")]
        internal static extern ulong MachAbsoluteTime();
        [DllImport(Library, EntryPoint = "mach_wait_until")]
        internal static extern int MachWaitUntil(ulong deadline);
    }

    private static SafeWaitHandle? GetTimer()
    {
        if (_timerProbed)
        {
            return _timer;
        }

        _timerProbed = true;
        if (!OperatingSystem.IsWindows())
        {
            return null;
        }

        try
        {
            SafeWaitHandle handle = NativeMethods.CreateWaitableTimerExW(IntPtr.Zero, null, CreateWaitableTimerHighResolution, TimerAllAccess);
            if (handle.IsInvalid)
            {
                handle.Dispose();
                return null;
            }

            _timer = handle;
            return handle;
        }
        catch
        {
            return null;
        }
    }

    private static class NativeMethods
    {
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern SafeWaitHandle CreateWaitableTimerExW(IntPtr attributes, string? name, uint flags, uint desiredAccess);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool SetWaitableTimer(SafeWaitHandle timer, ref long dueTime, int period, IntPtr completionRoutine, IntPtr argument, [MarshalAs(UnmanagedType.Bool)] bool resume);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern uint WaitForSingleObject(SafeWaitHandle handle, uint milliseconds);
    }
}
