// MadSense modification, 2026-10-31. GPL-3.0, like the containing project.
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace DualSenseClient.Core.Utilities;

/// <summary>
/// Blocks the calling thread until a Stopwatch deadline with sub-millisecond accuracy and
/// without spinning. On Windows 10 1803+ a high-resolution waitable timer (the same facility
/// the media stack uses) wakes the thread within tens of microseconds of the deadline; elsewhere
/// the wait falls back to whole-millisecond sleeps plus a short final spin. The timer handle is
/// created once per thread and reused for every wait.
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

        // Fallback (pre-1803 / non-Windows): coarse sleep, then yield out the final millisecond.
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
