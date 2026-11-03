using DualSenseClient.Controllers.DualSense.Enum;
using DualSenseClient.Controllers.DualSense.Input;

namespace MadSense;

internal static class BatteryPresentation
{
    public static UiMessage Connection(BatteryState? battery) => battery?.PowerState switch
    {
        BatteryPowerState.Charging => L.Message("Bridge.Charging"),
        BatteryPowerState.ChargingComplete => L.Message("Bridge.Charged"),
        _ => L.Message("Bridge.Battery", battery?.Percentage?.ToString() ?? "?")
    };

    public static string Tray(int percentage, bool charging) => charging
        ? L.Text(percentage >= 100 ? "Ui.TrayCharged" : "Ui.TrayCharging")
        : percentage >= 0 ? L.Text("Ui.TrayBattery", percentage) : "";
}
