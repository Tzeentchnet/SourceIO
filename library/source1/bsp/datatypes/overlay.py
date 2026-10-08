from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from SourceIO.library.shared.vector_types import Vector2, Vector3
from SourceIO.library.source1.bsp.bsp_file import VBSPFile
from SourceIO.library.utils.file_utils import Buffer


@dataclass(slots=True)
class Overlay:
    id: int
    tex_info: int
    face_count_and_render_order: int
    ofaces: tuple[int, ...]
    u: Vector2[float]
    v: Vector2[float]
    uv_points: npt.NDArray[np.float32]
    origin: Vector3[float]
    normal: Vector3[float]

    @property
    def face_count(self):
        return self.face_count_and_render_order & 0x3FFF

    @property
    def render_order(self):
        return self.face_count_and_render_order >> 14

    @property
    def face_ids(self):
        return self.ofaces[:self.face_count]

    @property
    def basis(self) -> npt.NDArray[np.float64]:
        """Orthonormal (U, V, normal) rows of the overlay plane.

        VBSP packs the U axis into the unused z of the first three UV points and sets the fourth
        point's z to 1 when V is -(normal x U). Hammer's U can lean out of the plane, so it is
        projected onto it here.
        """
        normal = np.asarray(self.normal, np.float64)
        normal /= np.linalg.norm(normal)
        u_axis = self.uv_points[:3, 2].astype(np.float64)
        u_axis -= normal * (u_axis @ normal)
        u_axis /= np.linalg.norm(u_axis)
        v_axis = np.cross(normal, u_axis)
        if self.uv_points[3, 2] == 1.0:
            v_axis = -v_axis
        return np.stack([u_axis, v_axis, normal])

    @property
    def plane_points(self) -> npt.NDArray[np.float64]:
        """Quad corners as (U, V) coordinates relative to `origin`."""
        return self.uv_points[:, :2].astype(np.float64)

    @property
    def corner_texcoords(self) -> npt.NDArray[np.float64]:
        """Source (s, t) texture coordinates of the four quad corners."""
        return np.array([(self.u[0], self.v[0]), (self.u[0], self.v[1]),
                         (self.u[1], self.v[1]), (self.u[1], self.v[0])], np.float64)

    @property
    def plane(self):
        """World-space quad corners and their texture coordinates."""
        u_axis, v_axis, _ = self.basis
        points = self.plane_points
        positions = np.asarray(self.origin, np.float64) + np.outer(points[:, 0], u_axis) + np.outer(points[:, 1], v_axis)
        return positions, self.corner_texcoords

    @classmethod
    def from_buffer(cls, buffer: Buffer, version: int, bsp: VBSPFile):
        id = buffer.read_int32()
        tex_info = buffer.read_int16()
        face_count_and_render_order = buffer.read_uint16()
        ofaces = buffer.read_fmt('64i')
        u = buffer.read_fmt('ff')
        v = buffer.read_fmt('ff')
        uv_points = np.array(buffer.read_fmt('12f'), dtype=np.float32).reshape((4, 3))
        origin = buffer.read_fmt('fff')
        normal = buffer.read_fmt('fff')
        return cls(id, tex_info, face_count_and_render_order, ofaces, u, v, uv_points, origin, normal)

class VOverlay(Overlay):
    @classmethod
    def from_buffer(cls, buffer: Buffer, version: int, bsp: VBSPFile):
        id = buffer.read_int32()
        tex_info = buffer.read_int32()
        face_count_and_render_order = buffer.read_uint32()
        ofaces = buffer.read_fmt('64i')
        u = buffer.read_fmt('ff')
        v = buffer.read_fmt('ff')
        uv_points = np.array(buffer.read_fmt('12f'), dtype=np.float32).reshape((4, 3))
        origin = buffer.read_fmt('fff')
        normal = buffer.read_fmt('fff')

        return cls(id, tex_info, face_count_and_render_order, ofaces, u, v, uv_points, origin, normal)
