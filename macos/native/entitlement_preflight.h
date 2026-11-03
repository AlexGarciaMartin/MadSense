// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "madsense_hid.h"
#include <CoreFoundation/CoreFoundation.h>

namespace madsense {
// Kept separate from the OS query so malformed values and error precedence
// can be tested without attaching an entitlement to a running executable.
inline ms_hid_entitlement_state classify_entitlement(CFTypeRef value, CFErrorRef error) {
    if (error) return MS_HID_ENTITLEMENT_UNKNOWN;
    if (!value || CFGetTypeID(value) != CFBooleanGetTypeID()) return MS_HID_ENTITLEMENT_MISSING;
    return CFBooleanGetValue(static_cast<CFBooleanRef>(value))
        ? MS_HID_ENTITLEMENT_PRESENT : MS_HID_ENTITLEMENT_MISSING;
}
}
