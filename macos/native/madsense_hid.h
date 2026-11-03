// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <stdint.h>

#if defined(__GNUC__)
#define MS_HID_API __attribute__((visibility("default")))
#else
#define MS_HID_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

// IOKit report types: input = 0, output = 1, feature = 2.
// Buffers preserve IOKit's report framing, including a nonzero report ID.
// Return an IOReturn (zero on success). GET length is capacity on entry and
// actual bytes on return. Never return more than the supplied capacity.
// Callbacks execute on a private serial queue; they must not throw, block on
// the main/UI thread, or destroy the device. Keep callbacks/context alive until
// ms_hid_destroy returns successfully; it drains callbacks before returning.
typedef int32_t (*ms_hid_get_callback)(void *context, uint32_t type,
    uint32_t report_id, uint8_t *data, uint32_t *length);
typedef int32_t (*ms_hid_set_callback)(void *context, uint32_t type,
    uint32_t report_id, const uint8_t *data, uint32_t length);

MS_HID_API uint32_t ms_hid_abi_version(void);
typedef enum ms_hid_entitlement_state {
    MS_HID_ENTITLEMENT_UNKNOWN = 0,
    MS_HID_ENTITLEMENT_MISSING = 1,
    MS_HID_ENTITLEMENT_PRESENT = 2
} ms_hid_entitlement_state;
// Inspect only this process's public virtual-HID entitlement through SecTask.
// No physical or virtual device is opened/created. PRESENT does not establish
// signature acceptance, successful virtual creation, publication, or hardware
// compatibility. UNKNOWN preserves query errors in ms_hid_copy_diagnostic.
MS_HID_API uint32_t ms_hid_preflight(void);
// Success means the exact new IOHIDDevice was published in the IORegistry.
// Publication has a five-second deadline and fully tears down on failure.
MS_HID_API int32_t ms_hid_create(const uint8_t *descriptor,
    uint32_t descriptor_length, uint16_t vendor_id, uint16_t product_id,
    const char *serial_utf8, ms_hid_get_callback get_callback,
    ms_hid_set_callback set_callback, void *context, void **out_handle);
MS_HID_API int32_t ms_hid_send_input(void *handle, const uint8_t *report,
    uint32_t report_length);
// The caller must prevent new calls after starting destruction. .NET callers
// should use SafeHandle P/Invoke arguments, which protect in-flight calls.
// Null is a successful no-op. Calling from a callback returns kIOReturnBusy
// without destroying anything, so closure can be scheduled on another thread.
MS_HID_API int32_t ms_hid_destroy(void *handle);
// Last diagnostic on the calling thread. Returns the required byte count,
// including NUL; always terminates output when capacity > 0.
MS_HID_API uint32_t ms_hid_copy_diagnostic(char *output, uint32_t capacity);

#ifdef __cplusplus
}
#endif
