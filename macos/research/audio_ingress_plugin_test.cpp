#include "AudioProtocol.hpp" // The same generated headers as the test driver.
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
    require(argc == 3);
    const UInt32 expectedPeriod = static_cast<UInt32>(std::stoul(argv[1]));
    require(expectedPeriod == 256 || expectedPeriod == 16384);
    const std::string recovery = argv[2];
    require(recovery == "observe" || recovery == "reject");
    const bool rejectResync = recovery == "reject";
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
    require((*driver)->StartIO(driver, kDevice, client.mClientID) == noErr);
    auto readPacket = [&] {
        auto readAddress = address(kRead);
        CFDataRef data = nullptr; UInt32 bytes = 0;
        require((*driver)->GetPropertyData(driver, kDevice, getpid(), &readAddress, 0, nullptr,
            sizeof(data), &bytes, &data) == noErr && data);
        AudioPacket packet{};
        require(CFDataGetLength(data) <= sizeof(packet));
        std::memcpy(&packet, CFDataGetBytePtr(data), CFDataGetLength(data)); CFRelease(data);
        return packet;
    };
    const auto before = readPacket();
    UInt64 recoveryClockSeed = 0;
    require((*driver)->GetZeroTimeStamp(driver, kDevice, client.mClientID, &sample, &ticks, &recoveryClockSeed) == noErr);
    for (unsigned i = 0; i < 96; ++i) audio_probe::encode(2, i, frames + i * 4);
    for (unsigned ordinal : {2u, 1u}) {
        cycle.mIOCycleCounter = ordinal;
        require((*driver)->DoIOOperation(driver, kDevice, kStream, client.mClientID,
            kAudioServerPlugInIOOperationWriteMix, 96, &cycle, frames, nullptr) == noErr);
    }
    for (unsigned i = 0; i < 96; ++i) audio_probe::encode(3, i, frames + i * 4);
    cycle.mIOCycleCounter = 2;
    require((*driver)->DoIOOperation(driver, kDevice, kStream, client.mClientID,
        kAudioServerPlugInIOOperationWriteMix, 96, &cycle, frames, nullptr) == noErr);
    require((*driver)->StopIO(driver, kDevice, client.mClientID) == noErr);
    const auto after = readPacket();
    require((*driver)->GetZeroTimeStamp(driver, kDevice, client.mClientID, &sample, &ticks, &seed) == noErr && seed == recoveryClockSeed);
    require(readTrace(getpid(), snapshot) == noErr && snapshot.nonIncreasing == 96 && snapshot.eventCount == 1);
    require(snapshot.generation == after.generation);
    if (rejectResync) {
        require(after.generation == before.generation + 1 && after.frames == 96);
        require(after.droppedFrames - before.droppedFrames == 192); // 96 rejected + 96 pending.
        require(snapshot.resyncEvents == 1 && snapshot.rejectedFrames == 96 && snapshot.advancedEpochs == 1);
        for (unsigned i = 0; i < 96; ++i) {
            audio_probe::Marker marker{};
            require(audio_probe::decode(after.samples + i * 4, marker) && marker.callback == 3 && marker.offset == i);
        }
    } else {
        require(after.generation == before.generation && after.frames == 288);
        require(after.droppedFrames == before.droppedFrames && snapshot.resyncEvents == 0);
    }
    claim = kCFBooleanFalse;
    require((*driver)->SetPropertyData(driver, kDevice, getpid(), &ownerAddress, 0, nullptr, sizeof(claim), &claim) == noErr);
    require(readTrace(getpid(), snapshot) != noErr);
    claim = kCFBooleanTrue;
    require((*driver)->SetPropertyData(driver, kDevice, getpid(), &ownerAddress, 0, nullptr, sizeof(claim), &claim) == noErr);
    require(readTrace(getpid(), snapshot) == noErr && snapshot.eventCount == 0 && snapshot.generation == 0);
    require(snapshot.events[0].current.cycle == 0); // No previous-owner bytes.
    std::cout << "Research ingress authentication, epoch isolation and period " << period << " passed\n";
}
