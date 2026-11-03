// Research-only observer. Never opens a physical HID and never writes a report.
#import <Foundation/Foundation.h>
#import <IOKit/IOKitLib.h>
#import <IOKit/hid/IOHIDDevice.h>
#import <IOKit/hid/IOHIDKeys.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <sys/sysctl.h>
#include <errno.h>

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
static uint64_t usbParent(io_registry_entry_t entry) {
    io_registry_entry_t current = entry;
    IOObjectRetain(current);
    for (unsigned depth = 0; depth < 32; ++depth) {
        io_registry_entry_t parent = IO_OBJECT_NULL;
        kern_return_t status = IORegistryEntryGetParentEntry(current, kIOServicePlane, &parent);
        IOObjectRelease(current);
        if (status != KERN_SUCCESS || !parent) return 0;
        current = parent;
        if (IOObjectConformsTo(current, "IOUSBHostDevice")) {
            uint64_t identifier = 0;
            status = IORegistryEntryGetRegistryEntryID(current, &identifier);
            IOObjectRelease(current);
            return status == KERN_SUCCESS ? identifier : 0;
        }
    }
    IOObjectRelease(current);
    return 0;
}
static NSArray *entries(const char *requestedClass, NSMutableArray *errors) {
    io_iterator_t iterator = IO_OBJECT_NULL;
    CFMutableDictionaryRef matching = IOServiceMatching(requestedClass);
    if (!matching) {
        [errors addObject:@{@"query": @(requestedClass), @"operation": @"matching-dictionary"}];
        return nil;
    }
    kern_return_t status = IOServiceGetMatchingServices(kIOMainPortDefault, matching, &iterator);
    if (status != KERN_SUCCESS) {
        [errors addObject:@{@"query": @(requestedClass), @"operation": @"matching", @"status": @(status)}];
        return nil;
    }
    // XNU returns success plus a null iterator when copyExistingServices has no matches.
    // IOIteratorIsValid(NULL) reports false because there is no iterator to validate.
    // See IOUserClient.cpp internal_io_service_get_matching_services/withIterator,
    // and IOService.cpp copyExistingServices in apple-oss-distributions/xnu.
    if (iterator == IO_OBJECT_NULL) return @[];
    NSMutableArray *result = [NSMutableArray array];
    io_registry_entry_t entry;
    while ((entry = IOIteratorNext(iterator))) {
        NSDictionary *snapshot = properties(entry);
        uint64_t identifier = 0;
        io_name_t className;
        if (!snapshot || IORegistryEntryGetRegistryEntryID(entry, &identifier) != KERN_SUCCESS || !identifier
            || IOObjectGetClass(entry, className) != KERN_SUCCESS) {
            IOObjectRelease(entry);
            IOObjectRelease(iterator);
            [errors addObject:@{@"query": @(requestedClass), @"operation": @"entry-metadata"}];
            return nil;
        }
        NSMutableDictionary *item = [snapshot mutableCopy];
        item[@"ProbeRegistryID"] = @(identifier);
        item[@"ProbeRegistryClass"] = [NSString stringWithUTF8String:className];
        if (strcmp(requestedClass, "IOUSBHostInterface") == 0) {
            uint64_t parent = usbParent(entry);
            if (!parent) {
                [errors addObject:@{@"query": @(requestedClass), @"operation": @"USB-parent"}];
                IOObjectRelease(entry); IOObjectRelease(iterator); return nil;
            }
            item[@"ProbeUSBParentRegistryID"] = @(parent);
        }
        [result addObject:item];
        IOObjectRelease(entry);
    }
    BOOL valid = IOIteratorIsValid(iterator);
    IOObjectRelease(iterator);
    if (!valid) {
        [errors addObject:@{@"query": @(requestedClass), @"operation": @"invalidated-iterator"}];
        return nil;
    }
    return result;
}
// NSNumber also includes CFBoolean. Only integral CFNumbers in range are facts.
static NSNumber *unsignedNumber(id value, int64_t maximum) {
    if (!value || CFGetTypeID((__bridge CFTypeRef)value) != CFNumberGetTypeID()
        || CFNumberIsFloatType((__bridge CFNumberRef)value)) return nil;
    int64_t number = -1;
    if (!CFNumberGetValue((__bridge CFNumberRef)value, kCFNumberSInt64Type, &number)
        || number < 0 || number > maximum) return nil;
    return @(number);
}
static void copyNumber(NSMutableDictionary *row, NSDictionary *source, NSString *key,
                       int64_t maximum, NSMutableArray *errors) {
    NSNumber *number = unsignedNumber(source[key], maximum);
    row[key] = number ?: NSNull.null;
    if (!number) [errors addObject:@{@"field": key, @"reason": source[key] ? @"invalid-type-or-range" : @"missing"}];
}
// Deliberately exclude serials, names, addresses and location IDs. Only class,
// standard transport and public numeric device/usage metadata may be logged.
static NSArray *inventoryMetadata(NSArray *items, NSString *kind, NSArray *interfaces) {
    NSMutableArray *result = [NSMutableArray array];
    for (NSDictionary *item in items) {
        NSMutableDictionary *row = [NSMutableDictionary dictionary];
        NSMutableArray *errors = [NSMutableArray array];
        row[@"class"] = item[@"ProbeRegistryClass"];
        if ([kind isEqual:@"hid"]) {
            for (NSString *key in @[@"VendorID", @"ProductID", @"PrimaryUsagePage", @"PrimaryUsage"])
                copyNumber(row, item, key, UINT16_MAX, errors);
            id pairs = item[@"DeviceUsagePairs"];
            if ([pairs isKindOfClass:NSArray.class] && [pairs count] > 0) {
                NSMutableArray *parsed = [NSMutableArray array];
                for (id pair in pairs) {
                    NSMutableDictionary *usage = [NSMutableDictionary dictionary];
                    if (![pair isKindOfClass:NSDictionary.class]) {
                        [errors addObject:@{@"field": @"DeviceUsagePairs", @"reason": @"invalid-entry"}];
                        continue;
                    }
                    copyNumber(usage, pair, @"DeviceUsagePage", UINT16_MAX, errors);
                    copyNumber(usage, pair, @"DeviceUsage", UINT16_MAX, errors);
                    [parsed addObject:usage];
                }
                row[@"usagePairs"] = parsed;
            } else {
                row[@"usagePairs"] = NSNull.null;
                [errors addObject:@{@"field": @"DeviceUsagePairs", @"reason": pairs ? @"invalid-or-empty" : @"missing"}];
            }
        } else if ([kind isEqual:@"usb"]) {
            for (NSString *key in @[@"idVendor", @"idProduct"])
                copyNumber(row, item, key, UINT16_MAX, errors);
            for (NSString *key in @[@"bDeviceClass", @"bDeviceSubClass", @"bDeviceProtocol",
                                   @"bNumConfigurations", @"kUSBCurrentConfiguration"])
                copyNumber(row, item, key, UINT8_MAX, errors);
            NSMutableArray *children = [NSMutableArray array];
            for (NSDictionary *interface in interfaces)
                if ([interface[@"ProbeUSBParentRegistryID"] isEqual:item[@"ProbeRegistryID"]]) [children addObject:interface];
            row[@"interfaces"] = interfaces ? inventoryMetadata(children, @"interface", nil) : (id)NSNull.null;
        } else if ([kind isEqual:@"interface"]) {
            for (NSString *key in @[@"bInterfaceNumber", @"bAlternateSetting", @"bInterfaceClass",
                                   @"bInterfaceSubClass", @"bInterfaceProtocol"])
                copyNumber(row, item, key, UINT8_MAX, errors);
        } else if ([kind isEqual:@"bluetooth"]) {
            id connected = item[@"BluetoothTransportConnected"];
            BOOL typed = connected && CFGetTypeID((__bridge CFTypeRef)connected) == CFBooleanGetTypeID();
            row[@"BluetoothTransportConnected"] = typed ? connected : NSNull.null;
            if (!typed) [errors addObject:@{@"field": @"BluetoothTransportConnected",
                @"reason": connected ? @"not-CFBoolean" : @"missing"}];
        }
        id transport = item[@"Transport"];
        if ([transport isKindOfClass:NSString.class]) {
            row[@"transport"] = [@[@"USB", @"Bluetooth", @"Bluetooth Low Energy", @"Virtual", @"SPI", @"I2C"] containsObject:transport]
                ? transport : @"other";
        } else if ([kind isEqual:@"hid"]) {
            row[@"transport"] = NSNull.null;
            [errors addObject:@{@"field": @"Transport", @"reason": transport ? @"invalid-type" : @"missing"}];
        }
        row[@"metadataErrors"] = errors;
        [result addObject:row];
    }
    return result;
}
static NSDictionary *vmProvenance(void) {
    int present = -1;
    size_t size = sizeof(present);
    errno = 0;
    int status = sysctlbyname("kern.hv_vmm_present", &present, &size, NULL, 0);
    int queryError = errno;
    BOOL valid = status == 0 && size == sizeof(present) && (present == 0 || present == 1);
    // This is corroborating kernel evidence, never proof that USB passthrough is absent.
    // C comparisons have type int; @(present == 1) serializes as JSON 0/1, not Boolean.
    id hypervisor = valid ? (present == 1 ? @YES : @NO) : NSNull.null;
    return @{@"source": @"sysctl:kern.hv_vmm_present", @"hypervisorPresent": hypervisor,
        @"queryStatus": @(status), @"queryErrno": @(queryError), @"valueSize": @(size),
        @"complete": @(valid), @"provesHardwareIsolation": @NO};
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
        if (argc == 2 && strcmp(argv[1], "self-test") == 0) {
            BOOL valid = [unsignedNumber(@0, 255) isEqual:@0] && [unsignedNumber(@255, 255) isEqual:@255];
            for (id invalid in @[@YES, @NO, @0.0, @(-1), @256, @"0", NSNull.null])
                valid = valid && unsignedNumber(invalid, 255) == nil;
            valid = valid && CFGetTypeID((__bridge CFTypeRef)@NO) == CFBooleanGetTypeID()
                && CFGetTypeID((__bridge CFTypeRef)@0) != CFBooleanGetTypeID();
            NSDictionary *vm = vmProvenance();
            id hypervisor = vm[@"hypervisorPresent"];
            valid = valid && (![vm[@"complete"] boolValue]
                ? hypervisor == NSNull.null
                : CFGetTypeID((__bridge CFTypeRef)hypervisor) == CFBooleanGetTypeID());
            NSMutableArray *queryErrors = [NSMutableArray array];
            NSArray *absent = entries("MadSenseObserverAbsentClass_5314928EF0344DAA", queryErrors);
            BOOL absentPassed = absent && absent.count == 0 && queryErrors.count == 0;
            emit(@{@"parserSelfTest": valid ? @"passed" : @"failed",
                @"absentRegistryClassTest": absentPassed ? @"passed" : @"failed", @"hardwareValidated": @NO});
            return valid && absentPassed ? 0 : 1;
        }
        if (argc == 2 && strcmp(argv[1], "inventory") == 0) {
            NSMutableArray *queryErrors = [NSMutableArray array];
            NSArray *hid = entries("IOHIDDevice", queryErrors), *usb = entries("IOUSBHostDevice", queryErrors),
                *bluetooth = entries("IOBluetoothHCIController", queryErrors),
                *interfaces = entries("IOUSBHostInterface", queryErrors);
            NSMutableDictionary *transports = [NSMutableDictionary dictionary];
            // Query base classes (including subclasses), plus explicit transport families for evidence.
            // These registry reads do not instantiate CoreBluetooth or IOBluetooth APIs.
            for (NSString *className in @[@"IOBluetoothHostControllerTransport", @"IOBluetoothHostController",
                @"IOBluetoothHostControllerUSBTransport", @"IOBluetoothHostControllerUARTTransport",
                @"IOBluetoothHostControllerPCIeTransport"]) {
                NSArray *found = entries(className.UTF8String, queryErrors);
                transports[className] = found ? inventoryMetadata(found, @"class", nil) : (id)NSNull.null;
            }
            if (usb && interfaces) {
                NSSet *usbIDs = [NSSet setWithArray:[usb valueForKey:@"ProbeRegistryID"]];
                for (NSDictionary *interface in interfaces) {
                    if (![usbIDs containsObject:interface[@"ProbeUSBParentRegistryID"]]) {
                        [queryErrors addObject:@{@"query": @"IOUSBHostInterface", @"operation": @"unobserved-USB-parent"}];
                    }
                }
            }
            unsigned unsafe = 0;
            BOOL unsafeKnown = hid != nil;
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
                if (!unsignedNumber(p[@"PrimaryUsagePage"], UINT16_MAX)
                    || !unsignedNumber(p[@"PrimaryUsage"], UINT16_MAX)
                    || ![p[@"Transport"] isKindOfClass:NSString.class]) { unsafeKnown = NO; continue; }
                int page = [p[@"PrimaryUsagePage"] intValue], usage = [p[@"PrimaryUsage"] intValue];
                NSString *transport = p[@"Transport"];
                if (page <= 0 || usage <= 0 || transport.length == 0) { unsafeKnown = NO; continue; }
                if ((page == 1 && (usage == 4 || usage == 5 || usage == 8)) || page >= 0xff00
                    || [transport hasPrefix:@"Bluetooth"] || [transport isEqual:@"USB"]) ++unsafe;
            }
            BOOL complete = hid && usb && bluetooth && interfaces && queryErrors.count == 0;
            emit(@{@"schemaVersion": @2, @"enumerationComplete": @(complete), @"queryErrors": queryErrors,
                @"mode": @"read-only-registry", @"unsafeHIDCount": unsafeKnown ? @(unsafe) : NSNull.null,
                @"usbDeviceCount": usb ? @(usb.count) : (id)NSNull.null,
                @"usbInterfaceCount": interfaces ? @(interfaces.count) : (id)NSNull.null,
                @"bluetoothControllerCount": bluetooth ? @(bluetooth.count) : (id)NSNull.null,
                @"feeds": hid ? feeds : (id)NSNull.null,
                @"hidInventory": hid ? inventoryMetadata(hid, @"hid", nil) : (id)NSNull.null,
                @"usbInventory": usb ? inventoryMetadata(usb, @"usb", interfaces) : (id)NSNull.null,
                @"bluetoothInventory": bluetooth ? inventoryMetadata(bluetooth, @"bluetooth", nil) : (id)NSNull.null,
                @"bluetoothTransportQueries": transports, @"vmProvenance": vmProvenance()});
            return complete ? 0 : 1;
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
