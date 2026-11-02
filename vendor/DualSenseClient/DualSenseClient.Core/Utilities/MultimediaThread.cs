// MadSense modification, 2026-10-31. GPL-3.0, like the containing project.
using System.Runtime.InteropServices;
using DualSenseClient.Logging;
using Microsoft.Win32.SafeHandles;

namespace DualSenseClient.Core.Utilities;

/// <summary>
/// Registers the calling thread with the Windows Multimedia Class Scheduler Service (MMCSS)
/// for the duration of a real-time loop, so the Bluetooth report pump and the HID input reader
/// keep their cadence while a game saturates the CPU. MMCSS raises the thread to the
/// scheduling class of the named task ("Pro Audio", "Games", ...) and lowers it again on
/// <see cref="IDisposable.Dispose"/>. macOS requests a scoped user-interactive pthread QoS
/// override while the input/audio worker serves the active controller session; disposing
/// it removes only that override and preserves the thread's requested QoS. This is a
/// scheduling hint, not a privileged real-time scheduling policy or a deadline guarantee.
/// On failure or another platform, the thread's existing scheduling remains in effect.
/// </summary>
public static class MultimediaThread
{
    private static readonly DualSenseClientLogger _log = DualSenseClientLogger.For("MultimediaThread");

    /// <summary>
    /// Enters the given MMCSS task (Windows) or scoped QoS override (macOS) on the current
    /// thread. Returns a token that reverts the characteristics when disposed, or
    /// <c>null</c> when the platform cannot apply the requested scheduling hint.
    /// </summary>
    /// <param name="task">An MMCSS task name registered in the system, e.g. "Pro Audio" or "Games".</param>
    public static IDisposable? Enter(string task)
    {
        if (OperatingSystem.IsMacOS()) return EnterDarwin(task);
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

    private static IDisposable? EnterDarwin(string task)
    {
        try
        {
            // A setter cannot restore QOS_CLASS_UNSPECIFIED, and changing
            // schedparam (including some runtime Thread.Priority operations)
            // can permanently opt a pthread out of the QoS setter system.
            // An override is independently removable and never rewrites the
            // requested QoS, including when nested or disposed on another thread.
            IntPtr handle = DarwinMethods.StartOverride(DarwinMethods.PthreadSelf(),
                DarwinMethods.UserInteractive, 0);
            if (handle == IntPtr.Zero)
            {
                _log.Debug($"macOS QoS override for '{task}' unavailable; retaining existing scheduling");
                return null;
            }
            return new DarwinRegistration(handle);
        }
        catch (Exception e) when (e is DllNotFoundException or EntryPointNotFoundException or BadImageFormatException)
        {
            _log.Debug($"macOS QoS unavailable: {e.Message}");
            return null;
        }
    }

    // SafeHandle ensures precisely one end call even if disposal is repeated,
    // concurrent or forgotten. pthread's public API permits ending on a different
    // thread, so finalizer cleanup also releases an abandoned override safely.
    internal sealed class DarwinRegistration : SafeHandleZeroOrMinusOneIsInvalid
    {
        private int releaseStatus = int.MinValue;
        internal int ReleaseStatus => Volatile.Read(ref releaseStatus);

        internal DarwinRegistration(IntPtr nativeHandle) : base(ownsHandle: true) => SetHandle(nativeHandle);

        protected override bool ReleaseHandle()
        {
            int result = DarwinMethods.EndOverride(handle);
            Volatile.Write(ref releaseStatus, result);
            // Apple consumes/deallocates the override object even on a nonzero
            // return. Retrying would double-free it. Preserve the result for
            // diagnostics and tests; the native ownership is always released.
            return true;
        }
    }

    private static class DarwinMethods
    {
        // Public declarations/constants: libpthread/include/pthread/qos.h and
        // libpthread/include/sys/qos.h. Overrides require no realtime privilege.
        private const string Library = "/usr/lib/libSystem.B.dylib";
        internal const uint UserInteractive = 0x21;
        [DllImport(Library, EntryPoint = "pthread_self")]
        internal static extern IntPtr PthreadSelf();
        [DllImport(Library, EntryPoint = "pthread_override_qos_class_start_np")]
        internal static extern IntPtr StartOverride(IntPtr thread, uint qosClass, int relativePriority);
        [DllImport(Library, EntryPoint = "pthread_override_qos_class_end_np")]
        internal static extern int EndOverride(IntPtr qosOverride);
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
