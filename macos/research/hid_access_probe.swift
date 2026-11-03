// SPDX-License-Identifier: GPL-3.0-or-later
// Independent public CoreHID route; this does not import the MadSense bridge.
import Foundation
import IOKit
import Darwin
#if canImport(CoreHID)
import CoreHID
#endif

private func emit(_ value: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) {
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([10]))
    }
}

private func publishedDevice(serial: String) -> io_service_t {
    let match = ["IOProviderClass": "IOHIDDevice", "IOPropertyMatch": ["SerialNumber": serial]] as [String: Any]
    return IOServiceGetMatchingService(kIOMainPortDefault, match as CFDictionary)
}

#if canImport(CoreHID)
@available(macOS 15.0, *)
private final class ProbeDelegate: HIDVirtualDeviceDelegate {
    func hidVirtualDevice(_ device: HIDVirtualDevice, receivedSetReportRequestOfType type: HIDReportType,
                          id: HIDReportID?, data: Data) async throws {
        throw HIDDeviceError.unsupported
    }
    func hidVirtualDevice(_ device: HIDVirtualDevice, receivedGetReportRequestOfType type: HIDReportType,
                          id: HIDReportID?, maxSize: Int) async throws -> Data {
        throw HIDDeviceError.unsupported
    }
}

@available(macOS 15.0, *)
private func run(descriptor: Data, dualsense: Bool) async -> Int32 {
    let serial = "MadSense-Access-Research-\(UUID().uuidString)"
    let properties = HIDVirtualDevice.Properties(descriptor: descriptor,
        vendorID: dualsense ? 0x054c : 0xffff, productID: dualsense ? 0x0ce6 : 1,
        product: "MadSense HID access research", manufacturer: "MadSense research", serialNumber: serial)
    guard let device = HIDVirtualDevice(properties: properties) else {
        emit(["stage": "create", "created": false]); return 20
    }
    emit(["stage": "create", "created": true])
    await device.activate(delegate: ProbeDelegate())
    let deadline = Date().addingTimeInterval(2)
    var service: io_service_t = 0
    repeat {
        service = publishedDevice(serial: serial)
        if service != 0 { break }
        try? await Task.sleep(nanoseconds: 20_000_000)
    } while Date() < deadline
    guard service != 0 else {
        emit(["stage": "registry", "published": false]); return 22
    }
    let transport = IORegistryEntryCreateCFProperty(service, "Transport" as CFString, kCFAllocatorDefault, 0)?.takeRetainedValue() as? String ?? "unknown"
    IOObjectRelease(service)
    emit(["stage": "registry", "published": true, "transport": transport])
    var neutral = [UInt8](repeating: 0, count: dualsense ? 64 : 3)
    if dualsense { neutral[0] = 1; neutral[1] = 128; neutral[2] = 128; neutral[3] = 128; neutral[4] = 128; neutral[8] = 8 }
    do {
        try await device.dispatchInputReport(data: Data(neutral), timestamp: SuspendingClock.now)
        emit(["stage": "neutralInput", "accepted": true])
    } catch {
        emit(["stage": "neutralInput", "accepted": false, "error": String(describing: error)]); return 23
    }
    emit(["stage": "finished", "scope": "creation, publication, and neutral input only; no physical controller or game validation"])
    return 0
}
#endif

@main
struct HIDAccessProbe {
    static func main() async {
        alarm(10) // Self-contained bound also applies when invoked through sudo.
        guard CommandLine.arguments.count == 3,
              let descriptor = try? Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])),
              !descriptor.isEmpty, descriptor.count <= 65535 else {
            emit(["stage": "arguments", "error": "Expected descriptor path and minimal|dualsense"]); exit(64)
        }
        let dualsense = CommandLine.arguments[2] == "dualsense"
        emit(["stage": "started", "api": "CoreHID.HIDVirtualDevice", "privilege": geteuid() == 0 ? "root" : "user",
              "descriptor": dualsense ? "dualsense" : "minimal", "descriptorBytes": descriptor.count])
        #if canImport(CoreHID)
        if #available(macOS 15.0, *) { exit(await run(descriptor: descriptor, dualsense: dualsense)) }
        #endif
        emit(["stage": "availability", "available": false]); exit(77)
    }
}
