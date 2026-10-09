from SourceIO.library.source2.export import (
    CornerData,
    MeshFace,
    MeshVertex,
    StaticMesh,
    untriangulate,
    weld_vertices,
)


def vertex(position, uv=(0.0, 0.0)):
    return MeshVertex(position, CornerData(normal=(0.0, 0.0, 1.0), texcoords=(uv,)))


def test_welding_requires_same_material_and_corner_data():
    mesh = StaticMesh(
        "seams",
        (
            vertex((0.0, 0.0, 0.0)),
            vertex((1.0, 0.0, 0.0)),
            vertex((0.0, 1.0, 0.0)),
            vertex((0.0, 0.0, 0.0)),
            vertex((1.0, 0.0, 0.0), (0.5, 0.0)),
            vertex((0.0, -1.0, 0.0)),
        ),
        (
            MeshFace((0, 1, 2), "materials/a.vmat"),
            MeshFace((3, 4, 5), "materials/b.vmat"),
        ),
    )

    result = weld_vertices(mesh)

    assert len(result.mesh.vertices) == 6
    assert result.blocked_by_material > 0
    assert result.blocked_by_corner_data > 0


def test_exact_welding_collapses_duplicate_corners():
    mesh = StaticMesh(
        "weld",
        (
            vertex((0.0, 0.0, 0.0)),
            vertex((1.0, 0.0, 0.0)),
            vertex((0.0, 1.0, 0.0)),
            vertex((0.0, 0.0, 0.0)),
        ),
        (
            MeshFace((0, 1, 2), "materials/a.vmat"),
            MeshFace((3, 2, 1), "materials/a.vmat"),
        ),
    )

    result = weld_vertices(mesh)

    assert len(result.mesh.vertices) == 3
    assert result.welded_vertices == 3


def test_untriangulation_preserves_material_boundaries():
    mesh = StaticMesh(
        "materials",
        (
            vertex((0.0, 0.0, 0.0)),
            vertex((1.0, 0.0, 0.0)),
            vertex((1.0, 1.0, 0.0)),
            vertex((0.0, 1.0, 0.0)),
        ),
        (
            MeshFace((0, 1, 2), "materials/a.vmat"),
            MeshFace((2, 1, 3), "materials/b.vmat"),
        ),
    )

    result = untriangulate(mesh)

    assert result.merged_faces == 0
    assert result.blocked_by_material == 1
    assert len(result.mesh.faces) == 2


def test_untriangulation_preserves_corner_seams():
    mesh = StaticMesh(
        "uv_seam",
        (
            vertex((0.0, 0.0, 0.0)),
            vertex((1.0, 0.0, 0.0), (0.0, 0.0)),
            vertex((1.0, 1.0, 0.0), (1.0, 1.0)),
            vertex((1.0, 1.0, 0.0), (0.5, 1.0)),
            vertex((1.0, 0.0, 0.0), (0.5, 0.0)),
            vertex((0.0, 1.0, 0.0)),
        ),
        (
            MeshFace((0, 1, 2), "materials/a.vmat"),
            MeshFace((3, 4, 5), "materials/a.vmat"),
        ),
    )

    result = untriangulate(mesh)

    assert result.merged_faces == 0
    assert result.blocked_by_corner_data == 1


def test_untriangulation_merges_only_exact_coplanar_faces():
    mesh = StaticMesh(
        "quad",
        (
            vertex((0.0, 0.0, 0.0)),
            vertex((1.0, 0.0, 0.0)),
            vertex((1.0, 1.0, 0.0)),
            vertex((0.0, 1.0, 0.0)),
        ),
        (
            MeshFace((0, 1, 2), "materials/a.vmat"),
            MeshFace((0, 2, 3), "materials/a.vmat"),
        ),
    )

    result = untriangulate(mesh)

    assert result.merged_faces == 1
    assert len(result.mesh.faces) == 1
    assert len(result.mesh.faces[0].vertices) == 4
