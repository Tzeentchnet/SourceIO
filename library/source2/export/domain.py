from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..provenance import ResourceProvenance, to_json_safe

Vector2 = tuple[float, float]
Vector3 = tuple[float, float, float]
Vector4 = tuple[float, float, float, float]


@dataclass(slots=True)
class CornerData:
    normal: Vector3 | None = None
    tangent: Vector4 | None = None
    texcoords: tuple[Vector2, ...] = ()
    color: Vector4 | None = None
    bone_indices: tuple[int, ...] = ()
    bone_weights: tuple[float, ...] = ()
    custom_attributes: tuple[tuple[str, tuple[int | float, ...]], ...] = ()

    def signature(self) -> tuple:
        return (
            self.normal,
            self.tangent,
            self.texcoords,
            self.color,
            self.bone_indices,
            self.bone_weights,
            tuple(sorted(self.custom_attributes)),
        )


@dataclass(slots=True)
class MeshVertex:
    position: Vector3
    corner: CornerData = field(default_factory=CornerData)

    def signature(self) -> tuple:
        return self.position, self.corner.signature()


@dataclass(slots=True)
class MeshFace:
    vertices: tuple[int, ...]
    material: str

    def __post_init__(self):
        self.vertices = tuple(int(vertex) for vertex in self.vertices)
        if len(self.vertices) < 3:
            raise ValueError("A mesh face requires at least three vertices")


@dataclass(slots=True)
class StaticMesh:
    name: str
    vertices: tuple[MeshVertex, ...]
    faces: tuple[MeshFace, ...]
    bodygroups: tuple[str, ...] = ()
    lods: tuple[int, ...] = ()
    overlay: bool = False
    source_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.vertices = tuple(self.vertices)
        self.faces = tuple(self.faces)
        self.bodygroups = tuple(self.bodygroups)
        self.lods = tuple(int(lod) for lod in self.lods)
        self.metadata = to_json_safe(self.metadata, _path=f"$.meshes.{self.name}.metadata")
        vertex_count = len(self.vertices)
        for face in self.faces:
            for vertex in face.vertices:
                if vertex < 0 or vertex >= vertex_count:
                    raise ValueError(f"Mesh {self.name!r} face references invalid vertex {vertex}")


@dataclass(slots=True)
class BodyGroup:
    name: str
    choices: tuple[tuple[str, ...], ...]


@dataclass(slots=True)
class LODLevel:
    index: int
    switch_distance: float
    meshes: tuple[str, ...]


@dataclass(slots=True)
class MaterialRemap:
    source: str
    target: str


@dataclass(slots=True)
class Skin:
    name: str
    remaps: tuple[MaterialRemap, ...]
    is_default: bool = False


@dataclass(slots=True)
class AttachmentInfluence:
    parent_bone: str
    origin: Vector3
    rotation: Vector4
    weight: float = 1.0


@dataclass(slots=True)
class Attachment:
    name: str
    influences: tuple[AttachmentInfluence, ...]
    ignore_rotation: bool = False


@dataclass(slots=True)
class PhysicsShape:
    name: str
    kind: str
    parent_bone: str = ""
    surface_property: str = ""
    collision_property: str = ""
    center: Vector3 | None = None
    radius: float | None = None
    point_a: Vector3 | None = None
    point_b: Vector3 | None = None
    vertices: tuple[Vector3, ...] = ()
    faces: tuple[tuple[int, ...], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.vertices = tuple(self.vertices)
        self.faces = tuple(tuple(face) for face in self.faces)
        self.metadata = to_json_safe(self.metadata, _path=f"$.physics.{self.name}.metadata")


@dataclass(slots=True)
class Hitbox:
    name: str
    set_name: str
    parent_bone: str
    minimum: Vector3
    maximum: Vector3
    group_id: int = 0
    surface_property: str = ""


@dataclass(slots=True)
class AssetReference:
    kind: str
    path: str
    name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.metadata = to_json_safe(self.metadata, _path=f"$.references.{self.path}.metadata")


@dataclass(slots=True)
class ModelDocument:
    name: str
    meshes: tuple[StaticMesh, ...]
    bodygroups: tuple[BodyGroup, ...] = ()
    lods: tuple[LODLevel, ...] = ()
    skins: tuple[Skin, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    physics_shapes: tuple[PhysicsShape, ...] = ()
    hitboxes: tuple[Hitbox, ...] = ()
    flex_references: tuple[AssetReference, ...] = ()
    animation_references: tuple[AssetReference, ...] = ()
    provenance: ResourceProvenance | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.meshes = tuple(self.meshes)
        self.bodygroups = tuple(self.bodygroups)
        self.lods = tuple(self.lods)
        self.skins = tuple(self.skins)
        self.attachments = tuple(self.attachments)
        self.physics_shapes = tuple(self.physics_shapes)
        self.hitboxes = tuple(self.hitboxes)
        self.flex_references = tuple(self.flex_references)
        self.animation_references = tuple(self.animation_references)
        self.metadata = to_json_safe(self.metadata, _path="$.metadata")

    def to_dict(self) -> dict[str, Any]:
        return to_json_safe(self)


@dataclass(slots=True)
class Transform:
    origin: Vector3 = (0.0, 0.0, 0.0)
    angles: Vector3 = (0.0, 0.0, 0.0)
    scale: Vector3 = (1.0, 1.0, 1.0)
    matrix: tuple[tuple[float, ...], ...] | None = None


@dataclass(slots=True)
class EntityConnection:
    output_name: str
    target_name: str
    input_name: str
    parameter: str = ""
    delay: float = 0.0
    times_to_fire: int = -1
    target_type: int = 0


@dataclass(slots=True)
class HammerEntity:
    classname: str
    properties: dict[str, Any]
    transform: Transform = field(default_factory=Transform)
    connections: tuple[EntityConnection, ...] = ()
    layer: str | None = None
    source_id: str | None = None

    def __post_init__(self):
        self.properties = to_json_safe(self.properties, _path=f"$.entities.{self.classname}.properties")
        self.connections = tuple(self.connections)


@dataclass(slots=True)
class HammerProp:
    model: str
    transform: Transform = field(default_factory=Transform)
    skin: str = "default"
    tint: Vector4 | None = None
    layer: str | None = None
    source_id: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.properties = to_json_safe(self.properties, _path=f"$.props.{self.model}.properties")


@dataclass(slots=True)
class HammerOverlay:
    name: str
    material: str | None = None
    mesh: StaticMesh | None = None
    transform: Transform = field(default_factory=Transform)
    layer: str | None = None
    source_model: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.properties = to_json_safe(self.properties, _path=f"$.overlays.{self.name}.properties")


@dataclass(slots=True)
class WorldLayer:
    name: str
    visible: bool = True
    source_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.metadata = to_json_safe(self.metadata, _path=f"$.layers.{self.name}.metadata")


@dataclass(slots=True)
class HammerMapDocument:
    name: str
    entities: tuple[HammerEntity, ...] = ()
    props: tuple[HammerProp, ...] = ()
    overlays: tuple[HammerOverlay, ...] = ()
    world_layers: tuple[WorldLayer, ...] = ()
    world_properties: dict[str, Any] = field(default_factory=lambda: {"classname": "worldspawn"})
    provenance: ResourceProvenance | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.entities = tuple(self.entities)
        self.props = tuple(self.props)
        self.overlays = tuple(self.overlays)
        self.world_layers = tuple(self.world_layers)
        self.world_properties = to_json_safe(self.world_properties, _path="$.world_properties")
        self.metadata = to_json_safe(self.metadata, _path="$.metadata")

    def to_dict(self) -> dict[str, Any]:
        return to_json_safe(self)
