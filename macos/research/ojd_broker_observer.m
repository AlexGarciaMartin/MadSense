// Research-only observer. Never opens a physical HID and never writes a report.
#import <Foundation/Foundation.h>
#import <IOKit/IOKitLib.h>
#import <IOKit/hid/IOHIDDevice.h>
#import <IOKit/hid/IOHIDKeys.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>

static NSDictionary *properties(io_registry_entry_t entry) {
    CFMutableDictionaryRef result = NULL;
    if (IORegistryEntryCreateCFProperties(entry, &result, kCFAllocatorDefault, 0) != KERN_SUCCESS || !result) {
        if (result) CFRelease(result);
        return nil;
    }
    return CFBridgingRelease(result);
}
static void emit(id value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingSortedKeys error:nil];
    fwrite(data.bytes, 1, data.length, stdout); fputc('\n', stdout); fflush(stdout);
}
static NSArray *entries(const char *className) {
    io_iterator_t iterator = IO_OBJECT_NULL;
    if (IOServiceGetMatchingServices(kIOMainPortDefault, IOServiceMatching(className), &iterator) != KERN_SUCCESS)
        return nil;
    NSMutableArray *result = [NSMutableArray array];
    io_registry_entry_t entry;
    while ((entry = IOIteratorNext(iterator))) {
        NSDictionary *snapshot = properties(entry);
        uint64_t identifier = 0;
        if (!snapshot || IORegistryEntryGetRegistryEntryID(entry, &identifier) != KERN_SUCCESS || !identifier) {
            IOObjectRelease(entry);
            IOObjectRelease(iterator);
            return nil;
        }
        NSMutableDictionary *item = [snapshot mutableCopy];
        item[@"ProbeRegistryID"] = @(identifier);
        [result addObject:item];
        IOObjectRelease(entry);
    }
    BOOL valid = IOIteratorIsValid(iterator);
    IOObjectRelease(iterator);
    if (!valid) return nil;
    return result;
}
static BOOL ownGeneric(NSDictionary *p) {
    id serial = p[@"SerialNumber"];
    if (!serial) serial = p[@"SerialNumberString"];
    return [p[@"VendorID"] intValue] == 0x1209 && [p[@"ProductID"] intValue] == 0x4a4f
        && [p[@"Manufacturer"] isEqual:@"OpenJoystickDriver"]
        && [serial isKindOfClass:NSString.class]
        && [serial hasPrefix:@"OpenJoystickDriver-UserSpace:"];
}
typedef struct { unsigned reports; BOOL invalid; } Received;
static void report(void *context, IOReturn status, void *sender, IOHIDReportType type,
                   uint32_t identifier, uint8_t *bytes, CFIndex length) {
    (void)sender;
    Received *received = context;
    if (status != kIOReturnSuccess || type != kIOHIDReportTypeInput || identifier != 0 || length != 14) {
        received->invalid = YES; return;
    }
    for (CFIndex i = 0; i < length; ++i) if (bytes[i] != 0) received->invalid = YES;
    ++received->reports;
}
int main(int argc, const char *argv[]) {
    alarm(12);
    @autoreleasepool {
        if (argc == 2 && strcmp(argv[1], "home") == 0) {
            emit(@{@"foundationHome": NSFileManager.defaultManager.homeDirectoryForCurrentUser.path});
            return 0;
        }
        if (argc == 2 && strcmp(argv[1], "inventory") == 0) {
            NSArray *hid = entries("IOHIDDevice"), *usb = entries("IOUSBHostDevice"),
                *bluetooth = entries("IOBluetoothHCIController");
            if (!hid || !usb || !bluetooth) return 1;
            unsigned unsafe = 0;
            NSMutableArray *feeds = [NSMutableArray array];
            for (NSDictionary *p in hid) {
                if (ownGeneric(p)) {
                    NSData *descriptor = p[@"ReportDescriptor"];
                    [feeds addObject:@{@"registryID": p[@"ProbeRegistryID"] ?: @0,
                        @"transport": p[@"Transport"] ?: @"",
                        @"descriptor": [descriptor isKindOfClass:NSData.class] ? [descriptor base64EncodedStringWithOptions:0] : @""}];
                    continue;
                }
                // Missing or malformed HID metadata cannot establish an empty host.
                if (![p[@"PrimaryUsagePage"] isKindOfClass:NSNumber.class]
                    || ![p[@"PrimaryUsage"] isKindOfClass:NSNumber.class]
                    || ![p[@"Transport"] isKindOfClass:NSString.class]) return 1;
                int page = [p[@"PrimaryUsagePage"] intValue], usage = [p[@"PrimaryUsage"] intValue];
                NSString *transport = p[@"Transport"];
                if (page <= 0 || usage <= 0 || transport.length == 0) return 1;
                if ((page == 1 && (usage == 4 || usage == 5 || usage == 8)) || page >= 0xff00
                    || [transport hasPrefix:@"Bluetooth"] || [transport isEqual:@"USB"]) ++unsafe;
            }
            emit(@{@"unsafeHIDCount": @(unsafe), @"usbDeviceCount": @(usb.count),
                @"bluetoothControllerCount": @(bluetooth.count), @"feeds": feeds});
            return 0;
        }
        if (argc != 3 || strcmp(argv[1], "inspect") != 0) return 2;
        char *end = NULL;
        uint64_t identifier = strtoull(argv[2], &end, 10);
        if (!identifier || !end || *end) return 2;
        io_registry_entry_t entry = IOServiceGetMatchingService(kIOMainPortDefault, IORegistryEntryIDMatching(identifier));
        if (!entry) return 3;
        if (!ownGeneric(properties(entry))) { IOObjectRelease(entry); return 4; }
        IOHIDDeviceRef device = IOHIDDeviceCreate(kCFAllocatorDefault, entry);
        IOObjectRelease(entry);
        if (!device) return 5;
        IOReturn opened = IOHIDDeviceOpen(device, kIOHIDOptionsTypeNone);
        if (opened != kIOReturnSuccess) {
            emit(@{@"openStatus": @((uint32_t)opened)}); CFRelease(device); return 77;
        }
        uint8_t buffer[64] = {0};
        Received received = {0};
        IOHIDDeviceRegisterInputReportCallback(device, buffer, sizeof(buffer), report, &received);
        IOHIDDeviceScheduleWithRunLoop(device, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
        emit(@{@"ready": @YES});
        // Repeated neutral frames are deduplicated by OJD. A report callback is
        // useful evidence if it arrives, but GET below is the deterministic check.
        CFAbsoluteTime deadline = CFAbsoluteTimeGetCurrent() + 0.5;
        while (!received.reports && !received.invalid && CFAbsoluteTimeGetCurrent() < deadline)
            CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.05, true);
        uint8_t input[64] = {0xff}; CFIndex inputLength = sizeof(input);
        IOReturn inputStatus = IOHIDDeviceGetReport(device, kIOHIDReportTypeInput, 0, input, &inputLength);
        BOOL neutral = inputStatus == kIOReturnSuccess && inputLength == 14;
        for (CFIndex i = 0; neutral && i < inputLength; ++i) neutral = input[i] == 0;
        uint8_t feature[64] = {0}; CFIndex featureLength = sizeof(feature);
        IOReturn featureStatus = IOHIDDeviceGetReport(device, kIOHIDReportTypeFeature, 0, feature, &featureLength);
        IOHIDDeviceUnscheduleFromRunLoop(device, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
        IOHIDDeviceClose(device, kIOHIDOptionsTypeNone); CFRelease(device);
        emit(@{@"inputReports": @(received.reports), @"inputInvalid": @(received.invalid),
            @"inputGetStatus": @((uint32_t)inputStatus), @"inputLength": @(inputLength),
            @"inputNeutral": @(neutral), @"featureGetStatus": @((uint32_t)featureStatus)});
        return !received.invalid && neutral && featureStatus != kIOReturnSuccess ? 0 : 1;
    }
}
