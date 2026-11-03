#include "AudioIngressTrace.hpp"
#include <cstdlib>
#include <iostream>

namespace {
void require(bool value) { if (!value) std::abort(); }
void fill(float* samples, uint32_t callback, uint32_t offset, unsigned frames) {
    for (unsigned i = 0; i < frames; ++i) audio_probe::encode(callback, offset + i, samples + i * 4);
}
}
int main() {
    audio_probe::IngressTrace trace;
    audio_probe::IngressSnapshot snapshot;
    auto observe = [&](const float* samples, uint32_t frames, uint64_t generation, const audio_probe::TraceCycle& cycle) {
        require(trace.begin(samples, frames, generation, cycle)); trace.end();
    };
    float block[96 * 4];
    audio_probe::TraceCycle cycle{};
    cycle.frames = cycle.nominalFrames = 96;
    cycle.cycle = 1; cycle.output.sample = 96; cycle.output.host = 100;
    fill(block, 1, 0, 96); observe(block, 96, 7, cycle);
    cycle.cycle = 2; cycle.output.sample = 192; cycle.output.host = 200;
    observe(block, 96, 7, cycle); // Same source block really arrived twice.
    require(trace.snapshot(snapshot));
    require(snapshot.generation == 7 && snapshot.calls == 2 && snapshot.verified == 192);
    require(snapshot.nonIncreasing == 96 && snapshot.eventCount == 1);
    const auto& event = snapshot.events[0];
    require(event.previous.cycle == 1 && event.current.cycle == 2 && event.nonIncreasing == 96);
    require(event.current.output.sample == 192 && event.markerCount == audio_probe::kIngressMarkers);
    require(event.markers[0].highest.offset == 95 && event.markers[0].current.offset == 0);
    cycle.cycle = 1;
    require(trace.begin(block, 96, 7, cycle));
    trace.recordRecovery(192, 8); trace.end();
    require(trace.snapshot(snapshot) && snapshot.generation == 8 && snapshot.nonIncreasing == 192);
    require(snapshot.resyncEvents == 1 && snapshot.rejectedFrames == 192 && snapshot.advancedEpochs == 1);
    require(snapshot.hostCycleResets == 1);
    require(trace.begin(block, 96, 8, cycle));
    trace.recordRecovery(96, 10); trace.end();
    require(trace.snapshot(snapshot) && snapshot.generation == 10 && snapshot.nonIncreasing == 288);
    require(snapshot.resyncEvents == 2 && snapshot.rejectedFrames == 288 && snapshot.advancedEpochs == 2);
    require(snapshot.hostCycleResets == 2); // Consecutive reset survives our adopted epoch.
    // A stream restart legitimately reuses callback1: its generation resets
    // order/count state while making old event records inaccessible.
    observe(block, 96, 9, cycle);
    require(trace.snapshot(snapshot));
    require(snapshot.generation == 9 && snapshot.calls == 1 && snapshot.nonIncreasing == 0 && snapshot.eventCount == 0);
    require(snapshot.resyncEvents == 0 && snapshot.rejectedFrames == 0);
    require(snapshot.hostCycleResets == 0); // External generation makes cycle one initial again.
    require(snapshot.events[0].current.cycle == 0); // Unused wire storage is scrubbed.
    // Bounded event storage must not stop counting later failures.
    for (unsigned i = 0; i < 40; ++i) observe(block, 96, 9, cycle);
    require(trace.snapshot(snapshot));
    require(snapshot.nonIncreasing == 40 * 96 && snapshot.eventCount == audio_probe::kIngressEvents);
    block[0] = -1;
    observe(block, 1, 9, cycle);
    for (unsigned i = 0; i < 4; ++i) block[i] = 0;
    observe(block, 1, 9, cycle);
    require(trace.snapshot(snapshot));
    require(snapshot.invalidMarkers == 1 && snapshot.silent == 1 && snapshot.overlaps == 0);
    // A second plugin write between observation and ring publication is also
    // diagnosed; neither the loser nor a snapshot clears the winner's guard.
    require(trace.begin(block, 1, 9, cycle));
    require(!trace.begin(block, 1, 9, cycle));
    require(!trace.snapshot(snapshot));
    require(!trace.begin(block, 1, 9, cycle));
    trace.end();
    require(trace.snapshot(snapshot) && snapshot.overlaps == 2);
    std::cout << "Ingress observer ordering, generation and bounds passed\n";
}
