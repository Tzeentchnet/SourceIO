from __future__ import annotations

from typing import Any, Mapping

import numpy as np

from ..keyvalues3.binary_keyvalues import BinaryBlob, TypedArray
from ..utils.entity_keyvalues_keys import EntityKeyValuesKeys
from .diagnostics import LossReport
from .domain import PhysicsShape


def _array(value, dtype, width: int):
    if isinstance(value, BinaryBlob):
        array = np.frombuffer(value, dtype)
    else:
        array = np.asarray(value, dtype)
    return array.reshape((-1, width))


def _shape_name(record: Mapping[str, Any], fallback: str) -> str:
    return str(record.get("m_UserFriendlyName") or fallback)


def _shape_metadata(
        record: Mapping[str, Any],
        collision_attributes,
        surface_properties,
        surface_hashes,
        report: LossReport,
        path,
):
    collision_index = int(record.get("m_nCollisionAttributeIndex", -1))
    surface_index = int(record.get("m_nSurfacePropertyIndex", -1))
    collision_property = ""
    if 0 <= collision_index < len(collision_attributes):
        collision_attribute = collision_attributes[collision_index]
        if isinstance(collision_attribute, Mapping):
            collision_property = str(
                collision_attribute.get(
                    "m_CollisionGroupString",
                    collision_attribute.get("m_collisionGroup", ""),
                )
            )
        elif collision_attribute is not None:
            collision_property = str(collision_attribute)
    elif collision_index >= 0:
        report.missing(
            "physics.collision_attribute.missing",
            "A physics shape references an unavailable collision attribute.",
            path=path,
            details={"index": collision_index, "attribute_count": len(collision_attributes)},
        )
    surface_property = ""
    if 0 <= surface_index < len(surface_properties):
        surface_property = str(surface_properties[surface_index])
    elif surface_index >= 0:
        report.missing(
            "physics.surface_property.missing",
            "A physics shape references an unavailable surface property.",
            path=path,
            details={"index": surface_index, "property_count": len(surface_properties)},
        )
    metadata = {
        "collision_attribute_index": collision_index,
        "surface_property_index": surface_index,
    }
    if 0 <= collision_index < len(collision_attributes):
        metadata["collision_attribute"] = collision_attributes[collision_index]
    if 0 <= surface_index < len(surface_hashes):
        metadata["surface_property_hash"] = int(surface_hashes[surface_index])
    return collision_property, surface_property, metadata


def _hull_faces(hull: Mapping[str, Any], report: LossReport, path) -> tuple[tuple[int, ...], ...]:
    edge_data = hull["m_Edges"]
    face_data = hull["m_Faces"]
    if isinstance(edge_data, BinaryBlob):
        edge_dtype = np.dtype([
            ("next", np.uint8),
            ("twin", np.uint8),
            ("origin", np.uint8),
            ("face", np.uint8),
        ])
        edges = np.frombuffer(edge_data, edge_dtype)
        edge_records = [
            {"next": int(edge["next"]), "origin": int(edge["origin"])}
            for edge in edges
        ]
    else:
        edge_records = [
            {
                "next": int(edge["m_nNext"]),
                "origin": int(edge["m_nOrigin"]),
            }
            for edge in edge_data
        ]

    if isinstance(face_data, BinaryBlob):
        starts = [int(value) for value in np.frombuffer(face_data, np.uint8)]
    else:
        starts = [int(face["m_nEdge"]) for face in face_data]

    faces = []
    for face_index, start in enumerate(starts):
        if start < 0 or start >= len(edge_records):
            report.irrecoverable(
                "physics.hull.edge.invalid",
                "A hull face references an invalid starting edge.",
                path=path + ("faces", face_index),
                details={"edge": start, "edge_count": len(edge_records)},
            )
            continue
        current = start
        visited = set()
        vertices = []
        while current not in visited:
            if current < 0 or current >= len(edge_records):
                report.irrecoverable(
                    "physics.hull.edge.invalid",
                    "A hull edge chain leaves the edge table.",
                    path=path + ("faces", face_index),
                    details={"edge": current, "edge_count": len(edge_records)},
                )
                vertices = []
                break
            visited.add(current)
            edge = edge_records[current]
            vertices.append(edge["origin"])
            current = edge["next"]
            if current == start:
                break
        if current != start:
            report.irrecoverable(
                "physics.hull.edge.cycle",
                "A hull edge chain does not close and cannot be reconstructed.",
                path=path + ("faces", face_index),
            )
            continue
        if len(vertices) >= 3:
            faces.append(tuple(vertices))
    return tuple(faces)


def physics_shapes_from_block(phys_block: Mapping[str, Any], *, report: LossReport | None = None
                              ) -> tuple[PhysicsShape, ...]:
    loss_report = report if report is not None else LossReport()
    parts = phys_block.get("m_parts", ())
    bone_parents = phys_block.get("m_boneParents", ())
    bone_names = phys_block.get("m_boneNames", ())
    collision_attributes = list(phys_block.get("m_collisionAttributes", ()))
    surface_hashes = list(phys_block.get("m_surfacePropertyHashes", ()))
    keys = EntityKeyValuesKeys()
    surface_properties = [
        keys.get(int(value)) for value in surface_hashes
    ]
    shapes: list[PhysicsShape] = []

    for part_index, part in enumerate(parts):
        parent_bone = ""
        if part_index < len(bone_parents):
            parent_index = int(bone_parents[part_index])
            if 0 <= parent_index < len(bone_names):
                parent_bone = str(bone_names[parent_index])
        fallback = parent_bone or f"physics_{part_index}"
        shape_set = part["m_rnShape"]

        for index, record in enumerate(shape_set.get("m_spheres", ())):
            sphere = record["m_Sphere"]
            path = ("physics", part_index, "spheres", index)
            collision, surface, metadata = _shape_metadata(
                record,
                collision_attributes,
                surface_properties,
                surface_hashes,
                loss_report,
                path,
            )
            shapes.append(PhysicsShape(
                name=_shape_name(record, f"{fallback}_sphere_{index}"),
                kind="sphere",
                parent_bone=parent_bone,
                collision_property=collision,
                surface_property=surface,
                center=tuple(float(value) for value in sphere["m_vCenter"]),
                radius=float(sphere["m_flRadius"]),
                metadata=metadata,
            ))

        for index, record in enumerate(shape_set.get("m_capsules", ())):
            capsule = record["m_Capsule"]
            centers = capsule["m_vCenter"]
            path = ("physics", part_index, "capsules", index)
            collision, surface, metadata = _shape_metadata(
                record,
                collision_attributes,
                surface_properties,
                surface_hashes,
                loss_report,
                path,
            )
            shapes.append(PhysicsShape(
                name=_shape_name(record, f"{fallback}_capsule_{index}"),
                kind="capsule",
                parent_bone=parent_bone,
                collision_property=collision,
                surface_property=surface,
                point_a=tuple(float(value) for value in centers[0]),
                point_b=tuple(float(value) for value in centers[1]),
                radius=float(capsule["m_flRadius"]),
                metadata=metadata,
            ))

        for index, record in enumerate(shape_set.get("m_meshes", ())):
            mesh = record["m_Mesh"]
            path = ("physics", part_index, "meshes", index)
            collision, surface, metadata = _shape_metadata(
                record,
                collision_attributes,
                surface_properties,
                surface_hashes,
                loss_report,
                path,
            )
            vertices = _array(mesh["m_Vertices"], np.float32, 3)
            triangles = mesh["m_Triangles"]
            if isinstance(triangles, TypedArray):
                indices = np.asarray([item["m_nIndex"] for item in triangles], np.uint32).reshape((-1, 3))
            else:
                indices = _array(triangles, np.uint32, 3)
            shapes.append(PhysicsShape(
                name=_shape_name(record, f"{fallback}_mesh_{index}"),
                kind="mesh",
                parent_bone=parent_bone,
                collision_property=collision,
                surface_property=surface,
                vertices=tuple(tuple(map(float, vertex)) for vertex in vertices),
                faces=tuple(tuple(map(int, face)) for face in indices),
                metadata=metadata,
            ))

        for index, record in enumerate(shape_set.get("m_hulls", ())):
            hull = record["m_Hull"]
            path = ("physics", part_index, "hulls", index)
            collision, surface, metadata = _shape_metadata(
                record,
                collision_attributes,
                surface_properties,
                surface_hashes,
                loss_report,
                path,
            )
            vertex_data = hull.get("m_VertexPositions", hull.get("m_Vertices"))
            vertices = _array(vertex_data, np.float32, 3)
            shapes.append(PhysicsShape(
                name=_shape_name(record, f"{fallback}_hull_{index}"),
                kind="hull",
                parent_bone=parent_bone,
                collision_property=collision,
                surface_property=surface,
                vertices=tuple(tuple(map(float, vertex)) for vertex in vertices),
                faces=_hull_faces(hull, loss_report, path),
                metadata=metadata,
            ))
    return tuple(shapes)
