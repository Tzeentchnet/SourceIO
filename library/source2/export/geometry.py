from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .diagnostics import LossReport
from .domain import MeshFace, MeshVertex, StaticMesh


@dataclass(slots=True)
class GeometryOperationResult:
    mesh: StaticMesh
    changed: bool
    welded_vertices: int = 0
    merged_faces: int = 0
    blocked_by_material: int = 0
    blocked_by_corner_data: int = 0


def weld_vertices(mesh: StaticMesh, *, tolerance: float = 0.0,
                  report: LossReport | None = None) -> GeometryOperationResult:
    if tolerance != 0.0:
        raise ValueError("Approximate vertex welding is intentionally unsupported; tolerance must be zero")

    vertices: list[MeshVertex] = []
    faces: list[MeshFace] = []
    key_to_index: dict[tuple, int] = {}
    position_signatures: dict[tuple[float, float, float], set[tuple[str, tuple]]] = {}
    used_source_vertices: set[int] = set()
    blocked_material = 0
    blocked_corner = 0

    for face in mesh.faces:
        remapped = []
        for source_index in face.vertices:
            source_vertex = mesh.vertices[source_index]
            used_source_vertices.add(source_index)
            corner_signature = source_vertex.corner.signature()
            key = (face.material, source_vertex.position, corner_signature)
            existing = key_to_index.get(key)
            if existing is None:
                signatures = position_signatures.setdefault(source_vertex.position, set())
                if signatures:
                    if any(material != face.material for material, _ in signatures):
                        blocked_material += 1
                    if any(signature != corner_signature for _, signature in signatures):
                        blocked_corner += 1
                existing = len(vertices)
                key_to_index[key] = existing
                signatures.add((face.material, corner_signature))
                vertices.append(source_vertex)
            remapped.append(existing)
        faces.append(MeshFace(tuple(remapped), face.material))

    for source_index, source_vertex in enumerate(mesh.vertices):
        if source_index in used_source_vertices:
            continue
        vertices.append(source_vertex)

    welded_count = max(0, sum(len(face.vertices) for face in mesh.faces) - len(vertices))
    welded_mesh = replace(mesh, vertices=tuple(vertices), faces=tuple(faces))
    if report is not None and (blocked_material or blocked_corner):
        report.record(
            "geometry.weld.seams_preserved",
            "Coincident vertices were kept separate because material or corner data differed.",
            severity="info",
            details={
                "mesh": mesh.name,
                "blocked_by_material": blocked_material,
                "blocked_by_corner_data": blocked_corner,
            },
        )
    return GeometryOperationResult(
        mesh=welded_mesh,
        changed=welded_count > 0 or len(vertices) != len(mesh.vertices),
        welded_vertices=welded_count,
        blocked_by_material=blocked_material,
        blocked_by_corner_data=blocked_corner,
    )


def _subtract(a, b):
    return a[0] - b[0], a[1] - b[1], a[2] - b[2]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _normalized_face_normal(mesh: StaticMesh, face: MeshFace):
    a, b, c = (mesh.vertices[index].position for index in face.vertices[:3])
    normal = _cross(_subtract(b, a), _subtract(c, a))
    length = math.sqrt(sum(component * component for component in normal))
    if length == 0:
        return None
    return tuple(component / length for component in normal)


def _shared_opposite_edge(mesh: StaticMesh, first: MeshFace, second: MeshFace):
    first_edges = [
        (first.vertices[index], first.vertices[(index + 1) % 3])
        for index in range(3)
    ]
    second_edges = [
        (second.vertices[index], second.vertices[(index + 1) % 3])
        for index in range(3)
    ]
    for first_edge in first_edges:
        first_start = mesh.vertices[first_edge[0]].position
        first_end = mesh.vertices[first_edge[1]].position
        for second_edge in second_edges:
            second_start = mesh.vertices[second_edge[0]].position
            second_end = mesh.vertices[second_edge[1]].position
            if first_start == second_end and first_end == second_start:
                return first_edge, second_edge
    return None


def _quad_boundary(first: MeshFace, second: MeshFace, shared_edge: tuple[int, int]) -> tuple[int, ...] | None:
    directed_edges = []
    for face in (first, second):
        for index in range(3):
            edge = (face.vertices[index], face.vertices[(index + 1) % 3])
            if edge == shared_edge or edge == (shared_edge[1], shared_edge[0]):
                continue
            directed_edges.append(edge)
    next_vertex = {start: end for start, end in directed_edges}
    if len(next_vertex) != 4:
        return None
    start = min(next_vertex)
    boundary = [start]
    current = start
    for _ in range(4):
        current = next_vertex.get(current)
        if current is None:
            return None
        if current == start:
            break
        boundary.append(current)
    return tuple(boundary) if len(boundary) == 4 and current == start else None


def untriangulate(mesh: StaticMesh, *, normal_tolerance: float = 1e-6,
                  report: LossReport | None = None) -> GeometryOperationResult:
    if normal_tolerance < 0:
        raise ValueError("normal_tolerance must be non-negative")

    consumed: set[int] = set()
    merged: list[MeshFace] = []
    merged_count = 0
    blocked_material = 0
    blocked_corner = 0

    for first_index, first in enumerate(mesh.faces):
        if first_index in consumed or len(first.vertices) != 3:
            continue
        first_normal = _normalized_face_normal(mesh, first)
        if first_normal is None:
            continue
        for second_index in range(first_index + 1, len(mesh.faces)):
            if second_index in consumed:
                continue
            second = mesh.faces[second_index]
            if len(second.vertices) != 3:
                continue
            shared = _shared_opposite_edge(mesh, first, second)
            if shared is None:
                continue
            if first.material != second.material:
                blocked_material += 1
                continue
            first_edge, second_edge = shared
            if (
                mesh.vertices[first_edge[0]].corner.signature()
                != mesh.vertices[second_edge[1]].corner.signature()
                or mesh.vertices[first_edge[1]].corner.signature()
                != mesh.vertices[second_edge[0]].corner.signature()
            ):
                blocked_corner += 1
                continue
            second_normal = _normalized_face_normal(mesh, second)
            if second_normal is None:
                continue
            alignment = sum(a * b for a, b in zip(first_normal, second_normal))
            if abs(1.0 - alignment) > normal_tolerance:
                continue
            remapped_second = MeshFace(
                tuple(
                    first_edge[1] if vertex == second_edge[0]
                    else first_edge[0] if vertex == second_edge[1]
                    else vertex
                    for vertex in second.vertices
                ),
                second.material,
            )
            boundary = _quad_boundary(first, remapped_second, first_edge)
            if boundary is None:
                continue
            merged.append(MeshFace(boundary, first.material))
            consumed.update((first_index, second_index))
            merged_count += 1
            break

    faces = [
        face for index, face in enumerate(mesh.faces)
        if index not in consumed
    ]
    faces.extend(merged)
    result_mesh = replace(mesh, faces=tuple(faces))
    if report is not None and (blocked_material or blocked_corner):
        report.record(
            "geometry.untriangulate.seams_preserved",
            "Triangle boundaries were retained where material or corner data differed.",
            severity="info",
            details={
                "mesh": mesh.name,
                "blocked_by_material": blocked_material,
                "blocked_by_corner_data": blocked_corner,
            },
        )
    return GeometryOperationResult(
        mesh=result_mesh,
        changed=merged_count > 0,
        merged_faces=merged_count,
        blocked_by_material=blocked_material,
        blocked_by_corner_data=blocked_corner,
    )


safe_weld = weld_vertices
safe_untriangulate = untriangulate
