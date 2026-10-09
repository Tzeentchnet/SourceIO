from dataclasses import dataclass

from .....shared.vector_types import Vector3
from .....utils import Buffer


@dataclass(slots=True)
class StudioPivot:
    point: Vector3[float]
    start: int
    end: int

    @classmethod
    def from_buffer(cls, buffer: Buffer):
        point = buffer.read_fmt('3f')
        return cls(point, *buffer.read_fmt('2I'))
