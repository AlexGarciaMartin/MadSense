#include "AudioRing.hpp"
#include <cstdlib>
#include <iostream>
#include <iterator>
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
        require(ring.push(block.data(), frames, 1) == frames, "normal HAL block must fit");
        uint32_t read = 0;
        while (read < frames) {
            uint32_t count = ring.popLatest(output, kReadFrames, 1);
            require(count == std::min(kReadFrames, frames - read), "wrong block fragment length");
            for (uint32_t i = 0; i < count * kChannels; ++i)
                require(output[i] == block[read * kChannels + i], "normal HAL block lost/reordered samples");
            read += count;
        }
        require(ring.popLatest(output, kReadFrames, 1) == 0, "normal block fully drained");
        require(ring.dropped() == 0, "normal HAL block must never drop its tail");
    }
}
void verifyLateGenerationPublication() {
    using namespace madsense;
    AudioRing ring;
    float oldFrames[64 * kChannels], newFrames[64 * kChannels], output[kReadFrames * kChannels];
    std::fill(std::begin(oldFrames), std::end(oldFrames), -1.0f);
    for (uint32_t i = 0; i < 64 * kChannels; ++i) newFrames[i] = static_cast<float>(i);
    uint64_t generation = 1;
    const uint64_t capturedBeforeTransition = generation; // DoIO entered, not yet published.
    ring.discard();
    ++generation;
    require(ring.push(oldFrames, 64, capturedBeforeTransition) == 64, "late old block publication");
    require(ring.push(newFrames, 64, generation) == 64, "new generation publication");
    require(ring.popLatest(output, kReadFrames, generation) == 64, "old generation frames leaked");
    for (uint32_t i = 0; i < 64 * kChannels; ++i)
        require(output[i] == newFrames[i], "old PCM crossed generation after discard");
    require(ring.dropped() == 64, "stale generation discard accounting");
    require(ring.popLatest(output, kReadFrames, generation) == 0, "new generation completely drained");
}

void verifyConcurrentGenerationChanges() {
    using namespace madsense;
    AudioRing ring;
    std::atomic<uint64_t> generation{1};
    std::atomic<bool> done{false};
    std::atomic<bool> receivedFirst{false};
    std::atomic<uint32_t> progress{0};
    std::thread producer([&] {
        float block[64 * kChannels];
        for (uint32_t iteration = 0; iteration < 100000; ++iteration) {
            const uint64_t captured = generation.load(std::memory_order_acquire);
            for (uint32_t i = 0; i < 64; ++i)
                for (uint32_t channel = 0; channel < kChannels; ++channel)
                    block[i * kChannels + channel] = static_cast<float>(captured * 8 + channel);
            if (iteration % 7 == 0) std::this_thread::yield();
            ring.push(block, 64, captured);
            progress.store(iteration + 1, std::memory_order_release);
            if (iteration == 0)
                while (!receivedFirst.load(std::memory_order_acquire)) std::this_thread::yield();
        }
        done.store(true, std::memory_order_release);
    });
    float output[kReadFrames * kChannels];
    uint64_t current = 1;
    uint32_t nextTransition = 32, transitions = 0;
    uint64_t received = 0;
    do {
        // Model the property thread: only the consumer changes generations;
        // the producer can be anywhere between capturing a tag and publishing.
        if (progress.load(std::memory_order_acquire) >= nextTransition) {
            ring.discard();
            generation.store(++current, std::memory_order_release);
            nextTransition += 32;
            ++transitions;
        }
        const uint32_t count = ring.popLatest(output, kReadFrames, current);
        for (uint32_t frame = 0; frame < count; ++frame)
            for (uint32_t channel = 0; channel < kChannels; ++channel)
                require(output[frame * kChannels + channel] == static_cast<float>(current * 8 + channel),
                    "concurrent old generation or torn interleaved frame");
        received += count;
        if (count) receivedFirst.store(true, std::memory_order_release);
    } while (!done.load(std::memory_order_acquire));
    producer.join();
    require(transitions > 0 && received > 0, "generation stress did not transfer or transition");
}

int main() {
    using namespace madsense;
    verifyBlockContinuity(512);
    verifyBlockContinuity(1024);
    verifyLateGenerationPublication();
    verifyConcurrentGenerationChanges();
    AudioRing ring;
    std::vector<float> input(1536 * kChannels);
    for (size_t i = 0; i < input.size(); ++i) input[i] = static_cast<float>(i);
    float output[kReadFrames * kChannels]{};
    require(ring.push(input.data(), 1536, 1) == 1536, "push failed");
    require(ring.popLatest(output, 0, 1) == 0 && ring.dropped() == 0, "zero capacity must not consume or trim");
    require(ring.popLatest(output, kReadFrames, 1) == kReadFrames, "read should keep 10 ms");
    require(output[0] == 1056 * kChannels && output[1919] == 6143, "stale backlog must retain newest frames");
    require(ring.dropped() == 1056, "backlog drop accounting");
    require(ring.popLatest(output, kReadFrames, 1) == 0, "empty read");
    std::vector<float> tooMuch((kRingFrames + 20) * kChannels, 1);
    ring.push(tooMuch.data(), kRingFrames + 20, 1);
    require(ring.popLatest(output, kReadFrames, 1) == 0, "overrun must discard stale effects");
    require(ring.dropped() == 1056 + kRingFrames + 20, "overflow drop accounting");
    ring.push(input.data(), 100, 1);
    require(ring.popLatest(output, kReadFrames, 1) == 100 && output[3] == 3, "recover after overrun");
    ring.push(input.data(), 100, 1);
    ring.discard();
    require(ring.popLatest(output, kReadFrames, 2) == 0, "owner switch flush");

    // Concurrent stress verifies that complete frames stay in order and channel
    // relationships cannot tear even while the queue wraps and drops backlogs.
    AudioRing threaded;
    std::atomic<bool> done{false};
    std::thread producer([&] {
        float block[64 * kChannels];
        for (uint32_t base = 1; base <= 500000; base += 64) {
            for (uint32_t i = 0; i < 64; ++i)
                for (uint32_t c = 0; c < kChannels; ++c) block[i * kChannels + c] = static_cast<float>((base + i) * 4 + c);
            threaded.push(block, 64, 1);
        }
        done.store(true, std::memory_order_release);
    });
    float previous = -1;
    do {
        uint32_t count = threaded.popLatest(output, kReadFrames, 1);
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
