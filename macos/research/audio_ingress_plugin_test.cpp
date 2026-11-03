#include "../audio/AudioProtocol.hpp"
#include "AudioIngressTrace.hpp"
#include <unistd.h>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <chrono>
#include <thread>

extern "C" void* MadSenseAudio_Create(CFAllocatorRef, CFUUIDRef);
namespace {
void require(bool condition) { if (!condition) std::abort(); }
AudioObjectPropertyAddress address(UInt32 selector) {
    return {selector, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
}
}
int main(int argc, char** argv) {
    using namespace madsense;
    require(argc == 2);
    const UInt32 expectedPeriod = static_cast<UInt32>(std::stoul(argv[1]));
    require(expectedPeriod == 256 || expectedPeriod == 16384);
    auto driver = static_cast<AudioServerPlugInDriverRef>(MadSenseAudio_Create(nullptr, kAudioServerPlugInTypeUUID));
    require(driver != nullptr);
    AudioServerPlugInHostInterface host{};
    require((*driver)->Initialize(driver, &host) == noErr);
    auto ownerAddress = address(kOwner), traceAddress = address(audio_probe::kIngressProperty);
    CFBooleanRef claim = kCFBooleanTrue;
    require((*driver)->SetPropertyData(driver, kDevice, getpid(), &ownerAddress, 0, nullptr, sizeof(claim), &claim) == noErr);
    auto readTrace = [&](pid_t pid, audio_probe::IngressSnapshot& snapshot) {
        CFDataRef data = nullptr; UInt32 written = 0;
        OSStatus result = (*driver)->GetPropertyData(driver, kDevice, pid, &traceAddress, 0, nullptr, sizeof(data), &written, &data);
        if (!result) {
            require(data && written == sizeof(data) && CFDataGetLength(data) == sizeof(snapshot));
            std::memcpy(&snapshot, CFDataGetBytePtr(data), sizeof(snapshot)); CFRelease(data);
        } else require(data == nullptr);
        return result;
    };
    audio_probe::IngressSnapshot snapshot;
    require(readTrace(0, snapshot) != noErr); // Research data has no anonymous path.
    require(readTrace(1, snapshot) != noErr); // Live foreign process is not owner.
    AudioServerPlugInClientInfo client{42, getpid(), true, CFSTR("org.madsense.audio.ingress-test")};
    require((*driver)->AddDeviceClient(driver, kDevice, &client) == noErr);
    require((*driver)->StartIO(driver, kDevice, client.mClientID) == noErr);
    auto periodAddress = address(kAudioDevicePropertyZeroTimeStampPeriod);
    UInt32 period = 0, written = 0;
    require((*driver)->GetPropertyData(driver, kDevice, getpid(), &periodAddress, 0, nullptr,
        sizeof(period), &written, &period) == noErr && period == expectedPeriod);
    std::this_thread::sleep_for(std::chrono::milliseconds(8));
    Float64 sample = 0; UInt64 ticks = 0, seed = 0;
    require((*driver)->GetZeroTimeStamp(driver, kDevice, client.mClientID, &sample, &ticks, &seed) == noErr);
    require(ticks > 0 && seed > 0 && std::fmod(sample, period) == 0);
    require(readTrace(getpid(), snapshot) != noErr); // Never snapshot during IO.
    float frames[96 * 4];
    for (unsigned i = 0; i < 96; ++i) audio_probe::encode(1, i, frames + i * 4);
    AudioServerPlugInIOCycleInfo cycle{};
    cycle.mIOCycleCounter = 9; cycle.mOutputTime.mSampleTime = 123;
    for (unsigned i = 0; i < 2; ++i)
        require((*driver)->DoIOOperation(driver, kDevice, kStream, client.mClientID,
            kAudioServerPlugInIOOperationWriteMix, 96, &cycle, frames, nullptr) == noErr);
    require((*driver)->StopIO(driver, kDevice, client.mClientID) == noErr);
    require(readTrace(getpid(), snapshot) == noErr);
    require(snapshot.nonIncreasing == 96 && snapshot.events[0].current.output.sample == 123 && snapshot.events[0].current.client == 42);
    claim = kCFBooleanFalse;
    require((*driver)->SetPropertyData(driver, kDevice, getpid(), &ownerAddress, 0, nullptr, sizeof(claim), &claim) == noErr);
    require(readTrace(getpid(), snapshot) != noErr);
    claim = kCFBooleanTrue;
    require((*driver)->SetPropertyData(driver, kDevice, getpid(), &ownerAddress, 0, nullptr, sizeof(claim), &claim) == noErr);
    require(readTrace(getpid(), snapshot) == noErr && snapshot.eventCount == 0 && snapshot.generation == 0);
    require(snapshot.events[0].current.cycle == 0); // No previous-owner bytes.
    std::cout << "Research ingress authentication, epoch isolation and period " << period << " passed\n";
}
