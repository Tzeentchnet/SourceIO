from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ..blocks.kv3_block import KVBlock
from ..compiled_resource import CompiledResource
from ..exceptions import MissingBlock
from ..interfaces import (
    Maturity,
    ResourceCapabilities,
    ResourceKind,
    ResourceResolver,
)
from ..particles import (
    CapabilityReport,
    PARTICLE_RESOURCE_CAPABILITIES,
    ParticleCapabilityRegistry,
    ParticleChildResolution,
    ParticleDocument,
    ParticleUpgradeResult,
)
from ..particles.kv3 import FrozenKV3Object
from ..provenance import ResourceProvenance

if TYPE_CHECKING:
    from ..resource_registry import ResourceRegistration, ResourceRegistry


class CompiledParticleResource(CompiledResource):
    __slots__ = ("_particle_document_cache",)

    resource_kind = ResourceKind.PARTICLE_SYSTEM
    declared_capabilities = PARTICLE_RESOURCE_CAPABILITIES

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._particle_document_cache = self.create_particle_document()

    def get_data_block_type(self):
        return KVBlock

    @property
    def data_block(self) -> KVBlock:
        block = self.get_block(KVBlock, block_name="DATA")
        if block is None:
            raise MissingBlock('Required block "DATA" is missing')
        return block

    def create_particle_document(
        self,
        *,
        provenance: ResourceProvenance | None = None,
        resolver: ResourceResolver | None = None,
    ) -> ParticleDocument:
        try:
            cached = self._particle_document_cache
        except AttributeError:
            data = self.data_block
            source_format = getattr(data, "_format", None)
        else:
            data = cached.clone_original_kv3()
            source_format = cached.upgrade_result.source_format
        return ParticleDocument(
            data,
            source_format,
            provenance=provenance,
            resolver=resolver,
        )

    @property
    def particle_document(self) -> ParticleDocument:
        try:
            return self._particle_document_cache
        except AttributeError:
            document = self.create_particle_document()
            self._particle_document_cache = document
            return document

    @property
    def original_kv3(self) -> FrozenKV3Object:
        return self.particle_document.original_kv3

    @property
    def upgraded_kv3(self) -> Mapping[str, object]:
        return self.particle_document.upgraded_kv3

    @property
    def upgrade_result(self) -> ParticleUpgradeResult:
        return self.particle_document.upgrade_result

    def capability_report(
        self,
        *,
        registry: ParticleCapabilityRegistry | None = None,
        resource_capabilities: ResourceCapabilities | None = None,
        maturity: Maturity = Maturity.EXPERIMENTAL,
    ) -> CapabilityReport:
        return self.particle_document.capability_report(
            registry=registry,
            resource_capabilities=resource_capabilities or self.capabilities,
            maturity=maturity,
        )

    def _component_array(self, name: str) -> tuple[Mapping[str, object], ...]:
        entries = self.upgraded_kv3.get(name, ())
        if not isinstance(entries, (list, tuple)):
            return ()
        return tuple(entry for entry in entries if isinstance(entry, Mapping))

    def get_emitters(self) -> tuple[Mapping[str, object], ...]:
        return self._component_array("m_Emitters")

    def get_initializers(self) -> tuple[Mapping[str, object], ...]:
        return self._component_array("m_Initializers")

    def get_operators(self) -> tuple[Mapping[str, object], ...]:
        return self._component_array("m_Operators")

    def get_force_generators(self) -> tuple[Mapping[str, object], ...]:
        return self._component_array("m_ForceGenerators")

    def get_constraints(self) -> tuple[Mapping[str, object], ...]:
        return self._component_array("m_Constraints")

    def get_pre_emission_operators(self) -> tuple[Mapping[str, object], ...]:
        return self._component_array("m_PreEmissionOperators")

    def get_renderers(self) -> tuple[Mapping[str, object], ...]:
        return self._component_array("m_Renderers")

    def resolve_children(
        self,
        resolver: ResourceResolver,
    ) -> tuple[ParticleChildResolution, ...]:
        return self.particle_document.resolve_children(
            resolver,
            source_path=str(self.path),
        )


def register_particle_resource(registry: "ResourceRegistry") -> "ResourceRegistration":
    return registry.register(
        ResourceKind.PARTICLE_SYSTEM,
        CompiledParticleResource,
        extensions=(".vpcf_c",),
        capabilities=PARTICLE_RESOURCE_CAPABILITIES,
        priority=10,
    )
