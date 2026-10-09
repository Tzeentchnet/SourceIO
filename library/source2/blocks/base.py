import abc
from abc import ABC

from ..utils.ntro_reader import NTROBuffer
from ...utils import Buffer


class BaseBlock(ABC):
    custom_name: str | None = None

    @classmethod
    @abc.abstractmethod
    def from_buffer(cls, buffer: NTROBuffer) -> 'BaseBlock':
        raise NotImplementedError()


    @abc.abstractmethod
    def to_buffer(self, buffer: Buffer) -> None:
        raise NotImplementedError()