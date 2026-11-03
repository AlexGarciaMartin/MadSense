// Windows API observer for an isolated ordinary account in the owned QEMU guest.
// Never execute on native Windows or select a controller by VID/PID alone.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#define _WIN32_WINNT 0x0601
#include <windows.h>
#include <initguid.h>
#include <setupapi.h>
// MinGW's HID headers do not supply C++ linkage guards.
extern "C" {
#include <hidsdi.h>
#include <hidpi.h>
}
#include <devpkey.h>
#include <mmdeviceapi.h>
#include <audioclient.h>
#include <functiondiscoverykeys_devpkey.h>
#include <ks.h>
#include <ksmedia.h>
#include <xinput.h>
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstddef>
#include <cstdlib>
#include <cwctype>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <utility>

static std::string utf8(const std::wstring &s)
{
    if (s.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), nullptr, 0, nullptr, nullptr);
    if (!n) throw std::runtime_error("UTF-8 conversion failed");
    std::string result(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, s.data(), static_cast<int>(s.size()), result.data(), n, nullptr, nullptr);
    return result;
}
static std::string json(const std::string &s)
{
    std::ostringstream out; out << '"';
    for (unsigned char c : s) {
        if (c == '\\' || c == '"') out << '\\' << c;
        else if (c < 32) { char escaped[7]; std::snprintf(escaped, sizeof(escaped), "\\u%04x", c); out << escaped; }
        else out << c;
    }
    out << '"'; return out.str();
}
static std::string json(const std::wstring &s) { return json(utf8(s)); }
static void require(bool condition, const char *message)
{
    if (!condition) throw std::runtime_error(std::string(message) + " (Win32=" + std::to_string(GetLastError()) + ")");
}
static void check(HRESULT hr, const char *message)
{
    if (FAILED(hr)) { std::ostringstream text; text << message << " HRESULT=0x" << std::hex << static_cast<unsigned long>(hr); throw std::runtime_error(text.str()); }
}
static std::string hex(const unsigned char *bytes, size_t length)
{
    static const char digits[] = "0123456789abcdef";
    std::string result; result.reserve(length * 2);
    for (size_t i = 0; i < length; ++i) { result += digits[bytes[i] >> 4]; result += digits[bytes[i] & 15]; }
    return result;
}
static std::array<unsigned char, 6> parse_mac(const std::wstring &s)
{
    require(s.size() == 12 && s.substr(0, 4) == L"02d5", "Expected complete synthetic 02d5 MAC in lowercase hex");
    std::array<unsigned char, 6> mac{};
    for (size_t i = 0; i < mac.size(); ++i) {
        std::wstring pair = s.substr(2 * i, 2);
        require(pair.find_first_not_of(L"0123456789abcdef") == std::wstring::npos, "Invalid MAC hex");
        mac[i] = static_cast<unsigned char>(std::stoul(pair, nullptr, 16));
    }
    return mac;
}
struct Handle {
    HANDLE value = INVALID_HANDLE_VALUE;
    explicit Handle(HANDLE v = INVALID_HANDLE_VALUE) : value(v) {}
    ~Handle() { if (value != INVALID_HANDLE_VALUE && value) CloseHandle(value); }
    Handle(const Handle &) = delete;
    Handle &operator=(const Handle &) = delete;
};
template<class T> struct Com {
    T *value = nullptr;
    ~Com() { if (value) value->Release(); }
    T **out() { return &value; }
    T *operator->() const { return value; }
};
static std::wstring guid_text(const GUID &guid)
{
    wchar_t buffer[40]{}; StringFromGUID2(guid, buffer, 40); return buffer;
}
static std::string read_marker(const wchar_t *path)
{
    Handle file(CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr));
    require(file.value != INVALID_HANDLE_VALUE, "Open QEMU scope marker");
    char text[128]{}; DWORD read = 0;
    require(ReadFile(file.value, text, 127, &read, nullptr) && read < 127, "Read QEMU scope marker");
    std::string result(text, read);
    while (!result.empty() && (result.back() == '\r' || result.back() == '\n')) result.pop_back();
    return result;
}
static std::string validate_scope(const std::wstring &token)
{
    using WineVersion = const char *(__cdecl *)();
    FARPROC entry = GetProcAddress(GetModuleHandleW(L"ntdll.dll"), "wine_get_version");
    WineVersion wine_version = nullptr;
    static_assert(sizeof(entry) == sizeof(wine_version));
    std::memcpy(&wine_version, &entry, sizeof(wine_version));
    require(wine_version != nullptr, "This observer refuses native Windows execution");
    require(token.size() == 36, "Explicit owned guest UUID required");
    wchar_t isolated[64]{};
    require(GetEnvironmentVariableW(L"MADSENSE_CONSUMER_ISOLATED", isolated, 64) == 36 && token == isolated,
            "Linux orchestrator must confirm the isolated consumer account");
    require(read_marker(L"Z:\\etc\\madsense-ci-owner") == utf8(token)
            && read_marker(L"Z:\\sys\\class\\dmi\\id\\sys_vendor") == "QEMU", "Owned QEMU marker mismatch");
    return wine_version();
}

struct Hid {
    std::wstring path;
    GUID container{};
    bool container_known = false;
    HIDP_CAPS caps{};
    std::vector<unsigned char> pairing;
};
static HIDP_CAPS caps_for(HANDLE handle)
{
    PHIDP_PREPARSED_DATA data = nullptr;
    require(HidD_GetPreparsedData(handle, &data), "HidD_GetPreparsedData");
    HIDP_CAPS caps{};
    NTSTATUS status = HidP_GetCaps(data, &caps);
    HidD_FreePreparsedData(data);
    require(status == HIDP_STATUS_SUCCESS, "HidP_GetCaps");
    return caps;
}
static std::vector<unsigned char> feature(HANDLE handle, const HIDP_CAPS &caps, unsigned char id, size_t minimum)
{
    require(caps.FeatureReportByteLength >= minimum && caps.FeatureReportByteLength <= 4096, "Invalid HID feature capability length");
    std::vector<unsigned char> data(caps.FeatureReportByteLength); data[0] = id;
    require(HidD_GetFeature(handle, data.data(), static_cast<ULONG>(data.size())), "HidD_GetFeature");
    require(data[0] == id, "Feature report ID changed");
    return data;
}
static bool mac_matches(const std::vector<unsigned char> &pairing, const std::array<unsigned char, 6> &mac)
{
    if (pairing.size() < 7) return false;
    for (size_t i = 0; i < 6; ++i) if (pairing[i + 1] != mac[5 - i]) return false;
    return true;
}
static Hid identify_hid(unsigned vendor, unsigned product, const std::array<unsigned char, 6> &mac)
{
    GUID guid; HidD_GetHidGuid(&guid);
    HDEVINFO devices = SetupDiGetClassDevsW(&guid, nullptr, nullptr, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE);
    require(devices != INVALID_HANDLE_VALUE, "SetupDiGetClassDevs HID");
    std::vector<Hid> matches;
    try {
        for (DWORD index = 0;; ++index) {
            SP_DEVICE_INTERFACE_DATA iface{}; iface.cbSize = sizeof(iface);
            if (!SetupDiEnumDeviceInterfaces(devices, nullptr, &guid, index, &iface)) {
                require(GetLastError() == ERROR_NO_MORE_ITEMS, "SetupDiEnumDeviceInterfaces"); break;
            }
            DWORD size = 0;
            SetupDiGetDeviceInterfaceDetailW(devices, &iface, nullptr, 0, &size, nullptr);
            require(size >= sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W) && size <= 65536, "Unexpected SetupAPI detail size");
            std::vector<unsigned char> storage(size);
            auto *detail = reinterpret_cast<SP_DEVICE_INTERFACE_DETAIL_DATA_W *>(storage.data());
            detail->cbSize = sizeof(*detail);
            SP_DEVINFO_DATA info{}; info.cbSize = sizeof(info);
            require(SetupDiGetDeviceInterfaceDetailW(devices, &iface, detail, size, nullptr, &info), "SetupDiGetDeviceInterfaceDetail");
            Handle handle(CreateFileW(detail->DevicePath, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr));
            if (handle.value == INVALID_HANDLE_VALUE) continue;
            HIDD_ATTRIBUTES attributes{}; attributes.Size = sizeof(attributes);
            if (!HidD_GetAttributes(handle.value, &attributes) || attributes.VendorID != vendor || attributes.ProductID != product) continue;
            Hid candidate; candidate.path = detail->DevicePath; candidate.caps = caps_for(handle.value);
            // Preserve the backend's actual capability surface even when it
            // cannot serve the native DualSense feature reports required below.
            std::cout << "{\"kind\":\"hid-capabilities\",\"path\":" << json(candidate.path)
                      << ",\"usagePage\":" << candidate.caps.UsagePage << ",\"usage\":" << candidate.caps.Usage
                      << ",\"inputReportByteLength\":" << candidate.caps.InputReportByteLength
                      << ",\"outputReportByteLength\":" << candidate.caps.OutputReportByteLength
                      << ",\"featureReportByteLength\":" << candidate.caps.FeatureReportByteLength << "}\n";
            std::cout.flush();
            if (candidate.caps.UsagePage != 1 || candidate.caps.Usage != 5) continue;
            candidate.pairing = feature(handle.value, candidate.caps, 0x09, 20);
            bool same = mac_matches(candidate.pairing, mac);
            DEVPROPTYPE type = 0; DWORD returned = 0;
            candidate.container_known = SetupDiGetDevicePropertyW(devices, &info, &DEVPKEY_Device_ContainerId, &type,
                reinterpret_cast<BYTE *>(&candidate.container), sizeof(candidate.container), &returned, 0)
                && type == DEVPROP_TYPE_GUID && returned == sizeof(GUID) && !IsEqualGUID(candidate.container, GUID_NULL);
            std::cout << "{\"kind\":\"hid-candidate\",\"path\":" << json(candidate.path)
                      << ",\"macMatched\":" << (same ? "true" : "false")
                      << ",\"containerKnown\":" << (candidate.container_known ? "true" : "false")
                      << ",\"containerId\":" << json(guid_text(candidate.container)) << "}\n";
            if (same) matches.push_back(std::move(candidate));
        }
    } catch (...) { SetupDiDestroyDeviceInfoList(devices); throw; }
    SetupDiDestroyDeviceInfoList(devices);
    require(matches.size() == 1, "Expected exactly one Sony gamepad with the supplied virtual MAC");
    return matches.front();
}
static std::wstring canonical_hid_path(std::wstring path)
{
    std::transform(path.begin(), path.end(), path.begin(), [](wchar_t c) { return static_cast<wchar_t>(towlower(c)); });
    if (path.size() >= 4 && path.substr(0, 4) == L"\\??\\") path.replace(0, 4, L"\\\\?\\");
    return path;
}
static HANDLE raw_input_identity(const Hid &hid, unsigned vendor, unsigned product)
{
    UINT count = 0;
    require(GetRawInputDeviceList(nullptr, &count, sizeof(RAWINPUTDEVICELIST)) != static_cast<UINT>(-1) && count < 4096,
            "GetRawInputDeviceList count");
    std::vector<RAWINPUTDEVICELIST> list(count);
    UINT actual = GetRawInputDeviceList(list.data(), &count, sizeof(RAWINPUTDEVICELIST));
    require(actual != static_cast<UINT>(-1), "GetRawInputDeviceList");
    unsigned matches = 0, gamepads = 0; HANDLE selected = nullptr;
    for (UINT i = 0; i < actual; ++i) {
        if (list[i].dwType != RIM_TYPEHID) continue;
        RID_DEVICE_INFO info{}; info.cbSize = sizeof(info); UINT size = sizeof(info);
        require(GetRawInputDeviceInfoW(list[i].hDevice, RIDI_DEVICEINFO, &info, &size) != static_cast<UINT>(-1), "Raw Input device info");
        if (info.hid.usUsagePage != 1 || (info.hid.usUsage != 4 && info.hid.usUsage != 5)) continue;
        ++gamepads;
        if (info.hid.dwVendorId != vendor || info.hid.dwProductId != product || info.hid.usUsage != 5) continue;
        UINT chars = 0;
        require(GetRawInputDeviceInfoW(list[i].hDevice, RIDI_DEVICENAME, nullptr, &chars) != static_cast<UINT>(-1) && chars < 32768,
                "Raw Input name length");
        std::vector<wchar_t> name(chars + 1);
        require(GetRawInputDeviceInfoW(list[i].hDevice, RIDI_DEVICENAME, name.data(), &chars) != static_cast<UINT>(-1), "Raw Input name");
        bool same = canonical_hid_path(name.data()) == canonical_hid_path(hid.path);
        std::cout << "{\"kind\":\"raw-input-candidate\",\"path\":" << json(std::wstring(name.data()))
                  << ",\"selectedPathMatched\":" << (same ? "true" : "false") << "}\n";
        if (same) { ++matches; selected = list[i].hDevice; }
    }
    std::cout << "{\"kind\":\"raw-input-count\",\"gamepadCollections\":" << gamepads << ",\"selectedMatches\":" << matches << "}\n";
    require(matches == 1 && gamepads == 1, "Exactly one Raw Input gamepad must match the MAC-verified SetupAPI path");
    return selected;
}
static bool expected_sony_input(const unsigned char *bytes, size_t size, bool south)
{
    return size == 64 && bytes[0] == 1 && bytes[1] == 128 && bytes[2] == 128 && bytes[3] == 128 && bytes[4] == 128
        && bytes[5] == 0 && bytes[6] == 0 && bytes[8] == (south ? 0x28 : 0x08) && bytes[9] == 0 && (bytes[10] & 7) == 0;
}
static void raw_input_read(HANDLE selected, bool south)
{
    WNDCLASSW cls{}; cls.lpfnWndProc = DefWindowProcW; cls.hInstance = GetModuleHandleW(nullptr); cls.lpszClassName = L"MadSenseWineConsumer";
    require(RegisterClassW(&cls), "Register Raw Input message window class");
    HWND window = CreateWindowW(cls.lpszClassName, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, cls.hInstance, nullptr);
    require(window != nullptr, "Create Raw Input message window (headless Wine may need Xvfb)");
    RAWINPUTDEVICE device{1, 5, RIDEV_INPUTSINK, window};
    bool matched = false;
    try {
        require(RegisterRawInputDevices(&device, 1, sizeof(device)), "RegisterRawInputDevices");
        ULONGLONG deadline = GetTickCount64() + 5000;
        while (!matched && GetTickCount64() < deadline) {
            MSG message;
            while (PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) {
                if (message.message == WM_INPUT) {
                    UINT size = 0;
                    if (GetRawInputData(reinterpret_cast<HRAWINPUT>(message.lParam), RID_INPUT, nullptr, &size, sizeof(RAWINPUTHEADER)) == 0
                        && size >= sizeof(RAWINPUTHEADER) && size <= 65536) {
                        std::vector<unsigned char> storage(size);
                        if (GetRawInputData(reinterpret_cast<HRAWINPUT>(message.lParam), RID_INPUT, storage.data(), &size, sizeof(RAWINPUTHEADER)) == size) {
                            auto *raw = reinterpret_cast<RAWINPUT *>(storage.data());
                            if (raw->header.hDevice == selected && raw->header.dwType == RIM_TYPEHID) {
                                size_t offset = offsetof(RAWINPUT, data.hid.bRawData);
                                uint64_t bytes = static_cast<uint64_t>(raw->data.hid.dwSizeHid) * raw->data.hid.dwCount;
                                if (offset <= size && bytes <= size - offset)
                                    for (DWORD i = 0; i < raw->data.hid.dwCount; ++i) {
                                        const auto *report = raw->data.hid.bRawData + i * raw->data.hid.dwSizeHid;
                                        if (expected_sony_input(report, raw->data.hid.dwSizeHid, south)) {
                                            matched = true;
                                            std::cout << "{\"kind\":\"raw-input-report\",\"hex\":" << json(hex(report, 64)) << "}\n";
                                            break;
                                        }
                                    }
                            }
                        }
                    }
                }
                TranslateMessage(&message); DispatchMessageW(&message);
            }
            Sleep(5);
        }
    } catch (...) { DestroyWindow(window); throw; }
    device.dwFlags = RIDEV_REMOVE; device.hwndTarget = nullptr;
    RegisterRawInputDevices(&device, 1, sizeof(device)); DestroyWindow(window);
    require(matched, "No matching Raw Input report within five seconds");
}
static void sony_probe(unsigned vendor, unsigned product, const std::array<unsigned char, 6> &mac, bool south)
{
    Hid hid = identify_hid(vendor, product, mac);
    HANDLE raw = raw_input_identity(hid, vendor, product);
    Handle handle(CreateFileW(hid.path.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                              nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr));
    require(handle.value != INVALID_HANDLE_VALUE, "Open MAC-verified HID path for IO");
    HIDP_CAPS caps = caps_for(handle.value);
    require(caps.InputReportByteLength == 64 && caps.OutputReportByteLength == 48, "Expected native USB DualSense report lengths");
    for (auto spec : {std::pair<unsigned char, size_t>{9, 20}, {0x20, 64}, {5, 41}}) {
        auto data = feature(handle.value, caps, spec.first, spec.second);
        if (spec.first == 9) require(mac_matches(data, mac), "Virtual MAC changed after reopen");
        std::cout << "{\"kind\":\"hid-feature\",\"reportId\":" << static_cast<unsigned>(spec.first)
                  << ",\"bufferLength\":" << data.size() << ",\"meaningfulLength\":" << spec.second
                  << ",\"hex\":" << json(hex(data.data(), spec.second)) << "}\n";
    }
    // Raw Input exercises a Windows consumer path distinct from HidD_GetFeature.
    raw_input_read(raw, south);
    std::array<unsigned char, 48> output{}; output[0] = 2; output[2] = 4;
    output[45] = 0x12; output[46] = 0x34; output[47] = 0x56;
    Handle event(CreateEventW(nullptr, TRUE, FALSE, nullptr)); require(event.value != nullptr, "Output completion event");
    OVERLAPPED operation{}; operation.hEvent = event.value; DWORD written = 0;
    BOOL immediate = WriteFile(handle.value, output.data(), static_cast<DWORD>(output.size()), &written, &operation);
    if (!immediate) {
        require(GetLastError() == ERROR_IO_PENDING, "Write virtual HID RGB marker");
        DWORD wait = WaitForSingleObject(event.value, 3000);
        if (wait != WAIT_OBJECT_0) { CancelIoEx(handle.value, &operation); GetOverlappedResult(handle.value, &operation, &written, TRUE); }
        require(wait == WAIT_OBJECT_0 && GetOverlappedResult(handle.value, &operation, &written, FALSE), "HID output completion");
    }
    require(written == output.size(), "Short HID output write");
    std::cout << "{\"kind\":\"hid-output\",\"hex\":" << json(hex(output.data(), output.size()))
              << ",\"outputDeliveryVerified\":false}\n";
}

static void xinput_probe(bool south)
{
    unsigned matches = 0; DWORD chosen = 0; XINPUT_STATE selected{};
    for (DWORD i = 0; i < XUSER_MAX_COUNT; ++i) {
        XINPUT_STATE state{}; DWORD status = XInputGetState(i, &state);
        std::cout << "{\"kind\":\"xinput-slot\",\"index\":" << i << ",\"status\":" << status << "}\n";
        if (status == ERROR_SUCCESS) { ++matches; chosen = i; selected = state; }
        else require(status == ERROR_DEVICE_NOT_CONNECTED, "Unexpected XInput state error");
    }
    require(matches == 1, "Isolated Xbox experiment requires exactly one connected XInput slot");
    const auto &g = selected.Gamepad;
    require(g.wButtons == (south ? XINPUT_GAMEPAD_A : 0) && g.bLeftTrigger == 0 && g.bRightTrigger == 0
            && std::abs(static_cast<int>(g.sThumbLX)) <= 256 && std::abs(static_cast<int>(g.sThumbLY)) <= 256
            && std::abs(static_cast<int>(g.sThumbRX)) <= 256 && std::abs(static_cast<int>(g.sThumbRY)) <= 256, "XInput fixture state mismatch");
    XINPUT_CAPABILITIES capabilities{};
    require(XInputGetCapabilities(chosen, 0, &capabilities) == ERROR_SUCCESS && capabilities.Type == XINPUT_DEVTYPE_GAMEPAD,
            "XInput gamepad capabilities");
    XINPUT_VIBRATION vibration{0x1234, 0x5678};
    DWORD start = XInputSetState(chosen, &vibration); Sleep(200);
    vibration = {}; DWORD stop = XInputSetState(chosen, &vibration);
    std::cout << "{\"kind\":\"xinput-marker\",\"index\":" << chosen << ",\"subtype\":" << static_cast<unsigned>(capabilities.SubType)
              << ",\"packetNumber\":" << selected.dwPacketNumber << ",\"buttons\":" << g.wButtons
              << ",\"startStatus\":" << start << ",\"stopStatus\":" << stop
              << ",\"leftMotor\":4660,\"rightMotor\":22136,\"usbIdentityAssociationVerified\":false,\"outputDeliveryVerified\":false}\n";
    require(start == ERROR_SUCCESS && stop == ERROR_SUCCESS, "XInput rumble start/stop");
}

static GUID endpoint_container(IMMDevice *device)
{
    Com<IPropertyStore> properties; check(device->OpenPropertyStore(STGM_READ, properties.out()), "Open endpoint properties");
    PROPVARIANT value; PropVariantInit(&value);
    // PROPERTYKEY and DEVPROPKEY have the same GUID/pid representation.
    PROPERTYKEY key{DEVPKEY_Device_ContainerId.fmtid, DEVPKEY_Device_ContainerId.pid};
    HRESULT hr = properties->GetValue(key, &value);
    GUID container{};
    if (SUCCEEDED(hr) && value.vt == VT_CLSID && value.puuid) container = *value.puuid;
    PropVariantClear(&value); return container;
}
static void audio_inventory(IMMDeviceEnumerator *enumerator)
{
    Com<IMMDeviceCollection> devices; check(enumerator->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, devices.out()), "Enumerate audio endpoints");
    UINT count = 0; check(devices->GetCount(&count), "Audio endpoint count");
    for (UINT i = 0; i < count; ++i) {
        Com<IMMDevice> device; check(devices->Item(i, device.out()), "Audio endpoint item");
        LPWSTR id = nullptr; check(device->GetId(&id), "Audio endpoint ID");
        std::wstring identity(id); CoTaskMemFree(id);
        GUID container = endpoint_container(device.value);
        std::cout << "{\"kind\":\"audio-endpoint\",\"id\":" << json(identity)
                  << ",\"containerId\":" << json(guid_text(container))
                  << ",\"containerKnown\":" << (!IsEqualGUID(container, GUID_NULL) ? "true" : "false") << "}\n";
    }
}
static void audio_probe(IMMDeviceEnumerator *enumerator, const wchar_t *identity, const wchar_t *expected_container)
{
    GUID expected{}; check(CLSIDFromString(expected_container, &expected), "Expected audio container GUID");
    require(!IsEqualGUID(expected, GUID_NULL), "Audio container identity may not be missing");
    Com<IMMDevice> device; check(enumerator->GetDevice(identity, device.out()), "Get exact audio endpoint");
    GUID actual = endpoint_container(device.value);
    require(IsEqualGUID(expected, actual), "Audio endpoint container differs from verified virtual HID container");
    Com<IAudioClient> client;
    check(device->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr, reinterpret_cast<void **>(client.out())), "Activate exact audio endpoint");
    WAVEFORMATEXTENSIBLE format{};
    format.Format.wFormatTag = WAVE_FORMAT_EXTENSIBLE; format.Format.nChannels = 4;
    format.Format.nSamplesPerSec = 48000; format.Format.wBitsPerSample = 16;
    format.Format.nBlockAlign = 8; format.Format.nAvgBytesPerSec = 384000;
    format.Format.cbSize = sizeof(format) - sizeof(WAVEFORMATEX);
    // Use the SDK's static GUID initializer; this value requires no ksuser DLL.
    const GUID pcm_subformat{STATIC_KSDATAFORMAT_SUBTYPE_PCM};
    format.Samples.wValidBitsPerSample = 16; format.dwChannelMask = KSAUDIO_SPEAKER_QUAD; format.SubFormat = pcm_subformat;
    WAVEFORMATEX *closest = nullptr;
    HRESULT supported = client->IsFormatSupported(AUDCLNT_SHAREMODE_SHARED, &format.Format, &closest);
    if (closest) CoTaskMemFree(closest);
    require(supported == S_OK, "WASAPI must support exact S16LE 48kHz quad format");
    check(client->Initialize(AUDCLNT_SHAREMODE_SHARED, 0, 10000000, 0, &format.Format, nullptr), "Initialize quad WASAPI stream");
    Com<IAudioRenderClient> render;
    check(client->GetService(__uuidof(IAudioRenderClient), reinterpret_cast<void **>(render.out())), "WASAPI render service");
    UINT32 capacity = 0; check(client->GetBufferSize(&capacity), "WASAPI buffer size");
    require(capacity > 0, "Empty WASAPI buffer");
    UINT32 sent = 0; bool started = false;
    try {
        ULONGLONG deadline = GetTickCount64() + 8000;
        while (GetTickCount64() < deadline) {
            UINT32 padding = 0; check(client->GetCurrentPadding(&padding), "WASAPI padding");
            require(padding <= capacity, "WASAPI padding exceeds buffer");
            UINT32 count = std::min(capacity - padding, 48000u - sent);
            if (count) {
                BYTE *bytes = nullptr; check(render->GetBuffer(count, &bytes), "WASAPI GetBuffer");
                auto *samples = reinterpret_cast<int16_t *>(bytes);
                for (UINT32 i = 0; i < count; ++i) {
                    UINT32 frame = sent + i;
                    samples[4 * i] = static_cast<int16_t>(1000 + frame % 997);
                    samples[4 * i + 1] = static_cast<int16_t>(-2000 - static_cast<int>(frame % 991));
                    samples[4 * i + 2] = static_cast<int16_t>(4000 + frame % 983);
                    samples[4 * i + 3] = static_cast<int16_t>(-6000 - static_cast<int>(frame % 977));
                }
                check(render->ReleaseBuffer(count, 0), "WASAPI ReleaseBuffer"); sent += count;
            }
            if (!started) { check(client->Start(), "WASAPI Start"); started = true; }
            if (sent == 48000 && padding == 0 && count == 0) break;
            Sleep(5);
        }
        UINT32 padding = 0; check(client->GetCurrentPadding(&padding), "WASAPI final padding");
        require(sent == 48000 && padding == 0, "WASAPI stream did not drain before deadline");
        check(client->Stop(), "WASAPI Stop"); started = false;
    } catch (...) { if (started) client->Stop(); throw; }
    std::cout << "{\"kind\":\"wasapi-playback\",\"frames\":" << sent << ",\"channels\":4,\"sampleRate\":48000,\"bits\":16,\"channelMask\":"
              << format.dwChannelMask << ",\"bufferFrames\":" << capacity << ",\"containerId\":" << json(guid_text(actual))
              << ",\"pcmDeliveryVerified\":false}\n";
}

int wmain(int argc, wchar_t **argv)
{
    bool com = false;
    try {
        require(argc >= 3, "usage: wine_consumer.exe MODE TOKEN [arguments]");
        std::string version = validate_scope(argv[2]);
        std::cout << "{\"kind\":\"runtime\",\"wineVersion\":" << json(version)
                  << ",\"protonValidated\":false,\"physicalDeviceAccess\":false,\"gamesValidated\":false,\"physicalLatencyValidated\":false}\n";
        std::wstring mode = argv[1];
        if (mode == L"sony") {
            require(argc == 6, "sony TOKEN dualsense|edge MAC_HEX neutral|south");
            require(std::wstring(argv[3]) == L"dualsense" || std::wstring(argv[3]) == L"edge", "Unknown Sony persona");
            require(std::wstring(argv[5]) == L"neutral" || std::wstring(argv[5]) == L"south", "Unknown input state");
            sony_probe(0x054c, std::wstring(argv[3]) == L"edge" ? 0x0df2 : 0x0ce6, parse_mac(argv[4]), std::wstring(argv[5]) == L"south");
        } else if (mode == L"xinput") {
            require(argc == 4 && (std::wstring(argv[3]) == L"neutral" || std::wstring(argv[3]) == L"south"), "xinput TOKEN neutral|south");
            xinput_probe(std::wstring(argv[3]) == L"south");
        } else if (mode == L"audio-inventory" || mode == L"audio") {
            check(CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED), "COM initialization"); com = true;
            {
                Com<IMMDeviceEnumerator> enumerator;
                check(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator),
                                       reinterpret_cast<void **>(enumerator.out())), "MMDevice enumerator");
                if (mode == L"audio-inventory") { require(argc == 3, "audio-inventory TOKEN"); audio_inventory(enumerator.value); }
                else { require(argc == 5, "audio TOKEN EXACT_ENDPOINT_ID VERIFIED_HID_CONTAINER_GUID"); audio_probe(enumerator.value, argv[3], argv[4]); }
            }
        } else throw std::runtime_error("Unknown observer mode");
        if (com) CoUninitialize();
        std::cout << "{\"kind\":\"result\",\"result\":\"passed\",\"outputDeliveryVerified\":false,\"pcmDeliveryVerified\":false}\n";
        return 0;
    } catch (const std::exception &error) {
        if (com) CoUninitialize();
        std::cout << "{\"kind\":\"result\",\"result\":\"failed\",\"error\":" << json(std::string(error.what())) << "}\n";
        return 1;
    }
}
