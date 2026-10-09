from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..interfaces import (
    Maturity,
    ResourceCapabilities,
    ResourceKind,
    ResourceRef,
    ResourceResolver,
)
from ..provenance import ResourceProvenance
from ..serialization.reporting import CapabilityReport
from .capabilities import (
    PARTICLE_RESOURCE_CAPABILITIES,
    ParticleCapabilityRegistry,
    report_particle_capabilities,
)
from .kv3 import FrozenKV3Object, deep_clone_kv3, freeze_kv3, thaw_kv3
from .upgrade import ParticleUpgradeResult, ParticleUpgrader, VERIFIED_PARTICLE_UPGRADER


def _compiled_particle_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.casefold().endswith(".vpcf") and not normalized.casefold().endswith(".vpcf_c"):
        return normalized + "_c"
    return normalized


@dataclass(frozen=True, slots=True)
class ParticleChildResolution:
    reference: ResourceRef
    value: object | None

    @property
    def resolved(self) -> bool:
        return self.value is not None


class ParticleDocument:
    __slots__ = (
        "_original",
        "_upgrade",
        "_provenance",
        "_resolver",
    )

    def __init__(
        self,
        source: Mapping[str, object],
        source_format: object | None,
        *,
        upgrader: ParticleUpgrader = VERIFIED_PARTICLE_UPGRADER,
        provenance: ResourceProvenance | None = None,
        resolver: ResourceResolver | None = None,
    ):
        original = freeze_kv3(source)
        if not isinstance(original, FrozenKV3Object):
            raise TypeError("Particle KV3 root must be an object")
        self._original = original
        self._upgrade = upgrader.upgrade(source, source_format)
        self._provenance = provenance
        self._resolver = resolver

    @property
    def original_kv3(self) -> FrozenKV3Object:
        return self._original

    @property
    def upgraded_kv3(self) -> Mapping[str, object]:
        return self._upgrade.data

    @property
    def upgrade_result(self) -> ParticleUpgradeResult:
        return self._upgrade

    @property
    def provenance(self) -> ResourceProvenance | None:
        return self._provenance

    @property
    def resolver(self) -> ResourceResolver | None:
        return self._resolver

    def clone_upgraded_kv3(self) -> Mapping[str, object]:
        clone = deep_clone_kv3(self._upgrade.data)
        if not isinstance(clone, Mapping):
            raise TypeError("Particle KV3 root must be an object")
        return clone

    def clone_original_kv3(self) -> Mapping[str, object]:
        clone = thaw_kv3(self._original)
        if not isinstance(clone, Mapping):
            raise TypeError("Particle KV3 root must be an object")
        return clone

    def child_references(self, *, source_path: str | None = None) -> tuple[ResourceRef, ...]:
        children = self._upgrade.data.get("m_Children", ())
        if children is None:
            return ()
        if not isinstance(children, (list, tuple)):
            raise TypeError("Particle m_Children must be an array")

        if source_path is None and self._provenance is not None:
            source_path = self._provenance.root_resource
        references: list[ResourceRef] = []
        for index, child in enumerate(children):
            if not isinstance(child, Mapping):
                raise TypeError(f"Particle child at index {index} must be an object")
            path = child.get("m_ChildRef")
            if not isinstance(path, str) or not path:
                raise ValueError(f"Particle child at index {index} has no m_ChildRef")
            references.append(ResourceRef(
                path=_compiled_particle_path(path),
                kind=ResourceKind.PARTICLE_SYSTEM,
                source_path=source_path,
            ))
        return tuple(references)

    def resolve_children(
        self,
        resolver: ResourceResolver | None = None,
        *,
        source_path: str | None = None,
    ) -> tuple[ParticleChildResolution, ...]:
        active_resolver = resolver or self._resolver
        if active_resolver is None:
            raise ValueError("A ResourceResolver is required to resolve particle children")
        return tuple(
            ParticleChildResolution(reference, active_resolver.resolve(reference))
            for reference in self.child_references(source_path=source_path)
        )

    def capability_report(
        self,
        *,
        registry: ParticleCapabilityRegistry | None = None,
        resource_capabilities: ResourceCapabilities | None = None,
        maturity: Maturity = Maturity.EXPERIMENTAL,
    ) -> CapabilityReport:
        return report_particle_capabilities(
            self._upgrade.data,
            registry=registry,
            upgrade=self._upgrade,
            resource_capabilities=resource_capabilities or PARTICLE_RESOURCE_CAPABILITIES,
            maturity=maturity,
        )
