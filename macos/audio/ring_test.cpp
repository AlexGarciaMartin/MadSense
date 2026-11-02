#include "AudioRing.hpp"
#include <cstdlib>
#include <iostream>
#include <thread>
#include <vector>
#include <cmath>

void require(bool condition, const char* reason) {
    if (!condition) { std::cerr << reason << '\n'; std::exit(1); }
}
void verifyBlockContinuity(uint32_t frames) {
    using namespace madsense;
    AudioRing ring;
    std::vector<float> block(frames * kChannels);
    float output[kReadFrames * kChannels]{};
    // Exercise many wraps, verifying every channel/sample rather than only the
    // packet endpoints. A 512-frame block drains 480+32; 1024 drains 480+480+64.
    for (uint32_t iteration = 0; iteration < 20; ++iteration) {
        for (uint32_t i = 0; i < block.size(); ++i) block[i] = static_cast<float>(iteration * block.size() + i);
        require(ring.push(block.data(), frames) == frames, "normal HAL block must fit");
        uint32_t read = 0;
        while (read < frames) {
            uint32_t count = ring.popLatest(output, kReadFrames);
            require(count == std::min(kReadFrames, frames - read), "wrong block fragment length");
            for (uint32_t i = 0; i < count * kChannels; ++i)
                require(output[i] == block[read * kChannels + i], "normal HAL block lost/reordered samples");
            read += count;
        }
        require(ring.popLatest(output, kReadFrames) == 0, "normal block fully drained");
        require(ring.dropped() == 0, "normal HAL block must never drop its tail");
    }
}
int main() {
    using namespace madsense;
    verifyBlockContinuity(512);
    verifyBlockContinuity(1024);
    AudioRing ring;
    std::vector<float> input(1536 * kChannels);
    for (size_t i = 0; i < input.size(); ++i) input[i] = static_cast<float>(i);
    float output[kReadFrames * kChannels]{};
    require(ring.push(input.data(), 1536) == 1536, "push failed");
    require(ring.popLatest(output, 0) == 0 && ring.dropped() == 0, "zero capacity must not consume or trim");
    require(ring.popLatest(output, kReadFrames) == kReadFrames, "read should keep 10 ms");
    require(output[0] == 1056 * kChannels && output[1919] == 6143, "stale backlog must retain newest frames");
    require(ring.dropped() == 1056, "backlog drop accounting");
    require(ring.popLatest(output, kReadFrames) == 0, "empty read");
    std::vector<float> tooMuch((kRingFrames + 20) * kChannels, 1);
    ring.push(tooMuch.data(), kRingFrames + 20);
    require(ring.popLatest(output, kReadFrames) == 0, "overrun must discard stale effects");
    require(ring.dropped() == 1056 + kRingFrames + 20, "overflow drop accounting");
    ring.push(input.data(), 100);
    require(ring.popLatest(output, kReadFrames) == 100 && output[3] == 3, "recover after overrun");
    ring.push(input.data(), 100);
    ring.discard();
    require(ring.popLatest(output, kReadFrames) == 0, "owner switch flush");

    // Concurrent stress verifies that complete frames stay in order and channel
    // relationships cannot tear even while the queue wraps and drops backlogs.
    AudioRing threaded;
    std::atomic<bool> done{false};
    std::thread producer([&] {
        float block[64 * kChannels];
        for (uint32_t base = 1; base <= 500000; base += 64) {
            for (uint32_t i = 0; i < 64; ++i)
                for (uint32_t c = 0; c < kChannels; ++c) block[i * kChannels + c] = static_cast<float>((base + i) * 4 + c);
            threaded.push(block, 64);
        }
        done.store(true, std::memory_order_release);
    });
    float previous = -1;
    do {
        uint32_t count = threaded.popLatest(output, kReadFrames);
        for (uint32_t i = 0; i < count; ++i) {
            float first = output[i * kChannels];
            require(first > previous, "frame order regressed");
            previous = first;
            for (uint32_t c = 1; c < kChannels; ++c)
                require(output[i * kChannels + c] == first + c, "torn interleaved frame");
        }
    } while (!done.load(std::memory_order_acquire));
    producer.join();
    std::cout << "audio ring: passed\n";
}
