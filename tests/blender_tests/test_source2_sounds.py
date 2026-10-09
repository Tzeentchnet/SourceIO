"""Run inside Blender with SourceIO importable: unittest ...test_source2_sounds."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import bpy

from SourceIO.blender_bindings.source2.vsnd_loader import load_compiled_sound
from SourceIO.library.source2.resource_types.compiled_sound_resource import CompiledSoundResource
from SourceIO.library.source2.sound import (
    EmphasisSample,
    PhonemeTag,
    SoundContainer,
    SoundEncoding,
)
from SourceIO.library.utils import MemoryBuffer, TinyPath
from SourceIO.tests.sound_tests.fixtures import build_vsnd, generated_pcm16


class Source2SoundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        scene = bpy.context.scene
        if scene.sequence_editor is not None:
            scene.sequence_editor_clear()
        for obj in list(bpy.data.objects):
            if obj.type == "SPEAKER":
                bpy.data.objects.remove(obj, do_unlink=True)
        for speaker in list(bpy.data.speakers):
            bpy.data.speakers.remove(speaker)
        for sound in list(bpy.data.sounds):
            bpy.data.sounds.remove(sound)
        self.temp.cleanup()

    @staticmethod
    def resource(with_track: bool = False) -> CompiledSoundResource:
        fixture = build_vsnd(
            4,
            SoundContainer.WAV,
            SoundEncoding.PCM16,
            generated_pcm16(),
            sample_rate=8000,
            phonemes=(PhonemeTag(0.0, 0.001, 97),) if with_track else (),
            emphasis=(EmphasisSample(0.0005, 0.75),) if with_track else (),
            include_sentence=with_track,
        )
        return CompiledSoundResource.from_buffer(MemoryBuffer(fixture.data), TinyPath("blender_test.vsnd_c"))

    def test_loads_packed_sound_and_speaker(self):
        loaded = load_compiled_sound(
            self.resource(),
            output_directory=self.temp.name,
            create_speaker=True,
            pack=True,
        )

        self.assertIsNotNone(loaded.sound.packed_file)
        self.assertEqual(loaded.speaker_object.type, "SPEAKER")
        self.assertIs(loaded.speaker_object.data.sound, loaded.sound)
        self.assertEqual(loaded.sound["sourceio_encoding"], "pcm16")
        self.assertEqual(loaded.sound["sourceio_sample_rate"], 8000)
        self.assertTrue(Path(loaded.audio_path).is_file())

    def test_adds_sound_to_vse_timeline_and_retains_track_metadata(self):
        loaded = load_compiled_sound(
            self.resource(with_track=True),
            scene=bpy.context.scene,
            output_directory=self.temp.name,
            create_speaker=False,
            add_to_timeline=True,
            frame_start=17,
            channel=3,
            pack=False,
        )

        self.assertIsNone(loaded.speaker_object)
        self.assertIsNotNone(loaded.strip)
        self.assertEqual(loaded.strip.frame_final_start, 17)
        self.assertEqual(loaded.strip.channel, 3)
        self.assertIs(loaded.strip.sound, loaded.sound)
        phoneme = loaded.sound["sourceio_phonemes"][0]
        self.assertAlmostEqual(phoneme[0], 0.0)
        self.assertAlmostEqual(phoneme[1], 0.001)
        self.assertEqual(phoneme[2], 97)
        self.assertAlmostEqual(loaded.sound["sourceio_emphasis"][0][1], 0.75)


if __name__ == "__main__":
    unittest.main()
