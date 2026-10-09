from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

import numpy as np

from ..blocks.vertex_index_buffer import VertexIndexBuffer
from ..blocks.vertex_index_buffer.index_buffer import IndexBuffer
from ..blocks.vertex_index_buffer.vertex_buffer import VertexBuffer
from ..common import convert_normals, convert_normals_2
from .diagnostics import LossReport
from .domain import CornerData, MeshFace, MeshVertex, StaticMesh


def _value(mapping: Mapping[str, Any], *keys: str, default=None):
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _normalized_float(values: np.ndarray) -> np.ndarray:
    if values.dtype.kind == "f":
        return values.astype(np.float32, copy=False)
    limits = np.iinfo(values.dtype)
    converted = values.astype(np.float32)
    if limits.kind == "u":
        return (converted - limits.min) / (limits.max - limits.min)
    return (converted - limits.min) / (limits.max - limits.min) * 2.0 - 1.0


def _combined_attributes(
        buffers: Iterable[VertexBuffer],
        resource,
        report: LossReport,
        path: tuple[str | int, ...],
):
    attributes: dict[str, np.ndarray] = {}
    infos = {}
    vertex_count = None
    duplicates = defaultdict(int)
    for buffer_index, vertex_buffer in enumerate(buffers):
        if vertex_count is None:
            vertex_count = vertex_buffer.vertex_count
        elif vertex_count != vertex_buffer.vertex_count:
            report.irrecoverable(
                "model.mesh.vertex_stream.count",
                "Draw-call vertex streams have different vertex counts.",
                path=path + ("vertex_buffers", buffer_index),
                details={"expected": vertex_count, "actual": vertex_buffer.vertex_count},
            )
            return None, None
        decoded = vertex_buffer.get_vertices(resource)
        for attribute in vertex_buffer.attributes:
            name = attribute.name
            output_name = name
            while output_name in attributes:
                duplicates[name] += 1
                output_name = f"{name}_{duplicates[name]}"
            attributes[output_name] = decoded[name]
            infos[output_name] = attribute
    return attributes, infos


def _vertex(
        index: int,
        attributes: Mapping[str, np.ndarray],
        infos: Mapping[str, Any],
        *,
        compressed_normals: bool,
) -> MeshVertex:
    position = tuple(float(value) for value in attributes["POSITION"][index][:3])
    normal = None
    if "NORMAL" in attributes:
        normals = attributes["NORMAL"]
        if compressed_normals:
            decoded = convert_normals_2(normals) if normals.dtype == np.uint32 else convert_normals(normals)
            normal = tuple(float(value) for value in decoded[index][:3])
        else:
            normal = tuple(float(value) for value in _normalized_float(normals)[index][:3])

    tangent = None
    if "TANGENT" in attributes:
        values = _normalized_float(attributes["TANGENT"])[index]
        tangent_values = list(map(float, values[:4]))
        while len(tangent_values) < 4:
            tangent_values.append(1.0)
        tangent = tuple(tangent_values)

    texcoords = []
    uv_names = [
        name for name in attributes
        if name == "TEXCOORD" or (name.startswith("TEXCOORD_") and name[9:].isdigit())
    ]
    uv_names.sort(key=lambda name: 0 if name == "TEXCOORD" else int(name[9:]))
    for name in uv_names:
        values = _normalized_float(attributes[name])[index]
        if len(values) >= 2:
            texcoords.append((float(values[0]), float(values[1])))
        if len(values) >= 4:
            texcoords.append((float(values[2]), float(values[3])))

    color = None
    color_name = next((name for name in ("COLOR", "COLOR_0") if name in attributes), None)
    if color_name is not None:
        values = list(map(float, _normalized_float(attributes[color_name])[index][:4]))
        while len(values) < 4:
            values.append(1.0)
        color = tuple(values)

    weight_name = next((name for name in ("BLENDWEIGHT", "BLENDWEIGHTS") if name in attributes), None)
    index_name = next((name for name in ("BLENDINDICES", "BLENDINDEX") if name in attributes), None)
    bone_weights = ()
    bone_indices = ()
    if weight_name is not None:
        bone_weights = tuple(map(float, _normalized_float(attributes[weight_name])[index]))
    if index_name is not None:
        bone_indices = tuple(map(int, attributes[index_name][index]))

    handled = {
        "POSITION", "NORMAL", "TANGENT", "COLOR", "COLOR_0",
        "BLENDWEIGHT", "BLENDWEIGHTS", "BLENDINDICES", "BLENDINDEX",
        *uv_names,
    }
    custom = []
    for name in sorted(attributes):
        if name in handled:
            continue
        values = np.asarray(attributes[name][index]).reshape(-1)
        custom.append((name, tuple(value.item() for value in values)))

    return MeshVertex(
        position=position,
        corner=CornerData(
            normal=normal,
            tangent=tangent,
            texcoords=tuple(texcoords),
            color=color,
            bone_indices=bone_indices,
            bone_weights=bone_weights,
            custom_attributes=tuple(custom),
        ),
    )


def _compressed_normals(draw_call: Mapping[str, Any]) -> bool:
    if draw_call.get("m_bUseCompressedNormalTangent", False):
        return True
    flags = draw_call.get("m_nFlags", 0)
    if isinstance(flags, int):
        return bool(flags & 0x2)
    return any("COMPRESSED_NORMAL_TANGENT" in str(flag) for flag in flags)


def decode_render_mesh(
        data_block: Mapping[str, Any],
        index_buffers: list[IndexBuffer],
        vertex_buffers: list[VertexBuffer],
        resource,
        *,
        extra_vertex_buffers: list[VertexBuffer] | None = None,
        name: str | None = None,
        bodygroups: tuple[str, ...] = (),
        lods: tuple[int, ...] = (),
        report: LossReport | None = None,
) -> tuple[StaticMesh, ...]:
    loss_report = report if report is not None else LossReport()
    meshes = []
    base_name = name or getattr(resource, "name", "mesh")
    extra_vertex_buffers = extra_vertex_buffers or []

    for scene_index, scene_object in enumerate(data_block.get("m_sceneObjects", ())):
        for draw_index, draw_call in enumerate(scene_object.get("m_drawCalls", ())):
            path = ("meshes", base_name, "scene_objects", scene_index, "draw_calls", draw_index)
            primitive = draw_call.get("m_nPrimitiveType")
            if primitive not in (5, "RENDER_PRIM_TRIANGLES"):
                loss_report.irrecoverable(
                    "model.mesh.primitive.unsupported",
                    "Only triangle-list draw calls can be reconstructed safely.",
                    path=path,
                    details={"primitive": str(primitive)},
                )
                continue

            index_info = draw_call["m_indexBuffer"]
            index_handle = int(index_info["m_hBuffer"])
            if index_handle < 0 or index_handle >= len(index_buffers):
                loss_report.irrecoverable(
                    "model.mesh.index_buffer.missing",
                    "Draw call references an unavailable index buffer.",
                    path=path,
                    details={"index_buffer": index_handle},
                )
                continue

            selected_buffers = []
            for buffer_info in draw_call.get("m_vertexBuffers", ()):
                handle = int(buffer_info["m_hBuffer"])
                if handle < 0 or handle >= len(vertex_buffers):
                    loss_report.irrecoverable(
                        "model.mesh.vertex_buffer.missing",
                        "Draw call references an unavailable vertex buffer.",
                        path=path,
                        details={"vertex_buffer": handle},
                    )
                    selected_buffers = []
                    break
                selected_buffers.append(vertex_buffers[handle])
                if handle < len(extra_vertex_buffers) and extra_vertex_buffers[handle].vertex_count:
                    selected_buffers.append(extra_vertex_buffers[handle])
            if not selected_buffers:
                continue

            attributes, infos = _combined_attributes(selected_buffers, resource, loss_report, path)
            if attributes is None:
                continue
            if "POSITION" not in attributes:
                loss_report.irrecoverable(
                    "model.mesh.position.missing",
                    "Draw call has no POSITION stream.",
                    path=path,
                )
                continue

            all_indices = index_buffers[index_handle].get_indices(resource)
            start = int(draw_call.get("m_nStartIndex", 0)) // 3
            count = int(draw_call.get("m_nIndexCount", 0)) // 3
            selected_indices = all_indices[start:start + count]
            base_vertex = int(draw_call.get("m_nBaseVertex", 0))
            absolute_indices = selected_indices.astype(np.int64) + base_vertex
            vertex_count = len(attributes["POSITION"])
            if absolute_indices.size and (absolute_indices.min() < 0 or absolute_indices.max() >= vertex_count):
                loss_report.irrecoverable(
                    "model.mesh.index.out_of_range",
                    "Draw-call indices leave the selected vertex streams.",
                    path=path,
                    details={
                        "minimum": int(absolute_indices.min()),
                        "maximum": int(absolute_indices.max()),
                        "vertex_count": vertex_count,
                    },
                )
                continue

            source_indices = []
            source_to_local = {}
            local_faces = []
            for triangle in absolute_indices:
                local_triangle = []
                for source_index in map(int, triangle):
                    if source_index not in source_to_local:
                        source_to_local[source_index] = len(source_indices)
                        source_indices.append(source_index)
                    local_triangle.append(source_to_local[source_index])
                local_faces.append(tuple(local_triangle))

            material = _value(draw_call, "m_material", "m_pMaterial", default="NullMaterial")
            if material is None or material.__class__.__name__ == "NullObject":
                material = "NullMaterial"
            material = str(material)
            material_stem = PurePosixPath(material).stem or "material"
            mesh_name = f"{base_name}_{material_stem}_{scene_index}_{draw_index}"
            vertices = tuple(
                _vertex(
                    source_index,
                    attributes,
                    infos,
                    compressed_normals=_compressed_normals(draw_call),
                )
                for source_index in source_indices
            )
            faces = tuple(MeshFace(face, material) for face in local_faces)
            meshes.append(StaticMesh(
                name=mesh_name,
                vertices=vertices,
                faces=faces,
                bodygroups=bodygroups,
                lods=lods,
                overlay=bool(draw_call.get("m_bIsOverlay", False)),
                source_reference=str(getattr(resource, "_filepath", "")) or None,
                metadata={
                    "scene_object_index": scene_index,
                    "draw_call_index": draw_index,
                    "draw_flags": draw_call.get("m_nFlags", 0),
                    "tint": draw_call.get("m_vTintColor"),
                    "material_groups": data_block.get("m_materialGroups", ()),
                },
            ))
    return tuple(meshes)


def decode_compiled_mesh(resource, *, report: LossReport | None = None) -> tuple[StaticMesh, ...]:
    from ..blocks.kv3_block import KVBlock

    loss_report = report if report is not None else LossReport()
    data = resource.get_block(KVBlock, block_name="DATA")
    if data is None:
        loss_report.irrecoverable(
            "model.mesh.data.missing",
            "Compiled mesh has no DATA block.",
            details={"resource": str(getattr(resource, "_filepath", resource.name))},
        )
        return ()
    vbib = resource.get_block(VertexIndexBuffer, block_name="VBIB")
    if vbib is not None:
        index_buffers = vbib.index_buffers
        vertex_buffers = vbib.vertex_buffers
    elif "m_vertexBuffers" in data and "m_indexBuffers" in data:
        vertex_buffers = [VertexBuffer.from_kv(value) for value in data["m_vertexBuffers"]]
        index_buffers = [IndexBuffer.from_kv(value) for value in data["m_indexBuffers"]]
    else:
        loss_report.irrecoverable(
            "model.mesh.buffers.missing",
            "Compiled mesh has no recoverable vertex and index buffers.",
            details={"resource": str(getattr(resource, "_filepath", resource.name))},
        )
        return ()
    return decode_render_mesh(
        data,
        index_buffers,
        vertex_buffers,
        resource,
        name=resource.name,
        report=loss_report,
    )
