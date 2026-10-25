from typing import TextIO

import bpy
from bpy.types import Node

from .base_node import SourceIOModelTreeNode


class SourceIOModelNode(Node, SourceIOModelTreeNode):
    bl_idname = 'SourceIOModelNode'
    bl_label = "Model output (deprecated)"
    model_name: bpy.props.StringProperty(name="$modelname")
    mostly_opaque: bpy.props.BoolProperty(name="$mostlyopaque", default=True)
    ambient_boost: bpy.props.BoolProperty(name="$ambientboost", default=True)

    def init(self, context):
        self.inputs.new('SourceIOObjectSocket', "Objects").link_limit = 32
        self.inputs.new('SourceIOBodygroupSocket', "Bodygroups").link_limit = 32
        self.inputs.new('SourceIOSkinSocket', "Skin")
        self.inputs.new('SourceIOTextureVtfSocket', "Textures")

    def draw_buttons(self, context, layout):
        layout.label(text="Deprecated: use File > Export", icon="ERROR")
        layout.operator(
            "SourceIO.evaluate_nodetree",
            text="Open supported export guidance",
        )
        layout.prop(self, 'model_name')
        layout.prop(self, 'mostly_opaque')
        layout.prop(self, 'ambient_boost')

    def write(self, buffer: TextIO):
        buffer.write(f'$modelname "{self.model_name}"\n')
        if self.mostly_opaque:
            buffer.write(f'$mostlyopaque\n')
        if self.ambient_boost:
            buffer.write(f'$ambientboost\n')
