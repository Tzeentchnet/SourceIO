"""Overlay fragments and displacement meshes (library.source1.bsp.geometry) on a hand-built BSP."""
import os
from types import SimpleNamespace

os.environ['NO_BPY'] = '1'

import numpy as np
import pytest

from SourceIO.library.source1.bsp.datatypes.overlay import Overlay
from SourceIO.library.source1.bsp.geometry import (OVERLAY_SURFACE_OFFSET, OverlayBuilder, displacement_mesh,
                                                   face_polygon)


class StubBSP:
    """Just the lumps geometry.py reads."""

    def __init__(self, polygons, normals, disp_infos=(), disp_offsets=None):
        vertices, surf_edges, edges, faces = [], [], [[0, 0]], []
        for polygon, normal in zip(polygons, normals):
            first_vertex, first_edge = len(vertices), len(surf_edges)
            vertices.extend(polygon)
            for i in range(len(polygon)):
                edges.append([first_vertex + i, first_vertex + (i + 1) % len(polygon)])
                surf_edges.append(len(edges) - 1)
            faces.append(SimpleNamespace(plane_index=len(faces), first_edge=first_edge, edge_count=len(polygon),
                                         disp_info_id=-1, side=0))
        self.lumps = {
            'LUMP_VERTICES': SimpleNamespace(vertices=np.array(vertices, np.float32)),
            'LUMP_EDGES': SimpleNamespace(edges=np.array(edges, np.int32)),
            'LUMP_SURFEDGES': SimpleNamespace(surf_edges=np.array(surf_edges, np.int32)),
            'LUMP_FACES': SimpleNamespace(faces=faces),
            'LUMP_PLANES': SimpleNamespace(planes=[SimpleNamespace(normal=tuple(n)) for n in normals]),
            'LUMP_DISPINFO': SimpleNamespace(infos=list(disp_infos)),
            'LUMP_DISP_VERTS': SimpleNamespace(transformed_vertices=disp_offsets),
        }

    def get_lump(self, name):
        return self.lumps.get(name)


# A 256x256 floor facing up, wound clockwise seen from above (the BSP's front-face winding).
FLOOR = [(-128, -128, 0), (-128, 128, 0), (128, 128, 0), (128, -128, 0)]


def make_overlay(half_u, half_v, origin=(0, 0, 0), normal=(0, 0, 1), u_axis=(1, 0, 0), flip=False,
                 faces=(0,), render_order=0, u=(0.0, 1.0), v=(1.0, 0.0)):
    points = np.array([(-half_u, -half_v, 0), (-half_u, half_v, 0), (half_u, half_v, 0), (half_u, -half_v, 0)],
                      np.float32)
    points[:3, 2] = u_axis
    points[3, 2] = 1.0 if flip else 0.0
    ofaces = tuple(faces) + (0,) * (64 - len(faces))
    return Overlay(0, 0, len(faces) | (render_order << 14), ofaces, u, v, points, origin, normal)


def polygon_area(points):
    return 0.5 * abs(np.sum(points[:, 0] * np.roll(points[:, 1], -1) - np.roll(points[:, 0], -1) * points[:, 1]))


def split(mesh, values):
    start = 0
    for size in mesh.polygon_sizes:
        yield values[start:start + size]
        start += size


def test_basis_projects_u_into_the_plane_and_honours_the_flip_flag():
    # Hammer U leaning out of a +X wall, as on ctf_2fort's BLU logo.
    overlay = make_overlay(32, 32, normal=(1, 0, 0), u_axis=(0.258819, 0.965926, 0))
    u_axis, v_axis, normal = overlay.basis
    np.testing.assert_allclose(u_axis, (0, 1, 0), atol=1e-6)
    np.testing.assert_allclose(v_axis, np.cross(normal, u_axis), atol=1e-6)
    flipped = make_overlay(32, 32, normal=(1, 0, 0), u_axis=(0, 1, 0), flip=True).basis
    np.testing.assert_allclose(flipped[1], -v_axis, atol=1e-6)


def test_plane_maps_uv_points_x_to_u_and_y_to_v():
    positions, texcoords = make_overlay(64, 16, origin=(10, 20, 30)).plane
    np.testing.assert_allclose(positions[0], (10 - 64, 20 - 16, 30))
    np.testing.assert_allclose(positions[2], (10 + 64, 20 + 16, 30))
    np.testing.assert_allclose(texcoords, [(0, 1), (0, 0), (1, 0), (1, 1)])


def test_overlay_inside_a_face_covers_its_whole_texture():
    bsp = StubBSP([FLOOR], [(0, 0, 1)])
    mesh = OverlayBuilder(bsp).build(make_overlay(32, 16, origin=(10, 0, 0)))
    assert len(mesh.polygon_sizes) == 2
    assert sum(polygon_area(tc) for tc in split(mesh, mesh.texcoords)) == pytest.approx(1.0)
    np.testing.assert_allclose(mesh.positions[:, 2], OVERLAY_SURFACE_OFFSET)
    assert mesh.positions[:, 0].min() == pytest.approx(10 - 32)
    assert mesh.positions[:, 0].max() == pytest.approx(10 + 32)
    # Each texcoord lands where the quad mapping says it should.
    s = (mesh.positions[:, 0] - (10 - 32)) / 64
    t = 1 - (mesh.positions[:, 1] + 16) / 32
    np.testing.assert_allclose(mesh.texcoords, np.stack([s, t], 1), atol=1e-6)
    # Counter-clockwise seen from the front, so Blender's normals face up.
    for positions in split(mesh, mesh.positions):
        assert np.cross(positions[1] - positions[0], positions[2] - positions[0])[2] > 0


def test_overlay_is_clipped_to_its_faces_and_render_order_lifts_it():
    bsp = StubBSP([FLOOR], [(0, 0, 1)])
    # Half of the quad hangs past the floor's +X edge.
    mesh = OverlayBuilder(bsp).build(make_overlay(32, 32, origin=(128, 0, 0), render_order=2))
    assert sum(polygon_area(tc) for tc in split(mesh, mesh.texcoords)) == pytest.approx(0.5)
    assert mesh.positions[:, 0].max() == pytest.approx(128)
    np.testing.assert_allclose(mesh.positions[:, 2], OVERLAY_SURFACE_OFFSET * 3)


def test_overlay_wraps_onto_a_second_face_at_its_own_height():
    ledge = [(128, -128, 16), (128, 128, 16), (384, 128, 16), (384, -128, 16)]
    bsp = StubBSP([FLOOR, ledge], [(0, 0, 1), (0, 0, 1)])
    mesh = OverlayBuilder(bsp).build(make_overlay(32, 32, origin=(128, 0, 0), faces=(0, 1)))
    assert sum(polygon_area(tc) for tc in split(mesh, mesh.texcoords)) == pytest.approx(1.0)
    on_ledge = mesh.positions[:, 0] > 128 + 1e-3
    np.testing.assert_allclose(mesh.positions[on_ledge, 2], 16 + OVERLAY_SURFACE_OFFSET)


def test_faces_facing_away_get_nothing():
    bsp = StubBSP([FLOOR[::-1]], [(0, 0, -1)])
    assert len(OverlayBuilder(bsp).build(make_overlay(32, 32)).polygon_sizes) == 0


def make_displacement(start_corner: int, polygon=FLOOR):
    """Power-1 (3x3) displacement; the middle vertex is raised by 8 units."""
    offsets = np.zeros((9, 3), np.float32)
    offsets[4] = (0, 0, 8)
    info = SimpleNamespace(start_position=polygon[start_corner], power=1, disp_vert_start=0)
    bsp = StubBSP([polygon], [(0, 0, 1)], [info], offsets)
    info.get_source_face = lambda _bsp: bsp.lumps['LUMP_FACES'].faces[0]
    return bsp, info


@pytest.mark.parametrize('start_corner', range(4))
def test_displacement_grid_starts_at_the_start_corner(start_corner):
    bsp, info = make_displacement(start_corner)
    mesh = displacement_mesh(bsp, info)
    corners = face_polygon(bsp, bsp.lumps['LUMP_FACES'].faces[0])
    np.testing.assert_allclose(mesh.flat_positions[0], corners[start_corner])
    np.testing.assert_allclose(mesh.flat_positions[8], corners[(start_corner + 2) % 4])
    np.testing.assert_allclose(mesh.positions[4], (0, 0, 8))
    assert mesh.triangles.shape == (8, 3)
    np.testing.assert_array_equal(mesh.disp_vertex_ids, np.arange(9))


def test_displacement_start_is_the_exact_corner_not_a_close_one():
    # A 4-unit strip far from the origin, laid out like the ctf_2fort displacements that the old
    # relative-tolerance match (0.5% of 1004 > 4) started from the neighbouring corner.
    strip = [(1000, 1000, 0), (1000, 1004, 0), (1256, 1004, 0), (1256, 1000, 0)]
    bsp, info = make_displacement(1, strip)
    np.testing.assert_allclose(displacement_mesh(bsp, info).flat_positions[0], strip[1])


def test_overlay_drapes_over_a_displacement():
    bsp, info = make_displacement(0)
    bsp.lumps['LUMP_FACES'].faces[0].disp_info_id = 0
    mesh = OverlayBuilder(bsp).build(make_overlay(16, 16))
    assert sum(polygon_area(tc) for tc in split(mesh, mesh.texcoords)) == pytest.approx(1.0)
    # The quad sits on the raised middle vertex, so every fragment vertex is above the flat floor.
    assert (mesh.positions[:, 2] > 7).all()
