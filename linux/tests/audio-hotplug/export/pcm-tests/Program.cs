using System.Buffers.Binary;
using DualSenseClient.Emulation.Research;

int cases = 0;
void Require(bool condition) { if (!condition) throw new InvalidDataException("PCM recorder contract failed."); }
{
    var recorder = new PcmIngressRecorder(32, 4); byte[] first = [1, 2, 3, 4, 5, 6, 7, 8];
    Require(recorder.TryCapture(first, 10)); var before = recorder.Mark(); first[0] = 99;
    Require(recorder.TryCapture(new byte[8], 20)); recorder.Stop(); var value = recorder.Snapshot();
    Require(before.Packets == 1 && before.Bytes == 8 && value.Bytes[0] == 1 && value.Packets[1].Offset == 8);
    Require(value.Attempts == 2 && value.Packets.Select(row => row.Timestamp).SequenceEqual([10L, 20L])); cases++;
}
{
    var recorder = new PcmIngressRecorder(8, 2);
    Require(recorder.TryCapture(new byte[8], 10)); Require(!recorder.TryCapture(new byte[8], 20));
    recorder.Stop(); var value = recorder.Snapshot();
    Require(value.Bytes.Length == 8 && value.Packets.Length == 1 && value.CapacityRejected == 1 && value.Attempts == 2); cases++;
}
{
    var recorder = new PcmIngressRecorder(32, 1);
    Require(!recorder.TryCapture(new byte[7], 0)); Require(!recorder.TryCapture([], 1));
    Require(recorder.TryCapture(new byte[8], 2)); Require(!recorder.TryCapture(new byte[8], 3));
    recorder.Stop(); Require(!recorder.TryCapture(new byte[8], 4)); var value = recorder.Snapshot();
    Require(value.MalformedRejected == 2 && value.CapacityRejected == 1 && value.Attempts == 4); cases++;
}
{
    const int count = 4000; var recorder = new PcmIngressRecorder(count * 64, count);
    Parallel.For(0, count, index => { byte[] report = new byte[64]; for (int i = 0; i < report.Length; i += 4) BinaryPrimitives.WriteInt32LittleEndian(report.AsSpan(i), index); recorder.TryCapture(report, index); });
    recorder.Stop(); var value = recorder.Snapshot(); HashSet<int> seen = [];
    Require(value.Attempts == count && value.Packets.Length + value.ConcurrentRejected == count && value.CapacityRejected == 0);
    foreach (var packet in value.Packets)
    {
        int index = BinaryPrimitives.ReadInt32LittleEndian(value.Bytes.AsSpan(packet.Offset)); Require(seen.Add(index));
        for (int offset = packet.Offset; offset < packet.Offset + packet.Length; offset += 4)
            Require(BinaryPrimitives.ReadInt32LittleEndian(value.Bytes.AsSpan(offset)) == index);
    }
    cases++;
}
{
    var recorder = new PcmIngressRecorder(1024 * 1024, 65536);
    using var entered = new ManualResetEventSlim();
    var worker = new Thread(() => { byte[] packet = new byte[8]; entered.Set(); for (int i = 0; i < 100000; i++) recorder.TryCapture(packet, i); });
    worker.Start(); Require(entered.Wait(TimeSpan.FromSeconds(2))); recorder.Stop();
    var before = recorder.Snapshot(); Require(worker.Join(TimeSpan.FromSeconds(2))); var after = recorder.Snapshot();
    Require(before.Bytes.SequenceEqual(after.Bytes) && before.Attempts == after.Attempts && before.Packets.SequenceEqual(after.Packets));
    cases++;
}
Console.WriteLine($"{cases} bounded PCM recorder cases passed");
