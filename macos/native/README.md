# macOS virtual HID bridge

This library uses Apple's public `IOKit/hidsystem/IOHIDUserDevice.h` API
(available since macOS 10.15); MadSense targets macOS 14 or newer. It creates a
HID service with **Virtual** transport. It does not emulate a USB controller,
USB composite audio, an Xbox XInput device, or HidHide's per-application rules.

Build on macOS:

```sh
cmake -S macos/native -B .build/macos-native -DCMAKE_BUILD_TYPE=Release
cmake --build .build/macos-native --parallel
ctest --test-dir .build/macos-native --output-on-failure
```

The outputs are `libmadsense_hid.dylib` and `madsense_hid_tests`. The ABI is in
`madsense_hid.h`; use a .NET SafeHandle and retain both delegate objects until
destruction completes. The GET callback receives a writable buffer and its
capacity, and returns actual length. Report IDs are preserved in the data.
Callbacks run on a serial queue; destruction cancels and drains that queue.
Never dispose from inside a callback or propagate managed exceptions across it.

The process that calls this library needs an **accepted**
`com.apple.developer.hid.virtual.device` entitlement. The included entitlement
file is a signing input, not an Apple approval, provisioning profile, certificate
or distribution guarantee. Root is not a replacement. A null create result is
reported as an error; Apple does not expose its underlying IOReturn, so the
library does not pretend every failure proves a specific permission problem.

The default native test returns **77 (skipped)** if virtual creation is refused.
`--abi-only` checks the ABI without device creation; `--require-device` fails on
creation refusal. Successful loopback must enumerate the service and verify
feature GET, output SET and input receipt through real IOKit, without a physical
controller. This cannot validate games, Bluetooth, exclusivity or USB audio.

For a CI development experiment only, sign the test executable with the included
entitlement, then run `--require-device`. Record whether macOS actually accepts
it; an ad-hoc signature's presence is not evidence of distribution readiness.

Primary sources:

- [Public IOKit user-device API](https://github.com/apple-oss-distributions/IOKitUser/blob/main/hidsystem.subproj/IOHIDUserDevice.h)
- [Apple virtual-device entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.hid.virtual.device)
- [Kernel entitlement checks](https://github.com/apple-oss-distributions/IOHIDFamily/blob/main/IOHIDFamily/IOHIDResourceUserClient.cpp)
- [Kernel virtual transport behavior](https://github.com/apple-oss-distributions/IOHIDFamily/blob/main/IOHIDFamily/IOHIDUserDevice.cpp)
