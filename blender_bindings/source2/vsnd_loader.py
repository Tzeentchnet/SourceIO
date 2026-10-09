from __future__ import annotations

import tempfile
from dataclasses import dataclass
from os import PathLike
from pathlib import Path

import bpy

from ...library.shared.content_manager import ResourceRef, ResourceResolverProtocol
from ...library.source2.resource_types.compiled_sound_resource import CompiledSoundResource
from ...library.source2.sound import SoundArtifact
from ...library.utils import TinyPath


@dataclass(slots=True)
class LoadedSound:
    artifact: SoundArtifact
    sound: bpy.types.Sound
    audio_path: Path
    speaker_object: bpy.types.Object | None = None
    strip: bpy.types.SoundStrip | None = None


def _output_directory(artifact: SoundArtifact, output_directory: str | Path | None) -> Path:
    if output_directory is not None:
        return Path(output_directory)
    root = Path(bpy.app.tempdir or tempfile.gettempdir()) / "SourceIO" / "sounds"
    return root / artifact.sha256[:16]


def _record_metadata(sound: bpy.types.Sound, artifact: SoundArtifact) -> None:
    metadata = artifact.metadata
    sound["sourceio_container"] = metadata.container.value
    sound["sourceio_encoding"] = metadata.encoding.value
    sound["sourceio_sample_rate"] = metadata.sample_rate
    sound["sourceio_channels"] = metadata.channels
    sound["sourceio_sample_count"] = metadata.sample_count
    sound["sourceio_loop_start"] = metadata.loop_start
    sound["sourceio_loop_end"] = metadata.loop_end
    sound["sourceio_duration"] = metadata.duration
    sound["sourceio_sha256"] = artifact.sha256
    if artifact.phoneme_track is not None:
        sound["sourceio_phonemes"] = [
            (phoneme.start_time, phoneme.end_time, phoneme.code)
            for phoneme in artifact.phoneme_track.phonemes
        ]
        sound["sourceio_emphasis"] = [
            (sample.time, sample.value)
            for sample in artifact.phoneme_track.emphasis_samples
        ]
        sound["sourceio_voice_duck"] = artifact.phoneme_track.should_voice_duck


def load_sound_artifact(
        artifact: SoundArtifact,
        *,
        scene: bpy.types.Scene | None = None,
        collection: bpy.types.Collection | None = None,
        output_directory: str | Path | None = None,
        create_speaker: bool = True,
        add_to_timeline: bool = False,
        frame_start: int | None = None,
        channel: int = 1,
        pack: bool = True,
) -> LoadedSound:
    if channel < 1:
        raise ValueError("Sound strip channel must be at least 1")

    scene = scene or bpy.context.scene
    destination = _output_directory(artifact, output_directory)
    paths = artifact.write_to(destination, overwrite=True)
    audio_path = paths[0]

    strip = None
    if add_to_timeline:
        editor = scene.sequence_editor_create()
        strip = editor.strips.new_sound(
            name=artifact.name,
            filepath=str(audio_path),
            channel=channel,
            frame_start=frame_start if frame_start is not None else scene.frame_current,
        )
        sound = strip.sound
    else:
        sound = bpy.data.sounds.load(str(audio_path), check_existing=False)

    if sound is None:
        raise RuntimeError(f"Blender did not create a sound datablock for {audio_path}")
    _record_metadata(sound, artifact)
    if pack and sound.packed_file is None:
        sound.pack()

    speaker_object = None
    if create_speaker:
        speaker = bpy.data.speakers.new(artifact.name)
        speaker.sound = sound
        speaker_object = bpy.data.objects.new(artifact.name, speaker)
        (collection or scene.collection).objects.link(speaker_object)

    return LoadedSound(artifact, sound, audio_path, speaker_object, strip)


def load_compiled_sound(
        resource: CompiledSoundResource,
        **kwargs,
) -> LoadedSound:
    return load_sound_artifact(resource.extract(), **kwargs)


def load_resolved_sound(
        resolver: ResourceResolverProtocol,
        reference: str | PathLike[str] | TinyPath | ResourceRef,
        **kwargs,
) -> LoadedSound:
    return load_compiled_sound(
        CompiledSoundResource.from_resolver(resolver, reference),
        **kwargs,
    )
