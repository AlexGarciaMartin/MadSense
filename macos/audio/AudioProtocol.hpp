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
constexpr uint32_t kProtocolVersion = 2;
// AudioServerPlugIn.h requires at least 10923 frames between zero timestamps.
// Match Apple's NullAudio clock period. This is independent of PCM queue size
// and does not request a client IO buffer size or add that much audio latency.
constexpr uint32_t kTimestampPeriodFrames = 16384;
constexpr const char* kDeviceUID = "org.madsense.audio.dualsense.v1";
// Custom properties use CFData, a supported CFPropertyList type. No pointers or
// shared-memory names cross the HAL host boundary. Both endpoints are local ABI.
struct AudioPacket {
    uint32_t version;
    uint32_t frames;
    uint64_t droppedFrames;
    uint64_t generation;
    uint32_t activeClients;
    uint32_t reserved;
    float samples[kReadFrames * kChannels];
};
constexpr size_t kPacketHeaderSize = offsetof(AudioPacket, samples);
}
