using System.Text.Json;
using Nefarius.Drivers.HidHide;

namespace MadSense;

/// <summary>Shared by ordinary bridge shutdown and installer maintenance; failed recovery never deletes the journal.</summary>
internal static class HiddenControllerRecovery
{
    internal const string JournalName = "hidden-controller.json";

    internal static void Recover(string directory, Func<IHidHideRecoveryState> createState)
    {
        var journal = Path.Combine(directory, JournalName);
        if (!File.Exists(journal)) return;
        var json = File.ReadAllText(journal);
        var lease = json.TrimStart().StartsWith('"')
            ? new HideLease(JsonSerializer.Deserialize<string>(json)!, false, true)
            : JsonSerializer.Deserialize<HideLease>(json);
        if (lease == null || string.IsNullOrWhiteSpace(lease.Id) || !lease.Id.StartsWith("HID\\", StringComparison.OrdinalIgnoreCase))
            throw new IOException("Registro de recuperación HidHide no válido.");

        var state = createState();
        if (!state.IsAvailable) throw new IOException("HidHide no está operativo; reinicia Windows y vuelve a intentarlo.");
        state.SetHidden(lease.Id, lease.WasHidden);
        state.Active = lease.WasActive;
        // The strict adapter propagates driver read failures instead of treating them as 'false'.
        if (state.IsHidden(lease.Id) != lease.WasHidden || state.Active != lease.WasActive)
            throw new IOException("No se pudo restaurar HidHide. Se conserva el registro de recuperación.");
        File.Delete(journal);
    }
}

internal sealed record HideLease(string Id, bool WasHidden, bool WasActive);

internal interface IHidHideRecoveryState
{
    bool IsAvailable { get; }
    bool Active { get; set; }
    bool IsHidden(string instanceId);
    void SetHidden(string instanceId, bool hidden);
}

internal sealed class HidHideRecoveryState : IHidHideRecoveryState
{
    private readonly HidHideControlService driver = new();
    public bool IsAvailable => driver.IsOperational;
    public bool Active { get => driver.IsActive; set => driver.IsActive = value; }
    public bool IsHidden(string instanceId) => driver.BlockedInstanceIds.Contains(instanceId, StringComparer.OrdinalIgnoreCase);
    public void SetHidden(string instanceId, bool hidden)
    {
        if (IsHidden(instanceId) == hidden) return;
        if (hidden) driver.AddBlockedInstanceId(instanceId);
        else driver.RemoveBlockedInstanceId(instanceId);
    }
}
