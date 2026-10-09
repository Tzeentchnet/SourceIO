from __future__ import annotations

import pytest

from SourceIO.library.source2.interfaces import (
    Diagnostic,
    DiagnosticSeverity,
    Maturity,
    ResourceCapabilities,
    ResourceKind,
    ResourceOperation,
    ResourceRef,
    ResourceResolver,
)


def test_capability_maturity_is_per_operation():
    capabilities = ResourceCapabilities(
        read=Maturity.STABLE,
        extract=Maturity.PARTIAL,
        render=Maturity.EXPERIMENTAL,
    )

    assert capabilities.supports(ResourceOperation.READ, Maturity.STABLE)
    assert capabilities.supports("extract", Maturity.PARTIAL)
    assert not capabilities.supports("write")
    assert capabilities.maturity_for("render") is Maturity.EXPERIMENTAL
    with pytest.raises(ValueError):
        capabilities.supports("execute")


def test_diagnostic_and_resource_reference_validation():
    diagnostic = Diagnostic(
        "source2.test",
        "A focused diagnostic",
        DiagnosticSeverity.INFO,
        offset=4,
        block_name="DATA",
    )
    reference = ResourceRef(
        "materials/test.vmat_c",
        kind=ResourceKind.MATERIAL,
    )

    assert diagnostic.severity is DiagnosticSeverity.INFO
    assert reference.path == "materials/test.vmat_c"
    with pytest.raises(ValueError):
        Diagnostic("", "missing code")
    with pytest.raises(ValueError):
        ResourceRef()


def test_resource_resolver_protocol_is_runtime_checkable():
    class Resolver:
        def resolve(self, reference: ResourceRef):
            return reference.path.encode("ascii")

    resolver = Resolver()

    assert isinstance(resolver, ResourceResolver)
    assert resolver.resolve(ResourceRef("test.vmdl_c")) == b"test.vmdl_c"
