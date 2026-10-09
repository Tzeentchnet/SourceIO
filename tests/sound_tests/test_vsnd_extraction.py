from __future__ import annotations

import hashlib
import os
import struct

os.environ["NO_BPY"] = "1"

import pytest

from SourceIO.library.shared.content_manager import MountLayer, ResolvedResource, ResourceRef
from SourceIO.library.source2.resource_registry import ResourceRegistry
from SourceIO.library.source2.resource_types.compiled_sound_resource import (
    CompiledSoundResource,
    register_sound_resource,
)
from SourceIO.library.source2.interfaces import Maturity, ResourceKind
from SourceIO.library.source2.sound import (
    EmphasisSample,
    PhonemeTag,
    SoundContainer,
    SoundEncoding,
)
from SourceIO.library.utils import MemoryBuffer, TinyPath
from SourceIO.tests.sound_tests.fixtures import (
    build_vsnd,
    generated_adpcm,
    generated_mp3,
    generated_pcm8,
    generated_pcm16,
)


def load(data: bytes, name: str = "generated.vsnd_c") -> CompiledSoundResource:
    return CompiledSoundResource.from_buffer(MemoryBuffer(data), TinyPath(name))


@pytest.mark.parametrize(
    ("version", "container", "encoding", "payload", "options", "expected_hash"),
    (
        (
            1,
            SoundContainer.WAV,
            SoundEncoding.PCM16,
            generated_pcm16(),
            {},
            "ff4e618e49e6ad624733abfb4338f7f72fc7852f898e7d0372b257cdf41f0e43",
        ),
        (
            2,
            SoundContainer.MP3,
            SoundEncoding.MP3,
            generated_mp3(),
            {"sample_rate": 44100, "channels": 2, "sample_count": 2304},
            "c6ea750f8035b941ebcf2f31752f6d88a09c5d40d56e75ac17a57e7b4dc95280",
        ),
        (
            3,
            SoundContainer.WAV,
            SoundEncoding.PCM8,
            generated_pcm8(),
            {},
            "f486f67a054c6e48a369037f29f4ec62855e3a9c8812610cb1a0535c4727a639",
        ),
        (
            4,
            SoundContainer.WAV,
            SoundEncoding.PCM16,
            generated_pcm16(),
            {},
            "ff4e618e49e6ad624733abfb4338f7f72fc7852f898e7d0372b257cdf41f0e43",
        ),
    ),
)
def test_versions_one_through_four_extract_exact_output(
        version, container, encoding, payload, options, expected_hash):
    fixture = build_vsnd(version, container, encoding, payload, **options)
    resource = load(fixture.data)
    artifact = resource.extract()

    assert artifact.metadata.version == version
    assert artifact.metadata.container is container
    assert artifact.metadata.encoding is encoding
    assert artifact.file_name == f"generated.{container.value}"
    assert hashlib.sha256(artifact.audio_bytes).hexdigest() == expected_hash
    if container in (SoundContainer.AAC, SoundContainer.MP3):
        assert artifact.audio_bytes == payload
    else:
        assert artifact.audio_bytes[artifact.audio_bytes.index(b"data") + 8:] == payload


def test_v4_mp3_payload_is_bit_exact():
    payload = generated_mp3()
    artifact = load(build_vsnd(
        4,
        SoundContainer.MP3,
        SoundEncoding.MP3,
        payload,
        sample_rate=44100,
        channels=2,
        sample_count=2304,
    ).data).extract()
    assert artifact.audio_bytes == payload
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()


def test_in_memory_resource_has_a_safe_artifact_name():
    fixture = build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.PCM8,
        generated_pcm8(),
    )
    resource = CompiledSoundResource.from_buffer(MemoryBuffer(fixture.data), None)
    assert resource.extract().file_name == "sound.wav"


def test_sound_resource_declares_stable_read_and_extract_capabilities():
    resource = load(build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.PCM8,
        generated_pcm8(),
    ).data)

    assert resource.identity.kind is ResourceKind.SOUND
    assert resource.capabilities.read is Maturity.STABLE
    assert resource.capabilities.extract is Maturity.STABLE
    assert resource.capabilities.render is Maturity.UNSUPPORTED


def test_sound_resource_registration_is_version_bounded():
    registry = ResourceRegistry(include_builtins=False)
    registration = register_sound_resource(registry)

    assert registration.kind is ResourceKind.SOUND
    assert registration.resource_type is CompiledSoundResource
    assert registration.extensions == frozenset({".vsnd_c"})
    assert registration.supported_versions == frozenset({1, 2, 3, 4})
    assert registration.capabilities.extract is Maturity.STABLE
    assert registration.control_signatures == frozenset({
        "cvoicecontainerdefault",
        "cvoicecontainerenvelope",
    })


def test_registered_resource_parser_accepts_streaming_payload():
    fixture = build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.PCM8,
        generated_pcm8(),
    )
    registry = ResourceRegistry(include_builtins=False)
    register_sound_resource(registry)

    resource = registry.parse(fixture.data, "sounds/registered.vsnd_c")
    assert isinstance(resource, CompiledSoundResource)
    assert resource.identity.kind is ResourceKind.SOUND
    assert resource.raw_payload == generated_pcm8()


def test_resource_resolver_contract_loads_sound():
    fixture = build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.PCM8,
        generated_pcm8(),
    )
    path = TinyPath("sounds/resolved.vsnd_c")
    opened_streams = []

    def open_stream():
        stream = MemoryBuffer(fixture.data)
        opened_streams.append(stream)
        return stream

    resolved = ResolvedResource(
        requested_path=path,
        path=path,
        provider=object(),
        layer=MountLayer.LOOSE_FILES,
        mount_name="generated",
        _opener=open_stream,
    )

    class Resolver:
        @staticmethod
        def find(reference):
            return resolved if reference is expected_reference else None

    resolver = Resolver()
    expected_reference = ResourceRef(path, source_path="maps/generated.vmap_c")
    resource = CompiledSoundResource.from_resolver(resolver, expected_reference)
    assert resource.path == path
    assert resource.extract().name == "resolved"
    assert opened_streams[0].closed

    with pytest.raises(FileNotFoundError):
        CompiledSoundResource.from_resolver(Resolver(), "sounds/missing.vsnd_c")


def test_adpcm_riff_uses_stored_format_header_without_transcoding():
    header, payload, sample_count = generated_adpcm()
    fixture = build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.ADPCM,
        payload,
        sample_rate=8000,
        sample_count=sample_count,
        adpcm_header=header,
    )
    artifact = load(fixture.data).extract()

    fmt_size = struct.unpack_from("<I", artifact.audio_bytes, 16)[0]
    assert fmt_size == len(header)
    assert artifact.audio_bytes[20:20 + fmt_size] == header
    data_offset = 20 + fmt_size
    assert artifact.audio_bytes[data_offset:data_offset + 4] == b"data"
    assert artifact.audio_bytes[data_offset + 8:] == payload
    assert artifact.metadata.encoding is SoundEncoding.ADPCM
    assert artifact.sha256 == "325d40bb2d03a10eab172215bb0abf37a977c8b67b0c9731cb7d1a2abb08f85a"


def test_legacy_adpcm_uses_the_same_stored_wave_format():
    header, payload, sample_count = generated_adpcm()
    fixture = build_vsnd(
        1,
        SoundContainer.WAV,
        SoundEncoding.ADPCM,
        payload,
        sample_rate=8000,
        sample_count=sample_count,
        adpcm_header=header,
    )
    artifact = load(fixture.data).extract()

    assert artifact.metadata.version == 1
    assert artifact.metadata.container is SoundContainer.WAV
    assert artifact.metadata.encoding is SoundEncoding.ADPCM
    assert artifact.sha256 == "325d40bb2d03a10eab172215bb0abf37a977c8b67b0c9731cb7d1a2abb08f85a"


def test_phonemes_emphasis_and_compiler_metadata_are_structured():
    phonemes = (
        PhonemeTag(0.0, 0.048, 240),
        PhonemeTag(0.048, 0.1, 115),
    )
    emphasis = (
        EmphasisSample(0.1, 0.75),
        EmphasisSample(0.5, 0.25),
    )
    fixture = build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.PCM16,
        generated_pcm16(),
        sample_rate=80,
        phonemes=phonemes,
        emphasis=emphasis,
        should_voice_duck=True,
        include_sentence=True,
    )
    artifact = load(fixture.data, "voice.vsnd_c").extract()
    track = artifact.phoneme_track

    assert track is not None
    assert [phoneme.code for phoneme in track.phonemes] == [240, 115]
    assert [
        timing
        for phoneme in track.phonemes
        for timing in (phoneme.start_time, phoneme.end_time)
    ] == pytest.approx([0.0, 0.048, 0.048, 0.1])
    assert [
        component
        for sample in track.emphasis_samples
        for component in (sample.time, sample.value)
    ] == pytest.approx([0.1, 0.75, 0.5, 0.25])
    assert track.should_voice_duck
    assert track.to_compiler_text() == (
        "VERSION 1.0\n"
        "PLAINTEXT\n"
        "{\n"
        "}\n"
        "WORDS\n"
        "{\n"
        "\tWORD ðs 0.000 0.100\n"
        "\t{\n"
        "\t\t240 ð 0.000 0.048 1\n"
        "\t\t115 s 0.048 0.100 1\n"
        "\t}\n"
        "}\n"
        "EMPHASIS\n"
        "{\n"
        "\t0.100000 0.750000\n"
        "\t0.500000 0.250000\n"
        "}\n"
        "OPTIONS\n"
        "{\n"
        "\tvoice_duck 1\n"
        "}\n"
    )
    assert artifact.companion_files["voice.txt"] == track.to_compiler_bytes()


def test_ctrl_voice_container_is_preserved_and_supplies_phonemes():
    ctrl = {
        "_class": "CVoiceContainerDefault",
        "m_vSound": {
            "m_nRate": 8000,
            "m_nFormat": "PCM8",
            "m_nChannels": 1,
            "m_nLoopStart": -1,
            "m_nSampleCount": 8,
            "m_flDuration": 0.001,
            "m_Sentences": [{
                "m_bShouldVoiceDuck": False,
                "m_RunTimePhonemes": [{
                    "m_flStartTime": 0.0,
                    "m_flEndTime": 0.001,
                    "m_nPhonemeCode": 97,
                }],
                "m_EmphasisSamples": [{
                    "m_flTime": 0.0005,
                    "m_flValue": 0.8,
                }],
            }],
            "m_nStreamingSize": 8,
            "m_nSeekTable": [],
            "m_nLoopEnd": 0,
            "m_encodedHeader": b"\x00\x7F\xFF",
        },
        "m_pEnvelopeAnalyzer": None,
    }
    fixture = build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.PCM8,
        generated_pcm8(),
        ctrl=ctrl,
    )
    artifact = load(fixture.data, "controlled.vsnd_c").extract()
    source = artifact.companion_files["controlled.vsnd"].decode("utf-8")

    assert source.startswith("<!-- kv3 encoding:text:version{")
    assert "VrfExportedSound = {" in source
    assert '_class = "CVoiceContainerDefault"' in source
    assert 'm_nFormat = "PCM8"' in source
    assert "m_encodedHeader = #[" in source
    assert "00 7F FF" in source
    assert artifact.phoneme_track is not None
    assert artifact.phoneme_track.phonemes[0] == PhonemeTag(0.0, pytest.approx(0.001), 97)
    assert artifact.phoneme_track.emphasis_samples[0].value == pytest.approx(0.8)


def test_artifact_writes_audio_and_companions(tmp_path):
    fixture = build_vsnd(
        4,
        SoundContainer.WAV,
        SoundEncoding.PCM8,
        generated_pcm8(),
        include_sentence=True,
    )
    artifact = load(fixture.data, "write_test.vsnd_c").extract()
    paths = artifact.write_to(tmp_path)

    assert {path.name for path in paths} == {"write_test.wav", "write_test.txt"}
    assert (tmp_path / "write_test.wav").read_bytes() == artifact.audio_bytes
    with pytest.raises(FileExistsError):
        artifact.write_to(tmp_path)
