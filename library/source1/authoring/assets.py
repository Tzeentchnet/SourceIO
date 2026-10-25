from __future__ import annotations

import math
from dataclasses import dataclass

from ...source2.authoring import AuthoringMaterial
from ...source2.workshop import ContentRelativePath


def _optional_finite(value: float | None, label: str) -> float | None:
    if value is None:
        return None
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be finite")
    return normalized


@dataclass(frozen=True, slots=True)
class Source1PhongParameters:
    exponent: float | None = None
    boost: float | None = None
    tint: tuple[float, float, float] | None = None
    fresnel_ranges: tuple[float, float, float] | None = None

    def __post_init__(self):
        exponent = _optional_finite(self.exponent, "Phong exponent")
        boost = _optional_finite(self.boost, "Phong boost")
        if exponent is not None and exponent < 0:
            raise ValueError("Phong exponent cannot be negative")
        if boost is not None and boost < 0:
            raise ValueError("Phong boost cannot be negative")
        object.__setattr__(self, "exponent", exponent)
        object.__setattr__(self, "boost", boost)
        for field_name, width in (("tint", 3), ("fresnel_ranges", 3)):
            value = getattr(self, field_name)
            if value is None:
                continue
            normalized = tuple(float(component) for component in value)
            if len(normalized) != width or any(
                    not math.isfinite(component) for component in normalized
            ):
                raise ValueError(
                    f"Phong {field_name.replace('_', ' ')} must contain "
                    f"{width} finite values"
                )
            object.__setattr__(self, field_name, normalized)


@dataclass(frozen=True, slots=True)
class Source1AuthoringMaterial(AuthoringMaterial):
    existing_vmt: ContentRelativePath | None = None
    phong: Source1PhongParameters | None = None

    def __post_init__(self):
        AuthoringMaterial.__post_init__(self)
        if self.existing_resource is not None:
            raise ValueError(
                "Source 1 materials cannot reuse Source 2 material resources"
            )
        if self.existing_vmt is not None:
            resource = ContentRelativePath(self.existing_vmt)
            if (
                    resource.suffix.casefold() != ".vmt"
                    or resource.parts[0].casefold() == "materials"
            ):
                raise ValueError(
                    "Existing Source 1 materials must be material-relative "
                    f"editable .vmt paths: {resource}"
                )
            if self.textures or self.phong is not None:
                raise ValueError(
                    "Reused Source 1 materials cannot also author textures or phong"
                )
            object.__setattr__(self, "existing_vmt", resource)
        if self.phong is not None and not isinstance(
                self.phong,
                Source1PhongParameters,
        ):
            raise TypeError("phong must be Source1PhongParameters or None")
