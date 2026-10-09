from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from ..interfaces import Maturity, ResourceCapabilities
from ..serialization.reporting import CapabilityDiagnostic, CapabilityReport
from .upgrade import ParticleUpgradeResult


PARTICLE_RESOURCE_CAPABILITIES = ResourceCapabilities(
    read=Maturity.EXPERIMENTAL,
    extract=Maturity.EXPERIMENTAL,
)


class ParticleComponentKind(str, Enum):
    EMITTER = "emitter"
    INITIALIZER = "initializer"
    OPERATOR = "operator"
    FORCE = "force"
    CONSTRAINT = "constraint"
    PRE_EMISSION = "pre_emission"
    RENDERER = "renderer"


class ParticleCapabilityLevel(str, Enum):
    PRESERVED = "preserved"
    INSPECTABLE = "inspectable"
    SIMULATABLE = "simulatable"
    RENDERABLE = "renderable"


@dataclass(frozen=True, slots=True)
class ParticleComponentCapability:
    kind: ParticleComponentKind
    index: int
    class_name: str
    levels: frozenset[ParticleCapabilityLevel]
    supported: bool
    reason: str | None = None

    @property
    def preserved(self) -> bool:
        return ParticleCapabilityLevel.PRESERVED in self.levels


class ParticleCapabilityRegistry:
    def __init__(self):
        self._components: dict[
            tuple[ParticleComponentKind, str],
            frozenset[ParticleCapabilityLevel],
        ] = {}
        self._resource_simulation_supported = False
        self._resource_evidence: tuple[str, ...] = ()

    @property
    def resource_simulation_supported(self) -> bool:
        return self._resource_simulation_supported

    @property
    def resource_evidence(self) -> tuple[str, ...]:
        return self._resource_evidence

    def declare_resource_simulation(self, *, evidence: tuple[str, ...]) -> None:
        if not evidence:
            raise ValueError("Particle resource simulation support requires evidence")
        self._resource_simulation_supported = True
        self._resource_evidence = tuple(evidence)

    def declare_component(
        self,
        kind: ParticleComponentKind,
        class_name: str,
        *levels: ParticleCapabilityLevel,
    ) -> None:
        if not class_name:
            raise ValueError("Particle component class name cannot be empty")
        declared = frozenset(levels)
        if ParticleCapabilityLevel.SIMULATABLE not in declared and ParticleCapabilityLevel.RENDERABLE not in declared:
            raise ValueError("A support declaration must include simulation or rendering capability")
        self._components[(kind, class_name)] = frozenset({
            ParticleCapabilityLevel.PRESERVED,
            ParticleCapabilityLevel.INSPECTABLE,
            *declared,
        })

    def levels_for(
        self,
        kind: ParticleComponentKind,
        class_name: str,
    ) -> frozenset[ParticleCapabilityLevel]:
        return self._components.get(
            (kind, class_name),
            frozenset({
                ParticleCapabilityLevel.PRESERVED,
                ParticleCapabilityLevel.INSPECTABLE,
            }),
        )


_COMPONENT_ARRAYS = (
    (ParticleComponentKind.EMITTER, "m_Emitters"),
    (ParticleComponentKind.INITIALIZER, "m_Initializers"),
    (ParticleComponentKind.OPERATOR, "m_Operators"),
    (ParticleComponentKind.FORCE, "m_ForceGenerators"),
    (ParticleComponentKind.CONSTRAINT, "m_Constraints"),
    (ParticleComponentKind.PRE_EMISSION, "m_PreEmissionOperators"),
    (ParticleComponentKind.RENDERER, "m_Renderers"),
)


def report_particle_capabilities(
    root: Mapping[str, object],
    *,
    registry: ParticleCapabilityRegistry | None = None,
    upgrade: ParticleUpgradeResult | None = None,
    resource_capabilities: ResourceCapabilities | None = PARTICLE_RESOURCE_CAPABILITIES,
    maturity: Maturity = Maturity.EXPERIMENTAL,
) -> CapabilityReport:
    registry = registry or ParticleCapabilityRegistry()
    components: list[ParticleComponentCapability] = []
    diagnostics = list(upgrade.diagnostics if upgrade is not None else ())

    for kind, member_name in _COMPONENT_ARRAYS:
        entries = root.get(member_name, ())
        if entries is None:
            continue
        if not isinstance(entries, (list, tuple)):
            diagnostics.append(CapabilityDiagnostic.error(
                "particle.component.invalid_array",
                f"{member_name} is not an array.",
                component_kind=kind.value,
                actual_type=type(entries).__name__,
            ))
            components.append(ParticleComponentCapability(
                kind,
                -1,
                "<invalid-array>",
                frozenset({ParticleCapabilityLevel.PRESERVED}),
                False,
                f"{member_name} is not an array",
            ))
            continue

        for index, entry in enumerate(entries):
            if isinstance(entry, Mapping):
                raw_class_name = entry.get("_class")
                class_name = str(raw_class_name) if raw_class_name else "<missing-class>"
            else:
                class_name = "<invalid-entry>"

            levels = registry.levels_for(kind, class_name)
            required = (
                ParticleCapabilityLevel.RENDERABLE
                if kind is ParticleComponentKind.RENDERER
                else ParticleCapabilityLevel.SIMULATABLE
            )
            supported = required in levels
            reason = None if supported else f"No verified {required.value} adapter is registered"
            component = ParticleComponentCapability(kind, index, class_name, levels, supported, reason)
            components.append(component)
            if not supported:
                diagnostics.append(CapabilityDiagnostic.warning(
                    "particle.component.unsupported",
                    "Particle component is preserved as data but is not accepted for execution.",
                    component_kind=kind.value,
                    index=index,
                    class_name=class_name,
                    required_capability=required.value,
                ))

    if not registry.resource_simulation_supported:
        diagnostics.append(CapabilityDiagnostic.warning(
            "particle.simulation.unavailable",
            "No verified whole-resource particle simulation is registered.",
        ))

    upgrade_complete = upgrade is None or upgrade.complete
    supported = (
        upgrade_complete
        and registry.resource_simulation_supported
        and all(component.supported for component in components)
    )
    assumptions = () if upgrade is None or upgrade.source_format is not None else (
        "No particle source format was assumed.",
    )
    return CapabilityReport(
        feature="source2_particles",
        operation="simulate_and_render",
        supported=supported,
        maturity=maturity,
        resource_capabilities=resource_capabilities,
        components=tuple(components),
        diagnostics=tuple(diagnostics),
        assumptions=assumptions,
    )
