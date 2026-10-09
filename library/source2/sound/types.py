from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable


class SoundError(ValueError):
    """Base error for malformed or unsupported compiled sounds."""


class UnsupportedSoundVersionError(SoundError):
    """Raised when a VSND resource version is not supported."""


class MalformedSoundError(SoundError):
    """Raised when a VSND resource is truncated or internally inconsistent."""


class SoundContainer(str, Enum):
    AAC = "aac"
    MP3 = "mp3"
    WAV = "wav"


class SoundEncoding(str, Enum):
    PCM8 = "pcm8"
    PCM16 = "pcm16"
    MP3 = "mp3"
    ADPCM = "adpcm"


@dataclass(frozen=True, slots=True)
class PhonemeTag:
    start_time: float
    end_time: float
    code: int

    @property
    def symbol(self) -> str:
        if 0xD800 <= self.code <= 0xDFFF:
            return "?"
        symbol = chr(self.code)
        return symbol if symbol.isprintable() and not symbol.isspace() else "?"


@dataclass(frozen=True, slots=True)
class EmphasisSample:
    time: float
    value: float


@dataclass(frozen=True, slots=True)
class PhonemeTrack:
    phonemes: tuple[PhonemeTag, ...] = ()
    emphasis_samples: tuple[EmphasisSample, ...] = ()
    should_voice_duck: bool = False

    @property
    def run_time_phonemes(self) -> tuple[PhonemeTag, ...]:
        return self.phonemes

    def to_compiler_text(self) -> str:
        lines = [
            "VERSION 1.0",
            "PLAINTEXT",
            "{",
            "}",
            "WORDS",
            "{",
        ]

        if self.phonemes:
            word = "".join(phoneme.symbol for phoneme in self.phonemes)
            lines.extend((
                f"\tWORD {word} {self.phonemes[0].start_time:.3f} {self.phonemes[-1].end_time:.3f}",
                "\t{",
            ))
            lines.extend(
                f"\t\t{phoneme.code} {phoneme.symbol} {phoneme.start_time:.3f} {phoneme.end_time:.3f} 1"
                for phoneme in self.phonemes
            )
            lines.append("\t}")

        lines.extend((
            "}",
            "EMPHASIS",
            "{",
        ))
        lines.extend(f"\t{sample.time:.6f} {sample.value:.6f}" for sample in self.emphasis_samples)
        lines.extend((
            "}",
            "OPTIONS",
            "{",
            f"\tvoice_duck {int(self.should_voice_duck)}",
            "}",
            "",
        ))
        return "\n".join(lines)

    def to_compiler_bytes(self) -> bytes:
        return self.to_compiler_text().encode("utf-8")


@dataclass(frozen=True, slots=True)
class SoundMetadata:
    version: int
    container: SoundContainer
    encoding: SoundEncoding
    sample_rate: int
    channels: int
    bits_per_sample: int
    sample_count: int
    loop_start: int
    loop_end: int
    duration: float
    streaming_data_size: int
    adpcm_format_header: bytes = b""

    @property
    def extension(self) -> str:
        return self.container.value

    @property
    def block_align(self) -> int:
        if self.encoding is SoundEncoding.ADPCM:
            return struct.unpack_from("<H", self.adpcm_format_header, 12)[0]
        if self.encoding in (SoundEncoding.PCM8, SoundEncoding.PCM16):
            return self.channels * (self.bits_per_sample // 8)
        return 0


@dataclass(frozen=True, slots=True)
class SoundCompanion:
    name: str
    data: bytes
    media_type: str

    def __post_init__(self):
        if not self.name or Path(self.name).name != self.name:
            raise ValueError("Sound companion names must be plain file names")


@dataclass(frozen=True, slots=True)
class SoundArtifact:
    name: str
    audio_bytes: bytes
    metadata: SoundMetadata
    phoneme_track: PhonemeTrack | None = None
    companions: tuple[SoundCompanion, ...] = ()

    def __post_init__(self):
        if not self.name or Path(self.name).name != self.name:
            raise ValueError("Sound artifact names must not contain a directory")
        names = [companion.name for companion in self.companions]
        if len(names) != len(set(names)):
            raise ValueError("Sound artifact companion names must be unique")

    @property
    def data(self) -> bytes:
        return self.audio_bytes

    @property
    def extension(self) -> str:
        return self.metadata.extension

    @property
    def container(self) -> SoundContainer:
        return self.metadata.container

    @property
    def encoding(self) -> SoundEncoding:
        return self.metadata.encoding

    @property
    def file_name(self) -> str:
        return f"{self.name}.{self.extension}"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.audio_bytes).hexdigest()

    @property
    def companion_files(self) -> dict[str, bytes]:
        return {companion.name: companion.data for companion in self.companions}

    def write_to(self, directory: str | Path, *, overwrite: bool = False) -> tuple[Path, ...]:
        destination = Path(directory)
        outputs = (
            (destination / self.file_name, self.audio_bytes),
            *((destination / companion.name, companion.data) for companion in self.companions),
        )
        if not overwrite:
            existing = [path for path, _ in outputs if path.exists()]
            if existing:
                raise FileExistsError(existing[0])

        destination.mkdir(parents=True, exist_ok=True)
        for path, data in outputs:
            path.write_bytes(data)
        return tuple(path for path, _ in outputs)


def build_wave(metadata: SoundMetadata, payload: bytes) -> bytes:
    if metadata.container is not SoundContainer.WAV:
        raise ValueError("RIFF output is only valid for WAV sounds")
    if len(payload) != metadata.streaming_data_size:
        raise MalformedSoundError(
            f"Streaming payload is {len(payload)} bytes, expected {metadata.streaming_data_size}"
        )

    if metadata.encoding is SoundEncoding.ADPCM:
        fmt_data = metadata.adpcm_format_header
    elif metadata.encoding in (SoundEncoding.PCM8, SoundEncoding.PCM16):
        block_align = metadata.channels * (metadata.bits_per_sample // 8)
        byte_rate = metadata.sample_rate * block_align
        fmt_data = struct.pack(
            "<HHIIHH",
            1,
            metadata.channels,
            metadata.sample_rate,
            byte_rate,
            block_align,
            metadata.bits_per_sample,
        )
    else:
        raise MalformedSoundError(f"Encoding {metadata.encoding.value} cannot be stored in a WAV container")

    riff_size = 4 + 8 + len(fmt_data) + 8 + len(payload)
    if riff_size > 0xFFFFFFFF:
        raise MalformedSoundError("Extracted WAV is too large for a RIFF container")
    return b"".join((
        b"RIFF",
        struct.pack("<I", riff_size),
        b"WAVEfmt ",
        struct.pack("<I", len(fmt_data)),
        fmt_data,
        b"data",
        struct.pack("<I", len(payload)),
        payload,
    ))


def validate_track(track: PhonemeTrack) -> None:
    previous_start = -math.inf
    for phoneme in track.phonemes:
        if not (math.isfinite(phoneme.start_time) and math.isfinite(phoneme.end_time)):
            raise MalformedSoundError("Phoneme timings must be finite")
        if phoneme.start_time < 0 or phoneme.end_time < phoneme.start_time:
            raise MalformedSoundError("Phoneme timing range is invalid")
        if phoneme.start_time < previous_start:
            raise MalformedSoundError("Phonemes are not sorted by start time")
        if not 0 <= phoneme.code <= 0xFFFF:
            raise MalformedSoundError(f"Phoneme code {phoneme.code} is outside uint16")
        previous_start = phoneme.start_time

    previous_time = -math.inf
    for sample in track.emphasis_samples:
        if not (math.isfinite(sample.time) and math.isfinite(sample.value)):
            raise MalformedSoundError("Emphasis samples must be finite")
        if sample.time < 0 or sample.time < previous_time:
            raise MalformedSoundError("Emphasis samples are not sorted by time")
        previous_time = sample.time


def companions_by_media_type(
        companions: Iterable[SoundCompanion], media_type: str) -> tuple[SoundCompanion, ...]:
    return tuple(companion for companion in companions if companion.media_type == media_type)
