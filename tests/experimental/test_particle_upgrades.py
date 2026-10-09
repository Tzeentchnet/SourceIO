import struct

import numpy as np
import pytest

from SourceIO.library.shared.content_manager import ContentManager
from SourceIO.library.shared.content_manager.providers.loose_files import LooseFilesContentProvider
from SourceIO.library.source2.keyvalues3.binary_keyvalues import write_valve_keyvalue3
from SourceIO.library.source2.keyvalues3.enums import (
    KV3CompressionMethod,
    KV3Format,
    KV3Signature,
    Specifier,
)
from SourceIO.library.source2.keyvalues3.types import Array, Int64, Object, String
from SourceIO.library.source2.interfaces import (
    Diagnostic,
    Maturity,
    ResourceCapabilities,
    ResourceKind,
)
from SourceIO.library.source2.particles import (
    PARTICLE_RESOURCE_CAPABILITIES,
    ParticleDocument,
    VERIFIED_PARTICLE_UPGRADER,
    kv3_to_python,
)
from SourceIO.library.source2.provenance import ResourceProvenance
from SourceIO.library.source2.resource_registry import ResourceRegistry
from SourceIO.library.source2.resource_types.compiled_particle_resource import (
    CompiledParticleResource,
    register_particle_resource,
)
from SourceIO.library.utils import MemoryBuffer, TinyPath, WritableMemoryBuffer


def _compiled_resource_bytes(name: str, payload: bytes) -> bytes:
    header_size = 28
    output = bytearray(header_size + len(payload))
    struct.pack_into("<IHHII", output, 0, len(output), 12, 1, 8, 1)
    struct.pack_into("<4sII", output, 16, name.encode("ascii"), header_size - 20, len(payload))
    output[header_size:] = payload
    return bytes(output)


def _particle_payload(data: Object, format_: KV3Format) -> bytes:
    output = WritableMemoryBuffer()
    write_valve_keyvalue3(
        output,
        data,
        format_,
        KV3Signature.KV3_V3,
        KV3CompressionMethod.UNCOMPRESSED,
    )
    return bytes(output.data)


def test_particle_resource_retains_immutable_original_and_separate_upgrade():
    source = Object({
        "_class": String("CParticleSystemDefinition"),
        "m_Operators": Array([
            Object({"_class": String("C_OP_SetControlPointPositions")}),
            Object({"_class": String("C_OP_FadeOutSimple")}),
        ]),
        "nested": Object({
            "value": Int64(7),
            "values": np.array([1, 2, 3], dtype=np.int32),
        }),
    })
    resource = CompiledParticleResource.from_buffer(
        MemoryBuffer(_compiled_resource_bytes("DATA", _particle_payload(source, KV3Format.vpcf1))),
        TinyPath("particles/test.vpcf_c"),
    )

    resource.data_block["nested"]["value"] = Int64(8)
    original = resource.original_kv3
    upgraded = resource.upgraded_kv3
    source_block = resource.data_block

    assert resource.upgrade_result.complete
    assert resource.upgrade_result.resulting_format == "vpcf2"
    assert "m_PreEmissionOperators" not in source_block
    assert len(source_block["m_Operators"]) == 2
    assert len(upgraded["m_PreEmissionOperators"]) == 1
    assert len(upgraded["m_Operators"]) == 1
    assert upgraded["nested"]["value"] == 7

    with pytest.raises(TypeError):
        original["new"] = "value"
    with pytest.raises(TypeError):
        original["nested"]["value"] = 9
    with pytest.raises(TypeError):
        original["nested"]["values"] = ()
    with pytest.raises((AttributeError, TypeError, ValueError)):
        original["nested"]["values"][0] = 9

    upgraded["nested"]["value"] = Int64(99)
    assert source_block["nested"]["value"] == 8
    assert original["nested"]["value"] == 7


def test_verified_upgrade_is_idempotent_and_does_not_mutate_source():
    source = Object({
        "m_Operators": Array([
            Object({"_class": String("C_OP_SetControlPointPositions")}),
            Object({"_class": String("C_OP_FadeOutSimple")}),
        ]),
    })

    first = VERIFIED_PARTICLE_UPGRADER.upgrade(source, "vpcf1")
    second = VERIFIED_PARTICLE_UPGRADER.upgrade(first.data, first.resulting_format)

    assert first.complete
    assert len(first.applied_steps) == 1
    assert second.complete
    assert not second.changed
    assert kv3_to_python(first.data) == kv3_to_python(second.data)
    assert len(source["m_Operators"]) == 2
    assert "m_PreEmissionOperators" not in source


def test_generic_snapshot_conversion_is_explicitly_verified():
    source = Object({
        "_class": String("CParticleSystemDefinition"),
        "m_pszSnapshotName": String(r"effects\impact.snapshot"),
    })

    result = VERIFIED_PARTICLE_UPGRADER.upgrade(source, "generic")

    assert result.complete
    assert [step.target_format for step in result.applied_steps] == ["vpcf1", "vpcf2"]
    assert "m_pszSnapshotName" in source
    assert result.data["m_hSnapshot"] == "particles/effects/impact.vsnap"
    assert result.data["m_hSnapshot"].specifier is Specifier.RESOURCE


@pytest.mark.parametrize(
    ("source_format", "diagnostic_code"),
    [
        (None, "particle.upgrade.missing_source_format"),
        ("unlisted-format", "particle.upgrade.unverified_format"),
        ("vpcf63", "particle.upgrade.source_newer_than_verified_target"),
    ],
)
def test_missing_and_unknown_versions_are_observable(source_format, diagnostic_code):
    source = Object({"value": Int64(1)})

    result = VERIFIED_PARTICLE_UPGRADER.upgrade(source, source_format)

    assert not result.complete
    assert not result.changed
    assert diagnostic_code in {diagnostic.code for diagnostic in result.diagnostics}
    result.data["value"] = Int64(2)
    assert source["value"] == 1


def test_particle_document_clones_the_upgraded_representation():
    source = Object({"nested": Object({"value": Int64(1)})})
    document = ParticleDocument(source, "vpcf2")

    clone = document.clone_upgraded_kv3()
    clone["nested"]["value"] = Int64(2)

    assert document.upgraded_kv3["nested"]["value"] == 1


def test_particle_children_use_shared_resolver_contract():
    source = Object({
        "m_Children": Array([
            Object({"m_ChildRef": String("particles/child.vpcf")}),
        ]),
    })
    provenance = ResourceProvenance("particles/parent.vpcf_c")

    class Resolver:
        def __init__(self):
            self.references = []

        def resolve(self, reference):
            self.references.append(reference)
            return b"child"

    resolver = Resolver()
    document = ParticleDocument(
        source,
        "vpcf2",
        provenance=provenance,
        resolver=resolver,
    )

    resolutions = document.resolve_children()

    assert resolutions[0].resolved
    assert resolutions[0].value == b"child"
    assert resolver.references[0].kind is ResourceKind.PARTICLE_SYSTEM
    assert str(resolver.references[0].path) == "particles/child.vpcf_c"
    assert resolver.references[0].source_path == "particles/parent.vpcf_c"


def test_particle_children_resolve_authored_paths_against_compiled_assets(tmp_path):
    root = tmp_path / "game"
    child_path = root / "particles" / "child.vpcf_c"
    child_path.parent.mkdir(parents=True)
    child_path.write_bytes(b"compiled child")
    manager = ContentManager()
    manager.clean()
    manager.add_child(LooseFilesContentProvider(TinyPath(root)))
    document = ParticleDocument(
        Object({
            "m_Children": Array([
                Object({"m_ChildRef": String(r"particles\child.vpcf")}),
            ]),
        }),
        "vpcf2",
        provenance=ResourceProvenance("particles/parent.vpcf_c"),
    )

    resolution = document.resolve_children(manager)[0]

    assert resolution.resolved
    try:
        assert resolution.value.read() == b"compiled child"
    finally:
        resolution.value.close()


def test_particle_resource_registers_through_shared_registry():
    source = Object({"_class": String("CParticleSystemDefinition")})
    compiled = _compiled_resource_bytes("DATA", _particle_payload(source, KV3Format.vpcf2))
    registry = ResourceRegistry(include_builtins=False)
    registration = register_particle_resource(registry)

    resource = registry.parse(compiled, "particles/test.vpcf_c")

    assert registration.kind is ResourceKind.PARTICLE_SYSTEM
    assert isinstance(resource, CompiledParticleResource)
    assert resource.capabilities == PARTICLE_RESOURCE_CAPABILITIES
    assert resource.capabilities.read is Maturity.EXPERIMENTAL
    assert resource.capabilities.render is Maturity.UNSUPPORTED


def test_particle_report_uses_shared_contract_types():
    document = ParticleDocument(Object({}), "vpcf2")

    report = document.capability_report()

    assert report.maturity is Maturity.EXPERIMENTAL
    assert isinstance(report.resource_capabilities, ResourceCapabilities)
    assert all(isinstance(diagnostic, Diagnostic) for diagnostic in report.diagnostics)
