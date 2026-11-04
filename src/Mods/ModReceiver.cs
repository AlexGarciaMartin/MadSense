using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace MadSense.Mods;

/// <summary>Facts about the pad the receiver reports back to mods (they read battery and connection).</summary>
sealed record ModPadStatus(bool Connected, int Battery, string? Mac, bool Bluetooth);

/// <summary>
/// The receiving end of the DSX mod protocol: a UDP listener on the loopback port the mods were
/// written for. Each datagram is a JSON packet of instructions; the receiver applies them to the
/// <see cref="ModOverlay"/> and answers with the status reply the mods wait for. It also writes the
/// port files DSX leaves behind, because several mods read the port from there.
/// </summary>
sealed class ModReceiver : IDisposable
{
    public const int DefaultPort = DsxProtocol.DefaultPort;
    const int MaximumPacketBytes = 64 * 1024;
    static readonly JsonSerializerOptions ReplyJson = new() { PropertyNamingPolicy = null };
    readonly ModOverlay overlay;
    readonly Func<ModPadStatus> status;
    readonly int port;
    readonly object gate = new();
    UdpClient? socket;
    Thread? thread;
    long lastPacket, packets, validPackets, rejected;
    string? lastError;

    public ModReceiver(ModOverlay overlay, Func<ModPadStatus> status, int port = DefaultPort)
    {
        this.overlay = overlay;
        this.status = status;
        this.port = port;
    }

    public int Port => port;
    public bool Listening { get { lock (gate) return socket != null; } }
    /// <summary>The port was taken (DSX or another receiver is running): mods will talk to that one.</summary>
    public bool PortBusy { get; private set; }
    public string? LastError => lastError;
    /// <summary>A mod has sent something in the last ten seconds.</summary>
    public bool Active => Interlocked.Read(ref lastPacket) is var t && t != 0 && Environment.TickCount64 - t < 10000;
    public long Packets => Interlocked.Read(ref packets);
    public long ValidPackets => Interlocked.Read(ref validPackets);
    public long Rejected => Interlocked.Read(ref rejected);
    /// <summary>Raised once when the first packet of a session arrives and once when activity resumes after a pause.</summary>
    public event Action? Connected;
    /// <summary>Raised when a packet is ignored as malformed, with the reason.</summary>
    public event Action<string>? Warning;

    /// <summary>Binds the port; false when it is busy. Safe to call again later.</summary>
    public bool Start()
    {
        lock (gate)
        {
            if (socket != null) return true;
            UdpClient? client = null;
            try
            {
                client = new UdpClient(AddressFamily.InterNetwork);
                client.Client.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, false);
                client.Client.Bind(new IPEndPoint(IPAddress.Loopback, port));
            }
            catch (SocketException e)
            {
                client?.Dispose();
                PortBusy = e.SocketErrorCode is SocketError.AddressAlreadyInUse or SocketError.AccessDenied;
                lastError = e.Message;
                return false;
            }
            PortBusy = false;
            lastError = null;
            WritePortFiles();
            socket = client;
            thread = new Thread(() => Run(client)) { IsBackground = true, Name = "MadSense mods (UDP)" };
            thread.Start();
            return true;
        }
    }

    public void Stop()
    {
        UdpClient? client;
        lock (gate) { client = socket; socket = null; thread = null; }
        client?.Dispose();
        Interlocked.Exchange(ref lastPacket, 0);
    }

    /// <summary>
    /// DSX writes its port to two files and the mods read them; without them a mod may fall back to a
    /// hard-coded 6969 (fine) or refuse to start (not fine). Best effort: a failure is not an error.
    /// </summary>
    void WritePortFiles()
    {
        foreach (var file in PortFiles())
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(file)!);
                File.WriteAllText(file, port.ToString(System.Globalization.CultureInfo.InvariantCulture));
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Security.SecurityException) { }
        }
    }

    public static IEnumerable<string> PortFiles()
    {
        yield return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DSX", "DSX_UDP_PortNumber.txt");
        yield return Path.Combine(Path.GetPathRoot(Environment.SystemDirectory) ?? @"C:\", "Temp", "DualSenseX", "DualSenseX_PortNumber.txt");
    }

    void Run(UdpClient client)
    {
        var from = new IPEndPoint(IPAddress.Any, 0);
        while (true)
        {
            byte[] data;
            try { data = client.Receive(ref from); }
            catch (SocketException e) when (e.SocketErrorCode == SocketError.ConnectionReset) { continue; }
            catch (Exception e) when (e is SocketException or ObjectDisposedException) { return; }
            // Only this machine: a datagram from elsewhere is dropped without a reply.
            if (!IPAddress.IsLoopback(from.Address)) continue;
            Accept(data);
            try
            {
                var reply = Reply();
                client.Send(reply, reply.Length, from);
            }
            catch (Exception e) when (e is SocketException or ObjectDisposedException) { }
        }
    }

    /// <summary>Counts activity only after a supported, complete instruction was accepted.</summary>
    internal bool Accept(byte[] data)
    {
        Interlocked.Increment(ref packets);
        try
        {
            if (!Handle(data)) return false;
            Interlocked.Increment(ref validPackets);
            var previous = Interlocked.Exchange(ref lastPacket, Environment.TickCount64);
            if (previous == 0 || Environment.TickCount64 - previous >= 10000) { try { Connected?.Invoke(); } catch { } }
            return true;
        }
        catch (Exception e) when (e is JsonException or InvalidOperationException or FormatException or IndexOutOfRangeException or ArgumentException)
        {
            Interlocked.Increment(ref rejected);
            try { Warning?.Invoke(e.Message); } catch { }
            return false;
        }
    }

    /// <summary>
    /// The reply shape DSX v2 mods parse (Status, TimeReceived, isControllerConnected, BatteryLevel)
    /// plus the v3 Devices list some newer mods inspect. Real battery and connection, not placeholders.
    /// </summary>
    byte[] Reply()
    {
        var pad = status();
        var device = new
        {
            Index = 0,
            MacAddress = pad.Mac ?? "",
            DeviceType = "DUALSENSE",
            ConnectionType = pad.Bluetooth ? "BLUETOOTH" : "USB",
            BatteryLevel = pad.Battery,
            IsSupportAT = true,
            IsSupportLightBar = true,
            IsSupportPlayerLED = true,
            IsSupportMicLED = true,
        };
        var reply = new
        {
            Status = "DSX Received UDP Instructions",
            TimeReceived = DateTime.Now.ToString("yyyy/MM/dd hh:mm:ss tt", System.Globalization.CultureInfo.InvariantCulture),
            isControllerConnected = pad.Connected,
            BatteryLevel = pad.Battery,
            Devices = pad.Connected ? new[] { device } : [],
        };
        return Encoding.UTF8.GetBytes(JsonSerializer.Serialize(reply, ReplyJson));
    }

    /// <summary>Applies one packet; throws on malformed input so the caller counts and reports it.</summary>
    internal bool Handle(byte[] data)
    {
        if (data.Length > MaximumPacketBytes) throw new FormatException("packet too large");
        using var document = JsonDocument.Parse(data);
        if (document.RootElement.ValueKind != JsonValueKind.Object || !TryProperty(document.RootElement, "instructions", out var list) || list.ValueKind != JsonValueKind.Array)
            throw new FormatException("no instructions array");
        bool accepted = false;
        foreach (var instruction in list.EnumerateArray())
        {
            if (instruction.ValueKind != JsonValueKind.Object || !TryProperty(instruction, "type", out var typeValue)) continue;
            var parameters = TryProperty(instruction, "parameters", out var raw) && raw.ValueKind == JsonValueKind.Array
                ? raw.EnumerateArray().Select(Int).ToArray() : [];
            var type = Enum(typeValue, out int typeNumber);
            Apply(type, typeNumber, parameters);
            accepted |= type switch
            {
                DsxProtocol.Instruction.TriggerUpdate => parameters.Length >= 3 && parameters[1] is 1 or 2,
                DsxProtocol.Instruction.RgbUpdate => parameters.Length >= 4,
                DsxProtocol.Instruction.PlayerLed => parameters.Length >= 6,
                DsxProtocol.Instruction.PlayerLedNewRevision or DsxProtocol.Instruction.MicLed => parameters.Length >= 2,
                DsxProtocol.Instruction.ResetToUserSettings or DsxProtocol.Instruction.TriggerThreshold => true,
                _ => DsxProtocol.IsStatusQuery(typeNumber),
            };
        }
        return accepted;
    }

    void Apply(DsxProtocol.Instruction type, int typeNumber, int[] p)
    {
        // Parameter 0 is the controller index (0, or -1 for "the selected one"); MadSense drives one pad.
        switch (type)
        {
            case DsxProtocol.Instruction.TriggerUpdate when p.Length >= 3:
            {
                var trigger = (DsxProtocol.Trigger)p[1];
                if (trigger is not (DsxProtocol.Trigger.Left or DsxProtocol.Trigger.Right)) return;
                overlay.SetTrigger(trigger, DsxProtocol.Effect((DsxProtocol.Mode)p[2], p[3..]));
                return;
            }
            case DsxProtocol.Instruction.RgbUpdate when p.Length >= 4:
                overlay.SetLightbar(Byte(p[1]), Byte(p[2]), Byte(p[3]));
                return;
            case DsxProtocol.Instruction.PlayerLed when p.Length >= 6:
            {
                int mask = 0;
                for (int i = 0; i < 5; i++) if (p[1 + i] != 0) mask |= 1 << i;
                overlay.SetPlayerLeds((DualSenseClient.Controllers.DualSense.Output.PlayerLedMask)mask);
                return;
            }
            case DsxProtocol.Instruction.PlayerLedNewRevision when p.Length >= 2:
                overlay.SetPlayerLeds(DsxProtocol.PlayerPattern(p[1]));
                return;
            case DsxProtocol.Instruction.MicLed when p.Length >= 2:
                overlay.SetMicLed(DsxProtocol.MicLedMode(p[1]));
                return;
            case DsxProtocol.Instruction.ResetToUserSettings:
                overlay.Clear();
                return;
            case DsxProtocol.Instruction.TriggerThreshold:
                // Input-side threshold (when the trigger counts as pressed): the virtual pad reports
                // raw travel and the game decides; nothing to apply on the physical pad.
                return;
            default:
                if (!DsxProtocol.IsStatusQuery(typeNumber) && type != DsxProtocol.Instruction.TriggerUpdate
                    && type != DsxProtocol.Instruction.RgbUpdate && type != DsxProtocol.Instruction.PlayerLed
                    && type != DsxProtocol.Instruction.PlayerLedNewRevision && type != DsxProtocol.Instruction.MicLed)
                    throw new FormatException($"unknown instruction {typeNumber}");
                return;
        }
    }

    // Mods serialize with whatever casing their JSON library chose: accept any.
    static bool TryProperty(JsonElement element, string name, out JsonElement value)
    {
        foreach (var property in element.EnumerateObject())
            if (property.NameEquals(name) || string.Equals(property.Name, name, StringComparison.OrdinalIgnoreCase)) { value = property.Value; return true; }
        value = default;
        return false;
    }

    static DsxProtocol.Instruction Enum(JsonElement value, out int number)
    {
        if (value.ValueKind == JsonValueKind.String && System.Enum.TryParse<DsxProtocol.Instruction>(value.GetString(), true, out var named)) { number = (int)named; return named; }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out number)) return (DsxProtocol.Instruction)number;
        throw new FormatException("invalid instruction type");
    }

    static int Int(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.Number => value.TryGetInt32(out int i) ? i : (int)Math.Clamp(value.GetDouble(), int.MinValue, int.MaxValue),
        JsonValueKind.True => 1,
        JsonValueKind.String when int.TryParse(value.GetString(), System.Globalization.NumberStyles.Integer, System.Globalization.CultureInfo.InvariantCulture, out int s) => s,
        JsonValueKind.String when System.Enum.TryParse<DsxProtocol.Mode>(value.GetString(), true, out var mode) => (int)mode,
        JsonValueKind.String when System.Enum.TryParse<DsxProtocol.Trigger>(value.GetString(), true, out var trigger) => (int)trigger,
        _ => 0,
    };

    static byte Byte(int value) => (byte)Math.Clamp(value, 0, 255);

    public void Dispose() => Stop();
}
