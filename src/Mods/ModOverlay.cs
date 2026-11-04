using DualSenseClient.Controllers.DualSense.Output;
using DualSenseClient.Controllers.DualSense.Triggers;

namespace MadSense.Mods;

/// <summary>
/// What a game mod currently owns on the pad: each trigger, the lightbar, the player LEDs and the
/// mic LED. The bridge installs <see cref="Apply"/> as the outputs adapter's state filter, so every
/// report that reaches the pad (the game's own 0x31, the audio-lane prime and every combined 0x36)
/// carries the mod's values in place of the game's for the channels the mod owns. A mod has
/// priority over the game, as in DSX: the game keeps driving everything the mod does not touch.
/// Thread-safe; the filter runs on the writer threads.
/// </summary>
sealed class ModOverlay
{
    readonly object gate = new();
    TriggerEffectBlock? left, right;
    (byte R, byte G, byte B)? lightbar;
    PlayerLedMask? players;
    byte? mute;
    int version;

    /// <summary>Raised after any change; the subscriber pushes a standalone report so an idle pad reacts now.</summary>
    public event Action? Changed;

    public bool OwnsAnything { get { lock (gate) return left.HasValue || right.HasValue || lightbar.HasValue || players.HasValue || mute.HasValue; } }
    public bool OwnsTriggers { get { lock (gate) return left.HasValue || right.HasValue; } }
    /// <summary>Increments on every change; tells "new effect" from "same effect" without comparing blocks.</summary>
    public int Version => Volatile.Read(ref version);

    public void SetTrigger(DsxProtocol.Trigger trigger, TriggerEffectBlock effect)
    {
        lock (gate)
        {
            if (trigger == DsxProtocol.Trigger.Left) left = effect; else right = effect;
            version++;
        }
        Changed?.Invoke();
    }

    public void SetLightbar(byte r, byte g, byte b) { lock (gate) { lightbar = (r, g, b); version++; } Changed?.Invoke(); }
    public void SetPlayerLeds(PlayerLedMask mask) { lock (gate) { players = mask; version++; } Changed?.Invoke(); }
    public void SetMicLed(byte mode) { lock (gate) { mute = mode; version++; } Changed?.Invoke(); }

    /// <summary>
    /// Releases everything the mod owned. The caller sends <see cref="Release"/> once (the pad keeps an
    /// effect until a report with its allow bit replaces it); the lights go back to the game's next report.
    /// </summary>
    public bool Clear()
    {
        bool had;
        lock (gate)
        {
            had = left.HasValue || right.HasValue || lightbar.HasValue || players.HasValue || mute.HasValue;
            left = right = null; lightbar = null; players = null; mute = null;
            if (had) version++;
        }
        if (had) Changed?.Invoke();
        return had;
    }

    /// <summary>The filter: a copy of <paramref name="state"/> with the owned channels replaced.</summary>
    public SetStateData Apply(SetStateData state)
    {
        TriggerEffectBlock? l, r; (byte R, byte G, byte B)? color; PlayerLedMask? leds; byte? mic;
        lock (gate) { l = left; r = right; color = lightbar; leds = players; mic = mute; }
        if (!l.HasValue && !r.HasValue && !color.HasValue && !leds.HasValue && !mic.HasValue) return state;
        return Merge(state, l, r, color, leds, mic);
    }

    /// <summary>
    /// A report carrying only the owned channels, for the moment the mod changes something while the
    /// game is quiet: the standalone 0x31 path applies it at once when the audio lane is closed, and
    /// the next combined report carries it anyway when the lane is open.
    /// </summary>
    public SetStateData Standalone()
    {
        TriggerEffectBlock? l, r; (byte R, byte G, byte B)? color; PlayerLedMask? leds; byte? mic;
        lock (gate) { l = left; r = right; color = lightbar; leds = players; mic = mute; }
        return Merge(new SetStateData(), l, r, color, leds, mic);
    }

    /// <summary>A report that turns both triggers off, sent once when a mod releases them.</summary>
    public static SetStateData Release() => new SetStateData
    {
        ValidFlag0 = ValidFlags.AllowLeftTriggerFfb | ValidFlags.AllowRightTriggerFfb,
        L2TriggerEffect = TriggerEffectBuilder.Off(),
        R2TriggerEffect = TriggerEffectBuilder.Off(),
    };

    static SetStateData Merge(SetStateData state, TriggerEffectBlock? l, TriggerEffectBlock? r, (byte R, byte G, byte B)? color, PlayerLedMask? leds, byte? mic)
    {
        // `with` writes through the struct's buffer: work on a private copy so the caller's
        // (possibly cached) state is never mutated.
        var raw = new byte[SetStateData.PayloadSize];
        state.CopyTo(raw, 0);
        var copy = new SetStateData(raw, 0);
        var flag0 = copy.ValidFlag0; var flag1 = copy.ValidFlag1;
        if (l.HasValue) { flag0 |= ValidFlags.AllowLeftTriggerFfb; copy = copy with { L2TriggerEffect = l.Value }; }
        if (r.HasValue) { flag0 |= ValidFlags.AllowRightTriggerFfb; copy = copy with { R2TriggerEffect = r.Value }; }
        if (color is { } c) { flag1 |= ValidFlags.AllowLedColor; copy = copy with { LedRed = c.R, LedGreen = c.G, LedBlue = c.B }; }
        if (leds is { } mask) { flag1 |= ValidFlags.AllowPlayerIndicators; copy = copy with { PlayerLeds = mask }; }
        if (mic is { } mode) { flag1 |= ValidFlags.AllowMuteLight; copy = copy with { MuteLedMode = mode }; }
        return copy with { ValidFlag0 = flag0, ValidFlag1 = flag1 };
    }
}
