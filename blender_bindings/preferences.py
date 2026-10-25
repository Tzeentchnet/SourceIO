import bpy
from bpy.props import BoolProperty


ADDON_PACKAGE = __package__.rsplit(".blender_bindings", 1)[0]


class SourceIOPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PACKAGE

    principled_backface_culling: BoolProperty(
        name="Cull Principled VertexLitGeneric backfaces",
        description=(
            "Apply Source backface culling to newly imported Principled VertexLitGeneric materials; "
            "$nocull materials remain two-sided"
        ),
        default=False,
    )

    def draw(self, context):
        self.layout.prop(self, "principled_backface_culling")


def principled_backface_culling_enabled(context=None) -> bool:
    context = context or bpy.context
    preferences = getattr(context, "preferences", None)
    if preferences is None:
        return False
    addon = preferences.addons.get(ADDON_PACKAGE)
    return bool(addon and addon.preferences.principled_backface_culling)
