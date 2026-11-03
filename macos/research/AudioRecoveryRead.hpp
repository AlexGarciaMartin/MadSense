#pragma once
#include <atomic>
#include <cstdint>

namespace audio_probe {
// Research-only read commit. The drop snapshot acquires the rejection counter,
// whose release publication follows the producer's epoch CAS. Thus a packet
// cannot acknowledge that rejection while retaining pre-recovery candidates.
template<class Ring>
bool finishCapturedRead(Ring& ring, const std::atomic<uint64_t>& currentGeneration,
                        uint64_t capturedGeneration, uint32_t& frames, uint64_t& dropped) noexcept {
    dropped = ring.dropped();
    // Linearization point: a later transition cannot revoke this completed read.
    if (currentGeneration.load(std::memory_order_acquire) == capturedGeneration) return true;
    ring.rejectFrames(frames);
    frames = 0;
    dropped = ring.dropped();
    return false;
}
}
