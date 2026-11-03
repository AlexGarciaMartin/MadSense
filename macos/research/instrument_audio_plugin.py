#!/usr/bin/env python3
"""Generate a research-only HAL source copy; fail if production patch sites drift."""
import argparse
import hashlib
import json
import pathlib


def instrument(source: str, timestamp_period: int = 256) -> str:
    if timestamp_period not in (256, 16384):
        raise ValueError('Only the explicit baseline and SDK-compliant research variants are supported')
    def replace(old: str, new: str) -> None:
        nonlocal source
        if source.count(old) != 1:
            raise ValueError(f"Expected exactly one production patch site: {old[:90]!r}")
        source = source.replace(old, new)

    replace('#include "AudioProtocol.hpp"', '#include "AudioProtocol.hpp"\n#include "AudioIngressTrace.hpp"')
    replace('AudioRing ring;', 'AudioRing ring;\naudio_probe::IngressTrace ingressTrace;\n'
            f'constexpr UInt32 kResearchTimestampPeriod = {timestamp_period};')
    replace('copyValue<UInt32>(kTimestampPeriodFrames, capacity, written, out)',
            'copyValue<UInt32>(kResearchTimestampPeriod, capacity, written, out)')
    replace('ticksPerFrame * kTimestampPeriodFrames', 'ticksPerFrame * kResearchTimestampPeriod')
    replace('*sample = periods * kTimestampPeriodFrames;', '*sample = periods * kResearchTimestampPeriod;')
    replace('const AudioServerPlugInCustomPropertyInfo customProperties[] = {',
            'const AudioServerPlugInCustomPropertyInfo customProperties[] = {\n'
            '    {audio_probe::kIngressProperty, kAudioServerPlugInCustomPropertyDataTypeCFPropertyList, '
            'kAudioServerPlugInCustomPropertyDataTypeNone},')
    replace('        case kRead: *size = sizeof(CFPropertyListRef); return noErr;',
            '        case audio_probe::kIngressProperty:\n'
            '        case kRead: *size = sizeof(CFPropertyListRef); return noErr;')
    replace('        case kRead: {', '''        case audio_probe::kIngressProperty: {
            std::lock_guard<std::mutex> lock(stateMutex);
            if (!isOwner(pid) || running.load(std::memory_order_acquire))
                return kAudioHardwareIllegalOperationError;
            audio_probe::IngressSnapshot snapshot;
            if (!ingressTrace.snapshot(snapshot)) return kAudioHardwareIllegalOperationError;
            if (snapshot.generation != pcmGeneration.load(std::memory_order_acquire)) snapshot = {};
            CFDataRef data = CFDataCreate(kCFAllocatorDefault,
                reinterpret_cast<const UInt8*>(&snapshot), sizeof(snapshot));
            if (!data) return kAudioHardwareUnspecifiedError;
            return copyValue(data, capacity, written, out);
        }
        case kRead: {''')
    replace('UInt32 frames, const AudioServerPlugInIOCycleInfo*, void* main, void*) {',
            'UInt32 frames, const AudioServerPlugInIOCycleInfo* cycle, void* main, void*) {')
    replace('AudioObjectID stream, UInt32, UInt32 op,',
            'AudioObjectID stream, UInt32 client, UInt32 op,')
    replace('''    if (running.load(std::memory_order_acquire) && mach_absolute_time() < leaseDeadline.load(std::memory_order_acquire))
        ring.push(static_cast<const float*>(main), frames, generation);''',
            '''    if (running.load(std::memory_order_acquire) && mach_absolute_time() < leaseDeadline.load(std::memory_order_acquire)) {
        audio_probe::TraceCycle trace{};
        trace.entered = mach_absolute_time(); trace.frames = frames;
        trace.operation = op; trace.client = client;
        if (cycle) {
            trace.cycle = cycle->mIOCycleCounter; trace.nominalFrames = cycle->mNominalIOBufferFrameSize;
            auto stamp = [](const AudioTimeStamp& time) -> audio_probe::TraceTime {
                return {time.mSampleTime, time.mHostTime, time.mFlags, 0};
            };
            trace.current = stamp(cycle->mCurrentTime);
            trace.input = stamp(cycle->mInputTime);
            trace.output = stamp(cycle->mOutputTime);
        }
        const bool observed = ingressTrace.begin(static_cast<const float*>(main), frames, generation, trace);
        ring.push(static_cast<const float*>(main), frames, generation);
        if (observed) ingressTrace.end();
    }''')
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=pathlib.Path)
    parser.add_argument('output', type=pathlib.Path)
    parser.add_argument('--timestamp-period', type=int, choices=(256, 16384), default=256)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    source = raw.decode('utf-8').replace('\r\n', '\n')
    result = instrument(source, args.timestamp_period).encode('utf-8')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(result)
    print(json.dumps({'production_source_sha256': hashlib.sha256(raw).hexdigest(),
                      'instrumented_source_sha256': hashlib.sha256(result).hexdigest(),
                      'timestamp_period_frames': args.timestamp_period,
                      'production_source_changed': False}))


if __name__ == '__main__':
    main()
