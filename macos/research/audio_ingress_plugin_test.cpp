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
    // Keep all 384 frames below the backlog threshold: trimming must not hide
    // a mistakenly accepted second reset. Both reset calls have an old prefix,
    // followed by fresh markers, and carry distinct host/sample timestamps.
    unsigned operation = 0;
    for (unsigned ordinal : {2u, 1u, 1u, 2u}) {
        for (unsigned i = 0; i < 96; ++i) {
            const bool oldPrefix = (operation == 1 || operation == 2) && i < 48;
            audio_probe::encode(2 + operation - (oldPrefix ? 1 : 0),
                oldPrefix ? 48 + i : i, frames + i * 4);
        }
        cycle.mIOCycleCounter = ordinal;
        cycle.mCurrentTime.mSampleTime = 1000 + operation * 96;
        cycle.mCurrentTime.mHostTime = 10000 + operation * 100;
        cycle.mCurrentTime.mFlags = kAudioTimeStampSampleTimeValid | kAudioTimeStampHostTimeValid;
        cycle.mOutputTime.mSampleTime = 2000 + operation * 96;
        cycle.mOutputTime.mHostTime = 20000 + operation * 100;
        cycle.mOutputTime.mFlags = kAudioTimeStampSampleTimeValid | kAudioTimeStampHostTimeValid;
        require((*driver)->DoIOOperation(driver, kDevice, kStream, client.mClientID,
            kAudioServerPlugInIOOperationWriteMix, 96, &cycle, frames, nullptr) == noErr);
        ++operation;
    }
    require((*driver)->StopIO(driver, kDevice, client.mClientID) == noErr);
    const auto after = readPacket();
    require((*driver)->GetZeroTimeStamp(driver, kDevice, client.mClientID, &sample, &ticks, &seed) == noErr && seed == recoveryClockSeed);
    require(readTrace(getpid(), snapshot) == noErr && snapshot.nonIncreasing == 96 && snapshot.eventCount == 2);
    require(snapshot.generation == after.generation);
    require(snapshot.calls == 4 && snapshot.frames == 384 && snapshot.hostCycleResets == 2);
    for (unsigned i = 0; i < 2; ++i) {
        const auto& event = snapshot.events[i];
        require(event.current.cycle == 1 && event.nonIncreasing == 48);
        require(event.previous.cycle == (i == 0 ? 2 : 1));
        require(event.current.current.host == 10100 + i * 100);
        require(event.current.output.sample == 2096 + i * 96 && event.current.output.host == 20100 + i * 100);
        require(event.markers[0].index == 0 && event.markers[0].highest.callback == 2 + i);
        require(event.markers[0].current.callback == 2 + i && event.markers[0].current.offset == 48);
        require(event.lastBad.index == 47);
    }
    if (rejectResync) {
        require(after.generation == before.generation + 2 && after.frames == 96);
        require(after.droppedFrames - before.droppedFrames == 288); // 192 rejected + 96 pending.
        require(snapshot.resyncEvents == 2 && snapshot.rejectedFrames == 192 && snapshot.advancedEpochs == 2);
        for (unsigned i = 0; i < 96; ++i) {
            audio_probe::Marker marker{};
            require(audio_probe::decode(after.samples + i * 4, marker) && marker.callback == 5 && marker.offset == i);
        }
    } else {
        require(after.generation == before.generation && after.frames == 384);
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
