import bpy
from bpy.props import BoolProperty


ADDON_PACKAGE = __package__.rsplit(".blender_bindings", 1)[0]


class SourceIOPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PACKAGE

    use_source1_dds: BoolProperty(
        name="Use direct DDS staging for Source 1 textures",
        description=(
            "Load compatible single-frame DXT1/DXT5 VTF textures directly as DDS; "
            "unsupported and transformed textures still use decoded PNG images"
        ),
        default=True,
    )

    principled_backface_culling: BoolProperty(
        name="Cull Principled VertexLitGeneric backfaces",
        description=(
            "Apply Source backface culling to newly imported Principled VertexLitGeneric materials; "
            "$nocull materials remain two-sided"
        ),
        default=False,
    )

    def draw(self, context):
        self.layout.prop(self, "use_source1_dds")
        self.layout.prop(self, "principled_backface_culling")


def _addon_preferences(context=None):
    context = context or bpy.context
    preferences = getattr(context, "preferences", None)
    if preferences is None:
        return None
    addon = preferences.addons.get(ADDON_PACKAGE)
    return getattr(addon, "preferences", None)


def source1_dds_enabled(context=None) -> bool:
    preferences = _addon_preferences(context)
    return True if preferences is None else bool(getattr(preferences, "use_source1_dds", True))


def principled_backface_culling_enabled(context=None) -> bool:
    preferences = _addon_preferences(context)
    return bool(preferences and preferences.principled_backface_culling)
