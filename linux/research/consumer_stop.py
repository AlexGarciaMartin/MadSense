"""Narrow SDL selector-clear forwarding proof, never physical motor-stop proof."""
import base64

from consumer_phases import correlate
from consumer_session import expected_motors, require


# Exact public DualSenseOutputReport schema; unknown or missing fields fail.
# Reserved USB bytes are not represented here. Do not call this raw USB proof.
BOOLEAN_FIELDS = frozenset("""rumbleEmulationEnabled useRumbleNotHaptics
rightTriggerEffectAllowed leftTriggerEffectAllowed headphoneVolumeAllowed
speakerVolumeAllowed micVolumeAllowed audioControlAllowed muteLightAllowed
audioMuteAllowed lightbarColorAllowed resetLights playerIndicatorsAllowed
hapticLowPassFilterAllowed motorPowerLevelAllowed audioControl2Allowed
lightBrightnessAllowed lightFadeAllowed improvedRumbleEmulationEnabled
useRumbleNotHaptics2 hapticLowPassFilter""".split())
INTEGER_FIELDS = frozenset("""rumbleRight rumbleLeft headphoneVolume speakerVolume
micVolume audioControl muteLightMode muteControl rightTriggerEffectMode
leftTriggerEffectMode hostTimestamp motorPowerLevel audioControl2
lightFadeAnimation lightBrightness playerLeds red green blue""".split())
ARRAY_FIELDS = frozenset(("rightTriggerEffect", "leftTriggerEffect"))


def decoded_zero(value):
    require(isinstance(value, dict) and set(value) == BOOLEAN_FIELDS | INTEGER_FIELDS | ARRAY_FIELDS,
            "Stop decoded USB schema is incomplete or changed")
    require(all(value[name] is False for name in BOOLEAN_FIELDS), "Stop decoded USB has active flags")
    require(all(type(value[name]) is int and value[name] == 0 for name in INTEGER_FIELDS),
            "Stop decoded USB has nonzero or malformed fields")
    require(all(isinstance(value[name], str) and base64.b64decode(value[name], validate=True) == bytes(11)
                for name in ARRAY_FIELDS), "Stop decoded USB has nonzero or malformed trigger arrays")


def explicit_stop_delivery(record, ready, core_ready, core_rows, bt_rows):
    observed = record.get("evidence", {})
    require(ready.get("vendorId") == 0x054c and ready.get("productId") in (0x0ce6, 0x0df2)
            and observed.get("path") == ready.get("hidrawPath"), "Selector-clear proof requires exact Sony hidraw path")
    require(observed.get("sdlMajor") in (2, 3)
            and observed.get("version") in {2: ("2.30.0", "2.32.4"), 3: ("3.2.28",)}.get(observed.get("sdlMajor"), ()),
            "Selector-clear proof is limited to the audited SDL builds")
    require(record.get("exitCode") == 0 and observed.get("result") == "passed"
            and observed.get("identityVerified") is True and observed.get("inputMatches") is True,
            "SDL API and identity assertions must pass independently")
    require(observed.get("marker", {}).get("rumbleAccepted") is True
            and observed.get("marker", {}).get("stopAccepted") is True, "SDL did not accept rumble and explicit stop")
    require(core_ready.get("ownedGuestToken") == ready["ownedGuestToken"], "Core and consumer guest identity differ")
    left, right = expected_motors(ready, observed)
    correlation = correlate(observed, record, core_rows, bt_rows, core_ready["timestampFrequency"], left, right)
    phases = correlation["phases"]
    require(phases["rumble"]["usbSelectedMarkerCount"] == phases["rumble"]["btSelectedMarkerCount"] == 1,
            "Explicit stop requires one earlier nonzero marker on both sides")
    stop = phases["explicit-stop"]
    require(len(stop["decodedUsb02"]) == len(stop["bluetoothOutputs"]) == 1,
            "Explicit stop requires one unambiguous report on each side of the isolated phase")
    usb, bt = stop["decodedUsb02"][0], stop["bluetoothOutputs"][0]
    decoded_zero(usb["decoded"])
    data = bytes.fromhex(bt["hex"])
    require(len(data) == 78 and data[0] == 0x31 and bt["classification"] is not None
            and bt["classification"]["payload47Hex"] == bytes(47).hex(),
            "Explicit stop Bluetooth report must have a complete zero 47-byte state payload")
    raw_enabled = core_ready.get("rawOutputObservationEnabled", False)
    require(type(raw_enabled) is bool, "Unknown raw USB capture capability")
    raw = None
    if raw_enabled:
        captured = [row for row in core_rows if row.get("kind") == "virtual-output-raw"
                    and stop["startedMonotonicNs"] <= row["timestamp"] < stop["endedMonotonicNsExclusive"]]
        require(len(captured) == 1, "Explicit stop raw USB observation is missing or ambiguous")
        raw = captured[0]
        value = raw["value"]
        require(type(value.get("length")) is int and value["length"] == 48
                and bytes.fromhex(value["hex"]) == b"\x02" + bytes(47),
                "Explicit stop raw USB must be exact report02 plus47 zero bytes")
        correlation.update(completeUsbBytesCaptured=True,
            usbEvidence="raw USB report 0x02 bytes and decoded fields", stopRawUsb=raw)
    rgb = observed.get("capabilities", {}).get("rgbLed") is True
    if rgb:
        require(observed["marker"].get("ledAccepted") is True
                and sum(bool(row["classification"] and row["classification"]["rgbMarker"])
                        for row in phases["led"]["decodedUsb02"]) == 1
                and sum(bool(row["classification"] and row["classification"]["rgbMarker"])
                        for row in phases["led"]["bluetoothOutputs"]) == 1,
                "RGB marker must arrive separately in the later LED phase")
    return dict(outputDeliveryVerified=True, stopReportForwarded=True,
        stopSemantics="explicit-selector-clear", physicalActuationVerified=False,
        audioHapticsResumptionVerified=False, completeUsbBytesCaptured=raw_enabled,
        requiresCleanCoreRawObserverAtShutdown=raw_enabled,
        causalOneToOneMappingVerified=False, latencyValidated=False,
        stopDecodedUsbMonotonicNs=usb["monotonicNs"], stopBluetoothMonotonicNs=bt["monotonicNs"],
        stopBluetoothHex=bt["hex"], stopDecodedUsb=usb["decoded"], stopRawUsb=raw, correlation=correlation)
