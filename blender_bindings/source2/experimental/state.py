from __future__ import annotations

from dataclasses import dataclass

from ....library.source2.interfaces import Maturity
from ....library.source2.serialization.reporting import CapabilityReport


class ExperimentalFeatureError(RuntimeError):
    pass


class ExperimentalFeatureDisabled(ExperimentalFeatureError):
    pass


class ExperimentalFeatureUnavailable(ExperimentalFeatureError):
    pass


@dataclass(frozen=True, slots=True)
class ExperimentalFeatureState:
    feature: str
    label: str
    enabled: bool
    available: bool
    capability_report: CapabilityReport
    maturity: Maturity = Maturity.EXPERIMENTAL

    @property
    def active(self) -> bool:
        return self.enabled and self.available and self.capability_report.supported

    @property
    def diagnostics(self) -> tuple[object, ...]:
        return self.capability_report.diagnostics

    def require_active(self) -> None:
        if not self.enabled:
            raise ExperimentalFeatureDisabled(
                f"Experimental feature {self.feature!r} is disabled"
            )
        if not self.available or not self.capability_report.supported:
            raise ExperimentalFeatureUnavailable(
                f"Experimental feature {self.feature!r} is not supported by the current capability report"
            )

    @classmethod
    def from_report(
        cls,
        report: CapabilityReport,
        *,
        label: str,
        enabled: bool = False,
        available: bool | None = None,
        maturity: Maturity = Maturity.EXPERIMENTAL,
    ) -> "ExperimentalFeatureState":
        return cls(
            feature=report.feature,
            label=label,
            enabled=enabled,
            available=report.supported if available is None else available,
            capability_report=report,
            maturity=maturity,
        )
