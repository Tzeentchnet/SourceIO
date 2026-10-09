import bpy
from bpy.props import CollectionProperty, FloatProperty, IntProperty, StringProperty

from ..operators.flex_operators import SourceIO_PG_FlexController
from ..operators.shared_operators import SOURCEIO_UL_MountedResource


def register_props():
    bpy.types.Scene.TextureCachePath = StringProperty(name="TextureCachePath", subtype="FILE_PATH")

    bpy.types.Scene.use_bvlg = bpy.props.BoolProperty(
        name="Use BVLG",
        default=False
    )
    bpy.types.Scene.use_instances = bpy.props.BoolProperty(
        name="Use instances",
        default=True
    )
    bpy.types.Scene.import_materials = bpy.props.BoolProperty(
        name="Import materials",
        default=True
    )
    bpy.types.Scene.import_physics = bpy.props.BoolProperty(
        name="Import physics",
        default=False
    )
    bpy.types.Scene.replace_entity = bpy.props.BoolProperty(
        name="Replace entity",
        default=True
    )
    bpy.types.Object.flex_controllers = CollectionProperty(type=SourceIO_PG_FlexController)
    bpy.types.Object.flex_controller_index = IntProperty(default=0, options=set())
    bpy.types.Scene.sourceio_flex_lr_balance = FloatProperty(
        name="Left/Right balance",
        description="How much the stereo flex sliders move the left (-1) or right (1) side",
        default=0.0, min=-1.0, max=1.0, options=set()
    )
    bpy.types.Scene.source2_texture_mip_level = IntProperty(
        name="Source 2 texture mip",
        description="Mip level used by Source 2 texture, material, model, and map imports",
        default=0,
        min=0,
    )
    bpy.types.Scene.source2_decode_packed_channels = bpy.props.BoolProperty(
        name="Decode packed channels",
        description="Apply unambiguous Source 2 packed-channel metadata during texture import",
        default=True,
    )

    bpy.types.Scene.mounted_resources = CollectionProperty(type=SOURCEIO_UL_MountedResource)
    bpy.types.Scene.mounted_resources_index = IntProperty(default=0)


def unregister_props():
    del bpy.types.Scene.TextureCachePath
    del bpy.types.Object.flex_controllers
    del bpy.types.Object.flex_controller_index
    del bpy.types.Scene.sourceio_flex_lr_balance
    del bpy.types.Scene.source2_texture_mip_level
    del bpy.types.Scene.source2_decode_packed_channels
    del bpy.types.Scene.use_bvlg
    del bpy.types.Scene.use_instances
    del bpy.types.Scene.replace_entity
    del bpy.types.Scene.mounted_resources
    del bpy.types.Scene.mounted_resources_index
    del bpy.types.Scene.import_materials
    del bpy.types.Scene.import_physics
