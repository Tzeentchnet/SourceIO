from __future__ import annotations

import struct
from dataclasses import dataclass

from SourceIO.library.source2.keyvalues3.binary_keyvalues import write_valve_keyvalue3
from SourceIO.library.source2.keyvalues3.enums import KV3CompressionMethod, KV3Format, KV3Signature
from SourceIO.library.source2.keyvalues3.types import Object
from SourceIO.library.source2.sound import (
    EmphasisSample,
    PhonemeTag,
    SoundContainer,
    SoundEncoding,
)
from SourceIO.library.utils import WritableMemoryBuffer


@dataclass(frozen=True, slots=True)
class GeneratedVsnd:
    data: bytes
    payload: bytes
    adpcm_header: bytes = b""


def generated_pcm8() -> bytes:
    return bytes((0, 32, 64, 96, 128, 160, 192, 224))


def generated_pcm16() -> bytes:
    return struct.pack("<8h", -32768, -24576, -8192, -1, 0, 8192, 24576, 32767)


def generated_mp3() -> bytes:
    frame_header = b"\xFF\xFB\x90\x64"
    frame = frame_header + bytes((index * 29 + 7) & 0xFF for index in range(413))
    return frame * 2


def generated_adpcm() -> tuple[bytes, bytes, int]:
    channels = 1
    sample_rate = 8000
    block_align = 256
    samples_per_block = 500
    coefficients = (
        (256, 0),
        (512, -256),
        (0, 0),
        (192, 64),
        (240, 0),
        (460, -208),
        (392, -232),
    )
    average_bytes_per_second = sample_rate * block_align // samples_per_block
    extra_size = 4 + len(coefficients) * 4
    header = bytearray(struct.pack(
        "<HHIIHHHHH",
        2,
        channels,
        sample_rate,
        average_bytes_per_second,
        block_align,
        4,
        extra_size,
        samples_per_block,
        len(coefficients),
    ))
    for first, second in coefficients:
        header.extend(struct.pack("<hh", first, second))

    payload = bytearray(block_align)
    struct.pack_into("<Bh", payload, 0, 0, 16)
    struct.pack_into("<hh", payload, 3, 0, 0)
    return bytes(header), bytes(payload), samples_per_block


def _sentence(
        data: bytearray,
        phonemes: tuple[PhonemeTag, ...],
        emphasis: tuple[EmphasisSample, ...],
        should_voice_duck: bool,
) -> None:
    data.extend(bytes((int(should_voice_duck), 0, 0, 0)))
    phoneme_pointer = len(data)
    data.extend(struct.pack("<ii", 0, len(phonemes)))
    emphasis_pointer = len(data)
    data.extend(struct.pack("<ii", 0, len(emphasis)))

    phoneme_offset = len(data)
    for phoneme in phonemes:
        data.extend(struct.pack("<ffHxx", phoneme.start_time, phoneme.end_time, phoneme.code))
    emphasis_offset = len(data)
    for sample in emphasis:
        data.extend(struct.pack("<ff", sample.time, sample.value))

    struct.pack_into("<i", data, phoneme_pointer, phoneme_offset - phoneme_pointer)
    struct.pack_into("<i", data, emphasis_pointer, emphasis_offset - emphasis_pointer)


def ctrl_block(data: dict) -> bytes:
    buffer = WritableMemoryBuffer()
    write_valve_keyvalue3(
        buffer,
        Object.from_python(data),
        KV3Format.generic,
        KV3Signature.KV3_V3,
        KV3CompressionMethod.UNCOMPRESSED,
    )
    return buffer.getvalue()


def _compiled_resource(version: int, blocks: tuple[tuple[str, bytes], ...], payload: bytes) -> bytes:
    table_size = len(blocks) * 12
    block_offset = 16 + table_size
    output = bytearray(struct.pack("<IHHII", 0, 12, version, 8, len(blocks)))

    current_offset = block_offset
    for index, (name, block_data) in enumerate(blocks):
        entry_offset = 16 + index * 12 + 4
        output.extend(struct.pack("<4sII", name.encode("ascii"), current_offset - entry_offset, len(block_data)))
        current_offset += len(block_data)
    for _, block_data in blocks:
        output.extend(block_data)

    struct.pack_into("<I", output, 0, len(output))
    output.extend(payload)
    return bytes(output)


def build_vsnd(
        version: int,
        container: SoundContainer,
        encoding: SoundEncoding,
        payload: bytes,
        *,
        sample_rate: int = 8000,
        channels: int = 1,
        sample_count: int | None = None,
        loop_start: int = -1,
        loop_end: int = 0,
        phonemes: tuple[PhonemeTag, ...] = (),
        emphasis: tuple[EmphasisSample, ...] = (),
        should_voice_duck: bool = False,
        include_sentence: bool = False,
        adpcm_header: bytes = b"",
        ctrl: dict | None = None,
) -> GeneratedVsnd:
    if sample_count is None:
        if encoding is SoundEncoding.PCM8:
            sample_count = len(payload) // channels
        elif encoding is SoundEncoding.PCM16:
            sample_count = len(payload) // (channels * 2)
        else:
            sample_count = 1
    duration = sample_count / sample_rate

    data = bytearray()
    if version == 4:
        format_code = {
            SoundEncoding.PCM16: 0,
            SoundEncoding.PCM8: 1,
            SoundEncoding.MP3: 2,
            SoundEncoding.ADPCM: 3,
        }[encoding]
        data.extend(struct.pack("<HBB", sample_rate, format_code, channels))
    else:
        container_code = {
            SoundContainer.AAC: 0,
            SoundContainer.WAV: 1,
            SoundContainer.MP3: 2,
        }[container]
        bits = 8 if encoding is SoundEncoding.PCM8 else 16
        wave_format = 2 if encoding is SoundEncoding.ADPCM else 1
        packed = container_code | (bits << 2) | (channels << 7) | (wave_format << 12) | (sample_rate << 14)
        data.extend(struct.pack("<I", packed))

    data.extend(struct.pack("<iIf", loop_start, sample_count, duration))
    sentence_pointer = len(data)
    data.extend(struct.pack("<I", 0))
    header_pointer = len(data)
    data.extend(struct.pack("<IiI", 0, len(adpcm_header), len(payload)))
    data.extend(struct.pack("<ii", 0, 0))
    if version >= 2:
        data.extend(struct.pack("<i", 0))
    if version >= 4:
        data.extend(struct.pack("<i", loop_end))

    if include_sentence:
        sentence_offset = len(data)
        struct.pack_into("<I", data, sentence_pointer, sentence_offset - sentence_pointer)
        _sentence(data, phonemes, emphasis, should_voice_duck)
    if adpcm_header:
        header_offset = len(data)
        struct.pack_into("<I", data, header_pointer, header_offset - header_pointer)
        data.extend(adpcm_header)

    blocks = [("DATA", bytes(data))]
    if ctrl is not None:
        blocks.append(("CTRL", ctrl_block(ctrl)))
    return GeneratedVsnd(_compiled_resource(version, tuple(blocks), payload), payload, adpcm_header)
