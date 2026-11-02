// SPDX-License-Identifier: GPL-3.0-or-later
#include "madsense_hid.h"
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDManager.h>
#include <atomic>
#include <array>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {
void require(bool condition, const char *message) {
    if (!condition) throw std::runtime_error(message);
}

struct Owner {
    void *value = nullptr;
    ~Owner() { if (value) ms_hid_destroy(value); }
};

struct Reports {
    std::atomic<unsigned> sets{0};
    std::atomic<unsigned> gets{0};
    std::atomic<unsigned> inputs{0};
    std::atomic<bool> bad_frame{false};
    std::atomic<void *> callback_close_handle{nullptr};
    std::atomic<int32_t> callback_close_result{0};
};

int32_t get_report(void *context, uint32_t type, uint32_t report_id,
                   uint8_t *data, uint32_t *length) {
    auto &state = *static_cast<Reports *>(context);
    if (type != 2 || report_id != 5) return kIOReturnUnsupported;
    if (*length < 64) return kIOReturnNoSpace;
    std::memset(data, 0x5a, 64);
    data[0] = 5;
    *length = 64;
    state.gets.fetch_add(1);
    return kIOReturnSuccess;
}

int32_t set_report(void *context, uint32_t type, uint32_t report_id,
                   const uint8_t *data, uint32_t length) {
    auto &state = *static_cast<Reports *>(context);
    if (type != 1 || report_id != 2 || length != 64 || data[0] != 2 || data[1] != 0xa5) {
        state.bad_frame.store(true);
        return kIOReturnBadArgument;
    }
    if (void *handle = state.callback_close_handle.load())
        state.callback_close_result.store(ms_hid_destroy(handle));
    state.sets.fetch_add(1);
    return kIOReturnSuccess;
}

void input_report(void *context, IOReturn result, void *, IOHIDReportType type,
                  uint32_t report_id, uint8_t *report, CFIndex length) {
    auto &state = *static_cast<Reports *>(context);
    if (result != kIOReturnSuccess || type != kIOHIDReportTypeInput || report_id != 1 ||
        length != 64 || report[0] != 1 || report[1] != 0x39) {
        state.bad_frame.store(true);
    } else {
        state.inputs.fetch_add(1);
    }
}

// Vendor-defined collection avoids creating a keyboard or pointing device in CI.
constexpr uint8_t descriptor[] = {
    0x06, 0x00, 0xff, 0x09, 0x01, 0xa1, 0x01,
    0x15, 0x00, 0x26, 0xff, 0x00, 0x75, 0x08,
    0x85, 0x01, 0x95, 0x3f, 0x09, 0x01, 0x81, 0x02,
    0x85, 0x02, 0x95, 0x3f, 0x09, 0x02, 0x91, 0x02,
    0x85, 0x05, 0x95, 0x3f, 0x09, 0x03, 0xb1, 0x02, 0xc0
};

struct Client {
    IOHIDManagerRef manager = nullptr;
    IOHIDDeviceRef device = nullptr;
    bool scheduled = false;
    ~Client() {
        if (device) {
            if (scheduled) IOHIDDeviceUnscheduleFromRunLoop(device, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
            IOHIDDeviceClose(device, kIOHIDOptionsTypeNone);
            CFRelease(device);
        }
        if (manager) CFRelease(manager);
    }
};

void test_arguments() {
    require(ms_hid_abi_version() == 1, "unexpected native ABI version");
    void *sentinel = reinterpret_cast<void *>(1);
    require(ms_hid_create(nullptr, 0, 0, 0, nullptr, nullptr, nullptr, nullptr,
        &sentinel) == static_cast<int32_t>(kIOReturnBadArgument), "invalid create accepted");
    require(sentinel == nullptr, "failed create must clear its output handle");
    char short_diagnostic[3] = {'?', '?', '?'};
    require(ms_hid_copy_diagnostic(short_diagnostic, sizeof(short_diagnostic)) > 3,
        "diagnostic must report untruncated required size");
    require(short_diagnostic[2] == '\0', "diagnostic truncation was not terminated");
    require(ms_hid_copy_diagnostic(nullptr, 0) > 1, "diagnostic size query failed");
    require(ms_hid_destroy(nullptr) == 0, "destroy null must be safe");
    require(ms_hid_send_input(nullptr, descriptor, sizeof(descriptor)) ==
        static_cast<int32_t>(kIOReturnBadArgument), "null input handle accepted");
    std::puts("PASS native ABI validation and diagnostic truncation");
}

int test_loopback(bool required) {
    Reports reports;
    Owner virtual_device;
    std::string serial = "MadSense-CI-" + std::to_string(getpid());
    int32_t created = ms_hid_create(descriptor, sizeof(descriptor), 0x1209, 0x4d53,
        serial.c_str(), get_report, set_report, &reports, &virtual_device.value);
    if (created != 0) {
        char diagnostic[1024];
        ms_hid_copy_diagnostic(diagnostic, sizeof(diagnostic));
        // Only Apple's opaque creation refusal is an environmental skip. An
        // allocated device that fails to publish is a functional test failure.
        bool skip = !required && created == static_cast<int32_t>(kIOReturnError);
        std::fprintf(stderr, "%s virtual HID loopback: %s\n",
            skip ? "SKIP" : "FAIL", diagnostic);
        return skip ? 77 : 1;
    }

    // The callback buffer outlives the scheduled HID client, including unwind.
    std::array<uint8_t, 64> input_buffer{};
    Client client;
    client.manager = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
    require(client.manager != nullptr, "IOHIDManagerCreate failed");
    CFMutableDictionaryRef matching = CFDictionaryCreateMutable(kCFAllocatorDefault,
        0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    require(matching != nullptr, "matching dictionary allocation failed");
    CFStringRef serial_property = CFStringCreateWithCString(kCFAllocatorDefault,
        serial.c_str(), kCFStringEncodingUTF8);
    CFDictionarySetValue(matching, CFSTR(kIOHIDSerialNumberKey), serial_property);
    CFRelease(serial_property);
    IOHIDManagerSetDeviceMatching(client.manager, matching);
    CFRelease(matching);
    // Registration is asynchronous; bounded enumeration checks OS publication.
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (!client.device && std::chrono::steady_clock::now() < deadline) {
        CFSetRef devices = IOHIDManagerCopyDevices(client.manager);
        if (devices) {
            CFIndex count = CFSetGetCount(devices);
            std::vector<const void *> entries(static_cast<size_t>(count));
            CFSetGetValues(devices, entries.data());
            if (count) {
                client.device = static_cast<IOHIDDeviceRef>(const_cast<void *>(entries[0]));
                CFRetain(client.device);
            }
            CFRelease(devices);
        }
        if (!client.device) std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    require(client.device != nullptr, "virtual device creation succeeded but HID enumeration did not");
    CFTypeRef transport = IOHIDDeviceGetProperty(client.device, CFSTR(kIOHIDTransportKey));
    require(transport && CFEqual(transport, CFSTR("Virtual")), "virtual HID transport must be reported honestly");
    require(IOHIDDeviceOpen(client.device, kIOHIDOptionsTypeNone) == kIOReturnSuccess,
        "virtual HID client open failed");
    IOHIDDeviceRegisterInputReportCallback(client.device, input_buffer.data(),
        input_buffer.size(), input_report, &reports);
    IOHIDDeviceScheduleWithRunLoop(client.device, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
    client.scheduled = true;

    std::array<uint8_t, 64> feature{};
    feature[0] = 5;
    CFIndex feature_length = feature.size();
    require(IOHIDDeviceGetReport(client.device, kIOHIDReportTypeFeature, 5,
        feature.data(), &feature_length) == kIOReturnSuccess, "feature GET roundtrip failed");
    require(feature_length == 64 && feature[0] == 5 && feature[63] == 0x5a,
        "feature GET payload or length changed");

    std::array<uint8_t, 64> output{};
    output[0] = 2;
    output[1] = 0xa5;
    reports.callback_close_handle.store(virtual_device.value);
    require(IOHIDDeviceSetReport(client.device, kIOHIDReportTypeOutput, 2,
        output.data(), output.size()) == kIOReturnSuccess, "output SET roundtrip failed");
    require(reports.callback_close_result.load() == static_cast<int32_t>(kIOReturnBusy),
        "destruction inside a callback must reject reentrant closure");

    std::array<uint8_t, 64> input{};
    input[0] = 1;
    input[1] = 0x39;
    require(ms_hid_send_input(virtual_device.value, input.data(), input.size()) == 0,
        "virtual input publication failed");
    deadline = std::chrono::steady_clock::now() + std::chrono::seconds(3);
    while (reports.inputs.load() == 0 && std::chrono::steady_clock::now() < deadline)
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, true);
    require(!reports.bad_frame.load(), "report framing changed across IOKit callbacks");
    require(reports.gets.load() >= 1 && reports.sets.load() >= 1 && reports.inputs.load() >= 1,
        "missing feature, output or input callbacks");
    require(ms_hid_destroy(virtual_device.value) == 0, "virtual HID closure failed");
    virtual_device.value = nullptr;
    std::puts("PASS macOS virtual HID enumeration, transport, feature GET, output SET, input loopback and closure");
    return 0;
}
}

int main(int argc, char **argv) {
    bool abi_only = argc > 1 && std::strcmp(argv[1], "--abi-only") == 0;
    bool required = argc > 1 && std::strcmp(argv[1], "--require-device") == 0;
    try {
        test_arguments();
        if (abi_only) return 0;
        return test_loopback(required);
    } catch (const std::exception &error) {
        std::fprintf(stderr, "FAIL: %s\n", error.what());
        return 1;
    }
}
