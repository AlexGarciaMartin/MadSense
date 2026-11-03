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
// A normal HAL block may be 512 or 1024 frames. The reader's RPC packet size is
// not a latency/drop threshold: preserve complete blocks across multiple reads.
constexpr uint32_t kBacklogFrames = 1024;
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
    // PCM and its generation share the write cursor's release publication.
    // Plain per-slot tags are safe under the same SPSC ownership as the samples.
    std::array<uint64_t, kRingFrames> generations_{};
public:
    uint32_t push(const float* samples, uint32_t frames, uint64_t generation) noexcept {
        const uint64_t write = write_.load(std::memory_order_relaxed);
        const uint64_t read = read_.load(std::memory_order_acquire);
        const auto accepted = static_cast<uint32_t>(std::min<uint64_t>(frames, kRingFrames - (write - read)));
        for (uint32_t i = 0; i < accepted; ++i) {
            const auto slot = (write + i) % kRingFrames;
            std::memcpy(&data_[slot * kChannels],
                        samples + i * kChannels, kChannels * sizeof(float));
            generations_[slot] = generation;
        }
        write_.store(write + accepted, std::memory_order_release);
        dropped_.fetch_add(frames - accepted, std::memory_order_relaxed);
        if (frames != accepted) overrun_.fetch_add(1, std::memory_order_release);
        return accepted;
    }
    uint32_t popLatest(float* samples, uint32_t capacity, uint64_t generation) noexcept {
        if (!capacity) return 0;
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
        // Normal delivery is FIFO, including the tail of 512/1024-frame writes.
        // Only an actual backlog beyond 21.33 ms jumps to the newest 10 ms.
        if (write - read > kBacklogFrames) {
            dropped_.fetch_add(write - read - kReadFrames, std::memory_order_relaxed);
            read = write - kReadFrames;
        }
        uint32_t count = 0, stale = 0;
        while (read < write && count < max) {
            const auto slot = read % kRingFrames;
            if (generations_[slot] == generation) {
                std::memcpy(samples + count * kChannels,
                            &data_[slot * kChannels], kChannels * sizeof(float));
                ++count;
            } else {
                // discard() cannot cancel a producer already copying a block.
                // Its captured old tag prevents late publication crossing epochs.
                ++stale;
            }
            ++read;
        }
        dropped_.fetch_add(stale, std::memory_order_relaxed);
        read_.store(read, std::memory_order_release);
        return count;
    }
    // Consumer-only flush; the caller also advances the accepted generation so
    // an in-flight producer cannot repopulate this queue with the previous epoch.
    void discard() noexcept { read_.store(write_.load(std::memory_order_acquire), std::memory_order_release); }
    uint64_t dropped() const noexcept { return dropped_.load(std::memory_order_relaxed); }
};
static_assert(std::atomic<uint64_t>::is_always_lock_free, "Realtime queue requires lock-free 64-bit atomics.");
}
