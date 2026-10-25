from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ...source2.authoring.models import (
    is_valid_asset_name,
    suggest_asset_name,
)
from ...source2.export import (
    DiagnosticSeverity,
    ExportBlockedError,
    LossKind,
    LossReport,
)
from ...source2.workshop import ContentRelativePath
from .manifest import Source1AuthoringManifest
from .models import (
    Source1ArtifactKind,
    Source1ArtifactRoot,
    Source1AuthoringArtifact,
    Source1AuthoringRequest,
    Source1BackendOutput,
)
from .staging import stage_source1_artifacts


def _manifest_path(request: Source1AuthoringRequest) -> ContentRelativePath:
    return ContentRelativePath(
        f"sourceio/manifests/{request.asset_name}.sourceio.json"
    )


def _preflight_report_path(request: Source1AuthoringRequest) -> ContentRelativePath:
    return ContentRelativePath(
        f"sourceio/reports/{request.asset_name}.preflight.json"
    )


def _artifact_path(
        request: Source1AuthoringRequest,
        artifact: Source1AuthoringArtifact,
) -> Path:
    if artifact.root == Source1ArtifactRoot.CONTENT:
        return request.target.content_path(artifact.path)
    return request.target.game_path(artifact.path)


def validate_source1_request(request: Source1AuthoringRequest) -> LossReport:
    report = LossReport()
    if not is_valid_asset_name(request.asset_name):
        report.record(
            "authoring.asset_name.invalid",
            "Asset names must contain only lowercase ASCII letters, digits, and underscores.",
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.SAFETY,
            details={"suggestion": suggest_asset_name(request.asset_name)},
        )
    directory_roots = (
        ("model source", request.model_source_directory, "modelsrc"),
        ("material source", request.material_source_directory, "materialsrc"),
        ("model", request.model_directory, "models"),
        ("material", request.material_directory, "materials"),
    )
    for label, path, expected_root in directory_roots:
        if path.parts[0].casefold() != expected_root:
            report.record(
                f"authoring.source1.{label.replace(' ', '_')}_directory.invalid",
                f"The Source 1 {label} directory must be below {expected_root}.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
                details={"path": path.as_posix()},
            )
    if (
            tuple(part.casefold() for part in request.material_source_directory.parts[1:])
            != tuple(part.casefold() for part in request.material_directory.parts[1:])
    ):
        report.record(
            "authoring.source1.material_directories.mismatch",
            "The paths below materialsrc and materials must match so VTEX output resolves from the VMT.",
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.SAFETY,
        )
    if (
            not request.surface_property
            or any(ord(character) < 32 for character in request.surface_property)
    ):
        report.record(
            "authoring.source1.surface_property.invalid",
            "The Source 1 surface property must be non-empty and contain no control characters.",
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.SAFETY,
        )
    return report


def _validate_output(
        request: Source1AuthoringRequest,
        backend: Source1BackendOutput,
        report: LossReport,
):
    artifacts = tuple(backend.artifacts)
    if not artifacts:
        report.record(
            "authoring.artifacts.none",
            "The backend did not produce any editable artifacts.",
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.MISSING,
        )
        return

    resolved_paths: dict[str, Path] = {}
    for artifact in artifacts:
        resolved = _artifact_path(request, artifact)
        folded = str(resolved).casefold()
        previous = resolved_paths.get(folded)
        if previous is not None:
            report.record(
                "authoring.path.collision",
                "Two outputs collide on a case-insensitive filesystem.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
                details={
                    "first": str(previous),
                    "second": str(resolved),
                },
            )
        else:
            resolved_paths[folded] = resolved

    reserved = (
        Source1AuthoringArtifact(
            Source1ArtifactRoot.CONTENT,
            _manifest_path(request),
            Source1ArtifactKind.MANIFEST,
            "",
        ),
        Source1AuthoringArtifact(
            Source1ArtifactRoot.CONTENT,
            _preflight_report_path(request),
            Source1ArtifactKind.REPORT,
            "",
        ),
    )
    for artifact in reserved:
        resolved = _artifact_path(request, artifact)
        if str(resolved).casefold() in resolved_paths:
            report.record(
                "authoring.source1.reserved_path.collision",
                "A backend output collides with a reserved SourceIO metadata path.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
                details={"path": str(resolved)},
            )

    if not request.overwrite:
        for artifact in (*artifacts, *reserved):
            path = _artifact_path(request, artifact)
            if os.path.lexists(path):
                report.record(
                    "authoring.output.exists",
                    "An authored output already exists and overwrite is disabled.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"path": str(path)},
                )


@dataclass(slots=True)
class Source1AuthoringPlan:
    request: Source1AuthoringRequest
    artifacts: tuple[Source1AuthoringArtifact, ...]
    manifest: Source1AuthoringManifest
    manifest_path: ContentRelativePath
    report: LossReport

    def raise_for_errors(self):
        errors = [
            diagnostic
            for diagnostic in sorted(
                self.report.diagnostics,
                key=lambda item: item.sort_key(),
            )
            if diagnostic.severity == DiagnosticSeverity.ERROR
        ]
        if errors:
            summary = "; ".join(
                f"{diagnostic.code}: {diagnostic.message}"
                for diagnostic in errors[:3]
            )
            if len(errors) > 3:
                summary += f"; plus {len(errors) - 3} more error(s)"
            raise ExportBlockedError(f"Authoring preflight failed: {summary}")

    def write(self) -> tuple[Path, ...]:
        self.raise_for_errors()
        return stage_source1_artifacts(
            self.request.target,
            self.artifacts,
            overwrite=self.request.overwrite,
        )


@dataclass(frozen=True, slots=True)
class Source1AuthoringExecutionResult:
    written_paths: tuple[Path, ...]
    manifest_path: ContentRelativePath


class Source1AuthoringService:
    def plan(
            self,
            request: Source1AuthoringRequest,
            backend: Source1BackendOutput,
    ) -> Source1AuthoringPlan:
        report = validate_source1_request(request)
        report.extend(backend.report)
        _validate_output(request, backend, report)
        report_artifact = Source1AuthoringArtifact(
            Source1ArtifactRoot.CONTENT,
            _preflight_report_path(request),
            Source1ArtifactKind.REPORT,
            report.to_json() + "\n",
        )
        manifest_artifacts = (*backend.artifacts, report_artifact)
        manifest = Source1AuthoringManifest.build(
            request,
            manifest_artifacts,
            backend.model_paths,
        )
        manifest_path = _manifest_path(request)
        artifacts = (*manifest_artifacts, Source1AuthoringArtifact(
            Source1ArtifactRoot.CONTENT,
            manifest_path,
            Source1ArtifactKind.MANIFEST,
            manifest.to_json(),
        ))
        return Source1AuthoringPlan(
            request,
            tuple(artifacts),
            manifest,
            manifest_path,
            report,
        )

    def execute(
            self,
            plan: Source1AuthoringPlan,
    ) -> Source1AuthoringExecutionResult:
        return Source1AuthoringExecutionResult(
            plan.write(),
            plan.manifest_path,
        )
