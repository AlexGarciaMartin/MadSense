#include "madsense_audio.h"
#include "AudioProtocol.hpp"
#include <CoreAudio/CoreAudio.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstring>
#include <iostream>
#include <poll.h>
#include <pthread.h>
#include <pthread/qos.h>
#include <spawn.h>
#include <stdexcept>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

extern char** environ;
using namespace std::chrono_literals;

namespace {
void require(bool success, const char* message) {
    if (!success) throw std::runtime_error(message);
}
void check(OSStatus status, const char* message) {
    if (status) throw std::runtime_error(std::string(message) + ": " + std::to_string(status));
}
AudioDeviceID findDevice() {
    AudioObjectPropertyAddress addr{kAudioHardwarePropertyTranslateUIDToDevice,
        kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    CFStringRef uid = CFSTR("org.madsense.audio.dualsense.v1");
    AudioDeviceID device = kAudioObjectUnknown;
    UInt32 size = sizeof(device);
    check(AudioObjectGetPropertyData(kAudioObjectSystemObject, &addr, sizeof(uid), &uid, &size, &device), "Find endpoint");
    require(size == sizeof(device), "Invalid endpoint discovery response");
    return device;
}
struct Consumer {
    void* handle = nullptr;
    ~Consumer() { close(); }
    void close() { if (handle) ms_audio_close(handle); handle = nullptr; }
    void open() {
        char error[512]{};
        int result = ms_audio_open(&handle, error, sizeof(error));
        if (result) throw std::runtime_error(error);
    }
};
struct AudioScheduling {
    pthread_override_t handle = nullptr;
    AudioScheduling() {
        // Match the managed capture/forwarder scoped QoS hint. This does not
        // request a privileged real-time policy or change the requested QoS.
        handle = pthread_override_qos_class_start_np(pthread_self(), QOS_CLASS_USER_INTERACTIVE, 0);
        require(handle != nullptr, "Could not apply audio reader QoS override");
    }
    ~AudioScheduling() {
        if (handle) {
            int result = pthread_override_qos_class_end_np(handle);
            if (result) std::cerr << "Audio reader QoS cleanup failed: " << result << '\n';
        }
    }
    AudioScheduling(const AudioScheduling&) = delete;
    AudioScheduling& operator=(const AudioScheduling&) = delete;
};
struct Signal {
    std::atomic<int> phase{0};
    std::atomic<bool> wrongLayout{false};
};
float level(int phase, uint32_t channel) {
    return static_cast<float>(phase) * 0.01f + static_cast<float>(channel + 1) * 0.001f;
}
// The callback only fills the dedicated virtual endpoint. It does not touch the
// system default output, physical speakers, a controller, or a microphone.
OSStatus output(AudioDeviceID, const AudioTimeStamp*, const AudioBufferList*, const AudioTimeStamp*,
                AudioBufferList* out, const AudioTimeStamp*, void* context) {
    auto& signal = *static_cast<Signal*>(context);
    int phase = signal.phase.load(std::memory_order_relaxed);
    for (UInt32 b = 0; b < out->mNumberBuffers; ++b) {
        auto& buffer = out->mBuffers[b];
        if (buffer.mNumberChannels != madsense::kChannels ||
            buffer.mDataByteSize % (madsense::kChannels * sizeof(float))) {
            signal.wrongLayout.store(true, std::memory_order_relaxed);
            if (buffer.mData) std::memset(buffer.mData, 0, buffer.mDataByteSize);
            continue;
        }
        auto samples = static_cast<float*>(buffer.mData);
        if (!samples) continue;
        for (UInt32 i = 0; i < buffer.mDataByteSize / sizeof(float); ++i)
            samples[i] = level(phase, i % madsense::kChannels);
    }
    return noErr;
}
struct Stream {
    AudioDeviceID device = 0;
    AudioDeviceIOProcID proc = nullptr;
    bool started = false;
    ~Stream() {
        if (started) AudioDeviceStop(device, proc);
        if (proc) AudioDeviceDestroyIOProcID(device, proc);
    }
    void create(AudioDeviceID found, Signal& signal) {
        device = found;
        check(AudioDeviceCreateIOProcID(device, output, &signal, &proc), "Create IO proc");
    }
    void start() { check(AudioDeviceStart(device, proc), "Start endpoint"); started = true; }
    void stop() { check(AudioDeviceStop(device, proc), "Stop endpoint"); started = false; }
};

uint64_t receive(Consumer& consumer, Signal& signal, int phase, uint64_t previousGeneration = 0,
                 uint64_t* droppedAtEnd = nullptr, std::chrono::milliseconds idleWait = 1ms) {
    uint64_t validated = 0, silent = 0, lastDropped = 0, generation = 0;
    auto deadline = std::chrono::steady_clock::now() + 3s;
    while (std::chrono::steady_clock::now() < deadline) {
        float samples[madsense::kReadFrames * madsense::kChannels];
        uint32_t frames = 0, active = 0;
        check(ms_audio_read(consumer.handle, samples, madsense::kReadFrames,
            &frames, &lastDropped, &active, &generation), "Read endpoint");
        require(!signal.wrongLayout.load(std::memory_order_relaxed), "HAL callback did not provide interleaved four-channel float PCM");
        require(generation > 0 && frames <= madsense::kReadFrames, "Invalid packet metadata");
        for (uint32_t frame = 0; frame < frames; ++frame) {
            bool allSilent = true;
            for (uint32_t channel = 0; channel < madsense::kChannels; ++channel)
                allSilent &= samples[frame * madsense::kChannels + channel] == 0;
            // HAL may pre-roll with silence. Count it explicitly; never count
            // silence as successful channel transfer or hide a channel swap.
            if (allSilent) { ++silent; continue; }
            require(active >= 1, "PCM arrived without an active client");
            require(generation != previousGeneration, "Stream/owner restart did not advance generation");
            for (uint32_t channel = 0; channel < madsense::kChannels; ++channel) {
                float sample = samples[frame * madsense::kChannels + channel];
                require(std::isfinite(sample) && std::abs(sample - level(phase, channel)) < 0.00001f,
                    "Wrong channel value or stale PCM crossed a stream/owner generation");
            }
            ++validated;
        }
        if (validated >= 2048) {
            std::cout << "phase=" << phase << " verified_frames=" << validated
                      << " silent_frames=" << silent << " dropped=" << lastDropped
                      << " generation=" << generation << '\n';
            if (droppedAtEnd) *droppedAtEnd = lastDropped;
            return generation;
        }
        if (frames < madsense::kReadFrames) std::this_thread::sleep_for(idleWait);
    }
    throw std::runtime_error("Timed out validating complete four-channel PCM frames");
}

struct TransferPosition { uint64_t dropped; uint64_t generation; };
TransferPosition measureWarmTransfer(Consumer& consumer, Signal& signal, uint64_t baselineDropped,
                                     uint64_t previousGeneration, const char* policy,
                                     std::chrono::milliseconds idleWait) {
    uint64_t received = 0, validated = 0, silent = 0, lastDropped = baselineDropped;
    uint64_t calls = 0, generationChanges = 0, emptyReads = 0, fullReads = 0, idleWaits = 0;
    uint64_t firstReadDropDelta = 0;
    std::chrono::nanoseconds longestRead{0}, totalRead{0}, longestIdle{0}, totalIdle{0}, longestReadGap{0};
    auto started = std::chrono::steady_clock::now();
    auto previousReadEnded = started;
    auto deadline = started + 3s;
    while (std::chrono::steady_clock::now() < deadline) {
        float samples[madsense::kReadFrames * madsense::kChannels];
        uint32_t frames = 0, active = 0;
        uint64_t generation = 0;
        auto readStarted = std::chrono::steady_clock::now();
        longestReadGap = std::max(longestReadGap,
            std::chrono::duration_cast<std::chrono::nanoseconds>(readStarted - previousReadEnded));
        int result = ms_audio_read(consumer.handle, samples, madsense::kReadFrames,
            &frames, &lastDropped, &active, &generation);
        auto readEnded = std::chrono::steady_clock::now();
        auto readDuration = std::chrono::duration_cast<std::chrono::nanoseconds>(readEnded - readStarted);
        longestRead = std::max(longestRead, readDuration);
        totalRead += readDuration;
        previousReadEnded = readEnded;
        ++calls;
        check(result, "Warm transfer read");
        require(lastDropped >= baselineDropped, "Dropped frame counter moved backwards");
        if (calls == 1) firstReadDropDelta = lastDropped - baselineDropped;
        require(!signal.wrongLayout.load(std::memory_order_relaxed), "Wrong HAL layout during warm transfer");
        require(generation > 0 && frames <= madsense::kReadFrames, "Invalid warm transfer metadata");
        if (frames == 0) ++emptyReads;
        if (frames == madsense::kReadFrames) ++fullReads;
        if (generation != previousGeneration) { ++generationChanges; previousGeneration = generation; }
        received += frames;
        for (uint32_t frame = 0; frame < frames; ++frame) {
            bool allSilent = true;
            for (uint32_t channel = 0; channel < madsense::kChannels; ++channel)
                allSilent &= samples[frame * madsense::kChannels + channel] == 0;
            if (allSilent) { ++silent; continue; }
            require(active >= 1, "Warm PCM arrived without an active client");
            for (uint32_t channel = 0; channel < madsense::kChannels; ++channel) {
                float sample = samples[frame * madsense::kChannels + channel];
                require(std::isfinite(sample) && std::abs(sample - level(0, channel)) < 0.00001f,
                    "Corrupt channel value during warm transfer");
            }
            ++validated;
        }
        // Compare the former default-QoS/five-millisecond policy with the
        // backend's scoped audio-QoS/one-millisecond blocking idle wait. Measure
        // actual sleep and RPC time separately; neither is a deadline guarantee.
        if (frames < madsense::kReadFrames) {
            auto idleStarted = std::chrono::steady_clock::now();
            std::this_thread::sleep_for(idleWait);
            auto idleDuration = std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::steady_clock::now() - idleStarted);
            totalIdle += idleDuration;
            longestIdle = std::max(longestIdle, idleDuration);
            ++idleWaits;
        }
    }
    double elapsed = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
    require(lastDropped >= baselineDropped, "Dropped frame counter moved backwards");
    std::cout << "warm_measurement_not_latency_guarantee"
              << " policy=" << policy << " requested_idle_ms=" << idleWait.count()
              << " elapsed_ms=" << elapsed << " read_calls=" << calls
              << " empty_reads=" << emptyReads << " full_reads=" << fullReads
              << " received_frames=" << received << " received_samples=" << received * madsense::kChannels
              << " verified_frames=" << validated << " silent_frames=" << silent
              << " dropped_delta=" << lastDropped - baselineDropped
              << " first_read_dropped_delta=" << firstReadDropDelta
              << " max_read_rpc_ms=" << std::chrono::duration<double, std::milli>(longestRead).count()
              << " total_read_rpc_ms=" << std::chrono::duration<double, std::milli>(totalRead).count()
              << " idle_waits=" << idleWaits
              << " total_idle_ms=" << std::chrono::duration<double, std::milli>(totalIdle).count()
              << " max_idle_ms=" << std::chrono::duration<double, std::milli>(longestIdle).count()
              << " max_inter_read_gap_ms=" << std::chrono::duration<double, std::milli>(longestReadGap).count()
              << " generation_changes=" << generationChanges << '\n';
    require(validated > 0, "No four-channel signal received during warm transfer measurement");
    return {lastDropped, previousGeneration};
}

int holdOwner() {
    AudioScheduling scheduling;
    Consumer consumer;
    consumer.open();
    std::cout << "OWNER_READY\n" << std::flush;
    // Parent intentionally kills this process without ms_audio_close. A limit
    // also prevents an abandoned test child from lingering indefinitely.
    auto deadline = std::chrono::steady_clock::now() + 15s;
    while (std::chrono::steady_clock::now() < deadline) {
        float samples[madsense::kReadFrames * madsense::kChannels];
        uint32_t frames = 0, active = 0;
        uint64_t dropped = 0, generation = 0;
        check(ms_audio_read(consumer.handle, samples, madsense::kReadFrames,
            &frames, &dropped, &active, &generation), "Child owner read");
        std::this_thread::sleep_for(1ms);
    }
    return 2;
}
struct OwnerChild {
    pid_t pid = -1;
    int reader = -1;
    ~OwnerChild() {
        if (reader >= 0) ::close(reader);
        if (pid > 0) { kill(pid, SIGKILL); waitpid(pid, nullptr, 0); }
    }
    void start(const char* executable) {
        int pipefd[2];
        require(pipe(pipefd) == 0, "Create owner readiness pipe");
        reader = pipefd[0];
        posix_spawn_file_actions_t actions;
        posix_spawn_file_actions_init(&actions);
        posix_spawn_file_actions_adddup2(&actions, pipefd[1], STDOUT_FILENO);
        posix_spawn_file_actions_addclose(&actions, pipefd[0]);
        posix_spawn_file_actions_addclose(&actions, pipefd[1]);
        char* arguments[] = {const_cast<char*>(executable), const_cast<char*>("--hold-owner"), nullptr};
        int result = posix_spawn(&pid, executable, &actions, nullptr, arguments, environ);
        posix_spawn_file_actions_destroy(&actions);
        ::close(pipefd[1]);
        check(result, "Launch independent owner");
        pollfd readiness{reader, POLLIN, 0};
        require(poll(&readiness, 1, 5000) == 1, "Owner readiness timed out");
        char message[32]{};
        ssize_t count = ::read(reader, message, sizeof(message) - 1);
        require(count > 0 && std::string(message) == "OWNER_READY\n", "Independent owner did not acquire endpoint");
        ::close(reader); reader = -1;
    }
    void crash() {
        require(kill(pid, SIGKILL) == 0, "Kill independent owner");
        int status = 0;
        require(waitpid(pid, &status, 0) == pid, "Wait for crashed owner");
        pid = -1;
        require(WIFSIGNALED(status) && WTERMSIG(status) == SIGKILL, "Owner did not exit without cleanup");
    }
};
void requireForeignAccessDenied(AudioDeviceID device) {
    void* other = nullptr;
    char error[512]{};
    int result = ms_audio_open(&other, error, sizeof(error));
    if (other) ms_audio_close(other);
    require(result != 0 && other == nullptr, "Live owner lease was stolen by another process");
    AudioObjectPropertyAddress addr{madsense::kRead, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    CFDataRef data = nullptr;
    UInt32 size = sizeof(data);
    result = AudioObjectGetPropertyData(device, &addr, 0, nullptr, &size, &data);
    if (data) CFRelease(data);
    require(result != noErr, "Another process retrieved private PCM while the owner was alive");
}
}

int main(int argc, char** argv) {
    try {
        require(ms_audio_abi_version() == 2, "Incompatible audio consumer ABI");
        if (argc == 2 && std::strcmp(argv[1], "--hold-owner") == 0) return holdOwner();
        AudioDeviceID device = findDevice();
        if (device == kAudioObjectUnknown) {
            std::cerr << "MadSense endpoint has not loaded yet\n";
            return 75; // Only discovery can be retried; transfer/ownership failures are permanent.
        }
        Consumer consumer;
        consumer.open();
        Signal signal;
        Stream stream;
        stream.create(device, signal);
        stream.start();
        uint64_t baselineDropped = 0;
        uint64_t firstGeneration = receive(consumer, signal, 0, 0, &baselineDropped, 5ms);
        auto baseline = measureWarmTransfer(consumer, signal, baselineDropped, firstGeneration, "default_qos_5ms", 5ms);
        AudioScheduling scheduling;
        // Account for the previous policy's final idle interval, then establish
        // a fresh warm baseline under audio QoS without flushing the queue or
        // hiding any drops incurred during the transition.
        uint64_t rewarmedDropped = baseline.dropped;
        uint64_t rewarmedGeneration = receive(consumer, signal, 0, 0, &rewarmedDropped, 1ms);
        require(rewarmedDropped >= baseline.dropped, "Dropped frame counter moved backwards during QoS rewarm");
        std::cout << "audio_qos_rewarm verified_at_least_frames=2048"
                  << " dropped_delta=" << rewarmedDropped - baseline.dropped
                  << " generation=" << rewarmedGeneration << '\n';
        measureWarmTransfer(consumer, signal, rewarmedDropped, rewarmedGeneration, "audio_qos_1ms", 1ms);

        stream.stop();
        signal.phase.store(1, std::memory_order_relaxed);
        stream.start();
        uint64_t restartedGeneration = receive(consumer, signal, 1, firstGeneration);

        consumer.close();
        OwnerChild child;
        child.start(argv[0]);
        requireForeignAccessDenied(stream.device);
        child.crash();
        signal.phase.store(2, std::memory_order_relaxed);
        std::this_thread::sleep_for(1100ms); // Expire the crashed process's last one-second lease.
        consumer.open();
        receive(consumer, signal, 2, restartedGeneration);
        stream.stop();
        consumer.close();
        std::cout << "Installed HAL endpoint -> authenticated four-channel consumer: passed; "
                     "all lanes, restart generation, foreign access denial, owner crash/reclaim\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "Installed HAL transfer failed: " << error.what() << '\n';
        return 1;
    }
}
