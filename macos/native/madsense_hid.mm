// SPDX-License-Identifier: GPL-3.0-or-later
// Public IOHIDUserDevice API, available since macOS 10.15. The application
// targets macOS 14+. This does not implement a USB device or an audio endpoint.
#include "madsense_hid.h"
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitKeys.h>
#include <IOKit/hid/IOHIDKeys.h>
#include <IOKit/hidsystem/IOHIDUserDevice.h>
#include <dispatch/dispatch.h>
#include <mach/mach_time.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <memory>
#include <new>
#include <thread>

namespace {
constexpr uint32_t max_report_length = 4096;
constexpr uint32_t max_descriptor_length = 65535;
thread_local char diagnostic[768] = "No native HID error.";
char callback_queue_key;

int32_t fail(IOReturn status, const char *message) {
    std::snprintf(diagnostic, sizeof(diagnostic), "%s (0x%08x)",
                  message, static_cast<unsigned>(status));
    return static_cast<int32_t>(status);
}

struct Device {
    IOHIDUserDeviceRef device = nullptr;
    dispatch_queue_t queue = nullptr;
    dispatch_semaphore_t cancelled = nullptr;
    ms_hid_get_callback get = nullptr;
    ms_hid_set_callback set = nullptr;
    void *context = nullptr;
    std::atomic<bool> stopping{false};
};

void set_number(CFMutableDictionaryRef properties, CFStringRef key, int value) {
    CFNumberRef number = CFNumberCreate(kCFAllocatorDefault, kCFNumberIntType, &value);
    if (number) {
        CFDictionarySetValue(properties, key, number);
        CFRelease(number);
    }
}

bool is_published(CFStringRef token) {
    CFMutableDictionaryRef match = IOServiceMatching("IOHIDDevice");
    if (!match) return false;
    CFMutableDictionaryRef properties = CFDictionaryCreateMutable(kCFAllocatorDefault,
        0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    if (!properties) { CFRelease(match); return false; }
    // A per-creation marker prevents a stale service with the same caller serial
    // from satisfying the publication check during reconnect or parallel probes.
    CFDictionarySetValue(properties, CFSTR("MadSenseVirtualInstance"), token);
    CFDictionarySetValue(match, CFSTR(kIOPropertyMatchKey), properties);
    CFRelease(properties);
    // Matching consumes match and returns only published/registered services.
    io_service_t service = IOServiceGetMatchingService(kIOMainPortDefault, match);
    if (!service) return false;
    IOObjectRelease(service);
    return true;
}
}

uint32_t ms_hid_abi_version(void) { return 1; }

int32_t ms_hid_create(const uint8_t *descriptor, uint32_t descriptor_length,
    uint16_t vendor_id, uint16_t product_id, const char *serial_utf8,
    ms_hid_get_callback get_callback, ms_hid_set_callback set_callback,
    void *context, void **out_handle) {
    if (out_handle) *out_handle = nullptr;
    if (!out_handle || !descriptor || !descriptor_length ||
        descriptor_length > max_descriptor_length || !vendor_id ||
        !product_id || !serial_utf8 || !*serial_utf8 ||
        !get_callback || !set_callback) {
        return fail(kIOReturnBadArgument, "Invalid virtual HID creation arguments");
    }
    std::unique_ptr<Device> instance(new (std::nothrow) Device());
    if (!instance) return fail(kIOReturnNoMemory, "Could not allocate virtual HID state");
    CFStringRef serial = CFStringCreateWithCString(kCFAllocatorDefault, serial_utf8,
                                                  kCFStringEncodingUTF8);
    CFDataRef report_descriptor = CFDataCreate(kCFAllocatorDefault, descriptor,
                                               descriptor_length);
    CFUUIDRef uuid = CFUUIDCreate(kCFAllocatorDefault);
    CFStringRef instance_token = uuid ? CFUUIDCreateString(kCFAllocatorDefault, uuid) : nullptr;
    if (uuid) CFRelease(uuid);
    CFMutableDictionaryRef properties = CFDictionaryCreateMutable(kCFAllocatorDefault,
        0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    if (!serial || !report_descriptor || !properties || !instance_token) {
        if (serial) CFRelease(serial);
        if (report_descriptor) CFRelease(report_descriptor);
        if (properties) CFRelease(properties);
        if (instance_token) CFRelease(instance_token);
        return fail(kIOReturnBadArgument, "Could not create HID properties; serial must be valid UTF-8");
    }
    CFDictionarySetValue(properties, CFSTR(kIOHIDReportDescriptorKey), report_descriptor);
    CFDictionarySetValue(properties, CFSTR(kIOHIDSerialNumberKey), serial);
    CFDictionarySetValue(properties, CFSTR("MadSenseVirtualInstance"), instance_token);
    // The public entitlement creates a virtual transport. Claiming USB here
    // does not create USB interfaces and is overridden by the kernel.
    CFDictionarySetValue(properties, CFSTR(kIOHIDTransportKey), CFSTR("Virtual"));
    CFDictionarySetValue(properties, CFSTR(kIOHIDManufacturerKey), CFSTR("MadSense"));
    CFDictionarySetValue(properties, CFSTR(kIOHIDProductKey), CFSTR("MadSense DualSense"));
    set_number(properties, CFSTR(kIOHIDVendorIDKey), vendor_id);
    set_number(properties, CFSTR(kIOHIDProductIDKey), product_id);
    set_number(properties, CFSTR(kIOHIDVersionNumberKey), 0x0100);
    instance->device = IOHIDUserDeviceCreateWithProperties(kCFAllocatorDefault,
        properties, IOHIDUserDeviceOptionsCreateOnActivate);
    CFRelease(properties);
    CFRelease(report_descriptor);
    CFRelease(serial);
    if (!instance->device) {
        CFRelease(instance_token);
        // Apple exposes no underlying IOReturn from this creation function;
        // don't invent a permission result when a descriptor could also fail.
        return fail(kIOReturnError,
            "macOS refused virtual HID creation. Check the executable's accepted "
            "com.apple.developer.hid.virtual.device entitlement/signature, the "
            "descriptor and system HID diagnostics; root alone is not sufficient");
    }
    instance->queue = dispatch_queue_create("org.madsense.virtual-hid", DISPATCH_QUEUE_SERIAL);
    instance->cancelled = dispatch_semaphore_create(0);
    if (!instance->queue || !instance->cancelled) {
        CFRelease(instance_token);
        CFRelease(instance->device);
        if (instance->queue) dispatch_release(instance->queue);
        if (instance->cancelled) dispatch_release(instance->cancelled);
        return fail(kIOReturnNoMemory, "Could not allocate virtual HID callback queue");
    }
    instance->get = get_callback;
    instance->set = set_callback;
    instance->context = context;
    Device *state = instance.get();
    dispatch_queue_set_specific(state->queue, &callback_queue_key, state, nullptr);
    IOHIDUserDeviceRegisterGetReportBlock(state->device,
        ^IOReturn(IOHIDReportType type, uint32_t report_id, uint8_t *data, CFIndex *length) {
            if (state->stopping.load()) return kIOReturnOffline;
            if (!length || *length < 0 || *length > max_report_length || !data)
                return kIOReturnBadArgument;
            uint32_t capacity = static_cast<uint32_t>(*length);
            uint32_t actual = capacity;
            int32_t result = state->get(state->context, static_cast<uint32_t>(type),
                                        report_id, data, &actual);
            if (actual > capacity) return kIOReturnOverrun;
            *length = actual;
            return static_cast<IOReturn>(result);
        });
    IOHIDUserDeviceRegisterSetReportBlock(state->device,
        ^IOReturn(IOHIDReportType type, uint32_t report_id,
                  const uint8_t *data, CFIndex length) {
            if (state->stopping.load()) return kIOReturnOffline;
            if (!data || length < 0 || length > max_report_length)
                return kIOReturnBadArgument;
            return static_cast<IOReturn>(state->set(state->context,
                static_cast<uint32_t>(type), report_id, data, static_cast<uint32_t>(length)));
        });
    IOHIDUserDeviceSetCancelHandler(state->device, ^{
        dispatch_semaphore_signal(state->cancelled);
    });
    IOHIDUserDeviceSetDispatchQueue(state->device, state->queue);
    IOHIDUserDeviceActivate(state->device);
    // Activate has no status return. Confirm this exact kernel HID service was
    // registered instead of treating a resource-client allocation as readiness.
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    bool published = false;
    do {
        published = is_published(instance_token);
        if (published) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    } while (std::chrono::steady_clock::now() < deadline);
    CFRelease(instance_token);
    if (!published) {
        ms_hid_destroy(instance.release());
        return fail(kIOReturnNotReady, "Virtual HID activation did not publish its device in the macOS IORegistry");
    }
    *out_handle = instance.release();
    std::snprintf(diagnostic, sizeof(diagnostic), "Virtual HID created; transport is Virtual, not USB.");
    return kIOReturnSuccess;
}

int32_t ms_hid_send_input(void *handle, const uint8_t *report, uint32_t report_length) {
    if (!handle || !report || !report_length || report_length > max_report_length)
        return fail(kIOReturnBadArgument, "Invalid input report");
    Device *state = static_cast<Device *>(handle);
    if (state->stopping.load()) return fail(kIOReturnOffline, "Virtual HID is stopping");
    IOReturn result = IOHIDUserDeviceHandleReportWithTimeStamp(state->device,
        mach_absolute_time(), report, report_length);
    if (result != kIOReturnSuccess) return fail(result, "macOS rejected the virtual HID input report");
    return result;
}

int32_t ms_hid_destroy(void *handle) {
    if (!handle) return kIOReturnSuccess;
    Device *state = static_cast<Device *>(handle);
    if (dispatch_get_specific(&callback_queue_key) == state)
        return fail(kIOReturnBusy, "Schedule device destruction outside its HID callback");
    state->stopping.store(true);
    IOHIDUserDeviceCancel(state->device);
    // The cancel handler runs after IOKit has stopped dispatching callbacks.
    // Waiting is necessary: merely cancelling then freeing the callback context
    // allows an in-flight callback to use disposed managed delegates.
    dispatch_semaphore_wait(state->cancelled, DISPATCH_TIME_FOREVER);
    // Drain the cancel handler itself before releasing its semaphore/context.
    dispatch_sync(state->queue, ^{});
    CFRelease(state->device);
    dispatch_release(state->cancelled);
    dispatch_release(state->queue);
    delete state;
    return kIOReturnSuccess;
}

uint32_t ms_hid_copy_diagnostic(char *output, uint32_t capacity) {
    uint32_t required = static_cast<uint32_t>(std::strlen(diagnostic) + 1);
    if (output && capacity) {
        uint32_t count = std::min(required - 1, capacity - 1);
        std::memcpy(output, diagnostic, count);
        output[count] = '\0';
    }
    return required;
}
