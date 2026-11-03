#pragma once
// Research-only wire format and bounded observer. Never included in the app.
#include "AudioProbeSignal.hpp"
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <type_traits>

namespace audio_probe {
constexpr uint32_t kIngressProperty = 0x6d736967; // msig
constexpr uint32_t kIngressVersion = 1;
constexpr uint32_t kIngressEvents = 32, kIngressMarkers = 8;
struct TraceTime {
    double sample = 0;
    uint64_t host = 0;
    uint32_t flags = 0, reserved = 0;
};
struct TraceCycle {
    uint64_t entered = 0, cycle = 0;
    TraceTime current, input, output;
    uint32_t frames = 0, nominalFrames = 0, operation = 0, client = 0;
};
struct BadMarker {
    uint32_t index = 0, reserved = 0;
    Marker highest{}, current{};
};
struct IngressEvent {
    TraceCycle previous, current;
    uint64_t nonIncreasing = 0;
    uint32_t markerCount = 0, reserved = 0;
    BadMarker lastBad{};
    std::array<BadMarker, kIngressMarkers> markers{};
};
struct IngressSnapshot {
    uint32_t version = kIngressVersion, eventCount = 0;
    uint64_t generation = 0, calls = 0, frames = 0, verified = 0, silent = 0;
    uint64_t invalidMarkers = 0, nonIncreasing = 0, overlaps = 0;
    std::array<IngressEvent, kIngressEvents> events{};
};
static_assert(std::is_trivially_copyable<IngressSnapshot>::value, "Trace must be a plain CFData payload");
static_assert(std::atomic<uint64_t>::is_always_lock_free, "Observer counters must be lock-free");

class IngressTrace {
    std::atomic_flag active_ = ATOMIC_FLAG_INIT;
    // Cumulative, since a failed try-lock cannot safely inspect the epoch.
    std::atomic<uint64_t> overlaps_{0};
    IngressSnapshot data_{};
    TraceCycle previous_{};
    Marker highest_{};
    bool seen_ = false;
public:
    bool begin(const float* samples, uint32_t frames, uint64_t generation,
                 const TraceCycle& cycle) noexcept {
        if (active_.test_and_set(std::memory_order_acquire)) {
            overlaps_.fetch_add(1, std::memory_order_relaxed);
            return false; // Never block the original WriteMix or change its payload.
        }
        if (data_.generation != generation) {
            // Do not clear the large bounded array on the realtime thread.
            // eventCount makes previous-generation records unreachable.
            data_.eventCount = 0;
            data_.generation = generation;
            data_.calls = data_.frames = data_.verified = data_.silent = 0;
            data_.invalidMarkers = data_.nonIncreasing = 0;
            previous_ = {}; highest_ = {}; seen_ = false;
        }
        ++data_.calls; data_.frames += frames;
        IngressEvent* event = nullptr;
        for (uint32_t i = 0; i < frames; ++i) {
            const auto* frame = samples + i * 4;
            if (silent(frame)) { ++data_.silent; continue; }
            Marker marker{};
            if (!decode(frame, marker)) { ++data_.invalidMarkers; continue; }
            ++data_.verified;
            const bool increasing = !seen_ || marker.callback > highest_.callback ||
                (marker.callback == highest_.callback && marker.offset > highest_.offset);
            if (increasing) { highest_ = marker; seen_ = true; continue; }
            ++data_.nonIncreasing;
            if (!event && data_.eventCount < data_.events.size()) {
                event = &data_.events[data_.eventCount++];
                *event = {};
                event->previous = previous_; event->current = cycle;
            }
            if (event) {
                ++event->nonIncreasing;
                event->lastBad = {i, 0, highest_, marker};
                if (event->markerCount < event->markers.size())
                    event->markers[event->markerCount++] = {i, 0, highest_, marker};
            }
        }
        previous_ = cycle;
        return true;
    }
    // Keep the guard across the unchanged ring.push too, so overlapping plugin
    // writes are detected even when their observer scans themselves don't overlap.
    void end() noexcept { active_.clear(std::memory_order_release); }
    // Only called off realtime after StopIO. A lingering writer makes the
    // snapshot fail explicitly; it is never waited on or raced with.
    bool snapshot(IngressSnapshot& output) noexcept {
        if (active_.test_and_set(std::memory_order_acquire)) return false;
        output = data_;
        // The realtime epoch reset intentionally leaves unused storage alone.
        // Never export those previous-generation bytes in the CFData payload.
        for (size_t i = output.eventCount; i < output.events.size(); ++i) output.events[i] = {};
        output.overlaps = overlaps_.load(std::memory_order_relaxed);
        active_.clear(std::memory_order_release);
        return true;
    }
};
}
