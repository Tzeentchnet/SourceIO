from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ..interfaces import Diagnostic, Maturity, ResourceCapabilities, ResourceResolver
from ..provenance import ResourceProvenance
from ..serialization.reporting import CapabilityDiagnostic, CapabilityReport


class ClothLossSeverity(str, Enum):
    INFORMATIONAL = "informational"
    DEGRADING = "degrading"
    BLOCKING = "blocking"


@dataclass(frozen=True, slots=True)
class ClothReconstructionLoss:
    code: str
    message: str
    severity: ClothLossSeverity
    source_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClothBackendResult:
    geometry: object | None
    diagnostics: tuple[Diagnostic, ...] = ()
    losses: tuple[ClothReconstructionLoss, ...] = ()


ClothBackend = Callable[
    [object, ResourceProvenance | None, ResourceResolver | None],
    ClothBackendResult,
]


@dataclass(frozen=True, slots=True)
class ClothBackendCapability:
    name: str
    reconstruct: ClothBackend
    evidence: tuple[str, ...]
    lossless: bool

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Cloth reconstruction backend name cannot be empty")
        if not self.evidence:
            raise ValueError("Cloth reconstruction backends require verification evidence")


@dataclass(frozen=True, slots=True)
class ClothReconstructionResult:
    geometry: object | None
    report: CapabilityReport
    attempted: bool
    backend: str | None = None

    @property
    def supported(self) -> bool:
        return self.report.supported

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return self.report.diagnostics

    @property
    def losses(self) -> tuple[object, ...]:
        return self.report.losses


class ClothReconstructor:
    def __init__(
        self,
        *,
        enabled: bool = False,
        backend: ClothBackendCapability | None = None,
        maturity: Maturity = Maturity.EXPERIMENTAL,
    ):
        self.enabled = enabled
        self.backend = backend
        self.maturity = maturity

    def reconstruct(
        self,
        source: object,
        *,
        enabled: bool | None = None,
        provenance: ResourceProvenance | None = None,
        resolver: ResourceResolver | None = None,
        resource_capabilities: ResourceCapabilities | None = None,
    ) -> ClothReconstructionResult:
        active = self.enabled if enabled is None else enabled
        if not active:
            diagnostic = CapabilityDiagnostic.info(
                "cloth.reconstruction.disabled",
                "Cloth reconstruction is experimental and disabled by default.",
            )
            return self._result(
                geometry=None,
                supported=False,
                attempted=False,
                diagnostics=(diagnostic,),
                losses=(),
                resource_capabilities=resource_capabilities,
            )

        if self.backend is None:
            diagnostic = CapabilityDiagnostic.error(
                "cloth.reconstruction.no_verified_backend",
                "No verified cloth reconstruction backend is registered; no fallback geometry was created.",
            )
            loss = ClothReconstructionLoss(
                "cloth.geometry.not_reconstructed",
                "Compiled cloth was retained as source data, but no editable geometry was reconstructed.",
                ClothLossSeverity.BLOCKING,
            )
            return self._result(
                geometry=None,
                supported=False,
                attempted=False,
                diagnostics=(diagnostic,),
                losses=(loss,),
                resource_capabilities=resource_capabilities,
            )

        backend_result = self.backend.reconstruct(source, provenance, resolver)
        diagnostics = list(backend_result.diagnostics)
        losses = list(backend_result.losses)
        geometry = backend_result.geometry

        if geometry is None:
            diagnostics.append(CapabilityDiagnostic.error(
                "cloth.reconstruction.backend_returned_no_geometry",
                "The verified cloth backend returned no geometry.",
                backend=self.backend.name,
            ))
            if not losses:
                losses.append(ClothReconstructionLoss(
                    "cloth.geometry.missing",
                    "The backend did not reconstruct cloth geometry.",
                    ClothLossSeverity.BLOCKING,
                ))

        if not self.backend.lossless and not losses:
            diagnostics.append(CapabilityDiagnostic.error(
                "cloth.reconstruction.missing_loss_accounting",
                "A lossy cloth backend did not enumerate its losses; its geometry was rejected.",
                backend=self.backend.name,
            ))
            losses.append(ClothReconstructionLoss(
                "cloth.losses.unreported",
                "The backend is declared lossy but did not identify the lost information.",
                ClothLossSeverity.BLOCKING,
            ))
            geometry = None

        blocking_loss = any(
            loss.severity is ClothLossSeverity.BLOCKING
            for loss in losses
        )
        supported = geometry is not None and not blocking_loss
        return self._result(
            geometry=geometry if supported else None,
            supported=supported,
            attempted=True,
            diagnostics=tuple(diagnostics),
            losses=tuple(losses),
            resource_capabilities=resource_capabilities,
            backend=self.backend.name,
        )

    def _result(
        self,
        *,
        geometry: object | None,
        supported: bool,
        attempted: bool,
        diagnostics: tuple[Diagnostic, ...],
        losses: tuple[ClothReconstructionLoss, ...],
        resource_capabilities: ResourceCapabilities | None,
        backend: str | None = None,
    ) -> ClothReconstructionResult:
        report = CapabilityReport(
            feature="source2_cloth_reconstruction",
            operation="reconstruct_editable_geometry",
            supported=supported,
            maturity=self.maturity,
            resource_capabilities=resource_capabilities,
            diagnostics=diagnostics,
            losses=losses,
        )
        return ClothReconstructionResult(geometry, report, attempted, backend)


def reconstruct_cloth(
    source: object,
    *,
    enabled: bool = False,
    backend: ClothBackendCapability | None = None,
    provenance: ResourceProvenance | None = None,
    resolver: ResourceResolver | None = None,
    resource_capabilities: ResourceCapabilities | None = None,
    maturity: Maturity = Maturity.EXPERIMENTAL,
) -> ClothReconstructionResult:
    return ClothReconstructor(
        enabled=enabled,
        backend=backend,
        maturity=maturity,
    ).reconstruct(
        source,
        provenance=provenance,
        resolver=resolver,
        resource_capabilities=resource_capabilities,
    )
