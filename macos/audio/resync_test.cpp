#include "AudioResyncPolicy.hpp"
#include "AudioRecoveryRead.hpp"
#include "AudioRing.hpp"
#include <atomic>
#include <cstdlib>
#include <iostream>
#include <thread>

namespace {
void require(bool value) { if (!value) std::abort(); }
void fill(float* samples, unsigned frames, uint64_t generation, uint32_t sequence) {
    for (unsigned i = 0; i < frames; ++i) {
        samples[i * 4] = static_cast<float>(generation);
        samples[i * 4 + 1] = static_cast<float>(sequence + i);
        samples[i * 4 + 2] = .25f; samples[i * 4 + 3] = -.25f;
    }
}
}
int main() {
    madsense::ResyncPolicy policy;
    require(!policy.reject(1, 1)); require(!policy.reject(1, 1));
    require(!policy.reject(1, 0)); require(!policy.reject(1, 2));
    require(policy.reject(1, 1)); policy.adoptGeneration(2);
    require(policy.reject(2, 1)); policy.adoptGeneration(3);
    require(!policy.reject(3, 0)); require(policy.reject(3, 1));
    policy.adoptGeneration(4);
    require(!policy.reject(4, 2)); require(policy.reject(4, 1));
    require(!policy.reject(8, 1)); // A real stream/owner change starts afresh.
    require(!policy.reject(8, 1)); require(!policy.reject(8, 0));
    std::atomic<uint64_t> epoch{5}; uint64_t expected = 4;
    require(!epoch.compare_exchange_strong(expected, 5) && epoch.load() == 5);

    madsense::AudioRing late;
    float block[96 * 4], output[480 * 4];
    // Regression for consecutive host resets. No queue trim can mask an
    // accepted second cycle-one operation in this sub-threshold sequence.
    madsense::ResyncPolicy consecutivePolicy;
    madsense::AudioRing consecutive;
    uint64_t recoveryGeneration = 1;
    uint32_t sequence = 0;
    for (uint64_t cycle : {2u, 1u, 1u, 2u}) {
        if (consecutivePolicy.reject(recoveryGeneration, cycle)) {
            consecutive.rejectFrames(96);
            consecutivePolicy.adoptGeneration(++recoveryGeneration);
        } else {
            fill(block, 96, recoveryGeneration, sequence);
            consecutive.push(block, 96, recoveryGeneration);
        }
        sequence += 96;
    }
    require(recoveryGeneration == 3 && consecutive.popLatest(output, 480, recoveryGeneration) == 96);
    require(consecutive.dropped() == 288);
    for (unsigned i = 0; i < 96; ++i) require(output[i * 4] == 3 && output[i * 4 + 1] == 288 + i);

    fill(block, 64, 1, 0); late.push(block, 64, 1);
    // A producer with an old captured tag publishes after recovery. It cannot
    // populate the new epoch, and every rejected/filtered frame is counted.
    late.rejectFrames(128);
    fill(block, 64, 1, 64); late.push(block, 64, 1);
    fill(block, 64, 2, 128); late.push(block, 64, 2);
    require(late.popLatest(output, 480, 2) == 64);
    require(late.dropped() == 256);
    for (unsigned i = 0; i < 64; ++i) require(output[i * 4] == 2 && output[i * 4 + 1] == 128 + i);

    // Deterministic property-read race: capture G, recover to G+1, then finish
    // copying old G and acknowledge the new drop count. The final commit must
    // return no old candidate and charge it exactly once.
    madsense::AudioRing concurrentRead;
    epoch.store(1);
    fill(block, 64, 1, 0); concurrentRead.push(block, 64, 1);
    const auto captured = epoch.load();
    expected = captured;
    require(epoch.compare_exchange_strong(expected, captured + 1));
    concurrentRead.rejectFrames(128);
    uint32_t candidateFrames = concurrentRead.popLatest(output, 480, captured);
    uint64_t dropSnapshot = 0;
    require(candidateFrames == 64);
    require(!madsense::finishCapturedRead(concurrentRead, epoch, captured, candidateFrames, dropSnapshot));
    require(candidateFrames == 0 && dropSnapshot == 192);
    fill(block, 64, 2, 64); concurrentRead.push(block, 64, 2);
    candidateFrames = concurrentRead.popLatest(output, 480, 2);
    require(madsense::finishCapturedRead(concurrentRead, epoch, 2, candidateFrames, dropSnapshot));
    require(candidateFrames == 64 && dropSnapshot == 192 && output[0] == 2);

    madsense::AudioRing ring;
    epoch.store(1);
    std::atomic<bool> done{false}, started{false};
    uint64_t produced = 0, received = 0;
    std::thread writer([&] {
        float pcm[96 * 4];
        for (uint32_t i = 0; i < 10000; ++i) {
            produced += 96;
            if (i && i % 17 == 0) {
                epoch.fetch_add(1, std::memory_order_acq_rel);
                ring.rejectFrames(96);
            } else {
                const auto generation = epoch.load();
                fill(pcm, 96, generation, i * 96);
                ring.push(pcm, 96, generation);
            }
            if (!i) while (!started.load()) std::this_thread::yield();
            if (i % 31 == 0) std::this_thread::yield();
        }
        done.store(true, std::memory_order_release);
    });
    float previous = -1;
    while (true) {
        const bool finished = done.load(std::memory_order_acquire);
        const auto generation = epoch.load(std::memory_order_acquire);
        uint32_t count = ring.popLatest(output, 480, generation);
        uint64_t drops = 0;
        madsense::finishCapturedRead(ring, epoch, generation, count, drops);
        for (unsigned i = 0; i < count; ++i) {
            require(output[i * 4] == generation && output[i * 4 + 1] > previous);
            require(output[i * 4 + 2] == .25f && output[i * 4 + 3] == -.25f);
            previous = output[i * 4 + 1];
        }
        received += count;
        if (count) started.store(true);
        if (finished && !count) break;
        std::this_thread::yield();
    }
    writer.join();
    require(received > 0 && ring.dropped() > 0 && received + ring.dropped() == produced);
    std::cout << "Recovery epoch race and exact frame accounting passed\n";
}
