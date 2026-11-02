using System.Runtime.InteropServices;
using System.Text;
using DualSenseClient.Emulation.Devices.DualSense;

namespace MadSense.Mac;

internal sealed unsafe class MacVirtualHid : IDisposable
{
    private const string Library = "madsense_hid";
    private readonly object gate = new();
    private readonly HidReportRouter router;
    // Keep reverse P/Invoke delegates rooted until native destruction has drained callbacks.
    private readonly GetCallback get;
    private readonly SetCallback set;
    private IntPtr handle;

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int GetCallback(IntPtr context, uint type, uint id, byte* data, uint* length);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int SetCallback(IntPtr context, uint type, uint id, byte* data, uint length);

    public MacVirtualHid(DualSenseDevice persona, bool edge = false, Func<bool>? hasOutputFault = null)
    {
        if (!OperatingSystem.IsMacOS()) throw new PlatformNotSupportedException("IOKit requires macOS.");
        router = new(persona, hasOutputFault);
        get = GetReport;
        set = SetReport;
        byte[] descriptor = persona.Descriptors.HidReportDescriptor!.Value.ToArray();
        int result = Create(descriptor, (uint)descriptor.Length, 0x054c, edge ? (ushort)0x0df2 : (ushort)0x0ce6,
            "MadSense-Mac-Virtual", get, set, IntPtr.Zero, out handle);
        if (result != 0 || handle == IntPtr.Zero)
        {
            if (handle != IntPtr.Zero) { Destroy(handle); handle = IntPtr.Zero; }
            throw new InvalidOperationException($"Virtual HID unavailable (0x{result:x8}): {Diagnostic()}");
        }
    }

    private int GetReport(IntPtr context, uint type, uint id, byte* data, uint* length)
    {
        try
        {
            if (length == null || data == null || *length > 4096) return HidReportRouter.BadArgument;
            int result = router.Get(type, id, new Span<byte>(data, (int)*length), out int count);
            *length = (uint)count;
            return result;
        }
        catch { if (length != null) *length = 0; return HidReportRouter.Error; }
    }

    private int SetReport(IntPtr context, uint type, uint id, byte* data, uint length)
    {
        try
        {
            if (data == null || length > 4096) return HidReportRouter.BadArgument;
            return router.Set(type, id, new ReadOnlySpan<byte>(data, (int)length));
        }
        catch { return HidReportRouter.Error; }
    }

    public void Send(byte[] report)
    {
        lock (gate)
        {
            ObjectDisposedException.ThrowIf(handle == IntPtr.Zero, this);
            int result = SendInput(handle, report, (uint)report.Length);
            if (result != 0) throw new IOException($"Virtual input failed (0x{result:x8}).");
        }
    }

    public static string Diagnostic()
    {
        byte[] buffer = new byte[4096];
        CopyDiagnostic(buffer, (uint)buffer.Length);
        int end = Array.IndexOf(buffer, (byte)0);
        return Encoding.UTF8.GetString(buffer, 0, end < 0 ? buffer.Length : end);
    }

    public void Dispose()
    {
        lock (gate)
        {
            if (handle == IntPtr.Zero) return;
            int result = Destroy(handle);
            if (result != 0) throw new IOException($"Virtual HID destruction failed (0x{result:x8}).");
            handle = IntPtr.Zero;
            GC.KeepAlive(get);
            GC.KeepAlive(set);
        }
    }

    [DllImport(Library, EntryPoint = "ms_hid_create", CallingConvention = CallingConvention.Cdecl)]
    private static extern int Create(byte[] descriptor, uint length, ushort vid, ushort pid,
        [MarshalAs(UnmanagedType.LPUTF8Str)] string serial, GetCallback get, SetCallback set,
        IntPtr context, out IntPtr handle);
    [DllImport(Library, EntryPoint = "ms_hid_send_input", CallingConvention = CallingConvention.Cdecl)]
    private static extern int SendInput(IntPtr handle, byte[] report, uint length);
    [DllImport(Library, EntryPoint = "ms_hid_destroy", CallingConvention = CallingConvention.Cdecl)]
    private static extern int Destroy(IntPtr handle);
    [DllImport(Library, EntryPoint = "ms_hid_copy_diagnostic", CallingConvention = CallingConvention.Cdecl)]
    private static extern uint CopyDiagnostic(byte[] text, uint capacity);
}
