from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from os import PathLike
from typing import TYPE_CHECKING

from ...shared.content_manager import ResourceRef, ResourceResolverProtocol
from ...utils import Buffer, MemoryBuffer, TinyPath
from ..blocks.kv3_block import KVBlock
from ..capabilities import READ_EXTRACT_CAPABILITIES
from ..compiled_file_header import CompiledHeader
from ..compiled_resource import CompiledResource
from ..exceptions import Source2Error
from ..interfaces import ResourceKind
from ..sound.kv3_text import ctrl_to_compiler_text
from ..sound.parser import ParsedSound, SUPPORTED_SOUND_VERSIONS, parse_sound_data
from ..sound.types import (
    EmphasisSample,
    MalformedSoundError,
    PhonemeTag,
    PhonemeTrack,
    SoundArtifact,
    SoundCompanion,
    SoundContainer,
    SoundMetadata,
    UnsupportedSoundVersionError,
    build_wave,
    validate_track,
)

if TYPE_CHECKING:
    from ..resource_registry import ResourceRegistration, ResourceRegistry


@dataclass(slots=True)
class CompiledSoundResource(CompiledResource):
    resource_kind = ResourceKind.SOUND
    declared_capabilities = READ_EXTRACT_CAPABILITIES

    _parsed_sound: ParsedSound | None = field(default=None, init=False, repr=False)
    _ctrl: KVBlock | None = field(default=None, init=False, repr=False)
    _ctrl_loaded: bool = field(default=False, init=False, repr=False)

    @classmethod
    def from_buffer(cls, buffer, filename):
        if isinstance(buffer, Buffer):
            data = buffer.read()
        elif isinstance(buffer, (bytes, bytearray, memoryview)):
            data = bytes(buffer)
        else:
            raise TypeError(
                "buffer must be a SourceIO Buffer or bytes-like object, "
                f"got {type(buffer).__name__}"
            )
        path = TinyPath(filename or "<memory>")
        if len(data) < 4:
            raise MalformedSoundError("VSND is too short to contain its metadata length")
        metadata_size = int.from_bytes(data[:4], "little")
        if metadata_size > len(data):
            raise MalformedSoundError(
                f"VSND metadata length {metadata_size} exceeds file length {len(data)}"
            )
        try:
            header = CompiledHeader.from_buffer(MemoryBuffer(data[:metadata_size]))
        except Source2Error as exc:
            if exc.path is None:
                exc.path = str(path)
            raise
        resource = cls(
            MemoryBuffer(data),
            path,
            header,
            _capabilities=cls.declared_capabilities,
        )
        resource._validate_resource_layout()
        return resource

    @classmethod
    def from_resolver(
            cls,
            resolver: ResourceResolverProtocol,
            reference: str | PathLike[str] | TinyPath | ResourceRef,
    ) -> "CompiledSoundResource":
        resolved = resolver.find(reference)
        if resolved is None:
            raise FileNotFoundError(f"Could not resolve Source 2 sound {reference!s}")
        stream = resolved.open_stream()
        if stream is None:
            raise OSError(f"Resolved Source 2 sound could not be opened: {resolved.path}")
        try:
            return cls.from_buffer(stream, resolved.path)
        finally:
            stream.close()

    def _validate_resource_layout(self) -> None:
        version = self._header.resource_version
        if version not in SUPPORTED_SOUND_VERSIONS:
            raise UnsupportedSoundVersionError(
                f"Unsupported VSND resource version {version}; supported versions are 1 through 4"
            )

        total_size = self._buffer.size()
        metadata_size = self._header.file_size
        if metadata_size < 16 or metadata_size > total_size:
            raise MalformedSoundError(
                f"VSND metadata length {metadata_size} is outside file length {total_size}"
            )

        data_blocks = [block for block in self._header.blocks if block.name == "DATA"]
        if len(data_blocks) != 1:
            raise MalformedSoundError(f"VSND must contain exactly one DATA block, found {len(data_blocks)}")

        for block in self._header.blocks:
            if block.absolute_offset < 0 or block.size < 0:
                raise MalformedSoundError(f"{block.name} block has a negative offset or length")
            if block.absolute_offset + block.size > metadata_size:
                raise MalformedSoundError(
                    f"{block.name} block range exceeds VSND metadata length {metadata_size}"
                )

    def _data_bytes(self) -> bytes:
        block = next(block for block in self._header.blocks if block.name == "DATA")
        return self._buffer.ro_view(block.absolute_offset, block.size).tobytes()

    def _parsed(self) -> ParsedSound:
        if self._parsed_sound is None:
            self._parsed_sound = parse_sound_data(self._data_bytes(), self._header.resource_version)
            remaining = self._buffer.size() - self._header.file_size
            expected = self._parsed_sound.metadata.streaming_data_size
            if remaining != expected:
                raise MalformedSoundError(
                    f"VSND streaming section is {remaining} bytes, DATA metadata declares {expected}"
                )
        return self._parsed_sound

    @property
    def metadata(self) -> SoundMetadata:
        return self._parsed().metadata

    @property
    def raw_payload(self) -> bytes:
        metadata = self.metadata
        return self._buffer.ro_view(self._header.file_size, metadata.streaming_data_size).tobytes()

    @property
    def source_metadata(self) -> KVBlock | None:
        if not self._ctrl_loaded:
            self._ctrl = self.get_block(KVBlock, block_name="CTRL")
            self._ctrl_loaded = True
        return self._ctrl

    @staticmethod
    def _track_from_ctrl(ctrl: Mapping[str, object]) -> PhonemeTrack | None:
        sound = ctrl.get("m_vSound")
        if not isinstance(sound, Mapping):
            return None
        sentences = sound.get("m_Sentences")
        if not isinstance(sentences, list) or not sentences:
            return None
        sentence = sentences[0]
        if not isinstance(sentence, Mapping):
            raise MalformedSoundError("CTRL m_Sentences entry is not an object")

        phoneme_values = sentence.get("m_RunTimePhonemes")
        if not isinstance(phoneme_values, list):
            return None
        emphasis_values = sentence.get("m_EmphasisSamples", [])
        if not isinstance(emphasis_values, list):
            raise MalformedSoundError("CTRL m_EmphasisSamples is not an array")

        try:
            phonemes = tuple(
                PhonemeTag(
                    float(value["m_flStartTime"]),
                    float(value["m_flEndTime"]),
                    int(value["m_nPhonemeCode"]),
                )
                for value in phoneme_values
            )
            emphasis = tuple(
                EmphasisSample(float(value["m_flTime"]), float(value["m_flValue"]))
                for value in emphasis_values
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MalformedSoundError("CTRL sentence contains invalid phoneme or emphasis fields") from exc

        track = PhonemeTrack(phonemes, emphasis, bool(sentence.get("m_bShouldVoiceDuck", False)))
        validate_track(track)
        return track

    @property
    def phoneme_track(self) -> PhonemeTrack | None:
        track = self._parsed().phoneme_track
        if track is not None:
            return track
        ctrl = self.source_metadata
        return self._track_from_ctrl(ctrl) if ctrl is not None else None

    def _artifact_name(self) -> str:
        name = str(self._filepath).replace("\\", "/").rsplit("/", 1)[-1]
        if name in ("", "<memory>"):
            return "sound"
        for suffix in (".vsnd_c", ".vsnd"):
            if name.lower().endswith(suffix):
                return name[:-len(suffix)]
        return name.rsplit(".", 1)[0] if "." in name else name

    def extract(self) -> SoundArtifact:
        metadata = self.metadata
        payload = self.raw_payload
        audio_bytes = build_wave(metadata, payload) if metadata.container is SoundContainer.WAV else payload
        name = self._artifact_name()
        track = self.phoneme_track
        companions = []
        if track is not None:
            companions.append(SoundCompanion(
                f"{name}.txt",
                track.to_compiler_bytes(),
                "text/x-valve-sentence",
            ))
        ctrl = self.source_metadata
        if ctrl is not None:
            companions.append(SoundCompanion(
                f"{name}.vsnd",
                ctrl_to_compiler_text(ctrl).encode("utf-8"),
                "text/x-valve-kv3",
            ))
        return SoundArtifact(name, audio_bytes, metadata, track, tuple(companions))

    def get_sound(self) -> bytes:
        return self.extract().audio_bytes

    def extract_to(self, directory, *, overwrite: bool = False):
        return self.extract().write_to(directory, overwrite=overwrite)


def register_sound_resource(registry: "ResourceRegistry") -> "ResourceRegistration":
    return registry.register(
        ResourceKind.SOUND,
        CompiledSoundResource,
        extensions=(".vsnd_c",),
        capabilities=READ_EXTRACT_CAPABILITIES,
        supported_versions=SUPPORTED_SOUND_VERSIONS,
        control_signatures=("CVoiceContainerDefault", "CVoiceContainerEnvelope"),
        priority=10,
    )
