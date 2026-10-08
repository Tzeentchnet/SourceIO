import sys

# The code base imports itself as ``SourceIO``; alias whatever name Blender loaded us under
# (a legacy add-on folder name, or ``bl_ext.<repository>.sourceio`` for an extension).
if "SourceIO" not in sys.modules:
    sys.modules['SourceIO'] = sys.modules[__name__]

from SourceIO.library import loaded_as_addon, running_in_blender

try:
    import bpy
    if bpy.app.version < (5, 2, 0):
        raise Exception("SourceIO only supports Blender 5.2.0 and above")
except ImportError:
    bpy = ...

bl_info = {
    "name": "SourceIO",
    "author": "RED_EYE, ShadelessFox, Syborg64",
    "version": (5, 5, 5),
    "blender": (5, 2, 0),
    "location": "File > Import > SourceEngine assets",
    "description": "GoldSrc/Source1/Source2 Engine assets(.mdl, .bsp, .vmt, .vtf, .vmdl_c, .vwrld_c, .vtex_c)"
                   "Notice that you cannot delete this addon via blender UI, remove it manually from addons folder",
    "category": "Import-Export"
}

import warnings
warnings.simplefilter("always", DeprecationWarning)

if running_in_blender() and loaded_as_addon():
    from SourceIO.blender_bindings.bindings import register, unregister
    if __name__ == "__main__":
        register()
