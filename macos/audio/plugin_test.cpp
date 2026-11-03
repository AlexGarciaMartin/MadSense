#include "AudioProtocol.hpp"
#include <CoreFoundation/CFPlugInCOM.h>
#include <unistd.h>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <chrono>
#include <thread>
#include <limits>
#include <sys/wait.h>

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
    require((*driver)->SetPropertyData(driver, kDevice, std::numeric_limits<pid_t>::max(), &addr, 0, nullptr,
        sizeof(claim), &claim) != noErr, "nonexistent owner denied");
    require((*driver)->SetPropertyData(driver, kDevice, getpid(), &addr, 0, nullptr,
        sizeof(claim), &claim) == noErr, "owner claim");
    int childPipe[2];
    require(pipe(childPipe) == 0, "child pipe");
    pid_t child = fork();
    require(child >= 0, "child process");
    if (child == 0) {
        close(childPipe[1]); char value;
        (void)read(childPipe[0], &value, 1);
        _exit(0);
    }
    close(childPipe[0]);
    require((*driver)->SetPropertyData(driver, kDevice, child, &addr, 0, nullptr,
        sizeof(claim), &claim) != noErr, "same-user foreign process cannot replace live owner");
    AudioServerPlugInClientInfo client{42, getpid(), true, CFSTR("org.madsense.audio.test")};
    require((*driver)->AddDeviceClient(driver, kDevice, &client) == noErr, "register client");
    require((*driver)->StartIO(driver, kDevice, client.mClientID) == noErr, "start IO");
    // launchd gives a real, live process belonging to another UID. Reading its
    // public process snapshot must work; starting its stream under our owner
    // must still fail. No privileged test helper or synthetic UID is needed.
    AudioServerPlugInClientInfo foreign{43, 1, true, CFSTR("org.madsense.audio.foreign-test")};
    if (geteuid() != 0) {
        require((*driver)->AddDeviceClient(driver, kDevice, &foreign) == noErr, "register cross-user process snapshot");
        require((*driver)->StartIO(driver, kDevice, foreign.mClientID) != noErr, "cross-user audio mixing denied");
        require((*driver)->RemoveDeviceClient(driver, kDevice, &foreign) == noErr, "remove cross-user client");
    }
    float frames[64 * kChannels];
    AudioServerPlugInIOCycleInfo cycle{};
    for (uint32_t i = 0; i < 64 * kChannels; ++i) frames[i] = static_cast<float>(i) / 512;
    require((*driver)->DoIOOperation(driver, kDevice, kStream, client.mClientID,
        kAudioServerPlugInIOOperationWriteMix, 64, &cycle, frames, nullptr) == noErr, "write mix");
    addr = address(kRead);
    CFDataRef data = nullptr;
    require((*driver)->GetPropertyData(driver, kDevice, getpid() + 100000, &addr, 0, nullptr,
        sizeof(data), &written, &data) != noErr && !data, "foreign reader denied");
    require((*driver)->GetPropertyData(driver, kDevice, child, &addr, 0, nullptr,
        sizeof(data), &written, &data) != noErr && !data, "same-user live foreign reader denied");
    require((*driver)->GetPropertyData(driver, kDevice, getpid(), &addr, 0, nullptr,
        sizeof(data), &written, &data) == noErr && data, "owner read");
    AudioPacket packet{};
    std::memcpy(&packet, CFDataGetBytePtr(data), CFDataGetLength(data));
    CFRelease(data);
    require(packet.version == kProtocolVersion && packet.frames == 64 && packet.activeClients == 1 && packet.generation > 0 &&
        packet.samples[17] == frames[17], "audio payload");
    uint64_t firstGeneration = packet.generation;
    // Queue an unread tail, stop/start before the next poll, then deliver new
    // audio. Active clients stays 1 across reads, so only the generation reveals
    // that managed partial blocks must be flushed as well as this native ring.
    require((*driver)->DoIOOperation(driver, kDevice, kStream, client.mClientID,
        kAudioServerPlugInIOOperationWriteMix, 64, &cycle, frames, nullptr) == noErr, "queue previous stream tail");
    require((*driver)->StopIO(driver, kDevice, client.mClientID) == noErr, "rapid stop");
    require((*driver)->StartIO(driver, kDevice, client.mClientID) == noErr, "rapid restart");
    for (float& value : frames) value = 0.75f;
    require((*driver)->DoIOOperation(driver, kDevice, kStream, client.mClientID,
        kAudioServerPlugInIOOperationWriteMix, 64, &cycle, frames, nullptr) == noErr, "new stream write");
    data = nullptr;
    require((*driver)->GetPropertyData(driver, kDevice, getpid(), &addr, 0, nullptr,
        sizeof(data), &written, &data) == noErr && data, "new stream read");
    packet = {};
    std::memcpy(&packet, CFDataGetBytePtr(data), CFDataGetLength(data));
    CFRelease(data);
    require(packet.frames == 64 && packet.activeClients == 1 && packet.generation != firstGeneration,
        "rapid restart must change generation and discard previous frames");
    for (uint32_t i = 0; i < packet.frames * kChannels; ++i)
        require(packet.samples[i] == 0.75f, "old PCM crossed a stream generation");
    uint64_t restartedGeneration = packet.generation;
    require((*driver)->DoIOOperation(driver, kDevice, kStream, client.mClientID,
        kAudioServerPlugInIOOperationWriteMix, 64, &cycle, frames, nullptr) == noErr, "queue before lease expiry");
    std::this_thread::sleep_for(std::chrono::milliseconds(1100));
    if (geteuid() != 0) {
        auto ownerAddress = address(kOwner);
        require((*driver)->SetPropertyData(driver, kDevice, 1, &ownerAddress, 0, nullptr,
            sizeof(claim), &claim) != noErr, "expired lease cannot transfer active mixed audio to another user");
    }
    data = nullptr;
    require((*driver)->GetPropertyData(driver, kDevice, getpid(), &addr, 0, nullptr,
        sizeof(data), &written, &data) == noErr && data, "resume expired lease");
    packet = {};
    std::memcpy(&packet, CFDataGetBytePtr(data), CFDataGetLength(data));
    CFRelease(data);
    require(packet.frames == 0 && packet.activeClients == 1 && packet.generation != restartedGeneration,
        "lease expiry must discard queued PCM and change generation");
    require((*driver)->DoIOOperation(driver, kDevice, kStream, client.mClientID,
        kAudioServerPlugInIOOperationWriteMix, 64, &cycle, frames, nullptr) == noErr, "write after lease renewal");
    uint64_t resumedGeneration = packet.generation;
    data = nullptr;
    require((*driver)->GetPropertyData(driver, kDevice, getpid(), &addr, 0, nullptr,
        sizeof(data), &written, &data) == noErr && data, "read after lease renewal");
    packet = {};
    std::memcpy(&packet, CFDataGetBytePtr(data), CFDataGetLength(data));
    CFRelease(data);
    require(packet.frames == 64 && packet.generation == resumedGeneration && packet.samples[0] == 0.75f,
        "lease renewal must recover without repeated generation changes");
    Float64 sample = 0; UInt64 ticks = 0, seed = 0;
    require((*driver)->GetZeroTimeStamp(driver, kDevice, client.mClientID, &sample, &ticks, &seed) == noErr &&
        ticks > 0 && seed == packet.generation, "device clock generation");
    require((*driver)->StopIO(driver, kDevice, client.mClientID) == noErr, "stop IO");
    require((*driver)->RemoveDeviceClient(driver, kDevice, &client) == noErr, "remove client");
    addr = address(kOwner); claim = kCFBooleanFalse;
    require((*driver)->SetPropertyData(driver, kDevice, getpid(), &addr, 0, nullptr,
        sizeof(claim), &claim) == noErr, "release owner");
    addr = address(kRead); data = nullptr;
    require((*driver)->GetPropertyData(driver, kDevice, getpid(), &addr, 0, nullptr,
        sizeof(data), &written, &data) != noErr, "reads denied after release");
    close(childPipe[1]);
    require(waitpid(child, nullptr, 0) == child, "child terminated");
    addr = address(kOwner); claim = kCFBooleanTrue;
    require((*driver)->SetPropertyData(driver, kDevice, child, &addr, 0, nullptr,
        sizeof(claim), &claim) != noErr, "exited owner denied");
    std::cout << "HAL plugin contract: passed (in-process host; installed driver not tested)\n";
}
