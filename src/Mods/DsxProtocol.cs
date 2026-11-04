using DualSenseClient.Controllers.DualSense.Output;
using DualSenseClient.Controllers.DualSense.Triggers;

namespace MadSense.Mods;

/// <summary>
/// The UDP protocol that DualSense game mods speak to DSX (JSON datagrams on 127.0.0.1:6969):
/// instruction codes, trigger modes and their parameter order, as the public mods send them.
/// The numbering is the DSX v2 example's enum extended by the v3 modes (20-26); DSX v3 moved
/// GetDSXStatus to 0 and the legacy example had it at 8, so both are accepted as a status query.
/// </summary>
static class DsxProtocol
{
    public const int DefaultPort = 6969;

    public enum Instruction
    {
        GetDsxStatus = 0, TriggerUpdate = 1, RgbUpdate = 2, PlayerLed = 3, TriggerThreshold = 4,
        MicLed = 5, PlayerLedNewRevision = 6, ResetToUserSettings = 7, LegacyGetDsxStatus = 8,
    }

    public enum Trigger { Invalid = 0, Left = 1, Right = 2 }

    public enum Mode
    {
        Normal = 0, GameCube = 1, VerySoft = 2, Soft = 3, Hard = 4, VeryHard = 5, Hardest = 6, Rigid = 7,
        VibrateTrigger = 8, Choppy = 9, Medium = 10, VibrateTriggerPulse = 11, CustomTriggerValue = 12,
        Resistance = 13, Bow = 14, Galloping = 15, SemiAutomaticGun = 16, AutomaticGun = 17, Machine = 18,
        VibrateTrigger10Hz = 19,
        // DSX v3 names: the pad's own effect blocks with Sony's parameter order.
        Off = 20, Feedback = 21, Weapon = 22, Vibration = 23, SlopeFeedback = 24,
        MultiplePositionFeedback = 25, MultiplePositionVibration = 26,
    }

    /// <summary>
    /// CustomTriggerValue sub-modes (parameter 0) to effect mode byte, as the pydualsense table the
    /// mods were written against: Off, Rigid, RigidA, RigidB, RigidAB, Pulse, PulseA, PulseB, PulseAB,
    /// VibrateResistance(A/B/AB), VibratePulse(A/B/AB).
    /// </summary>
    static readonly byte[] CustomModes =
    [
        0x00, 0x01, 0x21, 0x05, 0x25, 0x02, 0x22, 0x06, 0x26,
        0x06, 0x26, 0x06, 0x26, 0x06, 0x26, 0x06, 0x26,
    ];

    /// <summary>
    /// Builds the 11-byte effect block for a TriggerUpdate: <paramref name="mode"/> and the parameters
    /// that follow the trigger selector. Out-of-range values are clamped the way the pad clamps them,
    /// never thrown: a mod must not be able to stop the receiver with one odd packet.
    /// </summary>
    public static TriggerEffectBlock Effect(Mode mode, int[] p)
    {
        int At(int i, int fallback = 0) => i < p.Length ? p[i] : fallback;
        byte Pos(int i, int max = 9, int fallback = 0) => (byte)Math.Clamp(At(i, fallback), 0, max);
        byte Str(int i, int fallback = 0) => (byte)Math.Clamp(At(i, fallback), 0, 8);
        byte Hz(int i, int fallback = 0) => (byte)Math.Clamp(At(i, fallback), 0, 255);
        try
        {
            switch (mode)
            {
                case Mode.Normal or Mode.Off: return TriggerEffectBuilder.Off();
                case Mode.VerySoft: return TriggerEffectBuilder.Feedback(0, 1);
                case Mode.Soft: return TriggerEffectBuilder.Feedback(0, 3);
                case Mode.Medium: return TriggerEffectBuilder.Feedback(0, 4);
                case Mode.Hard: return TriggerEffectBuilder.Feedback(0, 6);
                case Mode.VeryHard: return TriggerEffectBuilder.Feedback(0, 7);
                case Mode.Hardest or Mode.Rigid: return TriggerEffectBuilder.Feedback(0, 8);
                case Mode.GameCube: return TriggerEffectBuilder.Feedback(8, 8);
                case Mode.Choppy: return TriggerEffectBuilder.MultiplePositionFeedback([8, 0, 8, 0, 8, 0, 8, 0, 8, 0]);
                case Mode.VibrateTrigger: return TriggerEffectBuilder.Vibration(0, 8, (byte)Math.Clamp(At(0, 10), 1, 255));
                case Mode.VibrateTriggerPulse: return TriggerEffectBuilder.Vibration(0, 4, 10);
                case Mode.VibrateTrigger10Hz: return TriggerEffectBuilder.Vibration(0, 8, 10);
                case Mode.Resistance or Mode.Feedback: return TriggerEffectBuilder.Feedback(Pos(0), Str(1));
                case Mode.SemiAutomaticGun or Mode.Weapon:
                {
                    byte start = (byte)Math.Clamp(At(0), 2, 7), end = (byte)Math.Clamp(At(1), start + 1, 8);
                    return TriggerEffectBuilder.Weapon(start, end, Str(2));
                }
                case Mode.AutomaticGun or Mode.Vibration:
                    return Str(1) == 0 ? TriggerEffectBuilder.Off() : TriggerEffectBuilder.Vibration(Pos(0), Str(1), (byte)Math.Clamp(At(2), 1, 255));
                case Mode.Bow:
                {
                    byte start = Pos(0, 8), end = (byte)Math.Clamp(At(1), start + 1, 8);
                    return TriggerEffectBuilder.Bow(start, end, Str(2), Str(3));
                }
                case Mode.Galloping:
                {
                    byte start = Pos(0, 8), end = (byte)Math.Clamp(At(1), start + 1, 9);
                    return TriggerEffectBuilder.Galloping(start, end, (byte)Math.Clamp(At(2), 0, 7), (byte)Math.Clamp(At(3), 0, 7), Hz(4));
                }
                case Mode.Machine:
                {
                    byte start = Pos(0, 8), end = (byte)Math.Clamp(At(1), start + 1, 9);
                    return TriggerEffectBuilder.Machine(start, end, (byte)Math.Clamp(At(2), 0, 7), (byte)Math.Clamp(At(3), 0, 7), Hz(4), Hz(5));
                }
                case Mode.SlopeFeedback:
                {
                    byte start = Pos(0, 8), end = (byte)Math.Clamp(At(1), start + 1, 9);
                    return TriggerEffectBuilder.SlopeFeedback(start, end, (byte)Math.Clamp(At(2), 1, 8), (byte)Math.Clamp(At(3), 1, 8));
                }
                case Mode.MultiplePositionFeedback:
                {
                    var strength = new byte[10];
                    for (int i = 0; i < 10; i++) strength[i] = Str(i);
                    return TriggerEffectBuilder.MultiplePositionFeedback(strength);
                }
                case Mode.MultiplePositionVibration:
                {
                    var amplitude = new byte[10];
                    for (int i = 0; i < 10; i++) amplitude[i] = Str(1 + i);
                    return TriggerEffectBuilder.MultiplePositionVibration(Hz(0), amplitude);
                }
                case Mode.CustomTriggerValue:
                {
                    var raw = new byte[11];
                    int sub = At(0);
                    raw[0] = sub >= 0 && sub < CustomModes.Length ? CustomModes[sub] : (byte)0;
                    for (int i = 0; i < 7; i++) raw[1 + i] = (byte)Math.Clamp(At(1 + i), 0, 255);
                    return new TriggerEffectBlock(raw, 0);
                }
                default: return TriggerEffectBuilder.Off();
            }
        }
        catch (ArgumentOutOfRangeException)
        {
            // A combination the pad's own rules reject (an end zone below its start, say) becomes
            // "no effect" instead of an error: the mod keeps running and its next packet may fix it.
            return TriggerEffectBuilder.Off();
        }
    }

    /// <summary>PlayerLEDNewRevision 0-4 = players one to five (the DSX patterns), 5 = all off.</summary>
    public static PlayerLedMask PlayerPattern(int value) => value switch
    {
        0 => (PlayerLedMask)0x04, 1 => (PlayerLedMask)0x0A, 2 => (PlayerLedMask)0x15, 3 => (PlayerLedMask)0x1B, 4 => (PlayerLedMask)0x1F,
        _ => PlayerLedMask.None,
    };

    /// <summary>MicLED 0 = on, 1 = pulse, 2 = off, as the pad's mute-light mode byte (1 on, 2 pulse, 0 off).</summary>
    public static byte MicLedMode(int value) => value switch { 0 => 1, 1 => 2, _ => 0 };

    public static bool IsStatusQuery(int type) => type is (int)Instruction.GetDsxStatus or (int)Instruction.LegacyGetDsxStatus;
}
