// Fixed locations of the original root-owned VM marker. No marker fabrication,
// arbitrary path override or fallback after a failed read is permitted.
#pragma once
#include <stdexcept>
#include <string_view>

namespace owned_scope {
inline const wchar_t *marker_path(bool present, std::wstring_view layout)
{
    if (!present) return L"Z:\\etc\\madsense-ci-owner";
    if (layout == L"pressure-vessel") return L"Z:\\run\\host\\etc\\madsense-ci-owner";
    throw std::runtime_error("Unknown owned VM scope layout");
}

#ifdef _WIN32
// windows.h must precede this header in the Windows observers. The portable
// selector above is also compiled and exercised without Wine or device access.
inline const wchar_t *marker_path_from_environment()
{
    wchar_t layout[64]{};
    SetLastError(ERROR_SUCCESS);
    DWORD length = GetEnvironmentVariableW(L"MADSENSE_CONSUMER_SCOPE_LAYOUT", layout, 64);
    if (length == 0 && GetLastError() == ERROR_ENVVAR_NOT_FOUND)
        return marker_path(false, {});
    if (length == 0 || length >= 64)
        throw std::runtime_error("Invalid owned VM scope layout environment");
    return marker_path(true, std::wstring_view(layout, length));
}
#endif
}
