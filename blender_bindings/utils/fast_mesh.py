from collections.abc import Sequence
from typing import Any, Literal

import bpy
import numpy as np
from bpy.types import Mesh, VertexGroup


def set_custom_normals(mesh: Mesh, normals: np.ndarray, domain: Literal['POINT', 'CORNER'] = 'POINT'):
    normals = np.asarray(normals, dtype=np.float32)
    if normals.ndim == 2 and normals.shape[1] > 3:
        normals = normals[:, :3]
    normals = normals.reshape(-1, 3)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
    attribute = mesh.attributes.get("custom_normal")
    if attribute is not None and (attribute.domain != domain or attribute.data_type != 'FLOAT_VECTOR'):
        mesh.attributes.remove(attribute)
        attribute = None
    if attribute is None:
        attribute = mesh.attributes.new("custom_normal", 'FLOAT_VECTOR', domain)
    attribute.data.foreach_set("vector", normals.ravel())


def set_vertex_weights(groups: Sequence[VertexGroup], bone_ids: np.ndarray, weights: np.ndarray):
    """Assign per-vertex influences, groups[i] being the vertex group of bone i.

    Entries with weight <= 0 are skipped, the last entry wins for a repeated (vertex, group) pair.
    """
    bone_ids = np.asarray(bone_ids)
    weights = np.asarray(weights)
    vertex_count = len(bone_ids)
    if vertex_count == 0 or len(groups) == 0:
        return
    bone_ids = bone_ids.reshape(vertex_count, -1)
    weights = weights.reshape(vertex_count, -1)

    group_lookup = np.array([group.index for group in groups], dtype=np.int64)
    vertex_ids = np.repeat(np.arange(vertex_count, dtype=np.int64), bone_ids.shape[1])
    weights = weights.ravel()

    # Filter before the lookup: unused influence slots may hold out-of-range bone ids.
    mask = weights > 0
    vertex_ids, weights = vertex_ids[mask], weights[mask]
    if len(vertex_ids) == 0:
        return
    group_ids = group_lookup[bone_ids.ravel()[mask].astype(np.int64)]

    keys = vertex_ids * (group_lookup.max() + 1) + group_ids
    _, last_reversed = np.unique(keys[::-1], return_index=True)
    keep = len(keys) - 1 - last_reversed
    vertex_ids, group_ids, weights = vertex_ids[keep], group_ids[keep], weights[keep]

    order = np.lexsort((weights, group_ids))
    vertex_ids, group_ids, weights = vertex_ids[order], group_ids[order], weights[order]
    boundaries = np.flatnonzero((np.diff(group_ids) != 0) | (np.diff(weights) != 0)) + 1
    starts = np.concatenate(([0], boundaries))
    ends = np.concatenate((boundaries, [len(vertex_ids)]))

    group_by_index = {group.index: group for group in groups}
    for start, end in zip(starts.tolist(), ends.tolist()):
        group_by_index[int(group_ids[start])].add(vertex_ids[start:end].tolist(), float(weights[start]), 'REPLACE')


class FastMesh(Mesh):
    __slots__ = ()

    def set_custom_normals(self, normals: np.ndarray, domain: Literal['POINT', 'CORNER'] = 'POINT'):
        set_custom_normals(self, normals, domain)

    @classmethod
    def new(cls, name: str) -> 'FastMesh':
        mesh = bpy.data.meshes.new(name)
        mesh.__class__ = cls
        return mesh

    def from_pydata(self,
                    vertices: np.ndarray,
                    edges: Any | None,
                    faces: Any | None,
                    shade_flat=True):

        has_faces = faces is not None and len(faces) > 0
        has_edges = edges is not None and len(edges) > 0
        vertices_len = len(vertices)
        self.vertices.add(vertices_len)

        if has_faces:
            if not isinstance(faces, np.ndarray):
                raise NotImplementedError("FastMesh only works with numpy arrays")
            face_lengths = faces.shape[1]
            faces_len = faces.shape[0]
            self.loops.add(faces_len * face_lengths)
            self.polygons.add(faces_len)
            loop_starts = np.arange(0, faces_len * face_lengths, face_lengths, dtype=np.uint32)
            self.polygons.foreach_set("loop_start", loop_starts)
            self.polygons.foreach_set("vertices", faces.ravel())

        self.vertices.foreach_set("co", vertices.ravel())

        if has_edges:
            if not isinstance(edges, np.ndarray):
                raise NotImplementedError("FastMesh only works with numpy arrays")
            self.edges.add(len(edges))
            self.edges.foreach_set("vertices", edges.ravel())

        if shade_flat:
            self.shade_flat()

        if has_edges or has_faces:
            self.update(
                # Needed to either:
                # - Calculate edges that don't exist for polygons.
                # - Assign edges to polygon loops.
                calc_edges=has_edges,
                # Flag loose edges.
                calc_edges_loose=has_faces,
            )

    def update(self, calc_edges: bool = False, calc_edges_loose: bool = False) -> None:
        return super().update(calc_edges=calc_edges, calc_edges_loose=calc_edges_loose)
