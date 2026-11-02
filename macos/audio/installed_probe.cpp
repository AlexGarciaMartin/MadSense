#include "madsense_audio.h"
#include "AudioProtocol.hpp"
#include <CoreAudio/CoreAudio.h>
#include <chrono>
#include <cmath>
#include <iostream>
#include <thread>

// Opt-in integration probe: requires the installed driver, but no DualSense.
// Sends a small four-channel test signal only to the MadSense virtual endpoint.
OSStatus output(AudioDeviceID, const AudioTimeStamp*, const AudioBufferList*, const AudioTimeStamp*,
                 AudioBufferList* out, const AudioTimeStamp*, void*) {
    for (UInt32 b = 0; b < out->mNumberBuffers; ++b) {
        auto& buffer = out->mBuffers[b];
        auto samples = static_cast<float*>(buffer.mData);
        if (!samples) continue;
        for (UInt32 i = 0; i < buffer.mDataByteSize / sizeof(float); ++i)
            samples[i] = static_cast<float>((i % madsense::kChannels) + 1) * 0.001f;
    }
    return noErr;
}
int main() {
    void* consumer = nullptr;
    char error[512]{};
    int result = ms_audio_open(&consumer, error, sizeof(error));
    if (result) { std::cerr << error << '\n'; return 1; }
    AudioObjectPropertyAddress addr{kAudioHardwarePropertyTranslateUIDToDevice,
        kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    CFStringRef uid = CFSTR("org.madsense.audio.dualsense.v1");
    AudioDeviceID device = 0; UInt32 size = sizeof(device);
    result = AudioObjectGetPropertyData(kAudioObjectSystemObject, &addr, sizeof(uid), &uid, &size, &device);
    AudioDeviceIOProcID proc = nullptr;
    if (!result) result = AudioDeviceCreateIOProcID(device, output, nullptr, &proc);
    if (!result) result = AudioDeviceStart(device, proc);
    bool received = false;
    for (int attempt = 0; !result && attempt < 200; ++attempt) {
        float samples[madsense::kReadFrames * madsense::kChannels];
        uint32_t frames = 0, active = 0; uint64_t dropped = 0;
        result = ms_audio_read(consumer, samples, madsense::kReadFrames, &frames, &dropped, &active);
        if (frames >= 1 && active >= 1 && std::abs(samples[0] - 0.001f) < 0.00001f &&
            std::abs(samples[3] - 0.004f) < 0.00001f) { received = true; break; }
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    if (proc) { AudioDeviceStop(device, proc); AudioDeviceDestroyIOProcID(device, proc); }
    ms_audio_close(consumer);
    if (result || !received) { std::cerr << "Installed HAL transfer failed: " << result << '\n'; return 1; }
    std::cout << "Installed HAL endpoint -> authenticated four-channel consumer: passed\n";
}
