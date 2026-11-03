#include "AudioProbeSignal.hpp"
#include <cstdlib>
#include <iostream>
#include <limits>

void require(bool value) { if (!value) std::abort(); }
int main() {
    float samples[4];
    audio_probe::Marker decoded{};
    // Exercise every offset, ID digit rollover, and maximum representable ID.
    for (uint32_t id : {1u, 4095u, 4096u, 65537u, audio_probe::kMaxCallback}) {
        for (uint32_t offset = 0; offset < audio_probe::kMaxFrames; ++offset) {
            audio_probe::encode(id, offset, samples);
            require(audio_probe::decode(samples, decoded));
            require(decoded.callback == id && decoded.offset == offset);
        }
    }
    audio_probe::encode(1234, 17, samples);
    samples[2] += 1.0f / audio_probe::kRadix;
    require(!audio_probe::decode(samples, decoded));
    audio_probe::encode(1234, 17, samples);
    samples[1] *= 0.5f;
    require(!audio_probe::decode(samples, decoded));
    audio_probe::encode(1234, 17, samples);
    samples[0] = std::numeric_limits<float>::quiet_NaN();
    require(!audio_probe::decode(samples, decoded));
    for (float& sample : samples) sample = 0;
    require(audio_probe::silent(samples));
    require(!audio_probe::decode(samples, decoded));
    audio_probe::encode(0, 0, samples);
    require(!audio_probe::decode(samples, decoded));
    audio_probe::Sequence sequence;
    sequence.observe(100); // Initial warm-up prefix is deliberately unmeasured.
    sequence.observe(101);
    sequence.observe(105);
    sequence.observe(105);
    sequence.observe(103);
    sequence.observe(106);
    require(sequence.gaps == 3 && sequence.nonIncreasing == 2 && sequence.last == 106);
    // Reproduces the size of the first CI failure: one repeated 96-frame
    // callback must remain a failure, then allow the forward sequence to resume.
    audio_probe::Sequence repeated;
    for (uint64_t i = 0; i < 192; ++i) require(repeated.observe(i));
    for (uint64_t i = 96; i < 192; ++i) require(!repeated.observe(i));
    require(repeated.nonIncreasing == 96 && repeated.last == 191 && repeated.gaps == 0);
    require(repeated.observe(192));
    std::cout << "Audio probe marker and continuity tests passed\n";
}
