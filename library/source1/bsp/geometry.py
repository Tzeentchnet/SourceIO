"""BSP surface geometry without bpy: face polygons, displacement meshes and overlay fragments.

All positions are in Hammer units.
"""
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from SourceIO.library.source1.bsp.bsp_file import VBSPFile
from SourceIO.library.source1.bsp.datatypes.face import Face
from SourceIO.library.source1.bsp.datatypes.overlay import Overlay

# Overlays are drawn with a depth bias in the engine; here they are lifted off the surface
# instead, one step further for each render order so stacked overlays keep their order.
OVERLAY_SURFACE_OFFSET = 0.1


def face_polygon(bsp: VBSPFile, face: Face) -> npt.NDArray[np.float64]:
    """Corner positions of a face, in the BSP's (clockwise from the front) winding."""
    surf_edges = bsp.get_lump('LUMP_SURFEDGES').surf_edges[face.first_edge:face.first_edge + face.edge_count]
    edges = bsp.get_lump('LUMP_EDGES').edges[np.abs(surf_edges)]
    vertex_ids = np.where(surf_edges >= 0, edges[:, 0], edges[:, 1])
    return bsp.get_lump('LUMP_VERTICES').vertices[vertex_ids].astype(np.float64)


def face_normal(bsp: VBSPFile, face: Face) -> npt.NDArray[np.float64]:
    """Front-facing normal. A face's own plane already faces front; `side` is relative to its node."""
    return np.asarray(bsp.get_lump('LUMP_PLANES').planes[face.plane_index].normal, np.float64)


@dataclass(slots=True)
class DisplacementMesh:
    flat_positions: npt.NDArray[np.float64]
    """Grid on the undisplaced source face; texture coordinates are projected from these."""
    positions: npt.NDArray[np.float64]
    triangles: npt.NDArray[np.uint32]
    disp_vertex_ids: npt.NDArray[np.int64]
    """Rows of LUMP_DISP_VERTS (and of the multiblend lump) for each grid vertex."""


def displacement_mesh(bsp: VBSPFile, disp_info) -> DisplacementMesh:
    corners = face_polygon(bsp, disp_info.get_source_face(bsp))
    start = np.asarray(disp_info.start_position, np.float64)
    first = int(np.argmin(np.sum((corners - start) ** 2, axis=1)))

    size = (1 << disp_info.power) + 1
    steps = np.arange(size, dtype=np.float64) / (size - 1)
    left = corners[first & 3] + np.outer(steps, corners[(first + 1) & 3] - corners[first & 3])
    right = corners[(first + 3) & 3] + np.outer(steps, corners[(first + 2) & 3] - corners[(first + 3) & 3])
    flat = left[:, None, :] + steps[None, :, None] * (right - left)[:, None, :]
    flat = flat.reshape(-1, 3)

    disp_vertex_ids = np.arange(size * size, dtype=np.int64) + disp_info.disp_vert_start
    offsets = bsp.get_lump('LUMP_DISP_VERTS').transformed_vertices[disp_vertex_ids]

    # Quads alternate their split diagonal, as the engine's CDispUtilsHelper does.
    row, col = np.meshgrid(np.arange(size - 1), np.arange(size - 1), indexing='ij')
    index = (row * size + col).ravel().astype(np.uint32)
    odd = (index & 1).astype(bool)[:, None]
    first_tri = np.where(odd, np.stack([index, index + 1, index + size], 1),
                         np.stack([index, index + size + 1, index + size], 1))
    second_tri = np.where(odd, np.stack([index + 1, index + size + 1, index + size], 1),
                          np.stack([index, index + 1, index + size + 1], 1))
    triangles = np.stack([first_tri, second_tri], 1).reshape(-1, 3)
    return DisplacementMesh(flat, flat + offsets, triangles, disp_vertex_ids)


@dataclass(slots=True)
class OverlayMesh:
    positions: npt.NDArray[np.float64]
    texcoords: npt.NDArray[np.float64]
    """Source (s, t) per vertex; t grows downwards."""
    polygon_sizes: npt.NDArray[np.int64]
    """Vertices are not shared: polygon i uses the next polygon_sizes[i] vertices."""


def _clip_to_triangle(points: np.ndarray, positions: np.ndarray, triangle: np.ndarray):
    """Sutherland-Hodgman clip of a convex 2D polygon (carrying 3D positions) to a triangle."""
    a, b, c = triangle
    orientation = np.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
    for start, end in ((a, b), (b, c), (c, a)):
        if len(points) == 0:
            break
        edge = end - start
        side = orientation * (edge[0] * (points[:, 1] - start[1]) - edge[1] * (points[:, 0] - start[0]))
        inside = side >= -1e-6
        if inside.all():
            continue
        new_points, new_positions = [], []
        count = len(points)
        for i in range(count):
            j = (i + 1) % count
            if inside[i]:
                new_points.append(points[i])
                new_positions.append(positions[i])
            if inside[i] != inside[j]:
                t = side[i] / (side[i] - side[j])
                new_points.append(points[i] + (points[j] - points[i]) * t)
                new_positions.append(positions[i] + (positions[j] - positions[i]) * t)
        points = np.array(new_points).reshape(-1, 2)
        positions = np.array(new_positions).reshape(-1, 3)
    return points, positions


def _barycentric(points: np.ndarray, triangle: np.ndarray) -> np.ndarray:
    a, b, c = triangle
    v0, v1 = b - a, c - a
    v2 = points - a
    denom = v0[0] * v1[1] - v1[0] * v0[1]
    w1 = (v2[:, 0] * v1[1] - v1[0] * v2[:, 1]) / denom
    w2 = (v0[0] * v2[:, 1] - v2[:, 0] * v0[1]) / denom
    return np.stack([1 - w1 - w2, w1, w2], 1)


class OverlayBuilder:
    """Cuts overlays out of the surfaces they were applied to, as the engine's overlay fragments."""

    def __init__(self, bsp: VBSPFile):
        self._bsp = bsp
        self._faces: list[Face] = bsp.get_lump('LUMP_FACES').faces
        disp_info_lump = bsp.get_lump('LUMP_DISPINFO')
        self._disp_infos = disp_info_lump.infos if disp_info_lump else []
        self._displacements: dict[int, DisplacementMesh] = {}

    def _displacement(self, disp_info_id: int) -> DisplacementMesh:
        mesh = self._displacements.get(disp_info_id)
        if mesh is None:
            mesh = self._displacements[disp_info_id] = displacement_mesh(self._bsp, self._disp_infos[disp_info_id])
        return mesh

    def _surface_polygons(self, face: Face):
        """(polygon positions, outward normal) pairs that make up a face."""
        normal = face_normal(self._bsp, face)
        if face.disp_info_id == -1:
            yield face_polygon(self._bsp, face), normal
            return
        mesh = self._displacement(face.disp_info_id)
        triangles = mesh.positions[mesh.triangles]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        lengths = np.linalg.norm(normals, axis=1)
        valid = lengths > 1e-9
        normals[valid] /= lengths[valid, None]
        normals[normals @ normal < 0] *= -1
        for triangle, triangle_normal, ok in zip(triangles, normals, valid):
            if ok:
                yield triangle, triangle_normal

    def build(self, overlay: Overlay) -> OverlayMesh:
        u_axis, v_axis, overlay_normal = overlay.basis
        origin = np.asarray(overlay.origin, np.float64)
        quad = overlay.plane_points
        quad_texcoords = overlay.corner_texcoords
        quad_min, quad_max = quad.min(0), quad.max(0)
        offset = OVERLAY_SURFACE_OFFSET * (1 + overlay.render_order)

        positions, texcoords, sizes = [], [], []
        for face_id in overlay.face_ids:
            face = self._faces[face_id]
            for polygon, normal in self._surface_polygons(face):
                # Surfaces facing away from the overlay (displacement overhangs) don't receive it.
                if normal @ overlay_normal <= 1e-3:
                    continue
                relative = polygon - origin
                points = np.stack([relative @ u_axis, relative @ v_axis], 1)
                if (points.min(0) > quad_max).any() or (points.max(0) < quad_min).any():
                    continue
                for corner_ids in ((0, 1, 2), (0, 2, 3)):
                    triangle = quad[list(corner_ids)]
                    clipped, clipped_positions = _clip_to_triangle(points, polygon, triangle)
                    if len(clipped) < 3:
                        continue
                    edges = np.roll(clipped_positions, -1, 0) - clipped_positions
                    keep = np.linalg.norm(edges, axis=1) > 1e-4
                    clipped, clipped_positions = clipped[keep], clipped_positions[keep]
                    if len(clipped) < 3:
                        continue
                    winding = np.cross(clipped_positions, np.roll(clipped_positions, -1, 0)).sum(0)
                    if np.linalg.norm(winding) < 1e-4:
                        continue
                    if winding @ normal < 0:
                        clipped, clipped_positions = clipped[::-1], clipped_positions[::-1]
                    weights = _barycentric(clipped, triangle)
                    positions.append(clipped_positions + normal * offset)
                    texcoords.append(weights @ quad_texcoords[list(corner_ids)])
                    sizes.append(len(clipped))
        if not sizes:
            return OverlayMesh(np.zeros((0, 3)), np.zeros((0, 2)), np.zeros(0, np.int64))
        return OverlayMesh(np.concatenate(positions), np.concatenate(texcoords), np.asarray(sizes, np.int64))
