"""Engine-neutral sprite-sheet metadata.

Source 1 stores a sheet as the VTF ``\\x10\\0\\0`` resource and Source 2 as the VTEX ``SHEET`` extra data. Both
decode into these types, so code that slices or animates a sheet does not need to know which engine it came from.
Coordinates are normalized UVs with the origin at the top left of the image.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class SpriteSheetImage:
    cropped_min: tuple[float, float]
    cropped_max: tuple[float, float]
    uncropped_min: tuple[float, float]
    uncropped_max: tuple[float, float]

    @staticmethod
    def _pixel_rect(
            minimum: tuple[float, float],
            maximum: tuple[float, float],
            width: int,
            height: int,
    ) -> tuple[int, int, int, int]:
        if maximum[0] <= minimum[0] or maximum[1] <= minimum[1]:
            return 0, 0, 0, 0
        return (
            int(minimum[0] * width),
            int(minimum[1] * height),
            min(int(maximum[0] * width) + 1, width),
            min(int(maximum[1] * height) + 1, height),
        )

    def cropped_rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        return self._pixel_rect(self.cropped_min, self.cropped_max, width, height)

    def uncropped_rect(self, width: int, height: int) -> tuple[int, int, int, int]:
        return self._pixel_rect(self.uncropped_min, self.uncropped_max, width, height)

    @property
    def is_empty(self) -> bool:
        return self.uncropped_max[0] <= self.uncropped_min[0] or self.uncropped_max[1] <= self.uncropped_min[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cropped_min": list(self.cropped_min),
            "cropped_max": list(self.cropped_max),
            "uncropped_min": list(self.uncropped_min),
            "uncropped_max": list(self.uncropped_max),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SpriteSheetImage:
        return cls(
            _pair(data["cropped_min"]),
            _pair(data["cropped_max"]),
            _pair(data["uncropped_min"]),
            _pair(data["uncropped_max"]),
        )


@dataclass(frozen=True, slots=True)
class SpriteSheetFrame:
    display_time: float
    images: tuple[SpriteSheetImage, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_time": self.display_time,
            "images": [image.to_dict() for image in self.images],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SpriteSheetFrame:
        return cls(
            float(data["display_time"]),
            tuple(SpriteSheetImage.from_dict(image) for image in data["images"]),
        )


@dataclass(frozen=True, slots=True)
class SpriteSheetSequence:
    sequence_id: int
    clamp: bool
    alpha_crop: bool
    no_color: bool
    no_alpha: bool
    total_time: float
    name: str
    float_params: Mapping[str, float]
    frames: tuple[SpriteSheetFrame, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sequence_id": self.sequence_id,
            "clamp": self.clamp,
            "alpha_crop": self.alpha_crop,
            "no_color": self.no_color,
            "no_alpha": self.no_alpha,
            "total_time": self.total_time,
            "name": self.name,
            "float_params": dict(self.float_params),
            "frames": [frame.to_dict() for frame in self.frames],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SpriteSheetSequence:
        return cls(
            int(data["sequence_id"]),
            bool(data["clamp"]),
            bool(data.get("alpha_crop", False)),
            bool(data.get("no_color", False)),
            bool(data.get("no_alpha", False)),
            float(data["total_time"]),
            str(data.get("name", "")),
            {str(key): float(value) for key, value in data.get("float_params", {}).items()},
            tuple(SpriteSheetFrame.from_dict(frame) for frame in data["frames"]),
        )


@dataclass(frozen=True, slots=True)
class SpriteSheetMetadata:
    sequences: tuple[SpriteSheetSequence, ...]
    raw_data: bytes = field(default=b"", repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {"sequences": [sequence.to_dict() for sequence in self.sequences]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SpriteSheetMetadata:
        return cls(tuple(SpriteSheetSequence.from_dict(sequence) for sequence in data["sequences"]))


def _pair(value) -> tuple[float, float]:
    x, y = value
    return float(x), float(y)
