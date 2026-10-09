from __future__ import annotations

import json
import math
from pathlib import PurePosixPath

import numpy as np

from ...source1.dmx.source1_to_dmx import DmxModel2
from ...utils import datamodel
from ..utils.kv3_generator import KV3mdl
from .bundle import ExportBundle
from .diagnostics import DiagnosticSeverity, LossKind, LossReport
from .domain import AttachmentInfluence, ModelDocument, PhysicsShape, StaticMesh
from .geometry import weld_vertices


def _validate_source_string(value: str, field: str):
    if any(character in value for character in ('"', "\r", "\n", "\0")):
        raise ValueError(f"{field} contains characters that cannot be represented safely")


def _output_stem(document: ModelDocument, stem: str | None) -> str:
    value = stem or document.name
    path = PurePosixPath(value.replace("\\", "/"))
    if path.name != value or value in ("", ".", ".."):
        raise ValueError("ModelDoc output stem must be a single non-empty file name")
    _validate_source_string(value, "ModelDoc output stem")
    return value


def _add_attribute(mesh, dmx_name: str, values: np.ndarray, value_type):
    vertex_data = mesh["bindState"]
    vertex_data["vertexFormat"].append(dmx_name)
    vertex_data[dmx_name] = datamodel.make_array(values, value_type)
    vertex_data[dmx_name + "Indices"] = datamodel.make_array(np.arange(len(values)), int)


def _write_mesh(dmx: DmxModel2, mesh: StaticMesh, report: LossReport):
    welded = weld_vertices(mesh, report=report).mesh
    dmx_mesh = dmx.add_mesh(welded.name)
    positions = np.asarray([vertex.position for vertex in welded.vertices], dtype=np.float32)
    dmx.mesh_add_attribute(dmx_mesh, "pos", positions, datamodel.Vector3)

    normals = [vertex.corner.normal for vertex in welded.vertices]
    if normals and all(normal is not None for normal in normals):
        dmx.mesh_add_attribute(
            dmx_mesh,
            "norm",
            np.asarray(normals, dtype=np.float32),
            datamodel.Vector3,
        )
    else:
        report.missing(
            "model.mesh.normals.missing",
            "The DMX omits normals for this mesh; Resource Compiler may infer them.",
            path=("meshes", welded.name),
        )

    tangents = [vertex.corner.tangent for vertex in welded.vertices]
    if tangents and all(tangent is not None for tangent in tangents):
        _add_attribute(
            dmx_mesh,
            "tangent$0",
            np.asarray(tangents, dtype=np.float32),
            datamodel.Vector4,
        )
    elif any(tangent is not None for tangent in tangents):
        report.missing(
            "model.mesh.tangents.partial",
            "Partial tangent data cannot be represented in DMX and was retained only in the sidecar.",
            path=("meshes", welded.name),
        )

    uv_layer_count = max((len(vertex.corner.texcoords) for vertex in welded.vertices), default=0)
    for layer_index in range(uv_layer_count):
        values = [
            vertex.corner.texcoords[layer_index]
            for vertex in welded.vertices
            if len(vertex.corner.texcoords) > layer_index
        ]
        if len(values) != len(welded.vertices):
            report.missing(
                "model.mesh.uv.partial",
                f"UV layer {layer_index} is incomplete and was retained only in the sidecar.",
                path=("meshes", welded.name, "uv", layer_index),
            )
            continue
        if layer_index == 0:
            dmx.mesh_add_attribute(
                dmx_mesh,
                "texco",
                np.asarray(values, dtype=np.float32),
                datamodel.Vector2,
            )
        else:
            _add_attribute(
                dmx_mesh,
                f"texcoord${layer_index}",
                np.asarray(values, dtype=np.float32),
                datamodel.Vector2,
            )

    colors = [vertex.corner.color for vertex in welded.vertices]
    if colors and all(color is not None for color in colors):
        _add_attribute(
            dmx_mesh,
            "color$0",
            np.asarray(colors, dtype=np.float32),
            datamodel.Vector4,
        )
    elif any(color is not None for color in colors):
        report.missing(
            "model.mesh.colors.partial",
            "Partial color data cannot be represented in DMX and was retained only in the sidecar.",
            path=("meshes", welded.name),
        )

    if any(vertex.corner.bone_indices or vertex.corner.bone_weights for vertex in welded.vertices):
        report.deferred(
            "model.mesh.skinning.static_slice",
            "Bone weights are retained in the sidecar; this vertical slice emits static DMX geometry.",
            path=("meshes", welded.name),
        )
    if any(vertex.corner.custom_attributes for vertex in welded.vertices):
        report.deferred(
            "model.mesh.custom_attributes.sidecar",
            "Custom vertex streams are retained in the sidecar but are not emitted to DMX.",
            path=("meshes", welded.name),
        )

    material_faces: dict[str, list[int]] = {}
    for face_index, face in enumerate(welded.faces):
        if len(face.vertices) != 3:
            report.record(
                "model.mesh.face.not_triangle",
                "Non-triangle faces are retained in the sidecar and omitted from the static DMX slice.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.DEFERRED,
                path=("meshes", welded.name, "faces", face_index),
                details={"vertex_count": len(face.vertices)},
            )
            continue
        material_faces.setdefault(face.material, []).extend(face.vertices)

    for material_name in sorted(material_faces):
        _validate_source_string(material_name, "material name")
        if material_name not in dmx._materials:
            dmx.add_material(material_name, material_name)
        dmx.mesh_add_faceset(
            dmx_mesh,
            material_name,
            np.asarray(material_faces[material_name], dtype=np.int32),
        )


def _quaternion_to_euler_degrees(influence: AttachmentInfluence) -> tuple[float, float, float]:
    x, y, z, w = influence.rotation
    sin_x = 2.0 * (w * x + y * z)
    cos_x = 1.0 - 2.0 * (x * x + y * y)
    angle_x = math.atan2(sin_x, cos_x)
    sin_y = max(-1.0, min(1.0, 2.0 * (w * y - z * x)))
    angle_y = math.asin(sin_y)
    sin_z = 2.0 * (w * z + x * y)
    cos_z = 1.0 - 2.0 * (y * y + z * z)
    angle_z = math.atan2(sin_z, cos_z)
    return tuple(math.degrees(angle) for angle in (angle_x, angle_y, angle_z))


def _add_physics_shape(modeldoc: KV3mdl, shape: PhysicsShape, report: LossReport):
    common = {
        "name": shape.name,
        "parent_bone": shape.parent_bone,
        "surface_prop": shape.surface_property,
        "collision_prop": shape.collision_property,
    }
    if shape.kind == "sphere" and shape.center is not None and shape.radius is not None:
        modeldoc.add_physics_shape(
            "PhysicsShapeSphere",
            **common,
            radius=float(shape.radius),
            origin=list(shape.center),
        )
    elif (shape.kind == "capsule" and shape.point_a is not None and shape.point_b is not None
          and shape.radius is not None):
        modeldoc.add_physics_shape(
            "PhysicsShapeCapsule",
            **common,
            radius=float(shape.radius),
            point0=list(shape.point_a),
            point1=list(shape.point_b),
        )
    else:
        report.deferred(
            "model.physics.shape.sidecar",
            f"Physics shape kind {shape.kind!r} is retained in the sidecar but has no safe ModelDoc node.",
            path=("physics_shapes", shape.name),
        )


class ModelDocExporter:
    def __init__(self, *, modeldoc_version: int = 33, dmx_version: int = 22):
        self.modeldoc_version = modeldoc_version
        self.dmx_version = dmx_version

    def build(
            self,
            document: ModelDocument,
            *,
            stem: str | None = None,
            report: LossReport | None = None,
    ) -> ExportBundle:
        loss_report = report if report is not None else LossReport()
        output_stem = _output_stem(document, stem)
        mesh_filename = f"{output_stem}.dmx"

        _validate_source_string(document.name, "model name")
        mesh_names = [mesh.name for mesh in document.meshes]
        if len(mesh_names) != len(set(mesh_names)):
            loss_report.irrecoverable(
                "model.mesh.name.duplicate",
                "DMX mesh names must be unique.",
                details={"mesh_names": mesh_names},
            )
        if not document.meshes:
            loss_report.missing(
                "model.mesh.none",
                "The model has no recoverable render meshes.",
                severity=DiagnosticSeverity.ERROR,
            )

        dmx = DmxModel2(document.name, self.dmx_version)
        dmx.add_skeleton(document.name)
        for mesh in document.meshes:
            _validate_source_string(mesh.name, "mesh name")
            _write_mesh(dmx, mesh, loss_report)

        modeldoc = KV3mdl(
            include_default_animation=False,
            modeldoc_version=self.modeldoc_version,
        )
        modeldoc.storage["rootNode"]["importer_notes"] = "Reconstructed by SourceIO from compiled resource data"
        for mesh in document.meshes:
            modeldoc.add_render_mesh(mesh.name, mesh_filename, import_filter=(mesh.name,))

        for bodygroup in document.bodygroups:
            group = modeldoc.add_bodygroup(bodygroup.name)
            for choice in bodygroup.choices:
                modeldoc.add_bodygroup_choice(group, list(choice))

        for lod in sorted(document.lods, key=lambda item: item.index):
            modeldoc.add_lod(f"LODGroup{lod.index}", lod.switch_distance, lod.meshes)

        for skin in document.skins:
            skin_class = "DefaultMaterialGroup" if skin.is_default else "MaterialGroup"
            skin_node = modeldoc.add_skin(skin.name, skin_class=skin_class)
            for remap in skin.remaps:
                modeldoc.add_skin_remap(skin_node, remap.source, remap.target)

        for attachment in document.attachments:
            if not attachment.influences:
                loss_report.missing(
                    "model.attachment.influence.missing",
                    "Attachment has no recoverable influence and remains only in the sidecar.",
                    path=("attachments", attachment.name),
                )
                continue
            if len(attachment.influences) > 1:
                loss_report.deferred(
                    "model.attachment.multi_influence",
                    "ModelDoc attachment nodes support one influence in this slice; all influences remain in the sidecar.",
                    path=("attachments", attachment.name),
                    details={"influence_count": len(attachment.influences)},
                )
            influence = attachment.influences[0]
            modeldoc.add_attachment(
                attachment.name,
                influence.parent_bone,
                influence.origin,
                _quaternion_to_euler_degrees(influence),
                influence.weight,
                attachment.ignore_rotation,
            )

        for shape in document.physics_shapes:
            _add_physics_shape(modeldoc, shape, loss_report)

        for hitbox in document.hitboxes:
            modeldoc.add_hitbox(
                hitbox.set_name,
                hitbox.name,
                hitbox.parent_bone,
                hitbox.minimum,
                hitbox.maximum,
                hitbox.group_id,
                hitbox.surface_property,
            )

        for reference in document.animation_references:
            suffix = PurePosixPath(reference.path).suffix.lower()
            if suffix in {".dmx", ".fbx"}:
                modeldoc.add_animation_file(reference.name or PurePosixPath(reference.path).stem, reference.path)
            elif suffix == ".vanmgrph":
                modeldoc.storage["rootNode"]["anim_graph_name"] = reference.path
            else:
                loss_report.deferred(
                    "model.animation.reference.sidecar",
                    "Compiled or unknown animation references remain in the sidecar.",
                    path=("animation_references", reference.path),
                )

        for reference in document.flex_references:
            loss_report.deferred(
                "model.flex.reference.sidecar",
                "Flex references remain in the sidecar until editable delta data is available.",
                path=("flex_references", reference.path),
            )

        sidecar = {
            "schema": "sourceio.model-export",
            "schema_version": 1,
            "document": document.to_dict(),
        }
        files = {
            f"{output_stem}.vmdl": modeldoc.dump(),
            mesh_filename: dmx.dmx.echo("keyvalues2", 4),
            f"{output_stem}.sourceio.json": json.dumps(sidecar, indent=2, sort_keys=True, allow_nan=False),
            f"{output_stem}.loss.json": loss_report.to_json(),
        }
        return ExportBundle(files, loss_report)

    def export(
            self,
            document: ModelDocument,
            output_directory,
            *,
            stem: str | None = None,
            overwrite: bool = False,
            report: LossReport | None = None,
    ):
        bundle = self.build(document, stem=stem, report=report)
        return bundle.write(output_directory, overwrite=overwrite)


def export_modeldoc(document: ModelDocument, output_directory, **kwargs):
    return ModelDocExporter().export(document, output_directory, **kwargs)
