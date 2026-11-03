#pragma once
#include <cstdint>

namespace madsense {
// The SDK documents cycle 1 both at initial start and when the
// host resynchronizes. Initial start is accepted. Once the stream is established,
// every cycle-one operation is rejected, including consecutive resynchronizations.
// Like AudioRing, this state belongs exclusively to the single HAL mix producer.
// Control/property threads communicate transitions only through the PCM epoch.
class ResyncPolicy {
    uint64_t generation_ = 0;
    bool established_ = false;
public:
    bool reject(uint64_t generation, uint64_t cycle) noexcept {
        if (generation != generation_) { generation_ = generation; established_ = false; }
        if (!cycle) return false; // No documented ordinal: do not invent a reset.
        if (cycle > 1) established_ = true;
        return cycle == 1 && established_;
    }
    // Our own recovery changes the PCM epoch without starting a new host stream.
    void adoptGeneration(uint64_t generation) noexcept { generation_ = generation; }
};
}
