using DualSenseClient.Emulation.Devices.DualSense;
using DualSenseClient.Emulation.Research;

int cases = 0;
foreach (bool edge in new[] { false, true })
foreach (string serial in new[] { "", "UNCHANGED-HID-SERIAL" })
{
    var identity = new DualSenseIdentity { SerialNumber = serial };
    var original = edge ? DualSenseEdgeDescriptorSet.Create(identity) : DualSenseDescriptorSet.Create(identity);
    var modified = AudioLeaseDescriptor.WithInterface(original, "0123456789abcdef0123456789abcdef");
    if (!original.DeviceDescriptor.Span.SequenceEqual(modified.DeviceDescriptor.Span)
        || !original.HidReportDescriptor!.Value.Span.SequenceEqual(modified.HidReportDescriptor!.Value.Span))
        throw new Exception("Device identity or HID descriptor changed");
    var before = original.ConfigDescriptor.ToArray(); var after = modified.ConfigDescriptor.ToArray();
    var different = Enumerable.Range(0, before.Length).Where(i => before[i] != after[i]).ToArray();
    if (different.Length != 1 || different[0] != 17 || before[17] != 0)
        throw new Exception("Changed more than AC0 iInterface");
    if (!modified.TryGetString(after[17], out string text) || text != AudioLeaseDescriptor.Prefix + "0123456789abcdef0123456789abcdef")
        throw new Exception("Interface string missing");
    for (int i = 1; original.TryGetString(i, out string old); i++)
        if (!modified.TryGetString(i, out string value) || old != value) throw new Exception("Existing string changed");
    if (original.DeviceDescriptor.Span[16] != modified.DeviceDescriptor.Span[16]) throw new Exception("iSerial changed");
    cases++;
}
foreach (string invalid in new[] { "", "../nonce", new string('a', 31), new string('A', 32), new string('g', 32) })
{
    try { AudioLeaseDescriptor.WithInterface(DualSenseDescriptorSet.Create(new()), invalid); throw new Exception("Invalid nonce accepted"); }
    catch (ArgumentException) { cases++; }
}
Console.WriteLine($"{{\"result\":\"passed\",\"cases\":{cases},\"scope\":\"AC0-only-descriptor-copy\"}}");
if (args.Length != 0)
{
    if (args.Length != 2 || args[0] != "--reference") throw new ArgumentException("Expected --reference FILE");
    var reference = DualSenseDescriptorSet.Create(new());
    List<string> strings = [];
    for (int i = 1; reference.TryGetString(i, out string value); i++) strings.Add(value);
    File.WriteAllText(args[1], System.Text.Json.JsonSerializer.Serialize(new
    {
        device = Convert.ToHexString(reference.DeviceDescriptor.Span),
        config = Convert.ToHexString(reference.ConfigDescriptor.Span),
        hid = Convert.ToHexString(reference.HidReportDescriptor!.Value.Span), strings
    }) + "\n");
}
