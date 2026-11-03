#!/usr/bin/env python3
"""Offline inspection of captured synthetic UHID audio; no device access.

libopus API: https://opus-codec.org/docs/opus_api-1.5/group__opus__decoder.html
The decoder consumes each observed packet in order, with no loss concealment.
This measures software payloads, never radio, speaker or actuator behavior.
"""
import argparse
import binascii
import ctypes
import ctypes.util
import hashlib
import json
import math
from pathlib import Path
import struct


def audio_packet(report):
    if not report or report[0] not in (0x35, 0x36):
        return None
    expected = 334 if report[0] == 0x35 else 398
    if len(report) != expected:
        raise ValueError("Truncated or oversized audio report")
    if int.from_bytes(report[-4:], "little") != binascii.crc32(b"\xa2" + report[:-4]):
        raise ValueError("Audio report CRC mismatch")
    if report[2:5] != b"\x91\x07\xfe":
        raise ValueError("Unexpected audio session header")
    route_offset = 11 if report[0] == 0x35 else 142
    if report[route_offset] not in (0x93, 0x96) or report[route_offset + 1] != 200:
        raise ValueError("Unexpected audio route or Opus length")
    haptics = None
    if report[0] == 0x36:
        if report[11:13] != b"\x90\x3f" or report[76:78] != b"\x92\x40":
            raise ValueError("Unexpected combined state/haptics layout")
        haptics = report[78:142]
    return dict(route="speaker" if report[route_offset] == 0x93 else "headset",
                counter=report[10], sequence=report[1] >> 4,
                opus=report[route_offset + 2:route_offset + 202], haptics=haptics)


class OpusDecoder:
    def __init__(self, library=None):
        path = library or ctypes.util.find_library("opus")
        if not path:
            raise RuntimeError("Independent libopus is required; install libopus0 or pass --opus-library")
        self.lib = ctypes.CDLL(str(path))
        resolved = Path(path)
        if not resolved.is_file() and Path("/proc/self/maps").is_file():
            candidates = set()
            for line in Path("/proc/self/maps").read_text().splitlines():
                fields = line.split(None, 5)
                if len(fields) != 6 or not fields[5].startswith("/"):
                    continue
                candidate = Path(fields[5])
                if candidate.name == resolved.name or candidate.name.startswith(resolved.name + "."):
                    candidates.add(candidate)
            if len(candidates) == 1:
                resolved = candidates.pop()
        self.library_path = str(resolved)
        self.library_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest() if resolved.is_file() else None
        self.lib.opus_decoder_create.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
        self.lib.opus_decoder_create.restype = ctypes.c_void_p
        self.lib.opus_decode.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int32,
                                        ctypes.POINTER(ctypes.c_int16), ctypes.c_int, ctypes.c_int]
        self.lib.opus_decode.restype = ctypes.c_int
        self.lib.opus_decoder_destroy.argtypes = [ctypes.c_void_p]
        self.lib.opus_decoder_destroy.restype = None
        self.lib.opus_get_version_string.argtypes = []
        self.lib.opus_get_version_string.restype = ctypes.c_char_p
        error = ctypes.c_int()
        self.state = self.lib.opus_decoder_create(48000, 2, ctypes.byref(error))
        if not self.state or error.value:
            raise RuntimeError("opus_decoder_create failed: " + str(error.value))
        self.version = self.lib.opus_get_version_string().decode("ascii")

    def decode(self, data):
        if len(data) != 200 or not self.state:
            raise ValueError("Expected one 200-byte packet and an open decoder")
        packet = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        pcm = (ctypes.c_int16 * (5760 * 2))()
        frames = self.lib.opus_decode(self.state, packet, len(data), pcm, 5760, 0)
        if frames != 480:
            raise ValueError(f"Expected 480 decoded stereo frames; libopus returned {frames}")
        return struct.pack("<" + "h" * (frames * 2), *pcm[:frames * 2])

    def close(self):
        if self.state:
            self.lib.opus_decoder_destroy(self.state)
            self.state = None


def expected_native_haptics(pcm):
    """Reference arithmetic over continuous S16LE stereo, independent of queues."""
    if len(pcm) % 4:
        raise ValueError("Reference PCM ends inside a stereo frame")
    output = bytearray()
    for start in range(0, len(pcm) - 63, 64):
        samples = struct.unpack_from("<32h", pcm, start)
        for channel in (0, 1):
            value = round(sum(samples[channel::2]) / 4096)
            output.append(max(-128, min(127, value)) & 255)
    return bytes(output)


def haptic_matches(blocks, reference):
    """Keep ambiguous matches visible; only unique positions support ordering."""
    rows, cursor, unmatched, ambiguous, backwards, skipped = [], 0, 0, 0, 0, 0
    for index, block in enumerate(blocks):
        if len(block) != 64:
            raise ValueError("Expected a complete 32-frame stereo haptics block")
        if not any(block):
            rows.append(dict(packet=index, silence=True))
            continue
        candidates, start = [], 0
        while (position := reference.find(block, start)) >= 0:
            if position % 2 == 0:
                candidates.append(position // 2)
            start = position + 1
        row = dict(packet=index, candidateFrames=candidates)
        if not candidates:
            unmatched += 1
        elif len(candidates) != 1:
            ambiguous += 1
        elif candidates[0] < cursor:
            backwards += 1
        else:
            row["skippedReferenceFrames"] = candidates[0] - cursor
            skipped += row["skippedReferenceFrames"]
            cursor = candidates[0] + 32
        rows.append(row)
    return dict(blocks=rows, unmatchedNonzeroBlocks=unmatched, ambiguousBlocks=ambiguous,
                repeatedOrReorderedBlocks=backwards, skippedReferenceFrames=skipped,
                exactUniqueMonotonic=bool(blocks) and not (unmatched or ambiguous or backwards)
                    and any(not row.get("silence") for row in rows))


def speaker_reference(pcm):
    """Exact rational 512→480 stereo reference, with truncation toward zero."""
    if len(pcm) % 2048:
        raise ValueError("Ordered speaker reference requires complete 512-frame blocks")
    result = bytearray()
    for offset in range(0, len(pcm), 2048):
        source = struct.unpack_from("<1024h", pcm, offset)
        target = []
        for frame in range(480):
            index, remainder = divmod(frame * 511, 479)
            for channel in (0, 1):
                value = source[index * 2 + channel] * (479 - remainder)
                if remainder:
                    value += source[(index + 1) * 2 + channel] * remainder
                target.append((1 if value >= 0 else -1) * (abs(value) // 479))
        result.extend(struct.pack("<960h", *target))
    return bytes(result)


def waveform_metrics(reference, decoded, max_lag=960):
    """Compare an explicitly ordered reference after one global codec delay.

    This cannot align a live capture containing dropped/replaced source blocks.
    Per-window errors remain visible even when global correlation looks good.
    """
    if len(reference) != len(decoded) or len(reference) % 4 or len(reference) < 3840:
        raise ValueError("Equal complete stereo streams of at least 960 frames required")
    expected = list(struct.iter_unpack("<hh", reference))
    actual = list(struct.iter_unpack("<hh", decoded))
    if max_lag < 0 or max_lag >= len(expected) // 2:
        raise ValueError("Invalid codec-delay search bound")

    def score(lag, step):
        xy = xx = yy = 0
        for i in range(0, len(expected) - max_lag, step):
            for ch in (0, 1):
                x, y = expected[i][ch], actual[i + lag][ch]
                xy += x * y; xx += x * x; yy += y * y
        return xy / math.sqrt(xx * yy) if xx and yy else -1

    lag = max(range(max_lag + 1), key=lambda candidate: score(candidate, 16))
    windows = []
    for start in range(0, len(expected) - lag, 480):
        end = min(start + 480, len(expected) - lag)
        for channel in (0, 1):
            x = [expected[i][channel] for i in range(start, end)]
            y = [actual[i + lag][channel] for i in range(start, end)]
            energy = sum(value * value for value in x)
            if energy < len(x) * 100 * 100:
                continue  # Silence has no stable correlation denominator.
            actual_energy = sum(value * value for value in y)
            correlation = sum(a*b for a, b in zip(x, y)) / math.sqrt(energy * actual_energy) if actual_energy else 0
            windows.append(dict(referenceFrame=start, channel=channel, correlation=correlation,
                                normalizedRmse=math.sqrt(sum((a-b)**2 for a, b in zip(x, y)) / energy)))
    minimum = min((row["correlation"] for row in windows), default=-1)
    maximum = max((row["normalizedRmse"] for row in windows), default=math.inf)
    return dict(codecAlignmentFrames=lag, alignmentIsPhysicalLatency=False,
                globalCorrelation=score(lag, 1), minimumWindowCorrelation=minimum,
                maximumWindowNormalizedRmse=maximum, windows=windows,
                passed=bool(windows) and minimum >= .90 and maximum <= .45)


def inspect(trace, output, haptics_source=None, library=None):
    output.mkdir(parents=True, exist_ok=False)
    decoders, streams, blocks, rows = {}, {}, [], []
    last_ns, last_counter, session = None, None, 0
    result = dict(scope="synthetic-software-payload-only", physicalValidated=False,
                  waveformCompared=False, sequenceDiscontinuities=0,
                  traceSha256=hashlib.sha256(trace.read_bytes()).hexdigest())
    try:
        for line_number, line in enumerate(trace.read_text().splitlines(), 1):
            event = json.loads(line)
            timestamp = event["monotonicNs"]
            if last_ns is not None and timestamp < last_ns:
                raise ValueError("Capture timestamps move backwards")
            last_ns = timestamp
            if event.get("kind") != "output":
                continue
            report = bytes.fromhex(event["hex"])
            if report and report[0] == 0x32 and report[2:4] == b"\x90\x3f":
                if len(report) != 142 or int.from_bytes(report[-4:], "little") != binascii.crc32(b"\xa2" + report[:-4]):
                    raise ValueError("Invalid stream-prime capture")
                for decoder in decoders.values():
                    decoder.close()
                decoders.clear(); last_counter = None; session += 1
            packet = audio_packet(report)
            if packet is None:
                continue
            if last_counter is not None and packet["counter"] != (last_counter + 1) % 256:
                result["sequenceDiscontinuities"] += 1
            last_counter = packet["counter"]
            route = packet["route"]
            if route not in decoders:
                decoders[route] = OpusDecoder(library)
                result["decoderVersion"] = decoders[route].version
                result["decoderLibrary"] = decoders[route].library_path
                result["decoderLibrarySha256"] = decoders[route].library_sha256
            pcm = decoders[route].decode(packet["opus"])
            key = f"session-{session}-{route}"
            streams.setdefault(key, bytearray()).extend(pcm)
            samples = struct.unpack("<960h", pcm)
            rows.append(dict(line=line_number, monotonicNs=timestamp, session=session, route=route,
                             packetCounter=packet["counter"], opusSha256=hashlib.sha256(packet["opus"]).hexdigest(),
                             decodedRms=math.sqrt(sum(x*x for x in samples) / len(samples))))
            if packet["haptics"] is not None:
                blocks.append(packet["haptics"])
        if not rows:
            raise ValueError("No captured audio packets")
        for name, pcm in streams.items():
            (output / (name + ".s16le-stereo-48000.pcm")).write_bytes(pcm)
        result.update(audioPackets=len(rows), decodedFrames=480 * len(rows), packets=rows)
        if haptics_source:
            reference = expected_native_haptics(haptics_source.read_bytes())
            (output / "expected-native-haptics.s8-stereo-3000.pcm").write_bytes(reference)
            (output / "captured-native-haptics.s8-stereo-3000.pcm").write_bytes(b"".join(blocks))
            result["haptics"] = haptic_matches(blocks, reference)
        result["decodeResult"] = "passed"
    except BaseException as error:
        result.update(decodeResult="failed", reason=f"{type(error).__name__}: {error}")
    finally:
        for decoder in decoders.values():
            decoder.close()
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--haptics-source", type=Path)
    parser.add_argument("--opus-library")
    args = parser.parse_args()
    answer = inspect(args.trace, args.output, args.haptics_source, args.opus_library)
    print(json.dumps({key: value for key, value in answer.items() if key != "packets"}))
    raise SystemExit(0 if answer["decodeResult"] == "passed" else 1)
