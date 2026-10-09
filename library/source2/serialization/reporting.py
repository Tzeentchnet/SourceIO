from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..interfaces import (
    Diagnostic,
    DiagnosticSeverity,
    Maturity,
    ResourceCapabilities,
)


class CapabilityDiagnostic:
    @staticmethod
    def info(code: str, message: str, **details: object) -> Diagnostic:
        return Diagnostic(code, message, DiagnosticSeverity.INFO, details=details)

    @staticmethod
    def warning(code: str, message: str, **details: object) -> Diagnostic:
        return Diagnostic(code, message, DiagnosticSeverity.WARNING, details=details)

    @staticmethod
    def error(code: str, message: str, **details: object) -> Diagnostic:
        return Diagnostic(code, message, DiagnosticSeverity.ERROR, details=details)


@dataclass(frozen=True, slots=True)
class CapabilityReport:
    feature: str
    operation: str
    supported: bool
    maturity: Maturity = Maturity.EXPERIMENTAL
    resource_capabilities: ResourceCapabilities | None = None
    components: tuple[object, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    losses: tuple[object, ...] = ()
    assumptions: tuple[str, ...] = ()

    @property
    def diagnostic_codes(self) -> tuple[str, ...]:
        return tuple(
            code
            for diagnostic in self.diagnostics
            if isinstance((code := getattr(diagnostic, "code", None)), str)
        )

    @property
    def unsupported_components(self) -> tuple[object, ...]:
        return tuple(component for component in self.components if not getattr(component, "supported", False))

    def with_diagnostics(self, diagnostics: Iterable[Diagnostic]) -> "CapabilityReport":
        return CapabilityReport(
            feature=self.feature,
            operation=self.operation,
            supported=self.supported,
            maturity=self.maturity,
            resource_capabilities=self.resource_capabilities,
            components=self.components,
            diagnostics=(*self.diagnostics, *tuple(diagnostics)),
            losses=self.losses,
            assumptions=self.assumptions,
        )
