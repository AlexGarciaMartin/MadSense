using DualSenseClient.Controllers.Devices;
using DualSenseClient.Controllers.DualSense.Enum;
using DualSenseClient.Controllers.DualSense.Input;
using DualSenseClient.Controllers.Emulation;
using DualSenseClient.Emulation.Devices.DualSense;
using DualSenseClient.Hid;
using PhysicalPad = DualSenseClient.Controllers.Devices.DualSenseDevice;
using VirtualPad = DualSenseClient.Emulation.Devices.DualSense.DualSenseDevice;

namespace MadSense.Mac;

internal sealed class BridgeSession : IDisposable
{
    private readonly VirtualPad persona;
    private readonly MacVirtualHid virtualHid;
    private readonly PhysicalPad physical;
    private readonly VirtualDualSenseController adapter;
    private readonly HidDevice hid;
    private readonly ActivityHidDevice meteredHid;
    private readonly MacAudioCapture? audio;
    private readonly string? audioUnavailable;
    private readonly long started;
    private long reports;
    private string? fault;
    private int stopping;
    public long Reports => Interlocked.Read(ref reports);
    public int Battery => physical.InputReport?.Battery.DisplayPercentage ?? -1;
    public bool? Charging => ChargingFromReport(physical.InputReport);
    public ControllerActivity Activity => Volatile.Read(ref stopping) != 0 ? default : meteredHid.Activity;
    public string? Fault => Volatile.Read(ref fault);
    public bool AudioAvailable => audio != null && audio.Fault == null;
    public string? AudioDetail => audio?.Fault ?? audioUnavailable;
    public void SetSpeaker(string value) => audio?.SetSpeaker(value);
    public object AudioStatus => new { available = AudioAvailable, detail = AudioDetail,
        activeClients = audio?.ActiveClients ?? 0, droppedFrames = audio?.DroppedFrames ?? 0 };
    public bool Connected
    {
        get
        {
            if (Reports == 0 && Environment.TickCount64 - started > 5000)
                Volatile.Write(ref fault, "The controller did not send full input reports within five seconds.");
            return Fault == null && hid.IsExclusiveOpen && physical.IsConnected;
        }
    }

    internal static bool? ChargingFromReport(InputReport? report) => report is { } input
        ? input.Battery.PowerState is BatteryPowerState.Charging or BatteryPowerState.ChargingComplete
        : null;

    public BridgeSession(HidDeviceEnumerator enumerator, IHidDeviceInfo info, string speaker = "high")
    {
        bool edge = info.ProductId == 0x0df2;
        persona = new VirtualPad(new DualSenseIdentity { SerialNumber = "MadSense-Mac-Virtual" }, edge);
        HidDevice? opened = null;
        PhysicalPad? pad = null;
        VirtualDualSenseController? forwarding = null;
        MacVirtualHid? published = null;
        try
        {
            opened = enumerator.OpenDeviceExclusive(info.Path);
            hid = opened;
            meteredHid = new ActivityHidDevice(opened);
            pad = edge ? new DualSenseEdgeDevice(meteredHid, info) : new PhysicalPad(meteredHid, info);
            physical = pad;
            physical.ThrowOnAudioWriteFailure = true;
            physical.BluetoothAudioSessionBufferLengths = [0x40, 0x40, 0x40, 0x40, 0x20];
            // Reading calibration also switches the controller into full Bluetooth report mode.
            byte[] calibration = physical.GetFeatureReport(0x05, 41);
            if (!persona.TrySetMotionCalibration(calibration))
                throw new IOException("The controller did not provide valid motion calibration.");
            var outputs = new TrackedOutputs(physical, error => Volatile.Write(ref fault, error));
            adapter = forwarding = new VirtualDualSenseController(persona, outputs, physical.UsesVibrationV2);
            // Publish only after calibration and output delivery are ready: host
            // drivers can cache features and send effects during enumeration.
            virtualHid = published = new MacVirtualHid(persona, edge, () => Fault != null);
            try { audio = new MacAudioCapture(outputs, adapter, () => outputs.AudioFault, speaker); }
            catch (Exception e) { audioUnavailable = e.Message; Console.Error.WriteLine(e.Message); }
            physical.InputReportReceived += OnInput;
            started = Environment.TickCount64;
        }
        catch
        {
            // Callbacks must drain before the physical handle disappears.
            try { published?.Dispose(); }
            finally
            {
                audio?.Dispose(); forwarding?.Dispose();
                if (pad != null) pad.Dispose(); else opened?.Dispose();
            }
            throw;
        }
    }

    private void OnInput(object? sender, InputReport report)
    {
        if (Volatile.Read(ref stopping) != 0) return;
        try
        {
            adapter.PushInput(report);
            if (audio != null) audio.PlayToHeadset = report.Connection.Headphone;
            byte[] bytes = new byte[64];
            int count = persona.ProvideInputReport(bytes);
            if (count != bytes.Length) throw new IOException("Invalid virtual input report length.");
            virtualHid.Send(bytes);
            Interlocked.Increment(ref reports);
        }
        catch (Exception e) { Volatile.Write(ref fault, e.Message); }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref stopping, 1) != 0) return;
        physical.InputReportReceived -= OnInput;
        try { virtualHid.Dispose(); }
        finally
        {
            try { audio?.Dispose(); }
            finally
            {
                adapter.Dispose();
                try { physical.ResetOutputs(); }
                finally { physical.Dispose(); }
            }
        }
    }
}
