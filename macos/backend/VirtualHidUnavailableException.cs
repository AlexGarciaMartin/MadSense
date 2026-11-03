namespace MadSense.Mac;

/// <summary>A virtual transport failure that needs an explicit retry after its
/// signature, native library or system configuration has been corrected.</summary>
internal sealed class VirtualHidUnavailableException : InvalidOperationException
{
    public int? NativeErrorCode { get; }

    public VirtualHidUnavailableException(string diagnostic)
        : base($"Virtual HID unavailable: {diagnostic}")
    {
    }

    public VirtualHidUnavailableException(int nativeErrorCode, string diagnostic)
        : base($"Virtual HID unavailable (0x{nativeErrorCode:x8}): {diagnostic}")
    {
        NativeErrorCode = nativeErrorCode;
    }

    public VirtualHidUnavailableException(Exception nativeLibraryError)
        : base($"Virtual HID native library unavailable: {nativeLibraryError.Message}", nativeLibraryError)
    {
    }
}
