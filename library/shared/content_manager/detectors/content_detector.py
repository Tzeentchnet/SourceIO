import abc
from typing import Type, Collection

from ..provider import ContentProvider
from ..providers import register_provider
from ....utils import TinyPath


class ContentDetector(abc.ABC):

    @classmethod
    @abc.abstractmethod
    def game(cls) -> str:
        raise NotImplementedError("Implement me")

    @classmethod
    @abc.abstractmethod
    def find_game_root(cls, path: TinyPath) -> TinyPath | None:
        raise NotImplementedError("Implement me")

    @classmethod
    @abc.abstractmethod
    def scan(cls, path: TinyPath) -> tuple[Collection[ContentProvider] | None, TinyPath | None]:
        raise NotImplementedError("Implement me")

    @staticmethod
    def add_provider(provider: ContentProvider, content_providers: set[ContentProvider]):
        if provider not in content_providers:
            content_providers.add(register_provider(provider))

    @classmethod
    def add_if_exists(cls, path: TinyPath,
                      content_provider_class: Type[ContentProvider],
                      content_providers: set[ContentProvider]):
        if path.exists():
            cls.add_provider(content_provider_class(path), content_providers)
