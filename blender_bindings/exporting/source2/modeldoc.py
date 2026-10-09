from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace

from mathutils import Vector

from ....library.source2.export import (
    AssetReference,
    Attachment,
    AttachmentInfluence,
    BodyGroup,
    CornerData,
    LODLevel,
    LossReport,
    MaterialRemap,
    MeshFace,
    MeshVertex,
    ModelDocument,
    PhysicsShape,
    Skin,
    StaticMesh,
)
from ....library.source2.provenance import ResourceProvenance
from ....library.utils.math_utilities import SOURCE2_HAMMER_UNIT_TO_METERS
from .provenance import read_import_provenance

LOD_SUFFIX = re.compile(r"_LOD(\d+)(?:\D|$)", re.IGNORECASE)
SOURCEIO_MATERIAL_QUERY = re.compile(r"\?sourceio-[^#]*$", re.IGNORECASE)


def _vector(value, width: int):
    return tuple(float(value[index]) for index in range(width))


def _authored_material_path(material) -> str | None:
    if not hasattr(material, "get"):
        return None
    source_path = material.get("full_path") or material.get("source_path")
    if not source_path:
        return None
    return SOURCEIO_MATERIAL_QUERY.sub("", str(source_path))


def _material_name(obj, polygon, report: LossReport | None = None, path=()) -> str:
    slots = getattr(obj, "material_slots", ())
    material_index = int(getattr(polygon, "material_index", 0))
    if material_index < len(slots):
        material = getattr(slots[material_index], "material", None)
        if material is not None:
            source_path = _authored_material_path(material)
            if source_path:
                return source_path
            if report is not None:
                report.missing(
                    "model.material.resource_path.missing",
                    "A Blender material has no authored Source 2 resource path; its datablock name is used.",
                    path=path,
                    details={"material": material.name},
                )
            return str(material.name)
    materials = getattr(obj.data, "materials", ())
    if material_index < len(materials) and materials[material_index] is not None:
        material = materials[material_index]
        source_path = _authored_material_path(material)
        if source_path:
            return source_path
        if report is not None:
            report.missing(
                "model.material.resource_path.missing",
                "A Blender material has no authored Source 2 resource path; its datablock name is used.",
                path=path,
                details={"material": material.name},
            )
        return str(material.name)
    if report is not None:
        report.missing(
            "model.material.assignment.missing",
            "A mesh face has no material assignment; NullMaterial is used.",
            path=path,
        )
    return "NullMaterial"


def _active_color_data(mesh):
    attributes = getattr(mesh, "color_attributes", None)
    if not attributes:
        return None
    active = getattr(attributes, "active_color", None) or getattr(attributes, "active", None)
    if active is None or getattr(active, "domain", "CORNER") != "CORNER":
        return None
    return active.data


def _transformed_direction(matrix, value):
    direction = matrix @ Vector(value)
    if direction.length_squared:
        direction.normalize()
    return _vector(direction, 3)


def _mesh_from_object(obj, report: LossReport, unit_scale: float) -> StaticMesh:
    mesh = obj.data
    world_matrix = obj.matrix_world.copy()
    direction_matrix = world_matrix.to_3x3()
    normal_matrix = direction_matrix.inverted_safe().transposed()
    tangent_sign = -1.0 if direction_matrix.determinant() < 0 else 1.0
    uv_layers = list(getattr(mesh, "uv_layers", ()))
    color_data = _active_color_data(mesh)
    vertices = []
    faces = []
    if hasattr(mesh, "calc_loop_triangles"):
        mesh.calc_loop_triangles()
        face_records = [
            (mesh.polygons[triangle.polygon_index], tuple(triangle.loops))
            for triangle in mesh.loop_triangles
        ]
        if any(int(polygon.loop_total) != 3 for polygon in mesh.polygons):
            report.inferred(
                "model.mesh.triangulated.blender",
                "Non-triangle Blender polygons were triangulated without changing the scene mesh.",
                path=("meshes", obj.name),
            )
    else:
        face_records = [
            (polygon, tuple(getattr(polygon, "loop_indices", ())))
            for polygon in mesh.polygons
        ]

    for polygon, triangle_loops in face_records:
        face_vertices = []
        loop_indices = list(triangle_loops)
        if not loop_indices:
            start = int(polygon.loop_start)
            loop_indices = range(start, start + int(polygon.loop_total))
        for loop_index in loop_indices:
            loop = mesh.loops[loop_index]
            vertex = mesh.vertices[loop.vertex_index]
            normal_value = getattr(loop, "normal", getattr(vertex, "normal", None))
            tangent_value = getattr(loop, "tangent", None)
            tangent = None
            if tangent_value is not None:
                tangent = _transformed_direction(direction_matrix, tangent_value) + (
                    float(getattr(loop, "bitangent_sign", 1.0)) * tangent_sign,
                )
            texcoords = tuple(
                (float(layer.data[loop_index].uv[0]), 1.0 - float(layer.data[loop_index].uv[1]))
                for layer in uv_layers
            )
            color = None
            if color_data is not None and loop_index < len(color_data):
                color_value = getattr(color_data[loop_index], "color", None)
                if color_value is not None:
                    color = _vector(color_value, 4)

            group_values = sorted(
                (
                    int(group.group),
                    float(group.weight),
                )
                for group in getattr(vertex, "groups", ())
                if float(group.weight) != 0.0
            )
            bone_indices = tuple(group for group, _ in group_values)
            bone_weights = tuple(weight for _, weight in group_values)
            world_position = world_matrix @ vertex.co
            vertices.append(MeshVertex(
                position=tuple(float(value) / unit_scale for value in world_position),
                corner=CornerData(
                    normal=_transformed_direction(normal_matrix, normal_value)
                    if normal_value is not None else None,
                    tangent=tangent,
                    texcoords=texcoords,
                    color=color,
                    bone_indices=bone_indices,
                    bone_weights=bone_weights,
                ),
            ))
            face_vertices.append(len(vertices) - 1)
        faces.append(MeshFace(
            tuple(face_vertices),
            _material_name(
                obj,
                polygon,
                report,
                ("meshes", obj.name, "faces", int(getattr(polygon, "index", len(faces))), "material"),
            ),
        ))

    lod_match = LOD_SUFFIX.search(obj.name)
    lods = (int(lod_match.group(1)),) if lod_match else (0,)
    return StaticMesh(
        name=obj.name,
        vertices=tuple(vertices),
        faces=tuple(faces),
        lods=lods,
        metadata={"blender_object": obj.name},
    )


def _provenance(container):
    owners = [
        getattr(container, "master_collection", None),
        getattr(container, "armature", None),
        *getattr(container, "objects", ()),
    ]
    for owner in owners:
        if owner is None:
            continue
        payload = read_import_provenance(owner)
        if payload and isinstance(payload.get("provenance"), dict):
            return ResourceProvenance.from_dict(payload["provenance"])
    return None


def _source_unit_scale(
        provenance: ResourceProvenance | None,
        explicit_scale: float | None,
) -> float:
    value = explicit_scale
    if value is None and provenance is not None:
        settings = provenance.metadata.get("import_settings")
        if isinstance(settings, Mapping):
            value = settings.get("scale")
    if value is None:
        value = SOURCE2_HAMMER_UNIT_TO_METERS
    try:
        scale = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid Source 2 unit scale {value!r}") from exc
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError(f"Source 2 unit scale must be finite and greater than zero, got {value!r}")
    return scale


def model_document_from_blender(
        container,
        name: str,
        *,
        unit_scale: float | None = None,
        report: LossReport | None = None,
) -> ModelDocument:
    loss_report = report if report is not None else LossReport()
    provenance = _provenance(container)
    unit_scale = _source_unit_scale(provenance, unit_scale)
    object_meshes = {}
    meshes = []
    for obj in getattr(container, "objects", ()):
        if getattr(obj, "type", None) != "MESH":
            continue
        mesh = _mesh_from_object(obj, loss_report, unit_scale)
        object_meshes[id(obj)] = mesh.name
        meshes.append(mesh)

    bodygroups = []
    mesh_bodygroups = defaultdict(list)
    for group_name, objects in getattr(container, "bodygroups", {}).items():
        members = tuple(object_meshes[id(obj)] for obj in objects if id(obj) in object_meshes)
        if not members:
            continue
        bodygroups.append(BodyGroup(str(group_name), (members,)))
        for member in members:
            mesh_bodygroups[member].append(str(group_name))
        loss_report.inferred(
            "model.bodygroup.choices.blender",
            "Imported Blender collections retain membership but not original bodygroup choice boundaries.",
            path=("bodygroups", str(group_name)),
        )
    meshes = [
        replace(mesh, bodygroups=tuple(mesh_bodygroups.get(mesh.name, ())))
        for mesh in meshes
    ]

    lod_members = defaultdict(list)
    for mesh in meshes:
        for lod in mesh.lods:
            lod_members[lod].append(mesh.name)
    lods = tuple(
        LODLevel(index, 0.0, tuple(members))
        for index, members in sorted(lod_members.items())
    )
    if len(lods) > 1:
        loss_report.missing(
            "model.lod.switch_distance.blender",
            "Imported object names retain LOD indices but not switch distances; zero is written and reported.",
            path=("lods",),
        )

    skin_remaps = defaultdict(dict)
    default_skin = None
    for obj in getattr(container, "objects", ()):
        groups = obj.get("skin_groups", {}) if hasattr(obj, "get") else {}
        active = obj.get("active_skin") if hasattr(obj, "get") else None
        default_skin = default_skin or active
        if not hasattr(groups, "items"):
            continue
        base_material = _material_name(
            obj,
            next(iter(obj.data.polygons), type("Polygon", (), {"material_index": 0})()),
            loss_report,
            ("skins", obj.name, "base_material"),
        )
        for skin_name, target in groups.items():
            skin_remaps[str(skin_name)][base_material] = SOURCEIO_MATERIAL_QUERY.sub("", str(target))
    skins = tuple(
        Skin(
            name=skin_name,
            remaps=tuple(
                MaterialRemap(source, target)
                for source, target in sorted(remaps.items())
            ),
            is_default=skin_name == default_skin,
        )
        for skin_name, remaps in sorted(skin_remaps.items())
    )

    attachments = []
    for obj in getattr(container, "attachments", ()):
        quaternion = getattr(obj, "rotation_quaternion", (1.0, 0.0, 0.0, 0.0))
        w, x, y, z = map(float, quaternion)
        attachments.append(Attachment(
            name=obj.name,
            influences=(AttachmentInfluence(
                parent_bone=str(getattr(obj, "parent_bone", "")),
                origin=tuple(float(value) / unit_scale for value in obj.location),
                rotation=(x, y, z, w),
            ),),
        ))

    physics_shapes = []
    for obj in getattr(container, "physics_objects", ()):
        if getattr(obj, "type", None) != "MESH":
            continue
        mesh = _mesh_from_object(obj, loss_report, unit_scale)
        physics_shapes.append(PhysicsShape(
            name=obj.name,
            kind="mesh",
            vertices=tuple(vertex.position for vertex in mesh.vertices),
            faces=tuple(face.vertices for face in mesh.faces),
        ))

    flex_references = []
    for obj in getattr(container, "objects", ()):
        shape_keys = getattr(getattr(obj, "data", None), "shape_keys", None)
        for key in getattr(shape_keys, "key_blocks", ()):
            if key.name != "Basis":
                flex_references.append(AssetReference("flex", f"shape-key://{obj.name}/{key.name}", key.name))

    animation_references = []
    armature = getattr(container, "armature", None)
    animation_data = getattr(armature, "animation_data", None) if armature is not None else None
    action = getattr(animation_data, "action", None)
    if action is not None:
        animation_references.append(AssetReference("animation", f"action://{action.name}", action.name))
    for track in getattr(animation_data, "nla_tracks", ()) if animation_data is not None else ():
        for strip in track.strips:
            strip_action = getattr(strip, "action", None)
            if strip_action is not None:
                animation_references.append(
                    AssetReference("animation", f"action://{strip_action.name}", strip_action.name)
                )
    if animation_references:
        loss_report.deferred(
            "model.animation.blender_actions",
            "Blender actions are referenced in the sidecar; a separate animation-document exporter is required.",
            path=("animation_references",),
        )

    return ModelDocument(
        name=name,
        meshes=tuple(meshes),
        bodygroups=tuple(bodygroups),
        lods=lods,
        skins=skins,
        attachments=tuple(attachments),
        physics_shapes=tuple(physics_shapes),
        flex_references=tuple(flex_references),
        animation_references=tuple(animation_references),
        provenance=provenance,
        metadata={"source": "blender", "source_unit_scale": unit_scale},
    )
