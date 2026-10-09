from __future__ import annotations

import pytest

from SourceIO.library.source2.capabilities import READ_ONLY_CAPABILITIES
from SourceIO.library.source2.compiled_resource import CompiledResource
from SourceIO.library.source2.exceptions import (
    ResourceRegistrationError,
    UnsupportedResourceVersionError,
)
from SourceIO.library.source2.interfaces import ResourceKind
from SourceIO.library.source2.keyvalues3.binary_keyvalues import write_valve_keyvalue3
from SourceIO.library.source2.keyvalues3.enums import (
    KV3CompressionMethod,
    KV3Format,
    KV3Signature,
    KV3Type,
    Specifier,
)
from SourceIO.library.source2.keyvalues3.types import (
    Object,
    String,
    TypedArray,
    UInt32,
)
from SourceIO.library.source2.resource_registry import ResourceRegistry
from SourceIO.library.utils import MemoryBuffer, TinyPath, WritableMemoryBuffer

from ._builders import build_ntro_with_struct, build_resource


class StubResource(CompiledResource):
    pass


class AlternateStubResource(CompiledResource):
    pass


def _object_array(values=()):
    return TypedArray(KV3Type.OBJECT, Specifier.UNSPECIFIED, list(values))


def _kv3_payload(value: Object) -> bytes:
    output = WritableMemoryBuffer()
    write_valve_keyvalue3(
        output,
        value,
        KV3Format.generic,
        KV3Signature.KV3_V3,
        KV3CompressionMethod.UNCOMPRESSED,
    )
    return bytes(output.data)


def test_registration_identify_and_parse_preserve_typed_resource():
    registry = ResourceRegistry(include_builtins=False)
    registration = registry.register(
        ResourceKind.MODEL,
        StubResource,
        extensions=(".vmdl_c",),
        capabilities=READ_ONLY_CAPABILITIES,
        supported_versions=(1,),
    )
    data = build_resource(resource_version=1)
    identify_buffer = MemoryBuffer(data)
    parse_buffer = MemoryBuffer(data)

    identity = registry.identify(
        identify_buffer,
        TinyPath("models/test.vmdl_c"),
    )
    resource = registry.parse(parse_buffer, "models/test.vmdl_c")

    assert registration.resource_type is StubResource
    assert identity.kind is ResourceKind.MODEL
    assert identity.resource_version == 1
    assert identity.is_known
    assert any("path extension" in item for item in identity.evidence)
    assert isinstance(resource, StubResource)
    assert resource.identity == identity
    assert resource.capabilities == READ_ONLY_CAPABILITIES
    assert identify_buffer.tell() == 0
    assert parse_buffer.tell() == 0


def test_identical_registration_is_idempotent():
    registry = ResourceRegistry(include_builtins=False)

    first = registry.register(
        ResourceKind.MODEL,
        StubResource,
        extensions=(".vmdl_c",),
        supported_versions=(1,),
    )
    second = registry.register(
        ResourceKind.MODEL,
        StubResource,
        extensions=(".vmdl_c",),
        supported_versions=(1,),
    )

    assert second is first
    assert registry.registrations == (first,)


def test_future_resource_version_is_not_clamped_to_registered_parser():
    registry = ResourceRegistry(include_builtins=False)
    registry.register(
        ResourceKind.MODEL,
        StubResource,
        extensions=(".vmdl_c",),
        supported_versions=(1,),
    )
    registry.register(
        ResourceKind.MODEL,
        AlternateStubResource,
        extensions=(".vmdl_c",),
        supported_versions=(3,),
    )
    data = build_resource(resource_version=2)

    identity = registry.identify(data, "models/test.vmdl_c")

    assert identity.kind is ResourceKind.MODEL
    assert any(
        diagnostic.code == "source2.resource.unsupported_version"
        for diagnostic in identity.diagnostics
    )
    with pytest.raises(UnsupportedResourceVersionError):
        registry.parse(data, "models/test.vmdl_c")


def test_overlapping_parser_registrations_remain_explicitly_ambiguous():
    registry = ResourceRegistry(include_builtins=False)
    registry.register(
        ResourceKind.MODEL,
        StubResource,
        extensions=(".vmdl_c",),
        supported_versions=(1,),
    )
    registry.register(
        ResourceKind.MODEL,
        AlternateStubResource,
        extensions=(".vmdl_c",),
        supported_versions=(1,),
    )

    with pytest.raises(ResourceRegistrationError):
        registry.parse(build_resource(), "models/test.vmdl_c")


def test_ntro_structure_is_identity_evidence_without_a_path():
    registry = ResourceRegistry(include_builtins=False)
    data = build_resource(((
        "NTRO",
        build_ntro_with_struct("PermModelData_t"),
    ),))

    identity = registry.identify(data)

    assert identity.kind is ResourceKind.MODEL
    assert identity.confidence == 0.9
    assert "NTRO structure PermModelData_t" in identity.evidence


def test_red2_compiler_and_input_metadata_are_identity_evidence():
    registry = ResourceRegistry(include_builtins=False)
    input_dependency = Object({
        "m_RelativeFilename": String("materials/test.vmat"),
        "m_SearchPath": String("MOD"),
        "m_nFileCRC": UInt32(1),
    })
    special_dependency = Object({
        "m_String": String("Texture Compiler Version"),
        "m_CompilerIdentifier": String("CompileTexture"),
        "m_nFingerprint": UInt32(1),
        "m_nUserData": UInt32(0),
    })
    payload = _kv3_payload(Object({
        "m_InputDependencies": _object_array((input_dependency,)),
        "m_SpecialDependencies": _object_array((special_dependency,)),
    }))

    identity = registry.identify(build_resource((("RED2", payload),)))

    assert identity.kind is ResourceKind.TEXTURE
    assert identity.compiler == "CompileTexture"
    assert identity.input_path == "materials/test.vmat"
    assert any("compiler metadata" in item for item in identity.evidence)


def test_ctrl_tokens_and_registered_data_magic_are_identity_evidence():
    ctrl_registry = ResourceRegistry(include_builtins=False)
    ctrl_payload = _kv3_payload(Object({
        "m_CompilerIdentifier": String("CompileParticleSystem"),
    }))

    ctrl_identity = ctrl_registry.identify(
        build_resource((("CTRL", ctrl_payload),))
    )

    assert ctrl_identity.kind is ResourceKind.PARTICLE_SYSTEM

    magic_registry = ResourceRegistry(include_builtins=False)
    magic_registry.register(
        ResourceKind.MATERIAL,
        StubResource,
        data_magics=(b"CSTM",),
    )
    magic_identity = magic_registry.identify(
        build_resource((("DATA", b"CSTM payload"),))
    )

    assert magic_identity.kind is ResourceKind.MATERIAL
    assert "registered DATA magic 4353544d" in magic_identity.evidence


def test_known_and_future_kv3_data_magic_remain_inspectable():
    registry = ResourceRegistry(include_builtins=False)

    known = registry.identify(
        build_resource((("DATA", KV3Signature.KV3_V5.value),))
    )
    future = registry.identify(
        build_resource((("DATA", b"\x063VK"),))
    )

    assert known.kind is ResourceKind.KEYVALUES3
    assert future.kind is ResourceKind.UNKNOWN
    assert any(
        diagnostic.code == "source2.kv3.unsupported_version"
        for diagnostic in future.diagnostics
    )


def test_registry_inspects_resource_section_with_trailing_stream_data():
    registry = ResourceRegistry(include_builtins=False)
    metadata = build_resource((("DATA", b"metadata"),))
    data = metadata + b"streaming payload"

    identity = registry.identify(data, "textures/test.vtex_c")
    resource = registry.parse(data, "textures/test.vtex_c")

    assert identity.kind is ResourceKind.TEXTURE
    assert resource.header.file_size == len(metadata)
    assert resource._buffer.size() == len(data)
    assert resource.get_block_bytes(block_name="DATA") == b"metadata"
