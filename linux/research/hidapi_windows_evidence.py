"""Independent real-HIDAPI observation join; does not change Win32 count gates."""
from consumer_session import bt_state, require

COMMIT = "d6b2a974608dec3b76fb1e36c189f22b9cf3650c"


def one(rows, kind):
    found = [row for row in rows if row.get("kind") == kind]
    require(len(found) == 1, "Expected one " + kind)
    return found[0]


def verify(ready, command, core_rows, bluetooth_rows):
    require(ready.get("rawOutputObservationEnabled") is True, "Complete raw USB capture required")
    require(type(ready.get("timestampFrequency")) is int and ready["timestampFrequency"] == 1_000_000_000,
            "Core capture must use the same owned Linux monotonic nanosecond clock")
    require(command.get("exitCode") == 0, "HIDAPI consumer process failed")
    start, end = command["startedMonotonicNs"], command["endedMonotonicNs"]
    require(type(start) is int and type(end) is int and 0 < start < end, "Invalid command interval")
    rows = command["evidence"]
    runtime = one(rows, "runtime")
    require(runtime.get("scope") == "real-hidapi-windows-consumer" and runtime.get("hidapiVersion") == "0.15.0"
            and runtime.get("hidapiCommit") == COMMIT and runtime.get("physicalDevices") is False
            and runtime.get("protonValidated") is False and isinstance(runtime.get("wineVersion"), str)
            and runtime["wineVersion"], "Unknown library/runtime scope")
    require(one(rows, "result").get("result") == "observed", "HIDAPI observation did not finish")
    candidates = [row for row in rows if row.get("kind") == "hidapi-candidate" and row.get("macMatched") is True]
    require(len(candidates) == 1, "HIDAPI path was not unambiguous")
    features = [row for row in rows if row.get("kind") == "hidapi-feature"]
    expected_features = ready["features"]
    require(len(features) == 3 and {row.get("reportId") for row in features} == {9, 32, 5}, "Incomplete feature set")
    for row in features:
        key = f"{row['reportId']:02x}"
        value = bytes.fromhex(row["hex"])
        require(value.hex() == expected_features[key].lower() and type(row.get("returnValue")) is int
                and len(value) <= row["returnValue"] <= 64 and row.get("bufferLength") == 64
                and row.get("meaningfulLength") == len(value), "HIDAPI feature bytes/length differ")
    pairing = bytes.fromhex(expected_features["09"])
    require(pairing[1:7][::-1].hex() == ready["virtualMacHex"], "Ready MAC/features disagree")
    observed_input = one(rows, "hidapi-input")
    data = bytes.fromhex(observed_input["hex"])
    state = command["fixtureState"]
    require(state in ("south", "neutral") and observed_input.get("state") == state
            and observed_input.get("returnValue") == 64 and len(data) == 64
            and data[0:7] == bytes([1, 128, 128, 128, 128, 0, 0])
            and data[8] == (0x28 if state == "south" else 0x08) and data[9] == 0 and data[10] & 7 == 0,
            "HIDAPI input did not match the independent fixture")
    write = one(rows, "hidapi-write")
    count = write.get("returnValue")
    require(type(count) is int and count in (47, 48) and write.get("requested") == 48
            and write.get("libraryReportedError") is False and write.get("fullRequestedCount") is (count == 48)
            and write.get("outputDeliveryVerified") is False and write.get("nativeWindowsParityVerified") is False,
            "Unexpected HIDAPI write result; do not infer delivery from any nonnegative count")
    output = bytearray(48); output[0] = 2; output[2] = 4; output[45:48] = bytes.fromhex("123456")
    require(bytes.fromhex(write["hex"]) == output, "HIDAPI marker bytes changed")
    usb = [row for row in core_rows if row.get("kind") == "virtual-output-raw"
           and start <= row["timestamp"] <= end and row["value"].get("length") == 48
           and bytes.fromhex(row["value"]["hex"]) == output]
    require(len(usb) == 1, "Expected exactly one complete HIDAPI USB marker in its command interval")
    bluetooth = []
    for row in bluetooth_rows:
        if row.get("kind") == "output" and usb[0]["timestamp"] <= row["monotonicNs"] <= end:
            if bt_state(bytes.fromhex(row["hex"])) == bytes(output[1:]):
                bluetooth.append(row)
    require(len(bluetooth) == 1, "Expected one CRC-valid complete matching Bluetooth state after USB marker")
    return dict(scope="real-hidapi-synthetic-report-forwarding", hidapiVersion="0.15.0", returnValue=count,
        libraryReportedError=False, fullRequestedCount=count == 48, nativeWindowsParityVerified=False,
        rawUsbReportVerified=True, crcValidBluetoothStateVerified=True, outputReportForwarded=True,
        usbMonotonicNs=usb[0]["timestamp"], bluetoothMonotonicNs=bluetooth[0]["monotonicNs"],
        physicalActuationVerified=False, gamesValidated=False,
        finalCoreCaptureDrainStillRequired=True)
