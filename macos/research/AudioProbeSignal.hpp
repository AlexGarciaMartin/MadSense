#pragma once
#include <cmath>
#include <cstdint>

namespace audio_probe {
// Four exact binary fractions in (0, 1]. The installed output-only HAL path
// carries these ordinary PCM values unchanged; no production ABI is modified.
constexpr uint32_t kRadix = 4096;
constexpr uint32_t kMaxCallback = kRadix * kRadix - 1;
constexpr uint32_t kMaxFrames = kRadix;
struct Marker { uint32_t callback; uint32_t offset; };

inline void encode(uint32_t callback, uint32_t offset, float* out) noexcept {
    const uint32_t low = callback % kRadix, high = callback / kRadix;
    out[0] = static_cast<float>(low + 1) / kRadix;
    out[1] = static_cast<float>(high + 1) / kRadix;
    out[2] = static_cast<float>(offset + 1) / kRadix;
    out[3] = static_cast<float>((low * 17 + high * 31 + offset * 7) % kRadix + 1) / kRadix;
}
inline bool decode(const float* in, Marker& out) noexcept {
    uint32_t parts[4];
    for (unsigned i = 0; i < 4; ++i) {
        if (!std::isfinite(in[i]) || in[i] <= 0 || in[i] > 1) return false;
        const double value = static_cast<double>(in[i]) * kRadix;
        const double rounded = std::round(value);
        // Reject gain/mixing/corruption instead of interpreting it as latency.
        if (std::abs(value - rounded) > 0.01 || rounded < 1 || rounded > kRadix) return false;
        parts[i] = static_cast<uint32_t>(rounded) - 1;
    }
    out = {parts[0] + parts[1] * kRadix, parts[2]};
    return out.callback > 0 &&
        parts[3] == (parts[0] * 17 + parts[1] * 31 + parts[2] * 7) % kRadix;
}
inline bool silent(const float* in) noexcept {
    return in[0] == 0 && in[1] == 0 && in[2] == 0 && in[3] == 0;
}

struct Sequence {
    bool seen = false;
    uint64_t last = 0, gaps = 0, nonIncreasing = 0;
    void observe(uint64_t value) noexcept {
        if (seen) {
            if (value <= last) { ++nonIncreasing; return; }
            gaps += value - last - 1;
        }
        last = value;
        seen = true;
    }
};
}
