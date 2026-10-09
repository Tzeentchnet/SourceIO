from dataclasses import dataclass, field

from .....utils import Buffer
from ....keyvalues3.types import Object
from .dependency import Dependency, DependencyList


@dataclass(slots=True)
class CustomDependency(Dependency):
    data: Object = field(default_factory=Object)

    @classmethod
    def from_vkv3(cls, vkv: Object) -> 'CustomDependency':
        return cls(vkv)

    @classmethod
    def from_buffer(cls, buffer: Buffer):
        raise NotImplementedError('Unsupported, if found please report to ValveResourceFormat repo and to SourceIO2')

    def to_buffer(self, buffer: Buffer):
        raise NotImplementedError("Binary REDI custom dependencies have no known stable layout")

    def to_vkv3(self) -> Object:
        return self.data


class CustomDependencies(DependencyList[CustomDependency]):
    dependency_type = CustomDependency
