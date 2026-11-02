using DualSenseClient.Controllers.Devices;
using DualSenseClient.Controllers.DualSense.Input;
using DualSenseClient.Controllers.Emulation;
using DualSenseClient.Controllers.SpecialActions;
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
    private readonly SpecialActionEngine actions = new();
    private readonly HidDevice hid;
    private long reports;
    private string? fault;
    private int stopping;
    public long Reports => Interlocked.Read(ref reports);
    public int Battery => physical.InputReport?.Battery.DisplayPercentage ?? -1;
    public string? Fault => Volatile.Read(ref fault);
    public bool Connected => Fault == null && hid.IsExclusiveOpen && physical.IsConnected;

    public BridgeSession(HidDeviceEnumerator enumerator, IHidDeviceInfo info)
    {
        bool edge = info.ProductId == 0x0df2;
        persona = new VirtualPad(new DualSenseIdentity { SerialNumber = "MadSense-Mac-Virtual" }, edge);
        // Check entitlement before seizing the real pad: failure must leave native input usable.
        virtualHid = new MacVirtualHid(persona, edge);
        HidDevice? opened = null;
        PhysicalPad? pad = null;
        try
        {
            opened = enumerator.OpenDeviceExclusive(info.Path);
            hid = opened;
            pad = edge ? new DualSenseEdgeDevice(opened, info) : new PhysicalPad(opened, info);
            physical = pad;
            physical.BluetoothAudioSessionBufferLengths = [0x40, 0x40, 0x40, 0x40, 0x20];
            // Reading calibration also switches the controller into full Bluetooth report mode.
            byte[] calibration = physical.GetFeatureReport(0x05, 41);
            if (!persona.TrySetMotionCalibration(calibration))
                throw new IOException("The controller did not provide valid motion calibration.");
            adapter = new VirtualDualSenseController(persona, new DualSenseDeviceOutputs(physical, actions), physical.UsesVibrationV2);
            physical.InputReportReceived += OnInput;
        }
        catch
        {
            // Callbacks must drain before the physical handle disappears.
            try { virtualHid.Dispose(); }
            finally { if (pad != null) pad.Dispose(); else opened?.Dispose(); actions.Dispose(); }
            throw;
        }
    }

    private void OnInput(object? sender, InputReport report)
    {
        if (Volatile.Read(ref stopping) != 0) return;
        try
        {
            adapter.PushInput(report);
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
            adapter.Dispose();
            try { physical.ResetOutputs(); }
            finally { physical.Dispose(); actions.Dispose(); }
        }
    }
}
