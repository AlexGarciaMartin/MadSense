import Foundation
import IOKit
import CoreAudio
import GameController

// Read-only inventory: no HID open/seize, report writes, audio IO, virtual
// devices, recording, default-device changes, or GameController effect engines.
// A property/profile is a capability observation, never a hardware/game test.
func usbAudioLocation(_ uid: String) -> UInt32? {
    guard uid.hasPrefix("AppleUSBAudioEngine:") else { return nil }
    let parts = uid.split(separator: ":", omittingEmptySubsequences: false)
    guard parts.count >= 5, let locationText = parts.dropLast().last,
          !locationText.isEmpty, !parts.last!.isEmpty,
          locationText.allSatisfy({ $0.isASCII && $0.isHexDigit }),
          parts.last!.allSatisfy({ $0.isASCII && $0.isNumber }),
          let location = UInt32(locationText, radix: 16), location != 0 else { return nil }
    return location
}

func locationHex(_ value: UInt32) -> String { String(format: "0x%08x", value) }
func physicalTransport(_ transport: String) -> Bool? {
    switch transport.lowercased() {
    case "usb", "bluetooth": return true
    case "virtual": return false
    default: return nil
    }
}

final class InventoryDiagnostics {
    private(set) var issues: [[String: Any]] = []
    private(set) var enumerationComplete = true
    func record(_ source: String, _ operation: String, status: Int32? = nil,
                enumerationFailure: Bool = false) {
        var issue: [String: Any] = ["source": source, "operation": operation,
                                  "enumeration_failure": enumerationFailure]
        if let status = status {
            issue["status"] = status
            issue["status_hex"] = String(format: "0x%08x", UInt32(bitPattern: status))
        }
        issues.append(issue)
        if enumerationFailure { enumerationComplete = false }
    }
}
func registryNumber(_ properties: [String: Any], _ key: String) -> UInt32? {
    guard let value = properties[key] as? NSNumber, value.int64Value >= 0,
          value.uint64Value <= UInt64(UInt32.max) else { return nil }
    return value.uint32Value
}

func hidInventory(_ diagnostics: InventoryDiagnostics) -> [[String: Any]] {
    var iterator: io_iterator_t = 0
    let status = IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching("IOHIDDevice"), &iterator)
    guard status == KERN_SUCCESS else {
        diagnostics.record("hid", "IOServiceGetMatchingServices", status: status, enumerationFailure: true)
        return []
    }
    defer { IOObjectRelease(iterator) }
    var result: [[String: Any]] = []
    while true {
        let device = IOIteratorNext(iterator)
        if device == 0 { break }
        defer { IOObjectRelease(device) }
        var rawProperties: Unmanaged<CFMutableDictionary>?
        let propertyStatus = IORegistryEntryCreateCFProperties(device, &rawProperties, kCFAllocatorDefault, 0)
        let retainedProperties = rawProperties?.takeRetainedValue()
        guard propertyStatus == KERN_SUCCESS,
              let properties = retainedProperties as? [String: Any] else {
            diagnostics.record("hid", "IORegistryEntryCreateCFProperties", status: propertyStatus, enumerationFailure: true)
            continue
        }
        guard registryNumber(properties, "VendorID") == 0x054c,
              let product = registryNumber(properties, "ProductID"), [UInt32(0x0ce6), UInt32(0x0df2)].contains(product) else { continue }
        let transport = properties["Transport"] as? String ?? "unknown"
        let location = registryNumber(properties, "LocationID")
        var record: [String: Any] = ["vendor_id": "0x054c", "product_id": String(format: "0x%04x", product),
            "product": properties["Product"] as? String ?? "unknown", "transport": transport,
            "opened": false, "seized": false, "reports_written": 0]
        for key in ["PrimaryUsagePage", "PrimaryUsage", "MaxInputReportSize", "MaxOutputReportSize", "MaxFeatureReportSize"] {
            if let value = registryNumber(properties, key) { record[key] = value }
        }
        // Bluetooth LocationID can encode address bytes. Only publish USB port IDs.
        if transport.caseInsensitiveCompare("USB") == .orderedSame, let location = location, location != 0 {
            record["usb_location_id"] = locationHex(location)
        }
        if let descriptor = properties["ReportDescriptor"] as? Data { record["report_descriptor_bytes"] = descriptor.count }
        result.append(record)
    }
    if IOIteratorIsValid(iterator) == 0 {
        diagnostics.record("hid", "IORegistry iterator invalidated during enumeration", enumerationFailure: true)
    }
    return result
}

func propertyAddress(_ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}
func audioOperation(_ name: String, _ selector: AudioObjectPropertySelector, _ scope: AudioObjectPropertyScope) -> String {
    "\(name) selector=\(locationHex(selector)) scope=\(locationHex(scope))"
}
func readAudioValue(_ device: AudioObjectID, _ selector: AudioObjectPropertySelector,
                    _ scope: AudioObjectPropertyScope, _ destination: UnsafeMutableRawPointer,
                    _ capacity: Int, _ diagnostics: InventoryDiagnostics) -> Bool {
    var address = propertyAddress(selector, scope), size = UInt32(capacity)
    let status = AudioObjectGetPropertyData(device, &address, 0, nil, &size, destination)
    guard status == noErr, Int(size) == capacity else {
        diagnostics.record("audio", audioOperation("Read fixed-size property (expected \(capacity), returned \(size) bytes)", selector, scope), status: status)
        return false
    }
    return true
}
func audioUInt(_ device: AudioObjectID, _ selector: AudioObjectPropertySelector,
               _ diagnostics: InventoryDiagnostics,
               _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> UInt32? {
    var value: UInt32 = 0
    guard readAudioValue(device, selector, scope, &value, MemoryLayout<UInt32>.size, diagnostics) else { return nil }
    return value
}
func audioString(_ device: AudioObjectID, _ selector: AudioObjectPropertySelector, _ diagnostics: InventoryDiagnostics) -> String? {
    var value: CFString? = nil
    guard readAudioValue(device, selector, kAudioObjectPropertyScopeGlobal, &value, MemoryLayout<CFString?>.size, diagnostics) else { return nil }
    guard let value = value else {
        diagnostics.record("audio", audioOperation("Property returned a null string", selector, kAudioObjectPropertyScopeGlobal))
        return nil
    }
    return value as String
}
func audioRate(_ device: AudioObjectID, _ diagnostics: InventoryDiagnostics) -> Double? {
    var rate: Float64 = 0
    guard readAudioValue(device, kAudioDevicePropertyNominalSampleRate, kAudioObjectPropertyScopeGlobal,
                        &rate, MemoryLayout<Float64>.size, diagnostics) else { return nil }
    guard rate.isFinite else { diagnostics.record("audio", "Nominal sample rate is not finite"); return nil }
    return rate
}
func validArrayByteCount(_ size: UInt32, elementSize: Int) -> Bool {
    elementSize > 0 && Int(size) % elementSize == 0
}
func audioIDs(_ device: AudioObjectID, _ selector: AudioObjectPropertySelector,
              _ diagnostics: InventoryDiagnostics,
              _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal,
              enumeration: Bool = false) -> [AudioObjectID] {
    var address = propertyAddress(selector, scope), size: UInt32 = 0
    let sizeStatus = AudioObjectGetPropertyDataSize(device, &address, 0, nil, &size)
    let elementSize = MemoryLayout<AudioObjectID>.stride
    guard sizeStatus == noErr, validArrayByteCount(size, elementSize: elementSize) else {
        diagnostics.record("audio", audioOperation("Read array byte count", selector, scope), status: sizeStatus, enumerationFailure: enumeration)
        return []
    }
    if size == 0 { return [] }
    let capacity = size
    var ids = [AudioObjectID](repeating: 0, count: Int(capacity) / elementSize)
    let status = ids.withUnsafeMutableBytes { AudioObjectGetPropertyData(device, &address, 0, nil, &size, $0.baseAddress!) }
    guard status == noErr, size <= capacity, validArrayByteCount(size, elementSize: elementSize) else {
        diagnostics.record("audio", audioOperation("Read array data (capacity \(capacity), returned \(size) bytes)", selector, scope), status: status, enumerationFailure: enumeration)
        return []
    }
    return Array(ids.prefix(Int(size) / elementSize))
}
func audioBufferCountFits(_ count: UInt32, bytes: UInt32) -> Bool {
    let headerSize = MemoryLayout<AudioBufferList>.offset(of: \.mBuffers)!
    return Int(bytes) >= headerSize && Int(count) <= (Int(bytes) - headerSize) / MemoryLayout<AudioBuffer>.stride
}
func channelCount(_ device: AudioObjectID, _ scope: AudioObjectPropertyScope, _ diagnostics: InventoryDiagnostics) -> UInt32? {
    var address = propertyAddress(kAudioDevicePropertyStreamConfiguration, scope), size: UInt32 = 0
    let sizeStatus = AudioObjectGetPropertyDataSize(device, &address, 0, nil, &size)
    guard sizeStatus == noErr, audioBufferCountFits(0, bytes: size) else {
        diagnostics.record("audio", audioOperation("Read buffer-list byte count", address.mSelector, scope), status: sizeStatus)
        return nil
    }
    let capacity = size
    let storage = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
    defer { storage.deallocate() }
    let status = AudioObjectGetPropertyData(device, &address, 0, nil, &size, storage)
    guard status == noErr, size <= capacity, audioBufferCountFits(0, bytes: size) else {
        diagnostics.record("audio", audioOperation("Read buffer-list data", address.mSelector, scope), status: status)
        return nil
    }
    let count = storage.load(as: UInt32.self)
    guard audioBufferCountFits(count, bytes: size) else {
        diagnostics.record("audio", "Buffer count exceeds returned AudioBufferList length")
        return nil
    }
    let buffers = storage.advanced(by: MemoryLayout<AudioBufferList>.offset(of: \.mBuffers)!).assumingMemoryBound(to: AudioBuffer.self)
    var channels: UInt32 = 0
    for index in 0..<Int(count) {
        let (sum, overflow) = channels.addingReportingOverflow(buffers[index].mNumberChannels)
        guard !overflow else { diagnostics.record("audio", "Audio channel count overflow"); return nil }
        channels = sum
    }
    return channels
}
func streamFormats(_ device: AudioObjectID, _ scope: AudioObjectPropertyScope, _ diagnostics: InventoryDiagnostics) -> [[String: Any]] {
    audioIDs(device, kAudioDevicePropertyStreams, diagnostics, scope).map { stream in
        var item: [String: Any] = [:]
        if let latency = audioUInt(stream, kAudioStreamPropertyLatency, diagnostics) { item["reported_stream_latency_frames"] = latency }
        for (name, selector) in [("virtual_format", kAudioStreamPropertyVirtualFormat), ("physical_format", kAudioStreamPropertyPhysicalFormat)] {
            var value = AudioStreamBasicDescription()
            if readAudioValue(stream, selector, kAudioObjectPropertyScopeGlobal, &value, MemoryLayout<AudioStreamBasicDescription>.size, diagnostics) {
                guard value.mSampleRate.isFinite else { diagnostics.record("audio", "Stream sample rate is not finite"); continue }
                item[name] = ["sample_rate": value.mSampleRate, "channels": value.mChannelsPerFrame,
                    "bits_per_channel": value.mBitsPerChannel, "bytes_per_frame": value.mBytesPerFrame,
                    "format_id": value.mFormatID, "format_flags": value.mFormatFlags] as [String: Any]
            }
        }
        return item
    }
}
func audioInventory(_ hid: [[String: Any]], _ diagnostics: InventoryDiagnostics) -> [[String: Any]] {
    let locations = Set(hid.compactMap { $0["usb_location_id"] as? String })
    return audioIDs(AudioObjectID(kAudioObjectSystemObject), kAudioHardwarePropertyDevices, diagnostics, enumeration: true).compactMap { device in
        let uid = audioString(device, kAudioDevicePropertyDeviceUID, diagnostics)
        let location = uid.flatMap(usbAudioLocation)
        let transport = audioUInt(device, kAudioDevicePropertyTransportType, diagnostics)
        guard transport == kAudioDeviceTransportTypeUSB || (transport == nil && location != nil) else { return nil }
        let name = audioString(device, kAudioObjectPropertyName, diagnostics) ?? "unknown"
        let manufacturer = audioString(device, kAudioObjectPropertyManufacturer, diagnostics) ?? "unknown"
        let sameLocation = location.map { locations.contains(locationHex($0)) } ?? false
        guard sameLocation || name.lowercased().contains("dualsense") ||
                (manufacturer.lowercased().contains("sony") && name.lowercased().contains("controller")) else { return nil }
        var record: [String: Any] = ["name": name, "manufacturer": manufacturer,
            "hid_location_match": sameLocation, "usb_transport_reported": transport == kAudioDeviceTransportTypeUSB,
            "association_basis": location != nil
                ? "AppleUSBAudioEngine UID USB-location heuristic; not a guaranteed CoreAudio ABI or game association"
                : "USB audio device selected by name; UID location was not recognized, no HID association established",
            "input_streams": streamFormats(device, kAudioObjectPropertyScopeInput, diagnostics),
            "output_streams": streamFormats(device, kAudioObjectPropertyScopeOutput, diagnostics), "audio_started": false]
        if let location = location { record["usb_location_id"] = locationHex(location) }
        if let value = channelCount(device, kAudioObjectPropertyScopeInput, diagnostics) { record["input_channels"] = value }
        if let value = channelCount(device, kAudioObjectPropertyScopeOutput, diagnostics) { record["output_channels"] = value }
        if let value = audioRate(device, diagnostics) { record["sample_rate"] = value }
        if let value = audioUInt(device, kAudioDevicePropertyBufferFrameSize, diagnostics) { record["buffer_frames"] = value }
        for (name, scope) in [("input", kAudioObjectPropertyScopeInput), ("output", kAudioObjectPropertyScopeOutput)] {
            if let value = audioUInt(device, kAudioDevicePropertyLatency, diagnostics, scope) { record[name + "_reported_device_latency_frames"] = value }
            if let value = audioUInt(device, kAudioDevicePropertySafetyOffset, diagnostics, scope) { record[name + "_reported_safety_offset_frames"] = value }
        }
        record["latency_caveat"] = "Driver-reported frames are not measured USB, Bluetooth, game, or actuator latency"
        return record
    }
}

func gameControllerInventory() -> [[String: Any]] {
    // Discovery/listing only. No effect engine, sensor activation or output write.
    let controllers = GCController.controllers()
    return controllers.compactMap { controller in
        guard let profile = controller.extendedGamepad as? GCDualSenseGamepad else { return nil }
        return ["vendor": controller.vendorName ?? "unknown", "product_category": controller.productCategory,
            "profile": "GCDualSenseGamepad", "motion_profile_present": controller.motion != nil,
            "battery_profile_present": controller.battery != nil, "light_profile_present": controller.light != nil,
            "left_trigger_api": String(describing: type(of: profile.leftTrigger)),
            "right_trigger_api": String(describing: type(of: profile.rightTrigger)),
            "touchpad_finger_profiles": 2,
            "haptic_localities": controller.haptics?.supportedLocalities.map { $0.rawValue }.sorted() ?? [],
            "effects_sent": false, "hardware_behavior_verified": false] as [String: Any]
    }
}

if CommandLine.arguments.contains("--self-test") {
    let valid: [(String, UInt32)] = [
        ("AppleUSBAudioEngine:Sony Interactive Entertainment:DualSense Edge Wireless Controller:2100000:1", 0x02100000),
        ("AppleUSBAudioEngine:Sony:Name:with:colons:ffffffff:12", UInt32.max)
    ]
    for (uid, expected) in valid { precondition(usbAudioLocation(uid) == expected) }
    for uid in ["", "MadSense:2100000:1", "AppleUSBAudioEngine:Sony:Pad:0:1",
                "AppleUSBAudioEngine:Sony:Pad:100000000:1", "AppleUSBAudioEngine:Sony:Pad:+123:1",
                "AppleUSBAudioEngine:Sony:Pad: 123:1", "AppleUSBAudioEngine:Sony:Pad:123g:1",
                "AppleUSBAudioEngine:Sony:Pad:123:", "AppleUSBAudioEngine:Sony:Pad:123:x"] {
        precondition(usbAudioLocation(uid) == nil, uid)
    }
    precondition(validArrayByteCount(0, elementSize: 4))
    precondition(validArrayByteCount(8, elementSize: 4))
    precondition(!validArrayByteCount(7, elementSize: 4))
    precondition(!validArrayByteCount(0, elementSize: 0))
    precondition(physicalTransport("USB") == true)
    precondition(physicalTransport("Bluetooth") == true)
    precondition(physicalTransport("Virtual") == false)
    precondition(physicalTransport("unknown") == nil)
    let header = UInt32(MemoryLayout<AudioBufferList>.offset(of: \.mBuffers)!)
    let buffer = UInt32(MemoryLayout<AudioBuffer>.stride)
    precondition(audioBufferCountFits(0, bytes: header))
    precondition(!audioBufferCountFits(0, bytes: header - 1))
    precondition(audioBufferCountFits(2, bytes: header + 2 * buffer))
    precondition(!audioBufferCountFits(2, bytes: header + buffer))
    precondition(!audioBufferCountFits(UInt32.max, bytes: header))
    let diagnosticTest = InventoryDiagnostics()
    diagnosticTest.record("test", "optional property", status: -1)
    precondition(diagnosticTest.enumerationComplete)
    diagnosticTest.record("test", "enumeration failure", status: -1, enumerationFailure: true)
    precondition(!diagnosticTest.enumerationComplete && diagnosticTest.issues.count == 2)
    print("PASS USB-location parser, property-buffer bounds and error classification. No hardware exercised.")
} else {
    _ = GCController.controllers()
    RunLoop.current.run(until: Date(timeIntervalSinceNow: 1))
    let diagnostics = InventoryDiagnostics()
    let hid = hidInventory(diagnostics)
    let hidEnumerationComplete = diagnostics.enumerationComplete
    let audio = audioInventory(hid, diagnostics)
    let physicalCandidates = hid.compactMap { physicalTransport($0["transport"] as? String ?? "unknown") }
    let hasPhysical = physicalCandidates.contains(true)
    var physicalFound: Any = hasPhysical
    if !hasPhysical && (!hidEnumerationComplete || physicalCandidates.count != hid.count) { physicalFound = NSNull() }
    let result: [String: Any] = ["schema": 1, "mode": "read-only physical-controller inventory",
        "os": ProcessInfo.processInfo.operatingSystemVersionString,
        "hid": hid, "usb_audio": audio, "game_controller": gameControllerInventory(),
        "physical_dualsense_found": physicalFound,
        "hid_enumeration_complete": hidEnumerationComplete,
        "enumeration_complete": diagnostics.enumerationComplete,
        "property_reads_complete": diagnostics.issues.isEmpty, "issues": diagnostics.issues,
        "observed_hardware_behavior": false, "virtual_hid_created": false,
        "notes": ["No controller means no physical compatibility result.",
                  "Profiles/formats describe exposed APIs, not game support or actuator operation.",
                  "No entitlement for virtual HID is requested or needed by this read-only probe.",
                  "Missing properties are unknown, not unsupported; issues retain query errors.",
                  "GameController is a one-second discovery snapshot and has no enumeration error API.",
                  "Physical presence uses the reported USB/Bluetooth transport; unknown transport is inconclusive.",
                  "Serial numbers, Bluetooth addresses and device UIDs are omitted."]]
    let json = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
    print(String(decoding: json, as: UTF8.self))
    if !diagnostics.enumerationComplete { exit(1) }
}
