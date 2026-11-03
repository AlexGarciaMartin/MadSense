using DualSenseClient.Emulation.Core;

namespace DualSenseClient.Emulation.Research;

// Included only in a separately archived research source copy.
public static class AudioLeaseDescriptor
{
    public const string Prefix = "MadSense-audio-lease-";

    public static UsbDescriptorSet Apply(UsbDescriptorSet source)
    {
        if (!OperatingSystem.IsLinux()) throw new InvalidOperationException("Linux research copy required.");
        return WithInterface(source, Environment.GetEnvironmentVariable("MADSENSE_AUDIO_INTERFACE_NONCE")
            ?? throw new InvalidOperationException("Explicit audio research nonce required."));
    }

    public static UsbDescriptorSet WithInterface(UsbDescriptorSet source, string nonce)
    {
        if (nonce.Length != 32 || nonce.Any(c => !char.IsAsciiHexDigit(c) || char.IsUpper(c)))
            throw new ArgumentException("A lowercase 128-bit nonce is required.");
        byte[] config = source.ConfigDescriptor.ToArray();
        List<string> strings = [];
        for (int index = 1; source.TryGetString(index, out string value); index++) strings.Add(value);
        if (strings.Count >= 255) throw new InvalidOperationException("No free USB string index.");
        byte stringIndex = checked((byte)(strings.Count + 1));
        strings.Add(Prefix + nonce);
        int changed = 0;
        for (int offset = 0; offset < config.Length; offset += config[offset])
        {
            int length = config[offset];
            if (length < 2 || offset + length > config.Length) throw new InvalidDataException("Malformed descriptor chain.");
            if (config[offset + 1] != 4) continue;
            if (length < 9) throw new InvalidDataException("Short interface descriptor.");
            if (config[offset + 2] != 0) continue;
            if (config[offset + 3] != 0 || config[offset + 5] != 1 || config[offset + 6] != 1
                || config[offset + 8] != 0) throw new InvalidDataException("Unexpected audio control interface.");
            config[offset + 8] = stringIndex;
            changed++;
        }
        if (changed != 1) throw new InvalidDataException("Exactly one AC0 interface required.");
        return UsbDescriptorSet.Create(source.DeviceDescriptor.ToArray(), config,
            source.HidReportDescriptor?.ToArray(), strings);
    }
}
