from __future__ import annotations

import bpy
from bpy.props import BoolProperty, IntProperty, StringProperty

from ...library.source2.exceptions import Source2Error
from ...library.source2.resource_types.compiled_sound_resource import CompiledSoundResource
from ...library.source2.sound import SoundError
from ...library.utils import FileBuffer
from ..source2.vsnd_loader import load_compiled_sound
from .operator_helper import ImportOperatorHelper


class SOURCEIO_OT_VSNDImport(ImportOperatorHelper):
    """Extract a Source 2 compiled sound and optionally add it to Blender."""

    bl_idname = "sourceio.vsnd"
    bl_label = "Import Source2 sound"
    bl_options = {"UNDO"}
    need_popup = True

    filter_glob: StringProperty(default="*.vsnd_c", options={"HIDDEN"})
    output_directory: StringProperty(
        name="Extract directory",
        subtype="DIR_PATH",
        description="Leave empty to use Blender's temporary SourceIO sound cache",
    )
    create_speaker: BoolProperty(name="Create speaker", default=True)
    add_to_timeline: BoolProperty(name="Add sound strip", default=False)
    pack_sound: BoolProperty(name="Pack sound in blend", default=True)
    channel: IntProperty(name="Sound strip channel", default=1, min=1)

    def execute(self, context):
        directory = self.get_directory()
        for file in self.files:
            path = directory / file.name
            try:
                with FileBuffer(path) as buffer:
                    resource = CompiledSoundResource.from_buffer(buffer, path)
                loaded = load_compiled_sound(
                    resource,
                    scene=context.scene,
                    collection=context.collection,
                    output_directory=self.output_directory or None,
                    create_speaker=self.create_speaker,
                    add_to_timeline=self.add_to_timeline,
                    frame_start=context.scene.frame_current,
                    channel=self.channel,
                    pack=self.pack_sound,
                )
            except (OSError, SoundError, Source2Error) as exc:
                self.report({"ERROR"}, f"{file.name}: {exc}")
                return {"CANCELLED"}
            self.report({"INFO"}, f"Extracted {file.name} to {loaded.audio_path}")
        return {"FINISHED"}


classes = (SOURCEIO_OT_VSNDImport,)
