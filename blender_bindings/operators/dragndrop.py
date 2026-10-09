import bpy


def _in_window(context):
    return context.region is not None and context.region.type == 'WINDOW'


def _in_3d_view(context):
    return _in_window(context) and context.area is not None and context.area.ui_type == 'VIEW_3D'


# noinspection PyPep8Naming
class IMAGE_FH_vtf_import(bpy.types.FileHandler):
    bl_idname = "IMAGE_FH_vtf_import"
    bl_label = "Source texture (.vtf)"
    bl_import_operator = "sourceio.vtf"
    bl_file_extensions = ".vtf"

    @classmethod
    def poll_drop(cls, context):
        return _in_window(context)


# noinspection PyPep8Naming
class IMAGE_FH_vtex_import(bpy.types.FileHandler):
    bl_idname = "IMAGE_FH_vtex_import"
    bl_label = "Source2 texture (.vtex_c)"
    bl_import_operator = "sourceio.vtex"
    bl_file_extensions = ".vtex_c"

    @classmethod
    def poll_drop(cls, context):
        return _in_window(context)


# noinspection PyPep8Naming
class OBJECT_FH_mdl_import(bpy.types.FileHandler):
    bl_idname = "OBJECT_FH_mdl_import"
    bl_label = "GoldSrc/Source model (.mdl)"
    bl_import_operator = "sourceio.mdl"
    bl_file_extensions = ".mdl;.md3"

    @classmethod
    def poll_drop(cls, context):
        return _in_3d_view(context)


# noinspection PyPep8Naming
class OBJECT_FH_vmdl_import(bpy.types.FileHandler):
    bl_idname = "OBJECT_FH_vmdl_import"
    bl_label = "Source2 model (.vmdl_c)"
    bl_import_operator = "sourceio.vmdl"
    bl_file_extensions = ".vmdl_c"

    @classmethod
    def poll_drop(cls, context):
        return _in_3d_view(context)


# noinspection PyPep8Naming
class OBJECT_FH_vnmclip_import(bpy.types.FileHandler):
    bl_idname = "OBJECT_FH_vnmclip_import"
    bl_label = "Source2 animation clip (.vnmclip_c)"
    bl_import_operator = "sourceio.vnmclip"
    bl_file_extensions = ".vnmclip_c"

    @classmethod
    def poll_drop(cls, context):
        return _in_3d_view(context) and context.active_object is not None and context.active_object.type == 'ARMATURE'


# noinspection PyPep8Naming
class OBJECT_FH_source2_animation_import(bpy.types.FileHandler):
    bl_idname = "OBJECT_FH_source2_animation_import"
    bl_label = "Source2 animation (.vanim_c/.vagrp_c)"
    bl_import_operator = "sourceio.source2_animation"
    bl_file_extensions = ".vanim_c;.vagrp_c"

    @classmethod
    def poll_drop(cls, context):
        return _in_3d_view(context) and any(obj.type == 'ARMATURE' for obj in context.selected_objects)


# noinspection PyPep8Naming
class OBJECT_FH_vsnd_import(bpy.types.FileHandler):
    bl_idname = "OBJECT_FH_vsnd_import"
    bl_label = "Source2 sound (.vsnd_c)"
    bl_import_operator = "sourceio.vsnd"
    bl_file_extensions = ".vsnd_c"

    @classmethod
    def poll_drop(cls, context):
        return _in_window(context)


# noinspection PyPep8Naming
class OBJECT_FH_vphys_import(bpy.types.FileHandler):
    bl_idname = "OBJECT_FH_vphys_import"
    bl_label = "Source2 physics (.vphys_c)"
    bl_import_operator = "sourceio.vphys"
    bl_file_extensions = ".vphys_c"

    @classmethod
    def poll_drop(cls, context):
        return _in_3d_view(context)


# noinspection PyPep8Naming
class OBJECT_FH_dmx_camera_import(bpy.types.FileHandler):
    bl_idname = "OBJECT_FH_dmx_camera_import"
    bl_label = "Valve camera (.dmx)"
    bl_import_operator = "sourceio.dmx_camera"
    bl_file_extensions = ".dmx"

    @classmethod
    def poll_drop(cls, context):
        return _in_3d_view(context)


# noinspection PyPep8Naming
class MATERIAL_FH_vmt_import(bpy.types.FileHandler):
    bl_idname = "MATERIAL_FH_vmt_import"
    bl_label = "Source material (.vmt)"
    bl_import_operator = "sourceio.vmt"
    bl_file_extensions = ".vmt"

    @classmethod
    def poll_drop(cls, context):
        return _in_window(context)


# noinspection PyPep8Naming
class OBJECT_FH_bsp_import(bpy.types.FileHandler):
    bl_idname = "OBJECT_FH_bsp_import"
    bl_label = "Source map (.bsp)"
    bl_import_operator = "sourceio.bsp"
    bl_file_extensions = ".bsp"

    @classmethod
    def poll_drop(cls, context):
        return _in_window(context)


# noinspection PyPep8Naming
class OBJECT_FH_vmap_import(bpy.types.FileHandler):
    bl_idname = "OBJECT_FH_vmap_import"
    bl_label = "Source2 map (.vmap_c)"
    bl_import_operator = "sourceio.vmap"
    bl_file_extensions = ".vmap_c"

    @classmethod
    def poll_drop(cls, context):
        return _in_window(context)


# noinspection PyPep8Naming
class OBJECT_FH_vmap_vpk_import(bpy.types.FileHandler):
    bl_idname = "OBJECT_FH_vmap_vpk_import"
    bl_label = "Source2 packed map (.vpk)"
    bl_import_operator = "sourceio.vmap_vpk"
    bl_file_extensions = ".vpk"

    @classmethod
    def poll_drop(cls, context):
        return _in_window(context)


# noinspection PyPep8Naming
class MATERIAL_FH_vmat_import(bpy.types.FileHandler):
    bl_idname = "MATERIAL_FH_vmat_import"
    bl_label = "Source2 material (.vmat_c)"
    bl_import_operator = "sourceio.vmat"
    bl_file_extensions = ".vmat_c"

    @classmethod
    def poll_drop(cls, context):
        return _in_window(context)


file_handler_classes = (
    IMAGE_FH_vtf_import,
    IMAGE_FH_vtex_import,
    OBJECT_FH_mdl_import,
    OBJECT_FH_vmdl_import,
    OBJECT_FH_vnmclip_import,
    OBJECT_FH_source2_animation_import,
    OBJECT_FH_vsnd_import,
    OBJECT_FH_vphys_import,
    OBJECT_FH_dmx_camera_import,
    MATERIAL_FH_vmt_import,
    OBJECT_FH_bsp_import,
    OBJECT_FH_vmap_import,
    OBJECT_FH_vmap_vpk_import,
    MATERIAL_FH_vmat_import,
)
