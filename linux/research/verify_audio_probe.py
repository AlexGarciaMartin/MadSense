#!/usr/bin/env python3
"""Validate only the controlled offline production-pipeline capture."""
import argparse
import binascii
import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio_capture import expected_native_haptics, inspect, speaker_reference, waveform_metrics


def verify(directory, output, library=None):
    scope = json.loads((directory / "scope.json").read_text())
    if (scope.get("scope") != "offline-production-pipeline-only" or scope.get("physicalDevices") is not False
            or scope.get("uhidExercised") is not False or scope.get("timestampsAreLatencyEvidence") is not False):
        raise ValueError("Only the declared offline probe is supported")
    result = inspect(directory / "uhid-format-events.jsonl", output,
                     directory / "rear-source-48000.pcm", library)
    if result["decodeResult"] != "passed":
        raise ValueError(result.get("reason"))
    reference = speaker_reference((directory / "speaker-source-512.pcm").read_bytes())
    encoder = (directory / "encoder-input-480.pcm").read_bytes()
    if len(reference) != len(encoder):
        raise ValueError("Production resampler returned an unexpected frame count")
    maximum_error = max(abs(a[0] - b[0]) for a, b in zip(struct.iter_unpack("<h", reference), struct.iter_unpack("<h", encoder)))
    if maximum_error > 1:
        raise ValueError("Production resampler differs from independent rational reference")
    decoded = (output / "session-1-speaker.s16le-stereo-48000.pcm").read_bytes()
    waveform = waveform_metrics(reference, decoded)
    expected_haptics = expected_native_haptics((directory / "rear-source-48000.pcm").read_bytes())
    captured_haptics = (output / "captured-native-haptics.s8-stereo-3000.pcm").read_bytes()
    result.update(scope="offline-production-pipeline-only", waveformCompared=True,
                  resamplerMaximumErrorLsb=maximum_error, waveform=waveform,
                  hapticsByteExact=expected_haptics == captured_haptics,
                  validationPassed=waveform["passed"] and expected_haptics == captured_haptics
                      and result["sequenceDiscontinuities"] == 0)
    result["captureHashes"] = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                              for name in ("scope.json", "speaker-source-512.pcm", "encoder-input-480.pcm", "rear-source-48000.pcm")}
    (output / "validation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def verify_negatives(directory, output, library=None):
    """Mutate captured production payloads while retaining valid transport CRCs."""
    output.mkdir(parents=True, exist_ok=False)
    original = [json.loads(line) for line in (directory / "uhid-format-events.jsonl").read_text().splitlines()]
    evidence = []
    for case in ("duplicate_opus", "reordered_opus", "corrupt_haptics", "swapped_channels", "corrupt_crc"):
        capture = output / case / "capture"
        capture.mkdir(parents=True)
        for name in ("scope.json", "speaker-source-512.pcm", "encoder-input-480.pcm", "rear-source-48000.pcm"):
            shutil.copy2(directory / name, capture / name)
        rows = [dict(row) for row in original]
        frames = [i for i, row in enumerate(rows) if bytes.fromhex(row["hex"])[0] == 0x36]
        if len(frames) < 64:
            raise ValueError("Negative probes require the complete controlled capture")
        def mutate(index, apply, crc=True):
            report = bytearray.fromhex(rows[frames[index]]["hex"])
            apply(report)
            if crc:
                struct.pack_into("<I", report, len(report) - 4, binascii.crc32(b"\xa2" + report[:-4]))
            rows[frames[index]]["hex"] = report.hex()
        if case == "duplicate_opus":
            payload = bytes.fromhex(rows[frames[20]]["hex"])[144:344]
            mutate(30, lambda report: report.__setitem__(slice(144, 344), payload))
        elif case == "reordered_opus":
            a = bytes.fromhex(rows[frames[30]]["hex"])[144:344]
            b = bytes.fromhex(rows[frames[60]]["hex"])[144:344]
            mutate(30, lambda report: report.__setitem__(slice(144, 344), b))
            mutate(60, lambda report: report.__setitem__(slice(144, 344), a))
        elif case == "corrupt_haptics":
            mutate(30, lambda report: report.__setitem__(78, report[78] ^ 1))
        elif case == "corrupt_crc":
            mutate(30, lambda report: report.__setitem__(170, report[170] ^ 1), crc=False)
        else:
            for name in ("speaker-source-512.pcm", "encoder-input-480.pcm"):
                path = capture / name
                path.write_bytes(b"".join(struct.pack("<hh", right, left)
                                          for left, right in struct.iter_unpack("<hh", path.read_bytes())))
        (capture / "uhid-format-events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        try:
            result = verify(capture, output / case / "analysis", library)
        except ValueError as error:
            if case != "corrupt_crc" or "CRC mismatch" not in str(error):
                raise
            evidence.append(dict(case=case, rejected=True, reason=str(error)))
            continue
        if case == "corrupt_crc" or result["validationPassed"] or result["sequenceDiscontinuities"]:
            raise AssertionError("Controlled corruption was not rejected by payload validation: " + case)
        if case == "corrupt_haptics":
            if result["hapticsByteExact"] or not result["waveform"]["passed"]:
                raise AssertionError("Haptics corruption was not isolated")
        elif result["waveform"]["passed"] or not result["hapticsByteExact"]:
            raise AssertionError("Speaker corruption was not isolated")
        evidence.append(dict(case=case, rejected=True, waveformPassed=result["waveform"]["passed"],
                             hapticsByteExact=result["hapticsByteExact"]))
    (output / "result.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--opus-library")
    parser.add_argument("--negative-checks", action="store_true")
    args = parser.parse_args()
    value = verify(args.capture, args.output, args.opus_library)
    if args.negative_checks:
        value["negativeChecks"] = verify_negatives(args.capture, args.output / "negative-checks", args.opus_library)
    print(json.dumps({key: value[key] for key in ("scope", "validationPassed", "hapticsByteExact", "resamplerMaximumErrorLsb", "decoderVersion")}))
    raise SystemExit(0 if value["validationPassed"] else 1)
