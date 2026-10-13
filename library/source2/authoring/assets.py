from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..export import ModelDocument
from ..workshop import ContentRelativePath


class TextureRole(str, Enum):
    BASE_COLOR = "base_color"
    NORMAL = "normal"
    ROUGHNESS = "roughness"
    METALLIC = "metallic"


class MaterialAlphaMode(str, Enum):
    OPAQUE = "opaque"
    MASK = "mask"
    BLEND = "blend"


@dataclass(frozen=True, slots=True)
class AuthoringTexture:
    name: str
    role: TextureRole
    payload: bytes
    extension: str = ".png"
    srgb: bool = False

    def __post_init__(self):
        if isinstance(self.role, str) and not isinstance(self.role, TextureRole):
            object.__setattr__(self, "role", TextureRole(self.role))
        if not isinstance(self.payload, bytes):
            raise TypeError("Authoring texture payload must be bytes")
        if not self.extension.startswith("."):
            raise ValueError("Authoring texture extensions must start with '.'")


@dataclass(frozen=True, slots=True)
class AuthoringMaterial:
    name: str
    existing_resource: ContentRelativePath | None = None
    base_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    metallic: float = 0.0
    roughness: float = 0.5
    alpha_mode: MaterialAlphaMode = MaterialAlphaMode.OPAQUE
    alpha_cutoff: float = 0.5
    double_sided: bool = False
    textures: tuple[AuthoringTexture, ...] = ()

    def __post_init__(self):
        if self.existing_resource is not None:
            resource = ContentRelativePath(self.existing_resource)
            if resource.suffix != ".vmat" or resource.name.endswith("_c"):
                raise ValueError(
                    f"Existing material resources must be editable .vmat paths: {resource}"
                )
            object.__setattr__(self, "existing_resource", resource)
        if isinstance(self.alpha_mode, str) and not isinstance(self.alpha_mode, MaterialAlphaMode):
            object.__setattr__(self, "alpha_mode", MaterialAlphaMode(self.alpha_mode))
        object.__setattr__(self, "base_color", tuple(float(value) for value in self.base_color))
        object.__setattr__(self, "textures", tuple(self.textures))
        if len(self.base_color) != 4:
            raise ValueError("Base color must contain four components")
        if not 0.0 <= self.metallic <= 1.0:
            raise ValueError("Metallic must be between zero and one")
        if not 0.0 <= self.roughness <= 1.0:
            raise ValueError("Roughness must be between zero and one")
        if not 0.0 <= self.alpha_cutoff <= 1.0:
            raise ValueError("Alpha cutoff must be between zero and one")


@dataclass(frozen=True, slots=True)
class ExtractedAuthoringModel:
    name: str
    document: ModelDocument
    fbx_payload: bytes
    materials: tuple[AuthoringMaterial, ...]

    def __post_init__(self):
        if not isinstance(self.fbx_payload, bytes):
            raise TypeError("FBX payload must be bytes")
        object.__setattr__(self, "materials", tuple(self.materials))
