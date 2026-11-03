// Real upstream HIDAPI Windows consumer, restricted to an owned synthetic VM.
// Win32 count parity is measured independently by wine_consumer.cpp.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <objbase.h>
#include <hidapi.h>
#include <hidapi_winapi.h>
#include <array>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>

static void require(bool value, const char *message) { if (!value) throw std::runtime_error(message); }
static std::string utf8(const wchar_t *value)
{
    if (!value) return {};
    int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, nullptr, 0, nullptr, nullptr);
    require(size > 0, "Invalid UTF-16"); std::string result(size, '\0');
    require(WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, value, -1, result.data(), size, nullptr, nullptr) == size,
            "UTF-8 conversion failed"); result.pop_back(); return result;
}
static std::string json(const std::string &value)
{
    std::ostringstream out; out << '"';
    for (unsigned char c : value) {
        if (c == '\\' || c == '"') out << '\\' << c;
        else if (c < 32) { char escaped[7]; std::snprintf(escaped, sizeof(escaped), "\\u%04x", c); out << escaped; }
        else out << c;
    }
    out << '"'; return out.str();
}
static std::string hex(const unsigned char *data, size_t size)
{
    static const char digits[] = "0123456789abcdef";
    std::string out; out.reserve(size * 2);
    for (size_t i = 0; i < size; ++i) { out += digits[data[i] >> 4]; out += digits[data[i] & 15]; }
    return out;
}
static std::string marker(const wchar_t *path)
{
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    require(file != INVALID_HANDLE_VALUE, "Cannot read owned VM marker");
    char data[128]{}; DWORD count = 0;
    bool ok = ReadFile(file, data, 127, &count, nullptr) && count < 127;
    CloseHandle(file); require(ok, "Invalid VM marker length");
    std::string result(data, count);
    while (!result.empty() && (result.back() == '\n' || result.back() == '\r')) result.pop_back();
    return result;
}
static std::string scope(const std::wstring &token)
{
    using Version = const char *(__cdecl *)();
    FARPROC entry = GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "wine_get_version");
    Version version = nullptr; static_assert(sizeof(version) == sizeof(entry));
    std::memcpy(&version, &entry, sizeof(version));
    require(version != nullptr, "Native Windows execution is refused");
    wchar_t isolated[64]{};
    require(token.size() == 36 && GetEnvironmentVariableW(L"MADSENSE_CONSUMER_ISOLATED", isolated, 64) == 36
            && token == isolated, "Owned ordinary-account orchestration required");
    require(marker(L"Z:\\etc\\madsense-ci-owner") == utf8(token.c_str())
            && marker(L"Z:\\sys\\class\\dmi\\id\\sys_vendor") == "QEMU", "Owned QEMU identity mismatch");
    return version();
}
static std::array<unsigned char, 6> parse_mac(const std::wstring &text)
{
    require(text.size() == 12 && text.substr(0, 4) == L"02d5"
            && text.find_first_not_of(L"0123456789abcdef") == std::wstring::npos, "Exact virtual 02d5 MAC required");
    std::array<unsigned char, 6> result{};
    for (size_t i = 0; i < 6; ++i) result[i] = static_cast<unsigned char>(std::stoul(text.substr(2 * i, 2), nullptr, 16));
    return result;
}
static std::string observed_wine_build_id()
{
    using BuildId = const char *(__cdecl *)();
    FARPROC entry = GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "wine_get_build_id");
    BuildId build_id = nullptr; static_assert(sizeof(entry) == sizeof(build_id));
    std::memcpy(&build_id, &entry, sizeof(build_id));
    const char *value = build_id ? build_id() : nullptr;
    return value ? value : "";
}
using Device = std::unique_ptr<hid_device, decltype(&hid_close)>;
static bool same_mac(const std::array<unsigned char, 64> &data, int count, const std::array<unsigned char, 6> &mac)
{
    if (count < 20 || count > 64 || data[0] != 9) return false;
    for (size_t i = 0; i < 6; ++i) if (data[i + 1] != mac[5 - i]) return false;
    return true;
}
static Device select_device(unsigned short product, const std::array<unsigned char, 6> &mac)
{
    std::unique_ptr<hid_device_info, decltype(&hid_free_enumeration)> list(hid_enumerate(0x054c, product), hid_free_enumeration);
    const std::string enumeration_error = utf8(hid_error(nullptr));
    std::cout << "{\"kind\":\"hidapi-enumeration\",\"vendorId\":1356,\"productId\":" << product
              << ",\"listReturned\":" << (list ? "true" : "false") << ",\"hidError\":" << json(enumeration_error) << "}\n";
    Device selected(nullptr, hid_close); unsigned candidates = 0, matches = 0;
    for (auto *item = list.get(); item; item = item->next) {
        require(++candidates <= 16, "Too many Sony candidates in isolated fixture");
        std::cout << "{\"kind\":\"hidapi-enumerated-device\",\"path\":" << (item->path ? json(item->path) : "null")
                  << ",\"vendorId\":" << item->vendor_id << ",\"productId\":" << item->product_id
                  << ",\"usagePage\":" << item->usage_page << ",\"usage\":" << item->usage
                  << ",\"busType\":" << item->bus_type << ",\"interfaceNumber\":" << item->interface_number << "}\n";
        if (item->usage_page != 1 || item->usage != 5 || !item->path) continue;
        Device device(hid_open_path(item->path), hid_close);
        std::array<unsigned char, 64> data{}; data[0] = 9;
        int count = device ? hid_get_feature_report(device.get(), data.data(), data.size()) : -1;
        bool matches_mac = device && same_mac(data, count, mac);
        std::cout << "{\"kind\":\"hidapi-candidate\",\"path\":" << json(item->path)
                  << ",\"busType\":" << item->bus_type << ",\"interfaceNumber\":" << item->interface_number
                  << ",\"feature09Return\":" << count << ",\"macMatched\":" << (matches_mac ? "true" : "false") << "}\n";
        if (matches_mac) { ++matches; selected = std::move(device); }
    }
    require(matches == 1, "Exactly one MAC-verified HIDAPI path required");
    return selected;
}
static bool expected_input(const unsigned char *data, int count, bool south)
{
    return count == 64 && data[0] == 1 && data[1] == 128 && data[2] == 128 && data[3] == 128 && data[4] == 128
        && data[5] == 0 && data[6] == 0 && data[8] == (south ? 0x28 : 0x08) && data[9] == 0 && (data[10] & 7) == 0;
}
int wmain(int argc, wchar_t **argv)
{
    try {
        require(argc == 5, "usage: hidapi_windows_consumer UUID dualsense|edge MAC south|neutral");
        std::string wine = scope(argv[1]);
        auto mac = parse_mac(argv[3]); std::wstring persona = argv[2], state = argv[4];
        require(persona == L"dualsense" || persona == L"edge", "Unexpected Sony persona");
        require(state == L"south" || state == L"neutral", "Unexpected synthetic input state");
        require(std::string(hid_version_str()) == "0.15.0", "Unexpected real HIDAPI version");
        require(hid_init() == 0, "HIDAPI initialization failed");
        const std::string build_id = observed_wine_build_id();
        std::cout << "{\"kind\":\"runtime\",\"scope\":\"real-hidapi-windows-consumer\",\"wineVersion\":" << json(wine)
                  << ",\"wineBuildId\":" << (build_id.empty() ? "null" : json(build_id))
                  << ",\"hidapiVersion\":\"0.15.0\",\"hidapiCommit\":\"d6b2a974608dec3b76fb1e36c189f22b9cf3650c\","
                     "\"physicalDevices\":false,\"protonValidated\":false,\"gamesValidated\":false}\n";
        auto device = select_device(persona == L"edge" ? 0x0df2 : 0x0ce6, mac);
        for (auto spec : {std::pair<int, int>{9, 20}, {0x20, 64}, {5, 41}}) {
            std::array<unsigned char, 64> data{}; data[0] = static_cast<unsigned char>(spec.first);
            int count = hid_get_feature_report(device.get(), data.data(), data.size());
            std::cout << "{\"kind\":\"hidapi-feature\",\"reportId\":" << static_cast<unsigned>(spec.first)
                      << ",\"returnValue\":" << count << ",\"bufferLength\":64,\"meaningfulLength\":" << spec.second
                      << ",\"hex\":" << json(hex(data.data(), spec.second)) << "}\n";
            require(count >= spec.second && count <= 64 && data[0] == spec.first, "HIDAPI feature failed/short");
            if (spec.first == 9) require(same_mac(data, count, mac), "HIDAPI virtual MAC changed");
        }
        GUID container{}; int container_result = hid_winapi_get_container_id(device.get(), &container);
        wchar_t guid[40]{}; StringFromGUID2(container, guid, 40);
        std::cout << "{\"kind\":\"hidapi-container\",\"returnValue\":" << container_result
                  << ",\"containerId\":" << json(utf8(guid)) << "}\n";
        std::array<unsigned char, 64> input{}; bool matched = false;
        ULONGLONG deadline = GetTickCount64() + 5000;
        while (!matched && GetTickCount64() < deadline) {
            int count = hid_read_timeout(device.get(), input.data(), input.size(), 50);
            require(count >= 0, "HIDAPI input read failed");
            matched = expected_input(input.data(), count, state == L"south");
        }
        require(matched, "No matching HIDAPI input within five seconds");
        std::cout << "{\"kind\":\"hidapi-input\",\"returnValue\":64,\"state\":" << json(utf8(state.c_str()))
                  << ",\"hex\":" << json(hex(input.data(), input.size())) << "}\n";
        // Static lifetime plus process termination on a write failure avoids
        // freeing buffers/OVERLAPPED still owned by the upstream timeout path.
        static std::array<unsigned char, 48> output{};
        output[0] = 2; output[2] = 4; output[45] = 0x12; output[46] = 0x34; output[47] = 0x56;
        hid_winapi_set_write_timeout(device.get(), 3000);
        int count = hid_write(device.get(), output.data(), output.size());
        std::cout << "{\"kind\":\"hidapi-write\",\"requested\":48,\"returnValue\":" << count
                  << ",\"libraryReportedError\":" << (count < 0 ? "true" : "false")
                  << ",\"fullRequestedCount\":" << (count == 48 ? "true" : "false")
                  << ",\"hidError\":" << json(utf8(hid_error(device.get())))
                  << ",\"hex\":" << json(hex(output.data(), output.size()))
                  << ",\"outputDeliveryVerified\":false,\"nativeWindowsParityVerified\":false}\n" << std::flush;
        if (count < 0) {
            std::cout << "{\"kind\":\"result\",\"result\":\"failed\",\"error\":\"HIDAPI write failed; terminating without unwinding potentially pending library IO\"}\n" << std::flush;
            TerminateProcess(GetCurrentProcess(), 1); for (;;) Sleep(INFINITE);
        }
        require(count <= 48, "HIDAPI returned count larger than supplied report");
        Sleep(250); // Deliberate observation window; not a physical latency measurement.
        device.reset(); hid_exit();
        std::cout << "{\"kind\":\"result\",\"result\":\"observed\",\"outputDeliveryVerified\":false,"
                     "\"nativeWindowsParityVerified\":false,\"physicalActuationVerified\":false}\n";
        return 0;
    } catch (const std::exception &error) {
        std::cout << "{\"kind\":\"result\",\"result\":\"failed\",\"error\":" << json(error.what()) << "}\n";
        return 1;
    }
}
