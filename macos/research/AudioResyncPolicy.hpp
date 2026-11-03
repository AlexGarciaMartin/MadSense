#pragma once
#include <cstdint>

namespace audio_probe {
// Research only. The SDK documents cycle 1 both at initial start and when the
// host resynchronizes. Initial start is accepted; an observed rollback is not.
class ResyncPolicy {
    uint64_t generation_ = 0, previousCycle_ = 0;
public:
    bool reject(uint64_t generation, uint64_t cycle) noexcept {
        if (generation != generation_) { generation_ = generation; previousCycle_ = 0; }
        if (!cycle) return false; // No documented ordinal: do not invent a reset.
        const bool reset = cycle == 1 && previousCycle_ > 1;
        previousCycle_ = cycle;
        return reset;
    }
    void adoptGeneration(uint64_t generation) noexcept { generation_ = generation; }
};
}
