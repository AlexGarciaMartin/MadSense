#include "AudioProtocol.hpp"
#include <CoreFoundation/CFPlugInCOM.h>
#include <unistd.h>
#include <cstdlib>
#include <cstring>
#include <iostream>

extern "C" void* MadSenseAudio_Create(CFAllocatorRef, CFUUIDRef);
void require(bool condition, const char* reason) {
    if (!condition) { std::cerr << reason << '\n'; std::exit(1); }
}
AudioObjectPropertyAddress address(AudioObjectPropertySelector selector,
                                  AudioObjectPropertyScope scope = kAudioObjectPropertyScopeGlobal) {
    return {selector, scope, kAudioObjectPropertyElementMain};
}
int main() {
    using namespace madsense;
    auto driver = static_cast<AudioServerPlugInDriverRef>(MadSenseAudio_Create(nullptr, kAudioServerPlugInTypeUUID));
    require(driver != nullptr, "factory");
    AudioServerPlugInHostInterface host{};
    require((*driver)->Initialize(driver, &host) == noErr, "initialize");
    UInt32 written = 0, size = 0;
    auto addr = address(kAudioPlugInPropertyBoxList);
    AudioObjectID id = 0;
    require((*driver)->GetPropertyData(driver, kPlugin, getpid(), &addr, 0, nullptr,
        sizeof(id), &written, &id) == noErr && id == kBox, "box publication");
    addr = address(kAudioBoxPropertyAcquired);
    UInt32 acquired = 0;
    require((*driver)->GetPropertyData(driver, kBox, getpid(), &addr, 0, nullptr,
        sizeof(acquired), &written, &acquired) == noErr && acquired == 1, "box acquired");
    addr = address(kAudioObjectPropertyOwnedObjects);
    AudioClassID control = kAudioControlClassID;
    require((*driver)->GetPropertyDataSize(driver, kDevice, getpid(), &addr,
        sizeof(control), &control, &size) == noErr && size == 0, "owned-object qualifier filtering");
    addr = address(kAudioDevicePropertyStreams, kAudioObjectPropertyScopeInput);
    require((*driver)->GetPropertyDataSize(driver, kDevice, getpid(), &addr, 0, nullptr,
        &size) == noErr && size == 0, "no microphone claimed");
    addr = address(kAudioStreamPropertyPhysicalFormat);
    AudioStreamBasicDescription format{};
    require((*driver)->GetPropertyData(driver, kStream, getpid(), &addr, 0, nullptr,
        sizeof(format), &written, &format) == noErr, "stream format");
    require(format.mChannelsPerFrame == 4 && format.mSampleRate == 48000 && format.mBitsPerChannel == 32 &&
        format.mBytesPerFrame == 16 && (format.mFormatFlags & kAudioFormatFlagIsFloat), "four-channel float format");
    require((*driver)->StartIO(driver, kDevice, 9999) != noErr, "unregistered IO denied");
    addr = address(kOwner);
    CFBooleanRef claim = kCFBooleanTrue;
    require((*driver)->SetPropertyData(driver, kDevice, 0, &addr, 0, nullptr,
        sizeof(claim), &claim) != noErr, "anonymous owner denied");
    require((*driver)->SetPropertyData(driver, kDevice, getpid(), &addr, 0, nullptr,
        sizeof(claim), &claim) == noErr, "owner claim");
    AudioServerPlugInClientInfo client{42, getpid(), true, CFSTR("org.madsense.audio.test")};
    require((*driver)->AddDeviceClient(driver, kDevice, &client) == noErr, "register client");
    require((*driver)->StartIO(driver, kDevice, client.mClientID) == noErr, "start IO");
    float frames[64 * kChannels];
    AudioServerPlugInIOCycleInfo cycle{};
    for (uint32_t i = 0; i < 64 * kChannels; ++i) frames[i] = static_cast<float>(i) / 512;
    require((*driver)->DoIOOperation(driver, kDevice, kStream, client.mClientID,
        kAudioServerPlugInIOOperationWriteMix, 64, &cycle, frames, nullptr) == noErr, "write mix");
    addr = address(kRead);
    CFDataRef data = nullptr;
    require((*driver)->GetPropertyData(driver, kDevice, getpid() + 100000, &addr, 0, nullptr,
        sizeof(data), &written, &data) != noErr && !data, "foreign reader denied");
    require((*driver)->GetPropertyData(driver, kDevice, getpid(), &addr, 0, nullptr,
        sizeof(data), &written, &data) == noErr && data, "owner read");
    AudioPacket packet{};
    std::memcpy(&packet, CFDataGetBytePtr(data), CFDataGetLength(data));
    CFRelease(data);
    require(packet.version == 1 && packet.frames == 64 && packet.activeClients == 1 &&
        packet.samples[17] == frames[17], "audio payload");
    Float64 sample = 0; UInt64 ticks = 0, seed = 0;
    require((*driver)->GetZeroTimeStamp(driver, kDevice, client.mClientID, &sample, &ticks, &seed) == noErr &&
        ticks > 0 && seed > 0, "device clock");
    require((*driver)->StopIO(driver, kDevice, client.mClientID) == noErr, "stop IO");
    require((*driver)->RemoveDeviceClient(driver, kDevice, &client) == noErr, "remove client");
    addr = address(kOwner); claim = kCFBooleanFalse;
    require((*driver)->SetPropertyData(driver, kDevice, getpid(), &addr, 0, nullptr,
        sizeof(claim), &claim) == noErr, "release owner");
    addr = address(kRead); data = nullptr;
    require((*driver)->GetPropertyData(driver, kDevice, getpid(), &addr, 0, nullptr,
        sizeof(data), &written, &data) != noErr, "reads denied after release");
    std::cout << "HAL plugin contract: passed (in-process host; installed driver not tested)\n";
}
