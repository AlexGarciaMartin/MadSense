#include "madsense_audio.h"
#include "AudioProtocol.hpp"
#include <CoreAudio/CoreAudio.h>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <new>

namespace {
struct Handle { AudioDeviceID device; };
AudioObjectPropertyAddress address(AudioObjectPropertySelector selector) {
    return {selector, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
}
OSStatus owner(AudioDeviceID device, bool claim) {
    auto addr = address(madsense::kOwner);
    CFBooleanRef value = claim ? kCFBooleanTrue : kCFBooleanFalse;
    return AudioObjectSetPropertyData(device, &addr, 0, nullptr, sizeof(value), &value);
}
int fail(char* error, uint32_t capacity, const char* message, OSStatus status) {
    if (error && capacity) std::snprintf(error, capacity, "%s (CoreAudio %d)", message, status);
    return status ? status : -1;
}
}

extern "C" int ms_audio_open(void** out, char* error, uint32_t capacity) {
    if (!out) return -1;
    *out = nullptr;
    if (error && capacity) *error = 0;
    AudioObjectPropertyAddress addr = address(kAudioHardwarePropertyTranslateUIDToDevice);
    CFStringRef uid = CFSTR("org.madsense.audio.dualsense.v1");
    AudioDeviceID device = kAudioObjectUnknown;
    UInt32 size = sizeof(device);
    OSStatus result = AudioObjectGetPropertyData(kAudioObjectSystemObject, &addr,
        sizeof(uid), &uid, &size, &device);
    if (result || device == kAudioObjectUnknown)
        return fail(error, capacity, "MadSenseAudio.driver is not installed or not loaded", result);
    result = owner(device, true);
    if (result) return fail(error, capacity, "Cannot acquire the MadSense audio endpoint", result);
    auto handle = new (std::nothrow) Handle{device};
    if (!handle) { owner(device, false); return fail(error, capacity, "Out of memory", -1); }
    *out = handle;
    return 0;
}

extern "C" int ms_audio_read(void* pointer, float* samples, uint32_t capacity,
                              uint32_t* frames, uint64_t* dropped, uint32_t* active) {
    if (!pointer || !samples || !frames || !dropped || !active || capacity < madsense::kReadFrames) return -1;
    *frames = 0; *dropped = 0; *active = 0;
    auto handle = static_cast<Handle*>(pointer);
    auto addr = address(madsense::kRead);
    CFDataRef data = nullptr;
    UInt32 size = sizeof(data);
    OSStatus result = AudioObjectGetPropertyData(handle->device, &addr, 0, nullptr, &size, &data);
    if (result) return result;
    if (!data) return -1;
    if (CFGetTypeID(data) != CFDataGetTypeID()) { CFRelease(data); return -1; }
    const auto length = CFDataGetLength(data);
    madsense::AudioPacket packet{};
    if (length < static_cast<CFIndex>(madsense::kPacketHeaderSize) || length > sizeof(packet)) {
        CFRelease(data); return -1;
    }
    std::memcpy(&packet, CFDataGetBytePtr(data), static_cast<size_t>(length));
    CFRelease(data);
    if (packet.version != madsense::kProtocolVersion || packet.frames > madsense::kReadFrames ||
        length != static_cast<CFIndex>(madsense::kPacketHeaderSize + packet.frames * madsense::kChannels * sizeof(float))) return -1;
    std::memcpy(samples, packet.samples, packet.frames * madsense::kChannels * sizeof(float));
    *frames = packet.frames; *dropped = packet.droppedFrames; *active = packet.activeClients;
    return 0;
}

extern "C" void ms_audio_close(void* pointer) {
    if (!pointer) return;
    auto handle = static_cast<Handle*>(pointer);
    owner(handle->device, false);
    delete handle;
}
