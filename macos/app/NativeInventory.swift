import Foundation
import CoreFoundation
import Darwin

// The helper only inventories existing interfaces. Its output never changes
// the bridge state or enables an effect/audio capability in the application.
enum NativeInventoryResult {
    static func boolean(_ value: Any?) -> Bool? {
        guard let number = value as? NSNumber,
              CFGetTypeID(number) == CFBooleanGetTypeID() else { return nil }
        return number.boolValue
    }

    static func decode(_ output: Data, exitCode: Int32?) -> (String, [String: Any]?) {
        guard let object = try? JSONSerialization.jsonObject(with: output),
              let inventory = object as? [String: Any] else { return ("invalid_output", nil) }
        guard let schema = inventory["schema"] as? NSNumber,
              CFGetTypeID(schema) != CFBooleanGetTypeID(), schema.doubleValue == 1,
              inventory["mode"] as? String == "read-only physical-controller inventory",
              boolean(inventory["observed_hardware_behavior"]) == false,
              boolean(inventory["virtual_hid_created"]) == false,
              inventory["hid"] is [[String: Any]], inventory["usb_audio"] is [[String: Any]],
              inventory["game_controller"] is [[String: Any]],
              inventory["issues"] is [[String: Any]],
              let complete = boolean(inventory["enumeration_complete"]),
              inventory["physical_dualsense_found"] is NSNull || boolean(inventory["physical_dualsense_found"]) != nil else {
            // Retain structured evidence even when its schema is unexpected.
            return ("invalid_output", inventory)
        }
        if !complete { return ("incomplete", inventory) }
        return (exitCode == 0 ? "complete" : "probe_failed", inventory)
    }

    static func selfTest() throws {
        let empty: [String: Any] = ["schema": 1, "mode": "read-only physical-controller inventory",
            "observed_hardware_behavior": false, "virtual_hid_created": false,
            "hid": [], "usb_audio": [], "game_controller": [], "issues": [],
            "enumeration_complete": true, "physical_dualsense_found": false]
        func encode(_ value: [String: Any]) throws -> Data { try JSONSerialization.data(withJSONObject: value) }
        func expect(_ value: [String: Any], exit: Int32, _ outcome: String) throws {
            let decoded = try decode(encode(value), exitCode: exit)
            precondition(decoded.0 == outcome)
        }
        try expect(empty, exit: 0, "complete")
        try expect(empty, exit: 1, "probe_failed")
        var partial = empty
        partial["enumeration_complete"] = false
        partial["physical_dualsense_found"] = NSNull()
        partial["issues"] = [["source": "hid", "status": -1]]
        let incomplete = try decode(encode(partial), exitCode: 1)
        precondition(incomplete.0 == "incomplete")
        precondition((incomplete.1?["issues"] as? [[String: Any]])?.count == 1)
        precondition(decode(Data("not JSON".utf8), exitCode: 0).0 == "invalid_output")
        precondition(decode(Data("[]".utf8), exitCode: 0).0 == "invalid_output")
        for (key, value) in [("schema", 2 as Any), ("schema", true as Any),
                             ("observed_hardware_behavior", true as Any),
                             ("enumeration_complete", 1 as Any), ("physical_dualsense_found", "yes" as Any)] {
            var invalid = empty
            invalid[key] = value
            try expect(invalid, exit: 0, "invalid_output")
        }
        let capture = NativeInventoryCapture(limit: 8)
        precondition(!capture.append(Data("12345".utf8), standardError: false))
        precondition(capture.append(Data("abcdef".utf8), standardError: true))
        let captured = capture.snapshot()
        precondition(captured.output.count + captured.error.count == 8 && captured.exceeded)
        precondition(captured.errorBytes == 6 && captured.outputBytes == 5)
        print("PASS native inventory parser, partial/error preservation and output bound. No hardware exercised.")
    }
}

private final class NativeInventoryCapture {
    private let lock = NSLock()
    private let limit: Int
    private var output = Data(), error = Data()
    private var outputBytes = 0, errorBytes = 0
    private var exceeded = false
    private var readErrors: [String] = []
    init(limit: Int) { self.limit = limit }

    func append(_ data: Data, standardError: Bool) -> Bool {
        lock.lock(); defer { lock.unlock() }
        let available = max(0, limit - output.count - error.count)
        if standardError { errorBytes += data.count; error.append(data.prefix(available)) }
        else { outputBytes += data.count; output.append(data.prefix(available)) }
        let firstOverflow = !exceeded && data.count > available
        exceeded = exceeded || data.count > available
        return firstOverflow
    }
    func readError(_ message: String) {
        lock.lock(); defer { lock.unlock() }
        readErrors.append(message)
    }
    func snapshot() -> (output: Data, error: Data, outputBytes: Int, errorBytes: Int, exceeded: Bool, readErrors: [String]) {
        lock.lock(); defer { lock.unlock() }
        return (output, error, outputBytes, errorBytes, exceeded, readErrors)
    }
}

final class NativeInventoryTask {
    private let executable: URL
    private let trace: (String) -> Void
    private let lock = NSLock()
    private let wake = DispatchSemaphore(value: 0)
    private var cancelled = false
    private let stallForSmoke: Bool

    init(executable: URL, stallForSmoke: Bool = false, trace: @escaping (String) -> Void) {
        self.executable = executable
        self.stallForSmoke = stallForSmoke
        self.trace = trace
    }
    func cancel() {
        lock.lock(); cancelled = true; lock.unlock()
        wake.signal()
    }
    private var isCancelled: Bool {
        lock.lock(); defer { lock.unlock() }
        return cancelled
    }
    private func note(_ message: String) { onMainRunLoop { self.trace(message) } }

    func start(completion: @escaping ([String: Any], Bool) -> Void) {
        // All waits, pipe reads and cleanup run off the AppKit thread, including
        // while terminateLater is running its nested modal loop.
        DispatchQueue.global(qos: .utility).async {
            let started = ISO8601DateFormatter().string(from: Date())
            let child = Process(), stdout = Pipe(), stderr = Pipe()
            let capture = NativeInventoryCapture(limit: 1_048_576)
            let readers = DispatchGroup()
            var outcome: String?
            var launchError: String?
            var exitCode: Int32?
            var termination = "not_started"
            var cleanupComplete = true
            var drained = true
            child.executableURL = self.executable
            child.arguments = []
            child.currentDirectoryURL = self.executable.deletingLastPathComponent()
            child.standardInput = FileHandle.nullDevice
            child.standardOutput = stdout
            child.standardError = stderr
            child.terminationHandler = { [wake = self.wake] _ in wake.signal() }

            if self.isCancelled {
                outcome = "cancelled"
            } else {
                do {
                    try child.run()
                    // Only the child writes these pipes. Closing the parent's
                    // copies also lets readers reach EOF after forced cleanup.
                    try? stdout.fileHandleForWriting.close()
                    try? stderr.fileHandleForWriting.close()
                    self.note("native inventory child started: \(child.processIdentifier)")
                    for (handle, isError) in [(stdout.fileHandleForReading, false), (stderr.fileHandleForReading, true)] {
                        readers.enter()
                        DispatchQueue.global(qos: .utility).async {
                            defer { try? handle.close(); readers.leave() }
                            do {
                                while let data = try handle.read(upToCount: 16_384), !data.isEmpty {
                                    if capture.append(data, standardError: isError) { self.wake.signal() }
                                }
                            } catch { capture.readError(error.localizedDescription); self.wake.signal() }
                        }
                    }
                    if self.stallForSmoke && child.isRunning {
                        let status = Darwin.kill(child.processIdentifier, SIGSTOP)
                        self.note("native inventory smoke SIGSTOP result: \(status)")
                    }
                    let deadline = ProcessInfo.processInfo.systemUptime + 10
                    while child.isRunning {
                        if self.isCancelled { outcome = "cancelled"; break }
                        if capture.snapshot().exceeded { outcome = "output_limit"; break }
                        let remaining = deadline - ProcessInfo.processInfo.systemUptime
                        if remaining <= 0 { outcome = "timed_out"; break }
                        _ = self.wake.wait(timeout: .now() + remaining)
                    }
                    if child.isRunning {
                        self.note("native inventory sending SIGTERM to owned child: \(child.processIdentifier)")
                        child.terminate()
                        self.waitForExit(child, seconds: 2)
                    }
                    if child.isRunning {
                        let pid = child.processIdentifier
                        self.note("native inventory sending SIGKILL to owned child: \(pid)")
                        if pid > 0 && Darwin.kill(pid, SIGKILL) != 0 {
                            capture.readError("SIGKILL failed for inventory child (errno \(errno)).")
                        }
                        self.waitForExit(child, seconds: 2)
                    }
                    cleanupComplete = !child.isRunning
                    if cleanupComplete {
                        exitCode = child.terminationStatus
                        termination = child.terminationReason == .exit ? "exit" : "uncaught_signal"
                    } else {
                        outcome = "cleanup_failed"
                    }
                    drained = readers.wait(timeout: .now() + 1) == .success
                } catch {
                    launchError = error.localizedDescription
                    outcome = "launch_failed"
                }
            }
            // Also close all unused descriptors after cancellation/launch error.
            try? stdout.fileHandleForWriting.close()
            try? stderr.fileHandleForWriting.close()
            if termination == "not_started" {
                try? stdout.fileHandleForReading.close()
                try? stderr.fileHandleForReading.close()
            }
            let captured = capture.snapshot()
            let decoded = NativeInventoryResult.decode(captured.output, exitCode: exitCode)
            if outcome == nil {
                outcome = captured.exceeded ? "output_limit" : !drained || !captured.readErrors.isEmpty ? "output_incomplete" : decoded.0
            }
            var record: [String: Any] = ["source": "MadSenseNativeProbe", "started_at": started,
                "finished_at": ISO8601DateFormatter().string(from: Date()), "outcome": outcome!,
                "termination_reason": termination, "cleanup_complete": cleanupComplete, "pipes_drained": drained,
                "stdout": String(decoding: captured.output, as: UTF8.self),
                "stderr": String(decoding: captured.error, as: UTF8.self),
                "stdout_bytes": captured.outputBytes, "stderr_bytes": captured.errorBytes,
                "output_limit_bytes": 1_048_576, "output_truncated": captured.exceeded,
                "read_errors": captured.readErrors, "hardware_validation_performed": false]
            record["exit_code"] = exitCode.map { $0 as Any } ?? NSNull()
            record["inventory"] = decoded.1.map { $0 as Any } ?? NSNull()
            if let launchError = launchError { record["launch_error"] = launchError }
            self.note("native inventory completed: \(outcome!); cleanup complete=\(cleanupComplete)")
            let result = record, didCleanUp = cleanupComplete
            onMainRunLoop { completion(result, didCleanUp) }
        }
    }

    private func waitForExit(_ child: Process, seconds: TimeInterval) {
        let deadline = ProcessInfo.processInfo.systemUptime + seconds
        while child.isRunning {
            let remaining = deadline - ProcessInfo.processInfo.systemUptime
            if remaining <= 0 { return }
            _ = wake.wait(timeout: .now() + remaining)
        }
    }
}
