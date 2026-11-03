"""Exact quad ingress comparisons; no transport, hardware or latency inference."""
import hashlib
import json
from pathlib import Path
import random
import struct

CHANNELS = ("FL", "FR", "RL", "RR")
MAX_BYTES = 16 * 1024 * 1024

def require(condition, message):
    if not condition: raise RuntimeError(message)

def digest(value): return hashlib.sha256(value).hexdigest()

def marked_source(seed, frames=48000):
    require(type(seed) is int and type(frames) is int and 64 <= frames <= 48000, "Bounded deterministic source required")
    rng = random.Random(seed)
    return b"".join(struct.pack("<hhhh", *(rng.choice((-1, 1))*4*rng.randrange(250, 2001)
        for _ in CHANNELS)) for _ in range(frames))

def expected_gain(source, divisors):
    require(divisors in ((1,1,1,1), (2,4,1,1)) and len(source) % 8 == 0, "Unsupported exact gain control")
    output = bytearray()
    for frame in struct.iter_unpack("<hhhh", source):
        require(all(sample % divisor == 0 for sample, divisor in zip(frame, divisors)), "Gain reference would require rounding")
        output.extend(struct.pack("<hhhh", *(sample // divisor for sample, divisor in zip(frame, divisors))))
    return bytes(output)

def load_capture(directory):
    directory = Path(directory)
    value = json.loads((directory / "pcm-ingress.json").read_text())
    require(value.get("schema") == 1 and value.get("scope") == "research-copy-usb-quad-ingress"
        and value.get("productionBinary") is False and value.get("clean") is True,
        "PCM capture scope or clean shutdown is missing")
    packets = value["packets"]
    require(isinstance(packets, list) and len(packets) <= 65536 and value["attempts"] == len(packets)
        and all(value.get(name) == 0 for name in ("capacityRejected", "concurrentRejected", "malformedRejected")),
        "PCM recorder rejected or lost input")
    chunks = value["chunks"]; require(isinstance(chunks, list) and 0 < len(chunks) <= 4, "PCM capture has no bounded chunks")
    raw = bytearray()
    for index, row in enumerate(chunks):
        require(row["name"] == f"pcm-ingress-{index:03}.bin" and row["offset"] == len(raw), "PCM chunk identity/order differs")
        path = directory / row["name"]
        require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 4*1024*1024, "PCM chunk is not bounded regular data")
        data = path.read_bytes()
        require(len(data) == row["length"] and digest(data) == row["sha256"], "PCM chunk bytes differ")
        raw.extend(data)
    require(len(raw) == value["bytes"] <= MAX_BYTES and len(raw) % 8 == 0, "PCM total bytes differ")
    offset, timestamp = 0, -1
    for packet in packets:
        require(type(packet.get("offset")) is int and packet["offset"] == offset
            and type(packet.get("length")) is int and packet["length"] > 0 and packet["length"] % 8 == 0
            and type(packet.get("timestamp")) is int and packet["timestamp"] >= timestamp,
            "PCM packet offsets/timestamps are incomplete or reordered")
        offset += packet["length"]; timestamp = packet["timestamp"]
    require(offset == len(raw) and type(value["timestampFrequency"]) is int and value["timestampFrequency"] > 0,
        "PCM packets do not cover captured bytes")
    return value, bytes(raw)

def boundary_offset(capture, boundary):
    count = boundary.get("packets")
    require(type(count) is int and 0 <= count <= len(capture["packets"]), "Invalid PCM bookmark packet count")
    offset = 0 if count == 0 else capture["packets"][count-1]["offset"] + capture["packets"][count-1]["length"]
    require(boundary.get("bytes") == offset and type(boundary.get("timestamp")) is int, "PCM bookmark does not identify a complete prefix")
    require(boundary["timestamp"] >= 0 and (count == 0 or boundary["timestamp"] >= capture["packets"][count-1]["timestamp"]),
        "PCM bookmark predates its published prefix")
    # A writer may already have sampled the next packet timestamp before Mark
    # reads the published count. That next timestamp is not a safe upper bound.
    return offset

def verify_phase(capture, raw, source, start, end, divisors=(1,1,1,1)):
    begin, finish = boundary_offset(capture, start), boundary_offset(capture, end)
    require(begin < finish and start["timestamp"] < end["timestamp"], "PCM phase bookmarks are reversed or empty")
    window = raw[begin:finish]; expected = expected_gain(source, divisors)
    require(len(expected) >= 512, "PCM marker requires 64 complete quad frames")
    marker = expected[:512]; positions = []; cursor = 0
    while (found := window.find(marker, cursor)) >= 0:
        if found % 8 == 0: positions.append(found)
        cursor = found + 1
    require(len(positions) == 1, "PCM phase lacks one unique complete four-channel marker")
    position = positions[0]; actual = window[position:position+len(expected)]
    require(len(actual) == len(expected), "PCM marked tail is incomplete")
    frames = list(struct.iter_unpack("<hhhh", actual)); wanted = list(struct.iter_unpack("<hhhh", expected))
    exact = {name:all(a[index] == b[index] for a,b in zip(frames,wanted)) for index,name in enumerate(CHANNELS)}
    require(all(exact.values()), "PCM marked sequence differs: " + json.dumps(exact))
    return dict(windowStartBytes=begin, windowEndBytes=finish, alignmentFrames=position//8,
        markedFrames=len(source)//8, capturedFrames=len(window)//8, channelDivisors=list(divisors),
        exactChannels=exact, sourceSha256=digest(source), alignedSha256=digest(actual),
        alignmentIsLatency=False, physicalAudioValidated=False)
