#pragma once
#include "AudioRing.hpp"
#include <CoreAudio/AudioServerPlugIn.h>

namespace madsense {
constexpr AudioObjectID kPlugin = kAudioObjectPlugInObject;
constexpr AudioObjectID kBox = 2;
constexpr AudioObjectID kDevice = 3;
constexpr AudioObjectID kStream = 4;
constexpr AudioObjectPropertySelector kOwner = 'msow';
constexpr AudioObjectPropertySelector kRead = 'msrd';
constexpr uint32_t kProtocolVersion = 1;
constexpr const char* kDeviceUID = "org.madsense.audio.dualsense.v1";
// Custom properties use CFData, a supported CFPropertyList type. No pointers or
// shared-memory names cross the HAL host boundary. Both endpoints are local ABI.
struct AudioPacket {
    uint32_t version;
    uint32_t frames;
    uint64_t droppedFrames;
    uint32_t activeClients;
    uint32_t reserved;
    float samples[kReadFrames * kChannels];
};
constexpr size_t kPacketHeaderSize = offsetof(AudioPacket, samples);
}
