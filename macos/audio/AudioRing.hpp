#pragma once
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>

namespace madsense {
constexpr uint32_t kChannels = 4;
constexpr uint32_t kSampleRate = 48000;
constexpr uint32_t kReadFrames = 480;
constexpr uint32_t kRingFrames = 2048;

// One HAL mix producer and one serialized property-reader consumer. Neither side
// writes the other side's cursor. A producer overrun invalidates the accumulated
// audio, so a stalled reader never plays stale effects. No allocation in push.
class AudioRing {
    alignas(64) std::atomic<uint64_t> write_{0};
    alignas(64) std::atomic<uint64_t> read_{0};
    std::atomic<uint64_t> dropped_{0};
    std::atomic<uint64_t> overrun_{0};
    uint64_t seenOverrun_ = 0; // consumer-only
    std::array<float, kRingFrames * kChannels> data_{};
public:
    uint32_t push(const float* samples, uint32_t frames) noexcept {
        const uint64_t write = write_.load(std::memory_order_relaxed);
        const uint64_t read = read_.load(std::memory_order_acquire);
        const auto accepted = static_cast<uint32_t>(std::min<uint64_t>(frames, kRingFrames - (write - read)));
        for (uint32_t i = 0; i < accepted; ++i)
            std::memcpy(&data_[((write + i) % kRingFrames) * kChannels],
                        samples + i * kChannels, kChannels * sizeof(float));
        write_.store(write + accepted, std::memory_order_release);
        dropped_.fetch_add(frames - accepted, std::memory_order_relaxed);
        if (frames != accepted) overrun_.fetch_add(1, std::memory_order_release);
        return accepted;
    }
    uint32_t popLatest(float* samples, uint32_t capacity) noexcept {
        uint64_t read = read_.load(std::memory_order_relaxed);
        const uint64_t write = write_.load(std::memory_order_acquire);
        const uint64_t overrun = overrun_.load(std::memory_order_acquire);
        if (overrun != seenOverrun_) {
            seenOverrun_ = overrun;
            dropped_.fetch_add(write - read, std::memory_order_relaxed);
            read_.store(write, std::memory_order_release);
            return 0;
        }
        const uint32_t max = std::min(capacity, kReadFrames);
        if (write - read > max) {
            dropped_.fetch_add(write - read - max, std::memory_order_relaxed);
            read = write - max;
        }
        const auto count = static_cast<uint32_t>(write - read);
        for (uint32_t i = 0; i < count; ++i)
            std::memcpy(samples + i * kChannels,
                        &data_[((read + i) % kRingFrames) * kChannels], kChannels * sizeof(float));
        read_.store(read + count, std::memory_order_release);
        return count;
    }
    // Consumer-only flush; safe while the writer is active.
    void discard() noexcept { read_.store(write_.load(std::memory_order_acquire), std::memory_order_release); }
    uint64_t dropped() const noexcept { return dropped_.load(std::memory_order_relaxed); }
};
static_assert(std::atomic<uint64_t>::is_always_lock_free, "Realtime queue requires lock-free 64-bit atomics.");
}
