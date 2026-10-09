from __future__ import annotations

import os
import struct

os.environ["NO_BPY"] = "1"

import pytest

from SourceIO.library.source2.resource_types.compiled_sound_resource import CompiledSoundResource
from SourceIO.library.source2.exceptions import BlockBoundsError
from SourceIO.library.source2.sound import (
    MalformedSoundError,
    SoundContainer,
    SoundEncoding,
    UnsupportedSoundVersionError,
)
from SourceIO.library.utils import MemoryBuffer, TinyPath
from SourceIO.tests.sound_tests.fixtures import (
    build_vsnd,
    generated_adpcm,
    generated_mp3,
    generated_pcm8,
)


def load(data: bytes):
    return CompiledSoundResource.from_buffer(MemoryBuffer(data), TinyPath("bad.vsnd_c"))


def pcm_fixture() -> bytearray:
    return bytearray(build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.PCM8,
        generated_pcm8(),
    ).data)


def test_future_version_is_rejected_explicitly():
    data = pcm_fixture()
    struct.pack_into("<H", data, 6, 5)
    with pytest.raises(UnsupportedSoundVersionError, match="supported versions are 1 through 4"):
        load(bytes(data))


def test_metadata_file_length_outside_file_is_rejected():
    data = pcm_fixture()
    struct.pack_into("<I", data, 0, len(data) + 1)
    with pytest.raises(MalformedSoundError, match="metadata length"):
        load(bytes(data))


def test_truncated_data_block_is_rejected():
    data = pcm_fixture()
    struct.pack_into("<I", data, 24, 8)
    with pytest.raises(MalformedSoundError, match="Truncated DATA block"):
        load(bytes(data)).extract()


def test_declared_streaming_length_must_match_file():
    data = pcm_fixture()
    data_block_offset = 28
    struct.pack_into("<I", data, data_block_offset + 28, len(generated_pcm8()) + 4)
    with pytest.raises(MalformedSoundError, match="PCM stream"):
        load(bytes(data)).extract()


def test_compressed_streaming_length_must_match_file():
    data = bytearray(build_vsnd(
        4,
        SoundContainer.MP3,
        SoundEncoding.MP3,
        generated_mp3(),
    ).data)
    data_block_offset = 28
    struct.pack_into("<I", data, data_block_offset + 28, len(generated_mp3()) - 1)
    with pytest.raises(MalformedSoundError, match="streaming section"):
        load(bytes(data)).extract()


def test_pcm_length_must_match_sample_count():
    fixture = build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.PCM8,
        generated_pcm8(),
        sample_count=len(generated_pcm8()) + 1,
    )
    with pytest.raises(MalformedSoundError, match="PCM stream"):
        load(fixture.data).extract()


def test_sentence_array_range_is_validated():
    fixture = build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.PCM8,
        generated_pcm8(),
        include_sentence=True,
    )
    data = bytearray(fixture.data)
    data_block_offset = 28
    sentence_pointer = data_block_offset + 16
    sentence_offset = sentence_pointer + struct.unpack_from("<I", data, sentence_pointer)[0]
    struct.pack_into("<i", data, sentence_offset + 8, 0x7FFFFFFF)
    with pytest.raises(MalformedSoundError, match="Phoneme range"):
        load(bytes(data)).extract()


def test_negative_adpcm_header_length_is_rejected():
    data = pcm_fixture()
    data_block_offset = 28
    struct.pack_into("<i", data, data_block_offset + 24, -1)
    with pytest.raises(MalformedSoundError, match="header length"):
        load(bytes(data)).extract()


def test_invalid_adpcm_format_header_is_rejected():
    header, payload, sample_count = generated_adpcm()
    header = b"\x01\x00" + header[2:]
    fixture = build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.ADPCM,
        payload,
        sample_count=sample_count,
        adpcm_header=header,
    )
    with pytest.raises(MalformedSoundError, match="format tag"):
        load(fixture.data).extract()


def test_unknown_v4_encoding_is_rejected():
    data = pcm_fixture()
    data_block_offset = 28
    data[data_block_offset + 2] = 4
    with pytest.raises(MalformedSoundError, match="encoding code"):
        load(bytes(data)).extract()


def test_block_range_cannot_exceed_metadata_section():
    data = pcm_fixture()
    struct.pack_into("<I", data, 24, len(data))
    with pytest.raises(BlockBoundsError, match="outside resource range"):
        load(bytes(data))
