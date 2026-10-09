from __future__ import annotations

import re
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass

from ..keyvalues3.enums import Specifier
from ..keyvalues3.types import Array, String
from ..interfaces import Diagnostic
from ..serialization.reporting import CapabilityDiagnostic, DiagnosticSeverity
from .kv3 import deep_clone_kv3


@dataclass(frozen=True, slots=True)
class UpgradeStepApplication:
    success: bool
    diagnostics: tuple[Diagnostic, ...] = ()


UpgradeTransform = Callable[[MutableMapping[str, object]], UpgradeStepApplication]


@dataclass(frozen=True, slots=True)
class ParticleUpgradeStep:
    source_format: str
    target_format: str
    transform: UpgradeTransform
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParticleUpgradeResult:
    data: MutableMapping[str, object]
    source_format: str | None
    resulting_format: str | None
    target_format: str
    applied_steps: tuple[ParticleUpgradeStep, ...]
    diagnostics: tuple[Diagnostic, ...]
    complete: bool

    @property
    def changed(self) -> bool:
        return bool(self.applied_steps)


def normalize_particle_format(format_: object | None) -> str | None:
    if format_ is None:
        return None
    name = getattr(format_, "name", None)
    if isinstance(name, str):
        return name.lower()
    value = str(format_).lower()
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    return value


class ParticleUpgrader:
    def __init__(self, steps: Sequence[ParticleUpgradeStep]):
        self._steps = tuple(steps)
        self._by_source: dict[str, ParticleUpgradeStep] = {}
        previous_target: str | None = None
        for step in self._steps:
            source_format = normalize_particle_format(step.source_format)
            target_format = normalize_particle_format(step.target_format)
            if source_format is None or target_format is None:
                raise ValueError("Particle upgrade formats cannot be empty")
            if previous_target is not None and source_format != previous_target:
                raise ValueError(
                    f"Particle upgrade chain breaks between {previous_target} and {source_format}"
                )
            if source_format in self._by_source:
                raise ValueError(f"Duplicate particle upgrade source format: {source_format}")
            self._by_source[source_format] = step
            previous_target = target_format

    @property
    def steps(self) -> tuple[ParticleUpgradeStep, ...]:
        return self._steps

    @property
    def latest_verified_format(self) -> str:
        if not self._steps:
            raise ValueError("The particle upgrader has no verified steps")
        return normalize_particle_format(self._steps[-1].target_format) or ""

    def upgrade(
        self,
        root: Mapping[str, object],
        source_format: object | None,
        target_format: object | None = None,
    ) -> ParticleUpgradeResult:
        clone = deep_clone_kv3(root)
        if not isinstance(clone, MutableMapping):
            raise TypeError("Particle KV3 root must be an object")

        normalized_source = normalize_particle_format(source_format)
        normalized_target = normalize_particle_format(target_format) or self.latest_verified_format
        diagnostics: list[Diagnostic] = []
        applied: list[ParticleUpgradeStep] = []

        if normalized_source is None:
            diagnostics.append(CapabilityDiagnostic.error(
                "particle.upgrade.missing_source_format",
                "Particle format is missing; no oldest-version assumption was applied.",
                target_format=normalized_target,
            ))
            return ParticleUpgradeResult(
                clone, None, None, normalized_target, (), tuple(diagnostics), False
            )

        if normalized_source == normalized_target:
            return ParticleUpgradeResult(
                clone, normalized_source, normalized_source, normalized_target, (), (), True
            )

        current_format = normalized_source
        visited: set[str] = set()
        while current_format != normalized_target:
            if current_format in visited:
                diagnostics.append(CapabilityDiagnostic.error(
                    "particle.upgrade.cycle",
                    "The registered particle upgrade chain contains a cycle.",
                    source_format=current_format,
                    target_format=normalized_target,
                ))
                break
            visited.add(current_format)

            step = self._by_source.get(current_format)
            if step is None:
                diagnostics.append(self._missing_step_diagnostic(current_format, normalized_target))
                break

            application = step.transform(clone)
            diagnostics.extend(application.diagnostics)
            if not application.success:
                diagnostics.append(CapabilityDiagnostic.error(
                    "particle.upgrade.step_refused",
                    "A verified particle conversion refused malformed input.",
                    source_format=step.source_format,
                    target_format=step.target_format,
                ))
                break

            applied.append(step)
            current_format = normalize_particle_format(step.target_format) or current_format

        complete = (
            current_format == normalized_target
            and not any(diagnostic.severity is DiagnosticSeverity.ERROR for diagnostic in diagnostics)
        )
        return ParticleUpgradeResult(
            clone,
            normalized_source,
            current_format,
            normalized_target,
            tuple(applied),
            tuple(diagnostics),
            complete,
        )

    @staticmethod
    def _missing_step_diagnostic(source_format: str, target_format: str) -> Diagnostic:
        source_version = _vpcf_version(source_format)
        target_version = _vpcf_version(target_format)
        if source_version is not None and target_version is not None and source_version > target_version:
            return CapabilityDiagnostic.warning(
                "particle.upgrade.source_newer_than_verified_target",
                "The source particle format is newer than the locally verified conversion target; it was preserved.",
                source_format=source_format,
                target_format=target_format,
            )
        return CapabilityDiagnostic.error(
            "particle.upgrade.unverified_format",
            "No verified conversion step is registered for this particle format.",
            source_format=source_format,
            target_format=target_format,
        )


def _vpcf_version(format_: str) -> int | None:
    match = re.fullmatch(r"vpcf(\d+)", format_)
    return int(match.group(1)) if match else None


def _walk_objects(value: object, visitor: Callable[[MutableMapping[str, object]], None]) -> None:
    if isinstance(value, MutableMapping):
        visitor(value)
        for child in tuple(value.values()):
            _walk_objects(child, visitor)
    elif isinstance(value, (list, tuple)):
        for child in tuple(value):
            _walk_objects(child, visitor)


def _resource_string(value: object, path: str) -> object:
    if not isinstance(value, str):
        try:
            value.specifier = Specifier.RESOURCE
        except AttributeError:
            pass
        return value
    converted = String(path)
    converted.specifier = Specifier.RESOURCE
    return converted


def _convert_snapshot_member(node: MutableMapping[str, object], old_name: str) -> None:
    if old_name not in node:
        return
    value = node.pop(old_name)
    if not isinstance(value, str) or not value:
        node["m_hSnapshot"] = _resource_string(value, "")
        return

    path = value.replace("\\", "/")
    slash = path.rfind("/")
    extension = path.rfind(".")
    if extension > slash:
        path = path[:extension]
    path += ".vsnap"
    if not path.startswith("particles/"):
        path = "particles/" + path
    node["m_hSnapshot"] = _resource_string(value, path)


def _generic_to_vpcf1(root: MutableMapping[str, object]) -> UpgradeStepApplication:
    def convert(node: MutableMapping[str, object]) -> None:
        class_name = str(node.get("_class", ""))
        if class_name == "CParticleSystemDefinition":
            _convert_snapshot_member(node, "m_pszSnapshotName")
        elif class_name == "C_OP_InitSetSnapshotOnCP":
            _convert_snapshot_member(node, "m_snapshotName")

    _walk_objects(root, convert)
    return UpgradeStepApplication(True)


_PRE_EMISSION_CLASSES = frozenset({
    "C_OP_RemapSpeedtoCP",
    "C_OP_RemapModelVolumetoCP",
    "C_OP_RemapBoundingVolumetoCP",
    "C_OP_RemapAverageScalarValuetoCP",
    "C_OP_RampCPLinearRandom",
    "C_OP_SetParentControlPointsToChildCP",
    "C_OP_SetControlPointPositions",
    "C_OP_SetSingleControlPointPosition",
    "C_OP_SetRandomControlPointPosition",
    "C_OP_SetControlPointOrientation",
    "C_OP_SetControlPointFromObjectScale",
    "C_OP_DistanceBetweenCPsToCP",
    "C_OP_SetControlPointToPlayer",
    "C_OP_SetControlPointToHand",
    "C_OP_SetControlPointToHMD",
    "C_OP_SetControlPointPositionToTimeOfDayValue",
    "C_OP_SetControlPointToCenter",
    "C_OP_StopAfterCPDuration",
    "C_OP_SetControlPointRotation",
    "C_OP_RemapCPtoCP",
    "C_OP_HSVShiftToCP",
    "C_OP_SetControlPointToImpactPoint",
    "C_OP_SetCPOrientationToPointAtCP",
    "C_OP_EnableChildrenFromParentParticleCount",
    "C_OP_DriveCPFromGlobalSoundFloat",
    "C_OP_SetControlPointFieldToWater",
})


def _vpcf1_to_vpcf2(root: MutableMapping[str, object]) -> UpgradeStepApplication:
    pre_emission = root.get("m_PreEmissionOperators")
    if pre_emission is None:
        pre_emission = Array([])
        root["m_PreEmissionOperators"] = pre_emission
    elif not isinstance(pre_emission, list):
        return UpgradeStepApplication(False, (
            CapabilityDiagnostic.error(
                "particle.upgrade.invalid_pre_emission_array",
                "m_PreEmissionOperators is not an array.",
                actual_type=type(pre_emission).__name__,
            ),
        ))

    operators = root.get("m_Operators")
    if operators is None:
        return UpgradeStepApplication(True)
    if not isinstance(operators, list):
        return UpgradeStepApplication(False, (
            CapabilityDiagnostic.error(
                "particle.upgrade.invalid_operator_array",
                "m_Operators is not an array.",
                actual_type=type(operators).__name__,
            ),
        ))

    moved: list[object] = []
    kept: list[object] = []
    for operator in operators:
        class_name = str(operator.get("_class", "")) if isinstance(operator, Mapping) else ""
        (moved if class_name in _PRE_EMISSION_CLASSES else kept).append(operator)

    if moved:
        root["m_Operators"] = Array(kept)
        root["m_PreEmissionOperators"] = Array([*moved, *pre_emission])
    return UpgradeStepApplication(True)


VERIFIED_PARTICLE_UPGRADER = ParticleUpgrader((
    ParticleUpgradeStep(
        "generic",
        "vpcf1",
        _generic_to_vpcf1,
        (
            "ValveResourceFormat/Particles/Upgrade/GenericToVpcf1.cs",
            "SourceIO tests/experimental/test_particle_upgrades.py",
        ),
    ),
    ParticleUpgradeStep(
        "vpcf1",
        "vpcf2",
        _vpcf1_to_vpcf2,
        (
            "ValveResourceFormat/Particles/Upgrade/Vpcf1ToVpcf2.cs",
            "SourceIO tests/experimental/test_particle_upgrades.py",
        ),
    ),
))
