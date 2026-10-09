from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from .types import (
    EmphasisSample,
    MalformedSoundError,
    PhonemeTag,
    PhonemeTrack,
    SoundContainer,
    SoundEncoding,
    SoundMetadata,
    UnsupportedSoundVersionError,
    validate_track,
)


SUPPORTED_SOUND_VERSIONS = frozenset({1, 2, 3, 4})


@dataclass(frozen=True, slots=True)
class ParsedSound:
    metadata: SoundMetadata
    phoneme_track: PhonemeTrack | None


class _Cursor:
    __slots__ = ("data", "offset")

    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        if self.offset + size > len(self.data):
            raise MalformedSoundError(
                f"Truncated DATA block at offset {self.offset}: need {size} bytes, "
                f"have {len(self.data) - self.offset}"
            )
        values = struct.unpack_from(fmt, self.data, self.offset)
        self.offset += size
        return values

    def u8(self) -> int:
        return self.unpack("<B")[0]

    def u16(self) -> int:
        return self.unpack("<H")[0]

    def u32(self) -> int:
        return self.unpack("<I")[0]

    def i32(self) -> int:
        return self.unpack("<i")[0]

    def f32(self) -> float:
        return self.unpack("<f")[0]


def _bounded_range(data: bytes, offset: int, count: int, stride: int, label: str) -> tuple[int, int]:
    if count < 0:
        raise MalformedSoundError(f"{label} count cannot be negative")
    if offset < 0:
        raise MalformedSoundError(f"{label} offset points before the DATA block")
    size = count * stride
    end = offset + size
    if end > len(data):
        raise MalformedSoundError(
            f"{label} range [{offset}, {end}) exceeds DATA block length {len(data)}"
        )
    return offset, end


def _parse_sentence(data: bytes, offset: int) -> PhonemeTrack:
    _bounded_range(data, offset, 1, 20, "Sentence")
    should_voice_duck = data[offset]
    if should_voice_duck not in (0, 1):
        raise MalformedSoundError(f"Invalid voice-duck flag {should_voice_duck}")

    phoneme_pointer = offset + 4
    phoneme_relative, phoneme_count = struct.unpack_from("<ii", data, phoneme_pointer)
    emphasis_pointer = offset + 12
    emphasis_relative, emphasis_count = struct.unpack_from("<ii", data, emphasis_pointer)

    phoneme_offset = phoneme_pointer + phoneme_relative
    emphasis_offset = emphasis_pointer + emphasis_relative
    _bounded_range(data, phoneme_offset, phoneme_count, 12, "Phoneme")
    _bounded_range(data, emphasis_offset, emphasis_count, 8, "Emphasis")

    phonemes = tuple(
        PhonemeTag(*struct.unpack_from("<ffH", data, phoneme_offset + index * 12))
        for index in range(phoneme_count)
    )
    emphasis_samples = tuple(
        EmphasisSample(*struct.unpack_from("<ff", data, emphasis_offset + index * 8))
        for index in range(emphasis_count)
    )
    track = PhonemeTrack(phonemes, emphasis_samples, bool(should_voice_duck))
    validate_track(track)
    return track


def _validate_adpcm_header(header: bytes, sample_rate: int, channels: int) -> None:
    if len(header) < 22:
        raise MalformedSoundError("ADPCM format header is shorter than ADPCMWAVEFORMAT")

    (format_tag, header_channels, header_rate, average_bytes_per_second,
     block_align, stored_bits, extra_size) = struct.unpack_from("<HHIIHHH", header)
    samples_per_block, coefficient_count = struct.unpack_from("<HH", header, 18)

    if format_tag != 2:
        raise MalformedSoundError(f"ADPCM format header has WAVE format tag {format_tag}, expected 2")
    if header_channels != channels or header_rate != sample_rate:
        raise MalformedSoundError("ADPCM format header disagrees with VSND channel or sample-rate metadata")
    if average_bytes_per_second == 0 or block_align == 0 or samples_per_block == 0:
        raise MalformedSoundError("ADPCM format header contains a zero rate or block size")
    if stored_bits != 4:
        raise MalformedSoundError(f"ADPCM format header has {stored_bits} stored bits per sample, expected 4")
    if extra_size != len(header) - 18:
        raise MalformedSoundError("ADPCM format header cbSize does not match its stored length")
    if extra_size != 4 + coefficient_count * 4:
        raise MalformedSoundError("ADPCM coefficient table length does not match its coefficient count")


def _old_format(bitpacked: int) -> tuple[SoundContainer, SoundEncoding, int, int, int]:
    container_value = bitpacked & 0b11
    packed_bits = (bitpacked >> 2) & 0b1_1111
    channels = (bitpacked >> 7) & 0b11
    wave_format = (bitpacked >> 12) & 0b11
    sample_rate = (bitpacked >> 14) & 0xFFFF

    if container_value == 0:
        return SoundContainer.WAV, SoundEncoding.PCM16, 16, channels, sample_rate
    if container_value == 2:
        return SoundContainer.MP3, SoundEncoding.MP3, 0, channels, sample_rate
    if container_value != 1:
        raise MalformedSoundError(f"Unknown legacy VSND container code {container_value}")

    if wave_format == 2:
        return SoundContainer.WAV, SoundEncoding.ADPCM, 16, channels, sample_rate
    if wave_format not in (0, 1):
        raise MalformedSoundError(f"Unknown legacy WAVE format code {wave_format}")
    if packed_bits == 8:
        return SoundContainer.WAV, SoundEncoding.PCM8, 8, channels, sample_rate
    if packed_bits == 16:
        return SoundContainer.WAV, SoundEncoding.PCM16, 16, channels, sample_rate
    raise MalformedSoundError(f"Unsupported legacy PCM bit depth {packed_bits}")


def _version_four_format(value: int) -> tuple[SoundContainer, SoundEncoding, int]:
    try:
        encoding = (
            SoundEncoding.PCM16,
            SoundEncoding.PCM8,
            SoundEncoding.MP3,
            SoundEncoding.ADPCM,
        )[value]
    except IndexError as exc:
        raise MalformedSoundError(f"Unknown VSND v4 encoding code {value}") from exc

    if encoding is SoundEncoding.MP3:
        return SoundContainer.MP3, encoding, 0
    if encoding is SoundEncoding.PCM8:
        return SoundContainer.WAV, encoding, 8
    return SoundContainer.WAV, encoding, 16


def parse_sound_data(data: bytes, version: int) -> ParsedSound:
    if version not in SUPPORTED_SOUND_VERSIONS:
        raise UnsupportedSoundVersionError(
            f"Unsupported VSND resource version {version}; supported versions are 1 through 4"
        )

    cursor = _Cursor(data)
    if version == 4:
        sample_rate = cursor.u16()
        container, encoding, bits_per_sample = _version_four_format(cursor.u8())
        channels = cursor.u8()
    else:
        container, encoding, bits_per_sample, channels, sample_rate = _old_format(cursor.u32())

    loop_start = cursor.i32()
    sample_count = cursor.u32()
    duration = cursor.f32()

    sentence_pointer = cursor.offset
    sentence_relative = cursor.u32()
    sentence_offset = sentence_pointer + sentence_relative if sentence_relative else None

    header_pointer = cursor.offset
    header_relative = cursor.u32()
    header_size = cursor.i32()
    streaming_data_size = cursor.u32()

    cursor.unpack("<ii")
    if version >= 2:
        cursor.i32()
    loop_end = cursor.i32() if version >= 4 else 0

    if version < 3 and container is SoundContainer.MP3:
        loop_start = -1
    if channels not in (1, 2):
        raise MalformedSoundError(f"Unsupported channel count {channels}; expected mono or stereo")
    if sample_rate <= 0:
        raise MalformedSoundError("Sample rate must be greater than zero")
    if not math.isfinite(duration) or duration < 0:
        raise MalformedSoundError("Duration must be a finite, non-negative value")
    if loop_start < -1 or loop_start > sample_count:
        raise MalformedSoundError("Loop start is outside the sample range")
    if version >= 4 and loop_end not in (0, -1) and not loop_start <= loop_end <= sample_count:
        raise MalformedSoundError("Loop end is outside the sample range")
    if header_size < 0:
        raise MalformedSoundError("ADPCM format header length cannot be negative")

    header = b""
    if header_size:
        if encoding is not SoundEncoding.ADPCM:
            raise MalformedSoundError("Only ADPCM sounds may carry a WAVE format header")
        header_offset = header_pointer + header_relative
        start, end = _bounded_range(data, header_offset, header_size, 1, "ADPCM format header")
        header = data[start:end]
        _validate_adpcm_header(header, sample_rate, channels)
    elif encoding is SoundEncoding.ADPCM:
        raise MalformedSoundError("ADPCM sound is missing its WAVE format header")

    if container is SoundContainer.WAV and encoding in (SoundEncoding.PCM8, SoundEncoding.PCM16):
        expected_size = sample_count * channels * (bits_per_sample // 8)
        if streaming_data_size != expected_size:
            raise MalformedSoundError(
                f"PCM stream is {streaming_data_size} bytes, expected {expected_size} "
                f"for {sample_count} samples"
            )

    track = _parse_sentence(data, sentence_offset) if sentence_offset is not None else None
    metadata = SoundMetadata(
        version=version,
        container=container,
        encoding=encoding,
        sample_rate=sample_rate,
        channels=channels,
        bits_per_sample=bits_per_sample,
        sample_count=sample_count,
        loop_start=loop_start,
        loop_end=loop_end,
        duration=duration,
        streaming_data_size=streaming_data_size,
        adpcm_format_header=header,
    )
    return ParsedSound(metadata, track)
