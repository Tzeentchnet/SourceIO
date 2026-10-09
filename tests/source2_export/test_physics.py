from SourceIO.library.source2.export.physics import physics_shapes_from_block


def test_physics_shapes_preserve_primitives_meshes_and_hulls():
    block = {
        "m_parts": [{
            "m_rnShape": {
                "m_spheres": [{
                    "m_UserFriendlyName": "sphere",
                    "m_nCollisionAttributeIndex": 0,
                    "m_nSurfacePropertyIndex": 0,
                    "m_Sphere": {"m_vCenter": (1, 2, 3), "m_flRadius": 4},
                }],
                "m_capsules": [{
                    "m_UserFriendlyName": "capsule",
                    "m_nCollisionAttributeIndex": 0,
                    "m_nSurfacePropertyIndex": 0,
                    "m_Capsule": {"m_vCenter": ((0, 0, 0), (0, 0, 2)), "m_flRadius": 0.5},
                }],
                "m_meshes": [{
                    "m_UserFriendlyName": "mesh",
                    "m_nCollisionAttributeIndex": 0,
                    "m_nSurfacePropertyIndex": 0,
                    "m_Mesh": {
                        "m_Vertices": ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                        "m_Triangles": (0, 1, 2),
                    },
                }],
                "m_hulls": [{
                    "m_UserFriendlyName": "hull",
                    "m_nCollisionAttributeIndex": 0,
                    "m_nSurfacePropertyIndex": 0,
                    "m_Hull": {
                        "m_VertexPositions": ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
                        "m_Edges": (
                            {"m_nNext": 1, "m_nOrigin": 0},
                            {"m_nNext": 2, "m_nOrigin": 1},
                            {"m_nNext": 0, "m_nOrigin": 2},
                        ),
                        "m_Faces": ({"m_nEdge": 0},),
                    },
                }],
            },
        }],
        "m_boneParents": (),
        "m_boneNames": (),
        "m_collisionAttributes": ({"m_CollisionGroupString": "default"},),
        "m_surfacePropertyHashes": (),
    }

    shapes = physics_shapes_from_block(block)

    assert [shape.kind for shape in shapes] == ["sphere", "capsule", "mesh", "hull"]
    assert shapes[0].center == (1.0, 2.0, 3.0)
    assert shapes[0].collision_property == "default"
    assert shapes[0].metadata["collision_attribute"]["m_CollisionGroupString"] == "default"
    assert shapes[1].point_b == (0.0, 0.0, 2.0)
    assert shapes[2].faces == ((0, 1, 2),)
    assert shapes[3].faces == ((0, 1, 2),)
