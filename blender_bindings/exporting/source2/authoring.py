from __future__ import annotations

import tempfile
import datetime
import struct
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterable

import bpy
import bmesh
import numpy as np

from ....library.source2.authoring import (
    AuthoringGrouping,
    AuthoringMaterial,
    AuthoringScope,
    AuthoringTexture,
    ExtractedAuthoringModel,
    MaterialAlphaMode,
    Source2AuthoringRequest,
    TextureRole,
    suggest_asset_name,
)
from ....library.source2.export import (
    DiagnosticSeverity,
    LossKind,
    LossReport,
)
from ....library.source2.workshop import ContentRelativePath
from ....library.utils.math_utilities import SOURCE2_HAMMER_UNIT_TO_METERS
from ...shared.model_container import ModelContainer
from .modeldoc import model_document_from_blender


_TEXTURE_INPUTS = {
    TextureRole.BASE_COLOR: "Base Color",
    TextureRole.ROUGHNESS: "Roughness",
    TextureRole.METALLIC: "Metallic",
}


class _NonGrayscaleScalarTextureError(ValueError):
    pass


def _objects_in_collection(collection) -> tuple:
    objects = []
    seen = set()
    for obj in collection.all_objects:
        if obj.type == "MESH" and obj.as_pointer() not in seen:
            seen.add(obj.as_pointer())
            objects.append(obj)
    return tuple(objects)


def resolve_authoring_objects(context, scope: AuthoringScope) -> tuple:
    if scope == AuthoringScope.SELECTED:
        objects = tuple(obj for obj in context.selected_objects if obj.type == "MESH")
    elif scope == AuthoringScope.COLLECTION:
        if context.collection is None:
            return ()
        objects = _objects_in_collection(context.collection)
    elif scope == AuthoringScope.SCENE:
        objects = tuple(obj for obj in context.scene.objects if obj.type == "MESH")
    else:
        raise ValueError(f"Unsupported authoring scope: {scope}")
    return tuple(sorted(objects, key=lambda obj: (obj.name.casefold(), obj.name)))


def _material_resource_path(
        material,
        report: LossReport,
) -> ContentRelativePath | None:
    explicit = material.get("sourceio_export_material_path")
    value = explicit or material.get("full_path") or material.get("source_path")
    if not value:
        return None
    raw = str(value).split("?", 1)[0].replace("\\", "/")
    if explicit is None and raw.endswith(".vmat_c"):
        raw = raw[:-2]
    try:
        path = ContentRelativePath(raw)
    except ValueError:
        if explicit is not None:
            report.record(
                "authoring.material.override.invalid",
                "The explicit SourceIO material path is not a safe content-relative path.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
                path=("materials", material.name),
                details={"value": str(explicit)},
            )
        return None
    if path.suffix != ".vmat" or path.name.endswith("_c"):
        if explicit is not None:
            report.record(
                "authoring.material.override.invalid",
                "The explicit SourceIO material path must name an editable .vmat resource.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
                path=("materials", material.name),
                details={"value": str(explicit)},
            )
        return None
    return path


def _linked_node(socket):
    if not socket.is_linked or len(socket.links) != 1:
        return None
    return socket.links[0].from_node


def _image_texture(socket):
    if socket is None or not socket.is_linked or len(socket.links) != 1:
        return None
    link = socket.links[0]
    node = link.from_node
    if (
            node.bl_idname != "ShaderNodeTexImage"
            or node.image is None
            or link.from_socket.name != "Color"
    ):
        return None
    return node.image


def _normal_image(socket):
    normal = _linked_node(socket)
    if normal is None:
        return None
    if normal.bl_idname != "ShaderNodeNormalMap":
        return False
    strength = normal.inputs.get("Strength")
    if (
            strength is None
            or strength.is_linked
            or float(strength.default_value) != 1.0
            or normal.space != "TANGENT"
            or normal.uv_map
    ):
        return False
    image = _image_texture(normal.inputs.get("Color"))
    return image if image is not None else False


def _encode_image_tga(
        image,
        *,
        srgb: bool = False,
        invert_green: bool = False,
        require_grayscale: bool = False,
) -> bytes:
    width, height = (int(value) for value in image.size)
    if width <= 0 or height <= 0:
        raise ValueError(f'Cannot author unloaded image "{image.name}"')
    pixels = np.empty(len(image.pixels), dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, 4)
    if require_grayscale:
        rgb = pixels[..., :3]
        channel_spread = np.max(rgb, axis=2) - np.min(rgb, axis=2)
        if np.any(channel_spread > 1.0 / 255.0):
            raise _NonGrayscaleScalarTextureError(
                f'{image.name} must have matching RGB channels for a scalar texture'
            )
    if invert_green:
        pixels[..., 1] = 1.0 - pixels[..., 1]
    if srgb:
        rgb = np.clip(pixels[..., :3], 0.0, None)
        pixels[..., :3] = np.where(
            rgb <= 0.0031308,
            rgb * 12.92,
            1.055 * np.power(rgb, 1.0 / 2.4) - 0.055,
        )
    rgba = np.rint(np.clip(pixels[::-1], 0.0, 1.0) * 255.0).astype(np.uint8)
    header = struct.pack(
        "<BBBHHBHHHHBB",
        0,
        0,
        2,
        0,
        0,
        0,
        0,
        0,
        width,
        height,
        32,
        0x28,
    )
    return header + rgba[..., (2, 1, 0, 3)].tobytes()


def _alpha_mode(material, principled) -> MaterialAlphaMode:
    legacy = str(getattr(material, "blend_method", "")).casefold()
    if legacy == "clip":
        return MaterialAlphaMode.MASK
    alpha = principled.inputs.get("Alpha")
    if alpha is not None and (alpha.is_linked or float(alpha.default_value) < 1.0):
        return MaterialAlphaMode.BLEND
    return MaterialAlphaMode.OPAQUE


def _extract_principled_material(
        material,
        name: str,
        report: LossReport,
) -> AuthoringMaterial | None:
    if material.node_tree is None:
        report.record(
            "authoring.material.nodes.missing",
            "Material authoring requires a supported Principled BSDF node graph.",
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.MISSING,
            path=("materials", material.name),
        )
        return None
    outputs = [
        node for node in material.node_tree.nodes
        if node.bl_idname == "ShaderNodeOutputMaterial" and node.is_active_output
    ]
    if len(outputs) != 1:
        report.record(
            "authoring.material.output.ambiguous",
            "Material authoring requires exactly one active Material Output node.",
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.SAFETY,
            path=("materials", material.name),
        )
        return None
    surface = outputs[0].inputs.get("Surface")
    principled = _linked_node(surface) if surface is not None else None
    if principled is None or principled.bl_idname != "ShaderNodeBsdfPrincipled":
        report.record(
            "authoring.material.principled.required",
            "The active Material Output must be linked directly to one Principled BSDF.",
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.DEFERRED,
            path=("materials", material.name),
        )
        return None

    supported_inputs = {"Base Color", "Metallic", "Roughness", "Alpha", "Normal"}
    for socket in principled.inputs:
        if socket.is_linked and socket.name not in supported_inputs:
            report.record(
                "authoring.material.input.unsupported",
                f"{socket.name} is outside the supported static PBR subset.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.DEFERRED,
                path=("materials", material.name, socket.name),
            )

    textures = []
    images_by_role = {}
    for role, input_name in _TEXTURE_INPUTS.items():
        socket = principled.inputs.get(input_name)
        if socket is None or not socket.is_linked:
            continue
        image = _image_texture(socket)
        if image is None:
            report.record(
                "authoring.material.input.unsupported",
                f"{input_name} must be unlinked or connected directly to an Image Texture.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.DEFERRED,
                path=("materials", material.name, input_name),
            )
            continue
        texture_name = f"{name}_{role.value}"
        images_by_role[role] = image
        scalar_role = role in {
            TextureRole.METALLIC,
            TextureRole.ROUGHNESS,
        }
        if scalar_role:
            try:
                payload = _encode_image_tga(
                    image,
                    require_grayscale=True,
                )
            except _NonGrayscaleScalarTextureError as exc:
                report.record(
                    "authoring.material.scalar_texture.color_unsupported",
                    str(exc),
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.DEFERRED,
                    path=("materials", material.name, input_name),
                )
                continue
        else:
            payload = _encode_image_tga(
                image,
                srgb=role == TextureRole.BASE_COLOR,
            )
        textures.append(AuthoringTexture(
            texture_name,
            role,
            payload,
            extension=".tga",
            srgb=role == TextureRole.BASE_COLOR,
        ))

    alpha_socket = principled.inputs.get("Alpha")
    if alpha_socket is not None and alpha_socket.is_linked:
        alpha_link = alpha_socket.links[0] if len(alpha_socket.links) == 1 else None
        base_image = images_by_role.get(TextureRole.BASE_COLOR)
        if (
            alpha_link is None
            or alpha_link.from_node.bl_idname != "ShaderNodeTexImage"
            or alpha_link.from_node.image is not base_image
            or alpha_link.from_socket.name != "Alpha"
        ):
            report.record(
                "authoring.material.alpha.unsupported",
                "Alpha must come from the authored base-color Image Texture alpha channel.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.DEFERRED,
                path=("materials", material.name, "Alpha"),
            )

    normal_socket = principled.inputs.get("Normal")
    if normal_socket is not None and normal_socket.is_linked:
        image = _normal_image(normal_socket)
        if image is False or image is None:
            report.record(
                "authoring.material.normal.unsupported",
                "Normal must use a default-strength tangent-space Image Texture -> Normal Map -> Principled BSDF graph.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.DEFERRED,
                path=("materials", material.name, "Normal"),
            )
        else:
            texture_name = f"{name}_{TextureRole.NORMAL.value}"
            textures.append(AuthoringTexture(
                texture_name,
                TextureRole.NORMAL,
                _encode_image_tga(
                    image,
                    srgb=False,
                    invert_green=True,
                ),
                extension=".tga",
                srgb=False,
            ))

    base_socket = principled.inputs["Base Color"]
    base_color = (
        (1.0, 1.0, 1.0)
        if base_socket.is_linked
        else tuple(float(value) for value in base_socket.default_value[:3])
    )
    alpha_socket = principled.inputs["Alpha"]
    alpha = 1.0 if alpha_socket.is_linked else float(alpha_socket.default_value)
    if base_socket.is_linked and not alpha_socket.is_linked and alpha != 1.0:
        report.record(
            "authoring.material.alpha.constant_with_texture",
            "A textured base color cannot safely combine with a separate constant alpha.",
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.DEFERRED,
            path=("materials", material.name, "Alpha"),
        )
    base_color = (*base_color, alpha)
    return AuthoringMaterial(
        name=name,
        base_color=base_color,
        metallic=float(principled.inputs["Metallic"].default_value),
        roughness=float(principled.inputs["Roughness"].default_value),
        alpha_mode=_alpha_mode(material, principled),
        alpha_cutoff=float(getattr(material, "alpha_threshold", 0.5)),
        double_sided=not bool(getattr(material, "use_backface_culling", False)),
        textures=tuple(textures),
    )


def _extract_materials(
        objects: Iterable,
        report: LossReport,
) -> tuple[tuple[AuthoringMaterial, ...], dict[int, str]]:
    materials = []
    names_by_pointer = {}
    used_names: dict[str, str] = {}
    seen = set()
    for obj in objects:
        for material in obj.data.materials:
            if material is None or material.as_pointer() in seen:
                continue
            seen.add(material.as_pointer())
            name = suggest_asset_name(material.name)
            existing_name = used_names.get(name.casefold())
            if existing_name is not None and existing_name != material.name:
                report.record(
                    "authoring.material.name.collision",
                    "Material names collide after path-safe normalization.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    path=("materials", material.name),
                    details={"first": existing_name, "suggestion": name},
                )
                continue
            used_names[name.casefold()] = material.name
            if name != material.name:
                report.record(
                    "authoring.material.name.normalized",
                    "The material name is normalized for Source 2 output.",
                    severity=DiagnosticSeverity.WARNING,
                    loss_kind=LossKind.INFERRED,
                    path=("materials", material.name),
                    details={"output_name": name},
                )
            names_by_pointer[material.as_pointer()] = name
            resource_path = _material_resource_path(material, report)
            if resource_path is not None:
                materials.append(AuthoringMaterial(name, existing_resource=resource_path))
                continue
            extracted = _extract_principled_material(
                material,
                name,
                report,
            )
            if extracted is not None:
                materials.append(extracted)
    return tuple(materials), names_by_pointer


@contextmanager
def _preserve_context(context):
    active = context.view_layer.objects.active
    selected = tuple(context.selected_objects)
    mode = active.mode if active is not None else "OBJECT"
    if active is not None and mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        yield
    finally:
        for obj in context.view_layer.objects:
            if obj is not None:
                obj.select_set(False)
        for obj in selected:
            if obj.name in context.view_layer.objects:
                obj.select_set(True)
        if active is not None and active.name in context.view_layer.objects:
            context.view_layer.objects.active = active
            if mode != "OBJECT":
                bpy.ops.object.mode_set(mode=mode)


@contextmanager
def _evaluated_objects(context, source_objects, material_names):
    original_object_names = [(source, source.name) for source in source_objects]
    source_meshes = {
        source.data.as_pointer(): source.data
        for source in source_objects
        if source.type == "MESH"
    }
    original_mesh_names = [
        (mesh, mesh.name)
        for mesh in source_meshes.values()
    ]
    source_materials = {
        material.as_pointer(): material
        for source in source_objects
        for material in source.data.materials
        if material is not None
    }
    original_material_names = [
        (material, material.name)
        for material in source_materials.values()
    ]
    collection = None
    temporary_objects = []
    temporary_meshes = []
    temporary_materials = {}
    try:
        for source, _ in original_object_names:
            source.name = f"__SOURCEIO_OBJECT_{source.as_pointer():x}"
        for mesh, _ in original_mesh_names:
            mesh.name = f"__SOURCEIO_MESH_{mesh.as_pointer():x}"
        for material, _ in original_material_names:
            material.name = f"__SOURCEIO_MATERIAL_{material.as_pointer():x}"
        context.view_layer.update()

        depsgraph = context.evaluated_depsgraph_get()
        collection = bpy.data.collections.new("__SOURCEIO_AUTHORING__")
        context.scene.collection.children.link(collection)
        for source, source_name in original_object_names:
            evaluated = source.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(
                evaluated,
                preserve_all_data_layers=True,
                depsgraph=depsgraph,
            )
            temporary_meshes.append(mesh)
            mesh.name = f"{source_name}_mesh"
            triangulated = bmesh.new()
            try:
                triangulated.from_mesh(mesh)
                faces = [
                    face for face in triangulated.faces
                    if len(face.verts) != 3
                ]
                if faces:
                    bmesh.ops.triangulate(triangulated, faces=faces)
                    triangulated.to_mesh(mesh)
                    mesh.update()
            finally:
                triangulated.free()
            for index, material in enumerate(tuple(mesh.materials)):
                if material is None:
                    continue
                pointer = material.as_pointer()
                temporary = temporary_materials.get(pointer)
                if temporary is None:
                    temporary = material.copy()
                    temporary_materials[pointer] = temporary
                    temporary.name = material_names.get(pointer, suggest_asset_name(material.name))
                    for key in (
                        "full_path",
                        "source_path",
                        "sourceio_export_material_path",
                    ):
                        if key in temporary:
                            del temporary[key]
                mesh.materials[index] = temporary
            obj = bpy.data.objects.new(source_name, mesh)
            temporary_objects.append(obj)
            obj.matrix_world = source.matrix_world.copy()
            collection.objects.link(obj)
        yield tuple(temporary_objects)
    finally:
        try:
            for obj in temporary_objects:
                bpy.data.objects.remove(obj, do_unlink=True)
            for mesh in temporary_meshes:
                if mesh.name in bpy.data.meshes:
                    bpy.data.meshes.remove(mesh)
            for material in temporary_materials.values():
                if material.name in bpy.data.materials:
                    bpy.data.materials.remove(material)
            if collection is not None and collection.name in bpy.data.collections:
                bpy.data.collections.remove(collection)
        finally:
            for material, name in original_material_names:
                material.name = name
            for mesh, name in original_mesh_names:
                mesh.name = name
            for source, name in original_object_names:
                source.name = name
            context.view_layer.update()


def _default_fbx_exporter(path: Path):
    import io_scene_fbx.export_fbx_bin as fbx_exporter

    # Blender embeds the current time in FBX headers, which would destabilize manifests.
    class FixedDateTime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2000, 1, 1, tzinfo=tz)
            return value

    clock = fbx_exporter.datetime
    fbx_exporter.datetime = SimpleNamespace(datetime=FixedDateTime)
    try:
        result = bpy.ops.export_scene.fbx(
            filepath=str(path),
            use_selection=True,
            object_types={"MESH"},
            use_mesh_modifiers=False,
            use_triangles=False,
            bake_anim=False,
            add_leaf_bones=False,
            path_mode="AUTO",
            embed_textures=False,
            axis_forward="-Z",
            axis_up="Y",
            global_scale=1.0 / (100.0 * SOURCE2_HAMMER_UNIT_TO_METERS),
        )
    finally:
        fbx_exporter.datetime = clock
    if "FINISHED" not in result:
        raise RuntimeError(f"Blender FBX export returned {set(result)}")


def _export_group(
        context,
        objects,
        name: str,
        materials,
        material_names,
        report: LossReport,
        fbx_exporter: Callable[[Path], None],
) -> ExtractedAuthoringModel:
    with _evaluated_objects(context, objects, material_names) as evaluated:
        for obj in context.view_layer.objects:
            obj.select_set(False)
        for obj in evaluated:
            obj.select_set(True)
        context.view_layer.objects.active = evaluated[0]
        document = model_document_from_blender(
            ModelContainer(list(evaluated), {}, master_collection=context.collection),
            name,
            report=report,
        )
        with tempfile.TemporaryDirectory(prefix="sourceio_cs2_fbx_") as directory:
            output = Path(directory) / f"{name}.fbx"
            fbx_exporter(output)
            payload = output.read_bytes()
    return ExtractedAuthoringModel(name, document, payload, materials)


def extract_authoring_models(
        context,
        request: Source2AuthoringRequest,
        *,
        report: LossReport | None = None,
        fbx_exporter: Callable[[Path], None] | None = None,
) -> tuple[tuple[ExtractedAuthoringModel, ...], LossReport]:
    loss_report = report if report is not None else LossReport()
    objects = resolve_authoring_objects(context, request.scope)
    if not objects:
        loss_report.record(
            "authoring.selection.empty",
            "The selected authoring scope contains no mesh objects.",
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.MISSING,
        )
        return (), loss_report

    if request.grouping == AuthoringGrouping.COMBINED:
        groups = ((request.asset_name, objects),)
    else:
        groups = tuple((suggest_asset_name(obj.name), (obj,)) for obj in objects)
        names = [name.casefold() for name, _ in groups]
        if len(names) != len(set(names)):
            loss_report.record(
                "authoring.model.name.collision",
                "Batch object names collide after path-safe normalization.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
            )

    exporter = fbx_exporter or _default_fbx_exporter
    materials, material_names = _extract_materials(objects, loss_report)
    if loss_report.has_errors:
        return (), loss_report
    models = []
    with _preserve_context(context):
        for name, group in groups:
            models.append(_export_group(
                context,
                group,
                name,
                materials,
                material_names,
                loss_report,
                exporter,
            ))
    return tuple(models), loss_report
