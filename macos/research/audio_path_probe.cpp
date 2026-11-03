// Opt-in research executable. Never included in MadSense.app or its HAL plugin.
#include "AudioProbeSignal.hpp"
#include "AudioIngressTrace.hpp"
#include "../audio/AudioProtocol.hpp"
#include "../audio/madsense_audio.h"
#include <CoreAudio/CoreAudio.h>
#include <mach/mach.h>
#include <mach/mach_time.h>
#include <pthread.h>
#include <pthread/qos.h>
#include <sys/resource.h>
#include <sys/sysctl.h>
#include <sys/proc.h>
#include <unistd.h>
#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {
double millisecondsPerTick;
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
void check(int result, const char* message) {
    if (result) throw std::runtime_error(std::string(message) + ": " + std::to_string(result));
}
double elapsedMs(uint64_t end, uint64_t start) {
    return end >= start ? static_cast<double>(end - start) * millisecondsPerTick
                        : -static_cast<double>(start - end) * millisecondsPerTick;
}
uint64_t afterSeconds(uint64_t start, unsigned seconds) {
    return start + static_cast<uint64_t>(seconds * 1000.0 / millisecondsPerTick);
}
AudioObjectPropertyAddress address(AudioObjectPropertySelector selector) {
    return {selector, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
}
AudioDeviceID findDevice() {
    auto addr = address(kAudioHardwarePropertyTranslateUIDToDevice);
    CFStringRef uid = CFSTR("org.madsense.audio.dualsense.v1");
    AudioDeviceID device = kAudioObjectUnknown;
    UInt32 size = sizeof(device);
    check(AudioObjectGetPropertyData(kAudioObjectSystemObject, &addr, sizeof(uid), &uid, &size, &device), "Find endpoint");
    require(size == sizeof(device) && device != kAudioObjectUnknown, "Dedicated MadSense audio endpoint missing");
    return device;
}
struct Cpu {
    double processMs, readerThreadMs;
    long voluntary, involuntary;
};
Cpu cpu() {
    rusage usage{};
    check(getrusage(RUSAGE_SELF, &usage), "getrusage");
    thread_basic_info_data_t info{};
    mach_msg_type_number_t count = THREAD_BASIC_INFO_COUNT;
    check(thread_info(pthread_mach_thread_np(pthread_self()), THREAD_BASIC_INFO,
                      reinterpret_cast<thread_info_t>(&info), &count), "Reader CPU snapshot");
    return {(usage.ru_utime.tv_sec + usage.ru_stime.tv_sec) * 1000.0 +
                (usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1000.0,
            (info.user_time.seconds + info.system_time.seconds) * 1000.0 +
                (info.user_time.microseconds + info.system_time.microseconds) / 1000.0,
            usage.ru_nvcsw, usage.ru_nivcsw};
}
void printCpu(const Cpu& before, const Cpu& after) {
    std::cout << ",\"probe_process_cpu_ms\":" << after.processMs - before.processMs
              << ",\"reader_thread_cpu_ms\":" << after.readerThreadMs - before.readerThreadMs
              << ",\"voluntary_context_switches\":" << after.voluntary - before.voluntary
              << ",\"involuntary_context_switches\":" << after.involuntary - before.involuntary;
}
void printStats(const char* name, std::vector<double>& values) {
    std::cout << ",\"" << name << "\":{\"count\":" << values.size();
    if (!values.empty()) {
        std::sort(values.begin(), values.end());
        auto percentile = [&](double p) {
            size_t index = static_cast<size_t>(std::ceil(p * values.size())) - 1;
            return values[std::min(index, values.size() - 1)];
        };
        std::cout << ",\"min\":" << values.front() << ",\"p50\":" << percentile(.5)
                  << ",\"p95\":" << percentile(.95) << ",\"p99\":" << percentile(.99)
                  << ",\"max\":" << values.back();
    }
    std::cout << '}';
}
struct Consumer {
    void* handle = nullptr;
    Consumer() {
        require(ms_audio_abi_version() == 2, "Audio consumer ABI mismatch");
        char error[512]{};
        if (ms_audio_open(&handle, error, sizeof(error))) throw std::runtime_error(error);
        require(handle != nullptr, "Missing consumer handle");
    }
    ~Consumer() { ms_audio_close(handle); }
};
struct ReadBlock {
    float samples[madsense::kReadFrames * madsense::kChannels];
    uint32_t frames = 0, active = 0;
    uint64_t dropped = 0, generation = 0;
    void read(Consumer& consumer) {
        check(ms_audio_read(consumer.handle, samples, madsense::kReadFrames,
                           &frames, &dropped, &active, &generation), "Read PCM");
        require(frames <= madsense::kReadFrames && generation > 0, "Invalid audio packet metadata");
    }
};
struct Scheduling {
    pthread_override_t handle = nullptr;
    explicit Scheduling(bool enabled) {
        if (enabled) {
            handle = pthread_override_qos_class_start_np(pthread_self(), QOS_CLASS_USER_INTERACTIVE, 0);
            require(handle != nullptr, "Audio QoS override failed");
        }
    }
    ~Scheduling() { if (handle) pthread_override_qos_class_end_np(handle); }
};

struct CallbackRecord {
    uint64_t entered = 0, finished = 0, presentation = 0, firstFrame = 0;
    uint32_t frames = 0;
};
struct Signal {
    // Enough for >=16-frame callbacks throughout the bounded trial plus warmup.
    // An unexpectedly smaller callback fails explicitly instead of wrapping.
    std::vector<CallbackRecord> records;
    std::atomic<uint32_t> published{0};
    std::atomic_flag callbackActive = ATOMIC_FLAG_INIT;
    std::atomic<uint32_t> callbackOverlaps{0};
    std::atomic<bool> failed{false};
    uint64_t produced = 0; // callback-only until AudioDeviceStop completes
    explicit Signal(unsigned seconds) : records((seconds + 10) * 3000 + 1) {}
};
OSStatus output(AudioDeviceID, const AudioTimeStamp*, const AudioBufferList*, const AudioTimeStamp*,
                AudioBufferList* buffers, const AudioTimeStamp* presentation, void* context) {
    const uint64_t entered = mach_absolute_time();
    auto& signal = *static_cast<Signal*>(context);
    if (signal.callbackActive.test_and_set(std::memory_order_acquire)) {
        // Unexpected parallel callbacks would invalidate the source sequence
        // and plain immutable-record publication. Preserve that diagnosis and
        // leave this callback's HAL-zeroed output alone, rather than race.
        signal.callbackOverlaps.fetch_add(1, std::memory_order_relaxed);
        return noErr;
    }
    struct Exit {
        Signal& signal;
        ~Exit() { signal.callbackActive.clear(std::memory_order_release); }
    } exit{signal};
    if (!buffers || buffers->mNumberBuffers != 1 || buffers->mBuffers[0].mNumberChannels != 4 ||
        !buffers->mBuffers[0].mData || buffers->mBuffers[0].mDataByteSize % (4 * sizeof(float))) {
        signal.failed.store(true, std::memory_order_relaxed);
        return noErr;
    }
    auto& buffer = buffers->mBuffers[0];
    const uint32_t frames = buffer.mDataByteSize / (4 * sizeof(float));
    const uint32_t id = signal.published.load(std::memory_order_relaxed) + 1;
    if (!frames || frames > audio_probe::kMaxFrames || id >= signal.records.size() || id > audio_probe::kMaxCallback) {
        signal.failed.store(true, std::memory_order_relaxed);
        return noErr;
    }
    auto samples = static_cast<float*>(buffer.mData);
    auto& record = signal.records[id];
    record.entered = entered;
    record.presentation = presentation && (presentation->mFlags & kAudioTimeStampHostTimeValid) ? presentation->mHostTime : 0;
    record.firstFrame = signal.produced;
    record.frames = frames;
    for (uint32_t frame = 0; frame < frames; ++frame)
        audio_probe::encode(id, frame, samples + frame * 4);
    signal.produced += frames;
    record.finished = mach_absolute_time();
    // These immutable metadata records are acquired before the reader inspects
    // them. No allocator, lock, logging or cross-process call occurs here.
    signal.published.store(id, std::memory_order_release);
    return noErr;
}
struct Stream {
    AudioDeviceID device;
    AudioDeviceIOProcID proc = nullptr;
    bool started = false;
    Stream(AudioDeviceID id, Signal& signal) : device(id) {
        check(AudioDeviceCreateIOProcID(device, output, &signal, &proc), "Create output IOProc");
    }
    void start() { check(AudioDeviceStart(device, proc), "Start output IOProc"); started = true; }
    void stop() { if (started) { check(AudioDeviceStop(device, proc), "Stop output IOProc"); started = false; } }
    ~Stream() {
        if (started) AudioDeviceStop(device, proc);
        if (proc) AudioDeviceDestroyIOProcID(device, proc);
    }
};

template<class Work> void control(const char* label, unsigned requested, Work work) {
    std::vector<double> durations;
    durations.reserve(requested);
    const Cpu before = cpu();
    const uint64_t started = mach_absolute_time(), deadline = afterSeconds(started, 20);
    for (unsigned i = 0; i < requested && mach_absolute_time() < deadline; ++i) {
        const uint64_t callStarted = mach_absolute_time();
        work();
        durations.push_back(elapsedMs(mach_absolute_time(), callStarted));
    }
    const uint64_t ended = mach_absolute_time();
    const Cpu after = cpu();
    std::cout << "{\"kind\":\"audio_control\",\"case\":\"" << label
              << "\",\"requested_iterations\":" << requested << ",\"elapsed_ms\":" << elapsedMs(ended, started);
    printCpu(before, after);
    printStats("call_ms", durations);
    std::cout << "}\n" << std::flush;
}
void controls(AudioDeviceID device, Consumer& consumer, unsigned iterations) {
    control("self_identity_sysctl_only_not_sandbox_host", iterations, [] {
        const pid_t pid = getpid();
        int mib[] = {CTL_KERN, KERN_PROC, KERN_PROC_PID, pid};
        kinfo_proc info{};
        size_t size = sizeof(info);
        check(sysctl(mib, 4, &info, &size, nullptr, 0), "Self identity sysctl");
        require(size == sizeof(info) && info.kp_proc.p_pid == pid &&
                info.kp_proc.p_stat != 0 && info.kp_proc.p_stat != SZOMB &&
                info.kp_proc.p_starttime.tv_sec > 0 && info.kp_eproc.e_ucred.cr_uid == geteuid(),
                "Invalid self identity snapshot");
    });
    control("authenticated_owner_cfboolean_rpc", iterations, [device] {
        auto addr = address(madsense::kOwner);
        CFBooleanRef value = nullptr;
        UInt32 size = sizeof(value);
        check(AudioObjectGetPropertyData(device, &addr, 0, nullptr, &size, &value), "Owner property");
        const bool valid = value && size == sizeof(value) && CFGetTypeID(value) == CFBooleanGetTypeID() && CFBooleanGetValue(value);
        if (value) CFRelease(value);
        require(valid, "Owner authentication failed");
    });
    ReadBlock block{};
    control("authenticated_empty_cfdata_rpc", iterations, [&] {
        block.read(consumer);
        require(block.frames == 0 && block.active == 0, "Control endpoint is not idle; another client is using it");
    });
}

void printTime(const char* name, const audio_probe::TraceTime& time) {
    std::cout << ",\"" << name << "\":{\"sample\":";
    if (std::isfinite(time.sample)) std::cout << time.sample; else std::cout << "null";
    std::cout << ",\"host\":" << time.host << ",\"flags\":" << time.flags << '}';
}
void printCycle(const audio_probe::TraceCycle& cycle) {
    std::cout << "{\"entered_ticks\":" << cycle.entered << ",\"cycle\":" << cycle.cycle
              << ",\"frames\":" << cycle.frames << ",\"nominal_frames\":" << cycle.nominalFrames
              << ",\"operation\":" << cycle.operation << ",\"client\":" << cycle.client;
    printTime("current", cycle.current); printTime("input", cycle.input); printTime("output", cycle.output);
    std::cout << '}';
}
void printBadMarker(const audio_probe::BadMarker& marker) {
    std::cout << "{\"frame_index\":" << marker.index
              << ",\"highest_callback\":" << marker.highest.callback << ",\"highest_offset\":" << marker.highest.offset
              << ",\"current_callback\":" << marker.current.callback << ",\"current_offset\":" << marker.current.offset << '}';
}
bool ingressTrace(AudioDeviceID device, unsigned trialIndex, bool elevated, uint64_t generation) {
    auto addr = address(audio_probe::kIngressProperty);
    CFDataRef data = nullptr;
    UInt32 size = sizeof(data);
    check(AudioObjectGetPropertyData(device, &addr, 0, nullptr, &size, &data), "Read stopped ingress trace");
    const bool valid = data && size == sizeof(data) && CFGetTypeID(data) == CFDataGetTypeID() &&
        CFDataGetLength(data) == static_cast<CFIndex>(sizeof(audio_probe::IngressSnapshot));
    if (!valid) { if (data) CFRelease(data); throw std::runtime_error("Invalid ingress trace wire format"); }
    audio_probe::IngressSnapshot trace;
    std::memcpy(&trace, CFDataGetBytePtr(data), sizeof(trace)); CFRelease(data);
    require(trace.version == audio_probe::kIngressVersion && trace.generation == generation &&
        trace.eventCount <= trace.events.size(), "Ingress trace epoch/version mismatch");
    const bool integrity = trace.verified > 0 && trace.nonIncreasing == 0 && trace.invalidMarkers == 0 && trace.overlaps == 0;
    std::cout << "{\"kind\":\"audio_ingress_trace\",\"trial\":" << trialIndex
              << ",\"policy\":\"" << (elevated ? "scoped_user_interactive" : "default")
              << "\",\"includes_warmup\":true,\"generation\":" << trace.generation
              << ",\"data_integrity\":" << (integrity ? "true" : "false")
              << ",\"calls\":" << trace.calls << ",\"frames\":" << trace.frames
              << ",\"verified\":" << trace.verified << ",\"silent\":" << trace.silent
              << ",\"invalid_markers\":" << trace.invalidMarkers << ",\"nonincreasing_frames\":" << trace.nonIncreasing
              << ",\"observer_overlaps_since_load\":" << trace.overlaps << ",\"first_anomalous_blocks\":[";
    for (uint32_t i = 0; i < trace.eventCount; ++i) {
        const auto& event = trace.events[i];
        require(event.markerCount <= event.markers.size(), "Ingress trace marker count invalid");
        if (i) std::cout << ',';
        std::cout << "{\"previous_block\":"; printCycle(event.previous);
        std::cout << ",\"current_block\":"; printCycle(event.current);
        std::cout << ",\"nonincreasing_frames\":" << event.nonIncreasing << ",\"last_bad_marker\":";
        printBadMarker(event.lastBad);
        std::cout << ",\"first_bad_markers\":[";
        for (uint32_t j = 0; j < event.markerCount; ++j) {
            if (j) std::cout << ',';
            printBadMarker(event.markers[j]);
        }
        std::cout << "]}";
    }
    std::cout << "]}\n" << std::flush;
    return integrity;
}

bool trial(AudioDeviceID device, Consumer& consumer, unsigned seconds, unsigned warmup,
           unsigned trialIndex, bool elevated, unsigned idleMs, bool hasIngress) {
    Scheduling scheduling(elevated);
    Signal signal(seconds + warmup);
    std::vector<double> rpc, fullRpc, emptyRpc, idle, gaps, age, offset;
    for (auto* v : {&rpc, &fullRpc, &emptyRpc, &idle, &gaps}) v->reserve((seconds + 5) * 2000);
    age.reserve((seconds + 5) * 48000); offset.reserve((seconds + 5) * 48000);
    Stream stream(device, signal);
    // Sorting/reporting the preceding trial can take longer than the owner's
    // lease on a busy VM. Renew normally before StartIO; never bypass identity.
    ReadBlock renewal{};
    renewal.read(consumer);
    require(renewal.active == 0, "Another producer is using the research endpoint");
    stream.start();
    const uint64_t warmUntil = afterSeconds(mach_absolute_time(), warmup);
    uint64_t started = 0, ended = 0, baselineDropped = 0, lastDropped = 0, generation = 0;
    uint64_t lastReadEnd = 0, received = 0, silent = 0, changes = 0, empty = 0, full = 0;
    uint64_t missingPresentation = 0, activeZero = 0;
    audio_probe::Sequence sequence;
    audio_probe::Marker highestMarker{};
    struct Violation {
        uint64_t readCall, readStarted, readEnded, previous, current, dropped, generation;
        uint32_t frameIndex, framesInRead, published;
        audio_probe::Marker previousMarker, currentMarker;
    };
    std::array<Violation, 32> violations{};
    size_t violationCount = 0;
    Cpu before{};
    ReadBlock block{};
    while (true) {
        uint64_t readStarted = mach_absolute_time();
        if (!started && readStarted >= warmUntil) {
            before = cpu(); started = mach_absolute_time(); lastReadEnd = started;
            baselineDropped = lastDropped;
            readStarted = mach_absolute_time();
        }
        if (started && readStarted >= afterSeconds(started, seconds)) break;
        block.read(consumer);
        const uint64_t readEnded = mach_absolute_time();
        require(!signal.failed.load(std::memory_order_relaxed), "Unsupported callback layout/size or metadata capacity exceeded");
        require(block.dropped >= lastDropped, "Drop counter moved backwards");
        if (started) {
            const double duration = elapsedMs(readEnded, readStarted);
            rpc.push_back(duration); gaps.push_back(elapsedMs(readStarted, lastReadEnd));
            if (block.frames == 0) { ++empty; emptyRpc.push_back(duration); }
            if (block.frames == madsense::kReadFrames) { ++full; fullRpc.push_back(duration); }
            if (generation && block.generation != generation) ++changes;
            received += block.frames;
            if (block.active == 0) ++activeZero;
            const uint32_t published = signal.published.load(std::memory_order_acquire);
            for (uint32_t frame = 0; frame < block.frames; ++frame) {
                const float* samples = block.samples + frame * 4;
                if (audio_probe::silent(samples)) { ++silent; continue; }
                audio_probe::Marker marker{};
                require(audio_probe::decode(samples, marker), "PCM marker corrupt, mixed, scaled or channel-swapped");
                require(marker.callback <= published && marker.callback < signal.records.size(), "Unknown callback marker");
                const auto& record = signal.records[marker.callback];
                require(marker.offset < record.frames && record.finished <= readEnded, "Invalid frame offset or callback time");
                const uint64_t current = record.firstFrame + marker.offset, previous = sequence.last;
                if (sequence.observe(current)) highestMarker = marker;
                else if (violationCount < violations.size()) {
                    violations[violationCount++] = {rpc.size(), readStarted, readEnded, previous, current,
                        block.dropped, block.generation, frame, block.frames, published, highestMarker, marker};
                }
                age.push_back(elapsedMs(readEnded, record.entered));
                if (record.presentation) {
                    // Signed offset from the scheduled presentation of THIS frame.
                    // Negative means the reader obtained it before presentation.
                    offset.push_back(elapsedMs(readEnded, record.presentation) - marker.offset / 48.0);
                } else ++missingPresentation;
            }
        }
        generation = block.generation; lastDropped = block.dropped; lastReadEnd = readEnded;
        if (block.frames < madsense::kReadFrames) {
            const uint64_t idleStarted = mach_absolute_time();
            std::this_thread::sleep_for(std::chrono::milliseconds(idleMs));
            if (started) idle.push_back(elapsedMs(mach_absolute_time(), idleStarted));
        }
    }
    ended = mach_absolute_time();
    const Cpu after = cpu();
    stream.stop();
    const bool ingressIntegrity = !hasIngress || ingressTrace(device, trialIndex, elevated, generation);
    require(!signal.failed.load(), "Unsupported callback layout/size or metadata capacity exceeded");
    std::vector<double> callbackGap, callbackDuration;
    std::map<uint32_t, uint64_t> blockSizes;
    uint64_t previousCallback = 0, producedInWindow = 0;
    const uint32_t published = signal.published.load(std::memory_order_acquire);
    for (uint32_t id = 1; id <= published; ++id) {
        const auto& record = signal.records[id];
        if (record.entered < started || record.entered >= ended) continue;
        ++blockSizes[record.frames]; producedInWindow += record.frames;
        callbackDuration.push_back(elapsedMs(record.finished, record.entered));
        if (previousCallback) callbackGap.push_back(elapsedMs(record.entered, previousCallback));
        previousCallback = record.entered;
    }
    const uint32_t callbackOverlaps = signal.callbackOverlaps.load();
    const bool integrity = sequence.nonIncreasing == 0 && !age.empty() && callbackOverlaps == 0 && ingressIntegrity;
    const bool discontinuity = changes != 0 || activeZero != 0;
    std::cout << "{\"kind\":\"audio_path_trial\",\"trial\":" << trialIndex
              << ",\"data_integrity\":" << (integrity ? "true" : "false")
              << ",\"stream_discontinuity\":" << (discontinuity ? "true" : "false")
              << ",\"measurement_valid\":" << (integrity && !discontinuity ? "true" : "false")
              << ",\"policy\":\"" << (elevated ? "scoped_user_interactive" : "default")
              << "\",\"requested_idle_ms\":" << idleMs << ",\"requested_seconds\":" << seconds
              << ",\"warmup_seconds\":" << warmup << ",\"elapsed_ms\":" << elapsedMs(ended, started)
              << ",\"received_frames\":" << received << ",\"silent_frames\":" << silent
              << ",\"verified_frames\":" << age.size() << ",\"produced_frames_callback_window\":" << producedInWindow
              << ",\"dropped_delta\":" << lastDropped - baselineDropped
              << ",\"sequence_gap_frames\":" << sequence.gaps << ",\"nonincreasing_frames\":" << sequence.nonIncreasing
              << ",\"generation_changes\":" << changes << ",\"empty_reads\":" << empty << ",\"full_reads\":" << full
              << ",\"callback_overlaps\":" << callbackOverlaps
              << ",\"reads_without_active_client\":" << activeZero
              << ",\"frames_without_presentation_timestamp\":" << missingPresentation;
    printCpu(before, after);
    printStats("read_rpc_ms", rpc); printStats("full480_rpc_ms", fullRpc); printStats("empty_rpc_ms", emptyRpc);
    printStats("actual_idle_ms", idle); printStats("between_reads_ms", gaps);
    printStats("callback_entry_to_read_return_ms", age);
    printStats("read_return_minus_scheduled_frame_presentation_ms", offset);
    printStats("callback_gap_ms", callbackGap); printStats("callback_body_ms", callbackDuration);
    std::cout << ",\"callback_frame_sizes\":{";
    bool first = true;
    for (const auto& pair : blockSizes) {
        if (!first) std::cout << ',';
        first = false; std::cout << '\"' << pair.first << "\":" << pair.second;
    }
    std::cout << "},\"first_nonincreasing_frames\":[";
    for (size_t i = 0; i < violationCount; ++i) {
        const auto& v = violations[i];
        const auto& previousRecord = signal.records[v.previousMarker.callback];
        const auto& currentRecord = signal.records[v.currentMarker.callback];
        if (i) std::cout << ',';
        std::cout << "{\"read_call\":" << v.readCall << ",\"read_frame_index\":" << v.frameIndex
                  << ",\"frames_in_read\":" << v.framesInRead << ",\"published_callback\":" << v.published
                  << ",\"previous_highest_sequence\":" << v.previous << ",\"current_sequence\":" << v.current
                  << ",\"previous_callback\":" << v.previousMarker.callback
                  << ",\"previous_offset\":" << v.previousMarker.offset
                  << ",\"current_callback\":" << v.currentMarker.callback
                  << ",\"current_offset\":" << v.currentMarker.offset
                  << ",\"previous_callback_frames\":" << previousRecord.frames
                  << ",\"current_callback_frames\":" << currentRecord.frames
                  << ",\"previous_callback_entered_ticks\":" << previousRecord.entered
                  << ",\"current_callback_entered_ticks\":" << currentRecord.entered
                  << ",\"current_callback_presentation_ticks\":" << currentRecord.presentation
                  << ",\"read_started_ticks\":" << v.readStarted << ",\"read_ended_ticks\":" << v.readEnded
                  << ",\"read_ended_trial_ms\":" << elapsedMs(v.readEnded, started)
                  << ",\"dropped_frames\":" << v.dropped << ",\"generation\":" << v.generation << '}';
    }
    std::cout << "]}\n" << std::flush;
    // Keep later policy comparisons available, while latching an unsuccessful
    // process exit. No duplicate is reclassified as valid or hidden from data.
    return integrity && !discontinuity;
}

unsigned positive(const char* value, unsigned max) {
    std::string text(value);
    size_t consumed = 0;
    unsigned long parsed = std::stoul(text, &consumed);
    require(consumed == text.size() && parsed > 0 && parsed <= max, "Numeric option out of range");
    return static_cast<unsigned>(parsed);
}
}
int main(int argc, char** argv) {
    try {
        unsigned seconds = 30, trials = 3, iterations = 5000, warmup = 2;
        bool requireIngress = false;
        for (int i = 1; i < argc; i += 2) {
            require(i + 1 < argc, "Missing option value");
            std::string option(argv[i]);
            if (option == "--seconds") seconds = positive(argv[i + 1], 120);
            else if (option == "--trials") trials = positive(argv[i + 1], 5);
            else if (option == "--control-iterations") iterations = positive(argv[i + 1], 100000);
            else if (option == "--warmup") warmup = positive(argv[i + 1], 10);
            else if (option == "--require-ingress") requireIngress = positive(argv[i + 1], 1) == 1;
            else throw std::runtime_error("Unknown option: " + option);
        }
        mach_timebase_info_data_t timebase{};
        check(mach_timebase_info(&timebase), "Timebase");
        millisecondsPerTick = static_cast<double>(timebase.numer) / timebase.denom / 1000000;
        const AudioDeviceID device = findDevice();
        Consumer consumer;
        auto ingressAddress = address(audio_probe::kIngressProperty);
        const bool hasIngress = AudioObjectHasProperty(device, &ingressAddress);
        require(!requireIngress || hasIngress, "Required research ingress observer is missing");
        auto addr = address(kAudioDevicePropertyBufferFrameSize);
        UInt32 blockSize = 0, size = sizeof(blockSize);
        check(AudioObjectGetPropertyData(device, &addr, 0, nullptr, &size, &blockSize), "Get frame size");
        std::cout << std::setprecision(9)
                  << "{\"kind\":\"audio_path_environment\",\"hardware_latency_measured\":false,"
                  << "\"bluetooth_measured\":false,\"sample_rate\":48000,\"channels\":4,\"reported_buffer_frames\":"
                  << blockSize << ",\"consumer_abi\":" << ms_audio_abi_version()
                  << ",\"research_ingress_observer\":" << (hasIngress ? "true" : "false")
                  << ",\"timebase_numer\":" << timebase.numer << ",\"timebase_denom\":" << timebase.denom << "}\n";
        controls(device, consumer, iterations);
        bool allValid = true;
        for (unsigned i = 1; i <= trials; ++i) {
            // Alternate order to reduce the bias of warming the same VM.
            if (i % 2) {
                allValid = trial(device, consumer, seconds, warmup, i, false, 5, hasIngress) && allValid;
                allValid = trial(device, consumer, seconds, warmup, i, true, 1, hasIngress) && allValid;
            } else {
                allValid = trial(device, consumer, seconds, warmup, i, true, 1, hasIngress) && allValid;
                allValid = trial(device, consumer, seconds, warmup, i, false, 5, hasIngress) && allValid;
            }
        }
        if (!allValid) std::cerr << "Audio research completed with one or more invalid trials; see retained diagnostics.\n";
        return allValid ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << "Audio research failed: " << error.what() << '\n';
        return 1;
    }
}
