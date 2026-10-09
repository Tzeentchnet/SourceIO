from typing import Type

from .base import BaseBlock
from .agrp_block import AgrpBlock
from .aseq_block import AseqBlock
from .binary_blob import BinaryBlob, UnknownBlock
from .kv3_block import KVBlock
from .morph_block import MorphBlock
from .phys_block import PhysBlock
from .resource_edit_info import ResourceEditInfo, ResourceEditInfo2
from .resource_external_reference_list import ResourceExternalReferenceList
from .resource_introspection_manifest.manifest import ResourceIntrospectionManifest
from .vertex_index_buffer import VertexIndexBuffer


_BLOCK_TYPES: dict[str, Type[BaseBlock]] = {
    "NTRO": ResourceIntrospectionManifest,
    "REDI": ResourceEditInfo,
    "RED2": ResourceEditInfo2,
    "RERL": ResourceExternalReferenceList,
    "ASEQ": AseqBlock,
    "MDAT": KVBlock,
    "PHYS": PhysBlock,
    "AGRP": AgrpBlock,
    "DATA": KVBlock,
    "CTRL": KVBlock,
    "INSG": KVBlock,
    "ANIM": KVBlock,
    "DSTF": KVBlock,
    "LaCo": KVBlock,
    "SNAP": KVBlock,
    "MRPH": MorphBlock,
    "MBUF": VertexIndexBuffer,
    "VBIB": VertexIndexBuffer,
    "TBUF": VertexIndexBuffer,
    "MVTX": BinaryBlob,
    "MIDX": BinaryBlob,
}


def register_block_type(
        name: str,
        block_type: Type[BaseBlock],
        *,
        replace: bool = False,
) -> Type[BaseBlock]:
    if len(name) != 4 or not name.isascii() or not name.isprintable():
        raise ValueError(f"Block name must be a printable ASCII FourCC, got {name!r}")
    if not isinstance(block_type, type) or not issubclass(block_type, BaseBlock):
        raise TypeError("block_type must be a BaseBlock subclass")
    if name in _BLOCK_TYPES and not replace:
        raise ValueError(f"A decoder is already registered for block {name!r}")
    _BLOCK_TYPES[name] = block_type
    return block_type


def unregister_block_type(name: str) -> Type[BaseBlock] | None:
    return _BLOCK_TYPES.pop(name, None)


def guess_block_type(name: str) -> Type[BaseBlock]:
    return _BLOCK_TYPES.get(name, UnknownBlock)


register_block = register_block_type
