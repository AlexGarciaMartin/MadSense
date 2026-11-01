// MadSense modification, 2026-10-31. GPL-3.0, like the containing project.
using System.Runtime.InteropServices;
using DualSenseClient.Logging;

namespace DualSenseClient.Core.Utilities;

/// <summary>
/// Registers the calling thread with the Windows Multimedia Class Scheduler Service (MMCSS)
/// for the duration of a real-time loop, so the Bluetooth report pump and the HID input reader
/// keep their cadence while a game saturates the CPU. MMCSS raises the thread to the
/// scheduling class of the named task ("Pro Audio", "Games", ...) and lowers it again on
/// <see cref="IDisposable.Dispose"/>. On failure, or off Windows, nothing changes: the loop
/// keeps the priority its creator gave it.
/// </summary>
public static class MultimediaThread
{
    private static readonly DualSenseClientLogger _log = DualSenseClientLogger.For("MultimediaThread");

    /// <summary>
    /// Enters the given MMCSS task on the current thread. Returns a token that reverts the
    /// characteristics when disposed, or <c>null</c> when MMCSS is unavailable.
    /// </summary>
    /// <param name="task">An MMCSS task name registered in the system, e.g. "Pro Audio" or "Games".</param>
    public static IDisposable? Enter(string task)
    {
        if (!OperatingSystem.IsWindows())
        {
            return null;
        }

        try
        {
            uint taskIndex = 0;
            IntPtr handle = NativeMethods.AvSetMmThreadCharacteristicsW(task, ref taskIndex);
            if (handle == IntPtr.Zero)
            {
                _log.Debug($"MMCSS task '{task}' unavailable (error {Marshal.GetLastWin32Error()}); keeping the thread priority as is");
                return null;
            }

            _log.Debug($"Thread '{Thread.CurrentThread.Name}' registered with MMCSS task '{task}'");
            return new Registration(handle);
        }
        catch (Exception ex)
        {
            // avrt.dll missing (Server Core / N editions without the media feature pack).
            _log.Debug($"MMCSS not available: {ex.Message}");
            return null;
        }
    }

    private sealed class Registration(IntPtr handle) : IDisposable
    {
        private IntPtr _handle = handle;

        public void Dispose()
        {
            IntPtr current = Interlocked.Exchange(ref _handle, IntPtr.Zero);
            if (current != IntPtr.Zero)
            {
                try { NativeMethods.AvRevertMmThreadCharacteristics(current); } catch { /* best effort */ }
            }
        }
    }

    private static class NativeMethods
    {
        [DllImport("avrt.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern IntPtr AvSetMmThreadCharacteristicsW(string taskName, ref uint taskIndex);

        [DllImport("avrt.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool AvRevertMmThreadCharacteristics(IntPtr handle);
    }
}
