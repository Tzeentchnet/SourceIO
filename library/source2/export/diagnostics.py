from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

from ..provenance import to_json_safe


class DiagnosticSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class LossKind(str, Enum):
    INFERRED = "inferred"
    MISSING = "missing"
    DEFERRED = "deferred"
    IRRECOVERABLE = "irrecoverable"
    SAFETY = "safety"


@dataclass(slots=True)
class ExportDiagnostic:
    code: str
    message: str
    severity: DiagnosticSeverity | str = DiagnosticSeverity.WARNING
    loss_kind: LossKind | str | None = None
    path: tuple[str | int, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.severity, str) and not isinstance(self.severity, DiagnosticSeverity):
            self.severity = DiagnosticSeverity(self.severity)
        if isinstance(self.loss_kind, str) and not isinstance(self.loss_kind, LossKind):
            self.loss_kind = LossKind(self.loss_kind)
        self.path = tuple(self.path)
        self.details = to_json_safe(self.details, _path="$.details")

    @property
    def severity_name(self) -> str:
        return self.severity.value if isinstance(self.severity, DiagnosticSeverity) else str(self.severity)

    @property
    def loss_kind_name(self) -> str | None:
        if self.loss_kind is None:
            return None
        return self.loss_kind.value if isinstance(self.loss_kind, LossKind) else str(self.loss_kind)

    def sort_key(self) -> tuple:
        return (
            self.severity_name,
            self.loss_kind_name or "",
            self.code,
            tuple(map(str, self.path)),
            self.message,
            json.dumps(self.details, sort_keys=True, separators=(",", ":")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity_name,
            "loss_kind": self.loss_kind_name,
            "path": list(self.path),
            "details": self.details,
        }


class ExportBlockedError(RuntimeError):
    pass


@dataclass(slots=True)
class LossReport:
    diagnostics: list[ExportDiagnostic] = field(default_factory=list)
    schema_version: int = 1

    def add(self, diagnostic: ExportDiagnostic) -> ExportDiagnostic:
        if not isinstance(diagnostic, ExportDiagnostic):
            raise TypeError("LossReport.add expects an ExportDiagnostic")
        self.diagnostics.append(diagnostic)
        return diagnostic

    def record(
            self,
            code: str,
            message: str,
            *,
            severity: DiagnosticSeverity | str = DiagnosticSeverity.WARNING,
            loss_kind: LossKind | str | None = None,
            path: Iterable[str | int] = (),
            details: dict[str, Any] | None = None,
    ) -> ExportDiagnostic:
        return self.add(ExportDiagnostic(
            code=code,
            message=message,
            severity=severity,
            loss_kind=loss_kind,
            path=tuple(path),
            details=dict(details or {}),
        ))

    def inferred(self, code: str, message: str, **kwargs) -> ExportDiagnostic:
        return self.record(code, message, loss_kind=LossKind.INFERRED, **kwargs)

    def missing(self, code: str, message: str, **kwargs) -> ExportDiagnostic:
        return self.record(code, message, loss_kind=LossKind.MISSING, **kwargs)

    def deferred(self, code: str, message: str, **kwargs) -> ExportDiagnostic:
        return self.record(code, message, loss_kind=LossKind.DEFERRED, **kwargs)

    def irrecoverable(self, code: str, message: str, **kwargs) -> ExportDiagnostic:
        kwargs.setdefault("severity", DiagnosticSeverity.ERROR)
        return self.record(code, message, loss_kind=LossKind.IRRECOVERABLE, **kwargs)

    def safety(self, code: str, message: str, **kwargs) -> ExportDiagnostic:
        return self.record(code, message, loss_kind=LossKind.SAFETY, **kwargs)

    def extend(self, diagnostics: "LossReport | Iterable[ExportDiagnostic]"):
        values = diagnostics.diagnostics if isinstance(diagnostics, LossReport) else diagnostics
        for diagnostic in values:
            self.add(diagnostic)

    @property
    def has_errors(self) -> bool:
        return any(diagnostic.severity == DiagnosticSeverity.ERROR for diagnostic in self.diagnostics)

    @property
    def is_lossy(self) -> bool:
        return any(diagnostic.loss_kind is not None for diagnostic in self.diagnostics)

    def raise_for_errors(self):
        if self.has_errors:
            codes = ", ".join(sorted({diagnostic.code for diagnostic in self.diagnostics
                                      if diagnostic.severity == DiagnosticSeverity.ERROR}))
            raise ExportBlockedError(f"Export is blocked by diagnostics: {codes}")

    def to_dict(self) -> dict[str, Any]:
        diagnostics = sorted(self.diagnostics, key=ExportDiagnostic.sort_key)
        return {
            "schema": "sourceio.loss-report",
            "schema_version": self.schema_version,
            "lossy": self.is_lossy,
            "has_errors": self.has_errors,
            "diagnostics": [diagnostic.to_dict() for diagnostic in diagnostics],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            separators=None if indent is not None else (",", ":"),
            allow_nan=False,
        )

    def __iter__(self):
        return iter(self.diagnostics)

    def __len__(self):
        return len(self.diagnostics)
