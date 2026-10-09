from __future__ import annotations

import struct
from dataclasses import dataclass, field
from os import PathLike
from typing import ClassVar, Collection, Iterator, Optional, Type, TypeVar, Union

from ..shared.content_manager import ContentManager
from ..utils import Buffer, MemoryBuffer, TinyPath
from .blocks.base import BaseBlock
from .blocks.binary_blob import UnknownBlock
from .blocks.resource_external_reference_list import ResourceExternalReferenceList
from .blocks.resource_introspection_manifest.manifest import ResourceIntrospectionManifest
from .capabilities import CONTAINER_CAPABILITIES
from .compiled_file_header import BlockInfo, CompiledHeader
from .exceptions import BlockIndexError, BlockParseError, Source2Error
from .interfaces import Diagnostic, ResourceCapabilities, ResourceIdentity, ResourceKind
from .utils.ntro_reader import NTROBuffer

CompiledResourceT = TypeVar("CompiledResourceT", bound="CompiledResource")
BlockT = TypeVar("BlockT", bound=BaseBlock)
DATA_BLOCK = -9999

_INTROSPECTION_INDEPENDENT_BLOCKS = frozenset(("NTRO", "RERL"))
_PREREQUISITE_BLOCKS = ("NTRO", "RERL", "REDI", "RED2", "CTRL")


@dataclass(slots=True)
class CompiledResource:
    _buffer: Buffer
    _filepath: TinyPath
    _header: CompiledHeader
    _blocks: dict[int, BaseBlock | None] = field(default_factory=dict)
    _identity: ResourceIdentity | None = None
    _capabilities: ResourceCapabilities = CONTAINER_CAPABILITIES
    _diagnostics: list[Diagnostic] = field(default_factory=list)
    _parsing_blocks: set[int] = field(default_factory=set, repr=False)

    resource_kind: ClassVar[ResourceKind] = ResourceKind.UNKNOWN
    declared_capabilities: ClassVar[ResourceCapabilities] = CONTAINER_CAPABILITIES

    @property
    def name(self):
        return self._filepath.stem

    @property
    def path(self) -> TinyPath:
        return self._filepath

    @property
    def header(self) -> CompiledHeader:
        return self._header

    @property
    def identity(self) -> ResourceIdentity:
        if self._identity is None:
            self._identity = ResourceIdentity(
                kind=self.resource_kind,
                resource_version=self._header.resource_version,
                header_version=self._header.header_version,
                path=str(self._filepath),
                extension=self._filepath.suffix.lower() or None,
                confidence=1.0 if self.resource_kind is not ResourceKind.UNKNOWN else 0.0,
                evidence=("resource class declaration",)
                if self.resource_kind is not ResourceKind.UNKNOWN else (),
            )
        return self._identity

    @property
    def capabilities(self) -> ResourceCapabilities:
        return self._capabilities

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return tuple(self._diagnostics)

    @property
    def block_info(self) -> tuple[BlockInfo, ...]:
        return tuple(self._header.blocks)

    def _set_registry_metadata(
            self,
            identity: ResourceIdentity,
            capabilities: ResourceCapabilities,
    ):
        self._identity = identity
        self._capabilities = capabilities
        self._diagnostics.extend(identity.diagnostics)

    def get_data_block_type(self):
        return None

    def _find_block_id(self, block_name: str) -> int | None:
        for block_id, block in enumerate(self._header.blocks):
            if block.name == block_name:
                return block_id
        return None

    def _resolve_block_id(self, block_id: int) -> int | None:
        if block_id == -1:
            return None
        if block_id == DATA_BLOCK:
            for candidate in range(len(self._header.blocks) - 1, -1, -1):
                if self._header.blocks[candidate].name == "DATA":
                    return candidate
            return None
        if block_id < 0 or block_id >= len(self._header.blocks):
            raise BlockIndexError(
                f"Block index {block_id} is outside [0, {len(self._header.blocks)})",
                details={"block_id": block_id, "block_count": len(self._header.blocks)},
            )
        return block_id

    def get_block_bytes(
            self,
            *,
            block_id: int | None = None,
            block_name: str | None = None,
    ) -> bytes | None:
        if block_id is None:
            if block_name is None:
                raise ValueError("Either block_id or block_name must be provided")
            block_id = self._find_block_id(block_name)
            if block_id is None:
                return None
        else:
            block_id = self._resolve_block_id(block_id)
            if block_id is None:
                return None

        info = self._header.blocks[block_id]
        original_offset = self._buffer.tell()
        try:
            self._buffer.seek(info.absolute_offset)
            data = self._buffer.read(info.size)
        finally:
            self._buffer.seek(original_offset)
        if len(data) != info.size:
            raise BlockParseError(
                f"Block {info.name!r} is truncated",
                offset=info.absolute_offset,
                block_name=info.name,
                details={"expected": info.size, "actual": len(data)},
            )
        return data

    def _load_prerequisites(self, target_name: str):
        if target_name in _INTROSPECTION_INDEPENDENT_BLOCKS:
            return
        prerequisites = (
            _PREREQUISITE_BLOCKS
            if target_name == "DATA"
            else _PREREQUISITE_BLOCKS[:2]
        )
        for prerequisite_name in prerequisites:
            if prerequisite_name == target_name:
                continue
            for block_id, info in enumerate(self._header.blocks):
                if info.name == prerequisite_name and block_id not in self._blocks:
                    self._get_block(None, info, block_id)

    def _get_block(
            self,
            block_class: Type[BlockT] | None,
            info_block: BlockInfo,
            block_id: int | None = None,
    ) -> BlockT | None:
        from .blocks.all_blocks import guess_block_type

        if block_id is None:
            try:
                block_id = self._header.blocks.index(info_block)
            except ValueError as exc:
                raise BlockParseError(
                    "Block metadata does not belong to this resource",
                    block_name=info_block.name,
                ) from exc
        if block_id in self._blocks:
            return self._blocks[block_id]
        if block_id in self._parsing_blocks:
            raise BlockParseError(
                f"Cyclic prerequisite while parsing block {info_block.name!r}",
                offset=info_block.absolute_offset,
                block_name=info_block.name,
            )

        self._parsing_blocks.add(block_id)
        resolved_class: Type[BaseBlock] | None = block_class
        try:
            self._load_prerequisites(info_block.name)
            resolved_class = (
                block_class
                or (self.get_data_block_type() if info_block.name == "DATA" else None)
                or guess_block_type(info_block.name)
            )
            if not isinstance(resolved_class, type) or not issubclass(resolved_class, BaseBlock):
                raise BlockParseError(
                    f"Decoder for block {info_block.name!r} is not a BaseBlock subclass",
                    offset=info_block.absolute_offset,
                    block_name=info_block.name,
                )

            data = self.get_block_bytes(block_id=block_id)
            ntro_info = None
            resource_mapping = None
            if (
                    self.has_block(block_name="NTRO")
                    and info_block.name not in _INTROSPECTION_INDEPENDENT_BLOCKS
            ):
                ntro = self.get_block(
                    ResourceIntrospectionManifest,
                    block_name="NTRO",
                )
                resource_list = self.get_block(
                    ResourceExternalReferenceList,
                    block_name="RERL",
                ) or ()
                ntro_info = ntro.info if ntro is not None else None
                resource_mapping = {
                    resource.hash: resource.name for resource in resource_list
                }

            block_buffer = NTROBuffer(data, ntro_info, resource_mapping)
            parsed_block = resolved_class.from_buffer(block_buffer)
            if not isinstance(parsed_block, BaseBlock):
                raise BlockParseError(
                    f"Decoder {resolved_class.__name__} returned "
                    f"{type(parsed_block).__name__}, expected BaseBlock",
                    offset=info_block.absolute_offset,
                    block_name=info_block.name,
                )
            parsed_block.custom_name = info_block.name
            self._blocks[block_id] = parsed_block
            return parsed_block
        except BlockParseError:
            raise
        except Source2Error as exc:
            if exc.path is None:
                exc.path = str(self._filepath)
            if exc.block_name is None:
                exc.block_name = info_block.name
            exc.details.setdefault("block_absolute_offset", info_block.absolute_offset)
            exc.details.setdefault(
                "decoder",
                getattr(resolved_class, "__name__", None),
            )
            raise
        except (
                BufferError,
                EOFError,
                IndexError,
                KeyError,
                NotImplementedError,
                UnicodeError,
                ValueError,
                struct.error,
        ) as exc:
            raise BlockParseError(
                f"Failed to parse block {info_block.name!r}: {exc}",
                offset=info_block.absolute_offset,
                block_name=info_block.name,
                details={"decoder": getattr(block_class, "__name__", None)},
            ) from exc
        finally:
            self._parsing_blocks.discard(block_id)

    def get_block(
            self,
            block_class: Type[BlockT] | None = None,
            *,
            block_id: Optional[int] = None,
            block_name: Optional[str] = None,
    ) -> BlockT | None:
        if block_id is not None:
            resolved_id = self._resolve_block_id(block_id)
            if resolved_id is None:
                return None
        elif block_name is not None:
            resolved_id = self._find_block_id(block_name)
            if resolved_id is None:
                return None
        else:
            raise ValueError("Either block_id or block_name must be provided")

        if resolved_id in self._blocks:
            return self._blocks[resolved_id]
        return self._get_block(
            block_class,
            self._header.blocks[resolved_id],
            resolved_id,
        )

    def get_blocks(
            self,
            block_class: Type[BlockT] | None,
            block_name: str,
    ) -> Collection[BlockT]:
        blocks: list[BlockT] = []
        for block_id, info in enumerate(self._header.blocks):
            if info.name == block_name:
                block = self.get_block(block_class, block_id=block_id)
                if block is not None:
                    blocks.append(block)
        return blocks

    def has_block(self, block_name: str) -> bool:
        return any(block.name == block_name for block in self._header.blocks)

    def block_state(self, block_id: int) -> str:
        resolved_id = self._resolve_block_id(block_id)
        if resolved_id is None or resolved_id not in self._blocks:
            return "unparsed"
        if isinstance(self._blocks[resolved_id], UnknownBlock):
            return "unknown"
        return "parsed"

    def preload_metadata(self):
        for block_name in _PREREQUISITE_BLOCKS:
            self.get_blocks(None, block_name)

    def parse_blocks(self) -> tuple[BaseBlock, ...]:
        self.preload_metadata()
        for block_id, info in enumerate(self._header.blocks):
            if info.name not in _PREREQUISITE_BLOCKS and info.name != "DATA":
                self.get_block(None, block_id=block_id)
        for block_id, info in enumerate(self._header.blocks):
            if info.name == "DATA":
                self.get_block(None, block_id=block_id)
        return tuple(
            block for _, block in sorted(self._blocks.items())
            if block is not None
        )

    def iter_block_info(self, block_name: str | None = None) -> Iterator[BlockInfo]:
        for block in self._header.blocks:
            if block_name is None or block.name == block_name:
                yield block

    def has_child_resource(self, name_or_id: str | int, cm: ContentManager):
        resource_path = self.get_child_resource_path(name_or_id)
        if resource_path is None:
            return None
        return cm.find_file(resource_path) is not None

    def get_child_resource_path(self, name_or_id: str | int) -> TinyPath | None:
        external_resources = self.get_block(
            ResourceExternalReferenceList,
            block_name="RERL",
        ) or ()
        for child_resource in external_resources:
            if child_resource.hash == name_or_id or child_resource.name == name_or_id:
                return TinyPath(child_resource.name + "_c")
        return None

    def get_child_resource(
            self,
            name_or_id: Union[str, int],
            cm: ContentManager,
            resource_class: Type[CompiledResourceT],
    ) -> CompiledResourceT | None:
        resource_path = self.get_child_resource_path(name_or_id)
        if resource_path is None:
            return None
        file = cm.find_file(resource_path)
        if file is None:
            return None
        return resource_class.from_buffer(file, resource_path)

    def get_child_resources(self):
        external_resources = self.get_block(
            ResourceExternalReferenceList,
            block_name="RERL",
        ) or ()
        return (
            [resource.name for resource in external_resources]
            + [resource.hash for resource in external_resources]
        )

    def get_dependencies(self):
        external_resources = self.get_block(
            ResourceExternalReferenceList,
            block_name="RERL",
        ) or ()
        dependencies = []
        for dependency in external_resources:
            path = TinyPath(dependency.name)
            dependencies.append(path.with_suffix(path.suffix + "_c"))
        return dependencies

    @classmethod
    def from_buffer(
            cls,
            buffer: Buffer | bytes | bytearray | memoryview,
            filename: TinyPath | str | PathLike[str] | None = None,
    ):
        if isinstance(buffer, Buffer):
            data = buffer.read()
        elif isinstance(buffer, (bytes, bytearray, memoryview)):
            data = bytes(buffer)
        else:
            raise TypeError(
                "buffer must be a SourceIO Buffer or bytes-like object, "
                f"got {type(buffer).__name__}"
            )
        inmemory_buffer = MemoryBuffer(data)
        path = TinyPath(filename or "<memory>")
        try:
            header = CompiledHeader.from_buffer(inmemory_buffer)
        except Source2Error as exc:
            if exc.path is None:
                exc.path = str(path)
            raise
        return cls(
            inmemory_buffer,
            path,
            header,
            _capabilities=cls.declared_capabilities,
        )

    def to_buffer(self, buffer: Buffer):
        self._header.to_buffer(buffer)

        blocks_to_write = [
            block
            for _, block in sorted(self._blocks.items())
            if block is not None
            and not isinstance(block, ResourceIntrospectionManifest)
        ]
        buffer.write_uint32(len(blocks_to_write))
        block_labels = []
        for block in blocks_to_write:
            block_name = block.custom_name or block.__class__.__name__
            if len(block_name) != 4 or not block_name.isascii():
                raise ValueError(
                    f"Serialized block name must be an ASCII FourCC, got {block_name!r}"
                )
            label = buffer.new_label(f"{block_name} info", 12, None)
            label["block_name"] = block_name
            label["offset"] = buffer.tell() - 8
            block_labels.append(label)
        buffer.seek(12, 1)

        for block, label in zip(blocks_to_write, block_labels):
            start = buffer.tell()
            label["offset"] = start - label["offset"]
            block.to_buffer(buffer)
            label["size"] = buffer.tell() - start

        for label in block_labels:
            label.write("4s", label["block_name"].encode("ascii"))
            label.write("II", label["offset"], label["size"])
