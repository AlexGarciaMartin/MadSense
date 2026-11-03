// SPDX-License-Identifier: GPL-3.0-or-later
// Standalone access experiment. Never linked into the application.
#import <Foundation/Foundation.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/hid/IOHIDKeys.h>
#include <IOKit/hidsystem/IOHIDUserDevice.h>
#include <mach/mach_error.h>
#include <mach/mach_time.h>
#include <dispatch/dispatch.h>
#include <unistd.h>
#include <cstdio>
#include <cstring>

static void emit(NSDictionary *value) {
    NSData *json = [NSJSONSerialization dataWithJSONObject:value options:0 error:nullptr];
    fwrite(json.bytes, 1, json.length, stdout); fputc('\n', stdout); fflush(stdout);
}

static io_service_t findPublished(NSString *serial) {
    NSDictionary *match = @{ @"IOProviderClass": @"IOHIDDevice", @"IOPropertyMatch": @{ @"SerialNumber": serial } };
    return IOServiceGetMatchingService(kIOMainPortDefault, (CFDictionaryRef)[match retain]);
}

int main(int argc, char **argv) {
    alarm(10); // Bound even root execution if a framework call never returns.
    @autoreleasepool {
        if (argc != 3) { emit(@{ @"stage": @"arguments", @"error": @"Expected descriptor path and minimal|dualsense" }); return 64; }
        const bool dualsense = strcmp(argv[2], "dualsense") == 0;
        NSData *descriptor = [NSData dataWithContentsOfFile:[NSString stringWithUTF8String:argv[1]]];
        if (!descriptor || descriptor.length == 0 || descriptor.length > 65535) {
            emit(@{ @"stage": @"descriptor", @"error": @"Missing or invalid descriptor" }); return 65;
        }
        emit(@{ @"stage": @"started", @"api": @"IOHIDUserDeviceCreateWithProperties", @"privilege": geteuid() == 0 ? @"root" : @"user",
                @"descriptor": dualsense ? @"dualsense" : @"minimal", @"descriptorBytes": @(descriptor.length) });
        // The kernel client type 0 is kIOHIDResourceUserClientTypeDevice in
        // Apple's IOHIDResourceUserClient.h. No private entitlement is claimed.
        io_service_t resource = IOServiceGetMatchingService(kIOMainPortDefault, IOServiceMatching("IOHIDResource"));
        if (!resource) { emit(@{ @"stage": @"IOHIDResource", @"found": @NO }); return 21; }
        io_connect_t connection = IO_OBJECT_NULL;
        IOReturn opened = IOServiceOpen(resource, mach_task_self(), 0, &connection);
        IOObjectRelease(resource);
        emit(@{ @"stage": @"IOServiceOpen", @"status": [NSString stringWithFormat:@"0x%08x", opened],
                @"description": [NSString stringWithUTF8String:mach_error_string(opened)] ?: @"unknown" });
        if (connection) IOServiceClose(connection);

        NSString *serial = [@"MadSense-Access-Research-" stringByAppendingString:NSUUID.UUID.UUIDString];
        NSDictionary *properties = @{ @"ReportDescriptor": descriptor, @"VendorID": dualsense ? @0x054c : @0xffff,
            @"ProductID": dualsense ? @0x0ce6 : @1, @"Product": @"MadSense HID access research",
            @"Manufacturer": @"MadSense research", @"SerialNumber": serial, @"Transport": @"Virtual" };
        // Options 0 requests immediate creation, independently of the app's
        // create-on-activate path. Use only the current public SDK interface.
        IOHIDUserDeviceRef device = IOHIDUserDeviceCreateWithProperties(kCFAllocatorDefault, (CFDictionaryRef)properties, 0);
        emit(@{ @"stage": @"create", @"created": @(device != nullptr) });
        if (!device) return 20; // Evidence of refusal, not proof of a functional bridge.
        IOHIDUserDeviceRegisterGetReportBlock(device, ^IOReturn(IOHIDReportType, uint32_t, uint8_t *, CFIndex *) {
            return kIOReturnUnsupported;
        });
        IOHIDUserDeviceRegisterSetReportBlock(device, ^IOReturn(IOHIDReportType, uint32_t, const uint8_t *, CFIndex) {
            return kIOReturnUnsupported;
        });
        dispatch_queue_t queue = dispatch_queue_create("org.madsense.access-research", DISPATCH_QUEUE_SERIAL);
        dispatch_semaphore_t cancelled = dispatch_semaphore_create(0);
        IOHIDUserDeviceSetCancelHandler(device, ^{ dispatch_semaphore_signal(cancelled); });
        IOHIDUserDeviceSetDispatchQueue(device, queue);
        IOHIDUserDeviceActivate(device);
        io_service_t published = IO_OBJECT_NULL;
        CFAbsoluteTime deadline = CFAbsoluteTimeGetCurrent() + 2;
        do {
            published = findPublished(serial);
            if (published) break;
            CFRunLoopRunInMode(kCFRunLoopDefaultMode, .02, true);
        } while (CFAbsoluteTimeGetCurrent() < deadline);
        NSString *transport = @"unknown";
        if (published) {
            CFTypeRef value = IORegistryEntryCreateCFProperty(published, CFSTR(kIOHIDTransportKey), kCFAllocatorDefault, 0);
            if (value) {
                if (CFGetTypeID(value) == CFStringGetTypeID()) transport = [NSString stringWithString:(NSString *)value];
                CFRelease(value);
            }
            IOObjectRelease(published);
        }
        emit(@{ @"stage": @"registry", @"published": @(published != IO_OBJECT_NULL), @"transport": transport });
        IOReturn sent = kIOReturnNotReady;
        if (published) {
            uint8_t neutral[64]{};
            if (dualsense) { neutral[0] = 1; neutral[1] = neutral[2] = neutral[3] = neutral[4] = 128; neutral[8] = 8; }
            sent = IOHIDUserDeviceHandleReportWithTimeStamp(device, mach_absolute_time(), neutral, dualsense ? 64 : 3);
            emit(@{ @"stage": @"neutralInput", @"status": [NSString stringWithFormat:@"0x%08x", sent] });
        }
        IOHIDUserDeviceCancel(device);
        if (dispatch_semaphore_wait(cancelled, dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC))) {
            // Do not release an object whose asynchronous callbacks may still
            // run. Process exit bounds and cleans up this isolated experiment.
            emit(@{ @"stage": @"cancel", @"completed": @NO }); return 24;
        }
        dispatch_sync(queue, ^{});
        CFRelease(device);
        dispatch_release(cancelled);
        dispatch_release(queue);
        emit(@{ @"stage": @"finished", @"scope": @"creation, publication, and neutral input only; no physical controller or game validation" });
        return published && sent == kIOReturnSuccess ? 0 : 22;
    }
}
