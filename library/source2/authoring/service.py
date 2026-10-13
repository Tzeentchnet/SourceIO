from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from ..export import DiagnosticSeverity, ExportBlockedError, LossKind, LossReport
from ..workshop import (
    ContentRelativePath,
    ResourceCompilerError,
    ResourceCompilerRunner,
    WorkshopTargetProfile,
)
from .manifest import AuthoringManifestError, Source2AuthoringManifest
from .models import (
    AuthoringArtifact,
    AuthoringArtifactKind,
    AuthoringBackendOutput,
    CompileStep,
    Source2AuthoringRequest,
    is_valid_asset_name,
    suggest_asset_name,
)
from .staging import stage_authoring_artifacts


def _manifest_path(request: Source2AuthoringRequest) -> ContentRelativePath:
    return ContentRelativePath(
        f"sourceio/manifests/{request.asset_name}.sourceio.json"
    )


def _compile_log_path(asset_name: str) -> ContentRelativePath:
    return ContentRelativePath(f"sourceio/logs/{asset_name}.compile.log")


def _preflight_report_path(request: Source2AuthoringRequest) -> ContentRelativePath:
    return ContentRelativePath(
        f"sourceio/reports/{request.asset_name}.preflight.json"
    )


def validate_request(request: Source2AuthoringRequest) -> LossReport:
    report = LossReport()
    if not is_valid_asset_name(request.asset_name):
        report.record(
            "authoring.asset_name.invalid",
            "Asset names must contain only lowercase ASCII letters, digits, and underscores.",
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.SAFETY,
            details={"suggestion": suggest_asset_name(request.asset_name)},
        )
    directories = (
        request.model_directory,
        request.material_directory,
        request.texture_directory,
    )
    if len({path.as_posix().casefold() for path in directories}) != len(directories):
        report.record(
            "authoring.directory.collision",
            "Model, material, and texture directories must not collide.",
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.SAFETY,
        )
    return report


def _validate_output(
        request: Source2AuthoringRequest,
        backend: AuthoringBackendOutput,
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

    folded_paths: dict[str, ContentRelativePath] = {}
    for artifact in artifacts:
        folded = artifact.path.as_posix().casefold()
        previous = folded_paths.get(folded)
        if previous is not None:
            report.record(
                "authoring.path.collision",
                "Two outputs collide on a case-insensitive filesystem.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
                details={
                    "first": previous.as_posix(),
                    "second": artifact.path.as_posix(),
                },
            )
        else:
            folded_paths[folded] = artifact.path

    reserved_paths = (
        ("authoring.manifest.collision", _manifest_path(request), "manifest"),
        (
            "authoring.preflight_report.collision",
            _preflight_report_path(request),
            "preflight report",
        ),
    )
    for code, reserved_path, label in reserved_paths:
        if reserved_path.as_posix().casefold() in folded_paths:
            report.record(
                code,
                f"A backend output collides with the SourceIO {label} path.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
                details={"path": reserved_path.as_posix()},
            )

    artifact_paths = {artifact.path for artifact in artifacts}
    for step in backend.compile_steps:
        if step.source not in artifact_paths:
            report.record(
                "authoring.compile.source_missing",
                "Every compiler input must be an artifact in the same authoring plan.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
                details={"source": step.source.as_posix()},
            )

    if not request.overwrite:
        reserved = (
            AuthoringArtifact(
                _manifest_path(request),
                AuthoringArtifactKind.MANIFEST,
                "",
            ),
            AuthoringArtifact(
                _preflight_report_path(request),
                AuthoringArtifactKind.REPORT,
                "",
            ),
        )
        for artifact in (*artifacts, *reserved):
            path = request.target.content_path(artifact.path)
            if os.path.lexists(path):
                report.record(
                    "authoring.output.exists",
                    "An authored output already exists and overwrite is disabled.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"path": artifact.path.as_posix()},
                )


@dataclass(slots=True)
class AuthoringPlan:
    request: Source2AuthoringRequest
    artifacts: tuple[AuthoringArtifact, ...]
    compile_steps: tuple[CompileStep, ...]
    manifest: Source2AuthoringManifest
    manifest_path: ContentRelativePath
    report: LossReport

    def raise_for_errors(self):
        errors = [
            diagnostic
            for diagnostic in sorted(self.report.diagnostics, key=lambda item: item.sort_key())
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

    def write(self) -> tuple:
        self.raise_for_errors()
        return stage_authoring_artifacts(
            self.request.target,
            self.artifacts,
            overwrite=self.request.overwrite,
        )


@dataclass(frozen=True, slots=True)
class AuthoringExecutionResult:
    written_paths: tuple
    compiler_results: tuple
    manifest_path: ContentRelativePath


class Source2AuthoringService:
    def plan(
            self,
            request: Source2AuthoringRequest,
            backend: AuthoringBackendOutput,
    ) -> AuthoringPlan:
        report = validate_request(request)
        report.extend(backend.report)
        _validate_output(request, backend, report)
        report_artifact = AuthoringArtifact(
            _preflight_report_path(request),
            AuthoringArtifactKind.REPORT,
            report.to_json() + "\n",
        )
        manifest_artifacts = (*backend.artifacts, report_artifact)
        manifest = Source2AuthoringManifest.build(
            request,
            manifest_artifacts,
            backend.compile_steps,
            backend.model_paths,
        )
        manifest_path = _manifest_path(request)
        artifacts = (*manifest_artifacts, AuthoringArtifact(
            manifest_path,
            AuthoringArtifactKind.MANIFEST,
            manifest.to_json(),
        ))
        return AuthoringPlan(
            request,
            tuple(artifacts),
            backend.compile_steps,
            manifest,
            manifest_path,
            report,
        )

    def execute(self, plan: AuthoringPlan) -> AuthoringExecutionResult:
        written = plan.write()
        results = ()
        if plan.request.compile_after_export:
            results = self.compile(plan)
        return AuthoringExecutionResult(written, results, plan.manifest_path)

    def compile(self, plan: AuthoringPlan) -> tuple:
        plan.raise_for_errors()
        return self._compile_steps(
            plan.request.target,
            plan.request.asset_name,
            plan.compile_steps,
        )

    def recompile(
            self,
            target: WorkshopTargetProfile,
            manifest_path: str | ContentRelativePath,
    ) -> tuple:
        path = ContentRelativePath(manifest_path)
        manifest_file = target.content_path(path)
        if not manifest_file.is_file():
            raise FileNotFoundError(f"Authoring manifest is unavailable: {manifest_file}")
        manifest = Source2AuthoringManifest.from_json(
            manifest_file.read_text(encoding="utf-8")
        )
        manifest.validate_target(target)
        for artifact in manifest.artifacts:
            artifact_path = target.content_path(artifact.path)
            if not artifact_path.is_file():
                raise AuthoringManifestError(
                    f"Manifest artifact is unavailable: {artifact.path}"
                )
            payload = artifact_path.read_bytes()
            if len(payload) != artifact.size or hashlib.sha256(payload).hexdigest() != artifact.sha256:
                raise AuthoringManifestError(
                    f"Manifest artifact changed since export: {artifact.path}"
                )
        return self._compile_steps(
            target,
            manifest.asset_name,
            manifest.compile_steps,
        )

    @staticmethod
    def _compile_steps(
            target: WorkshopTargetProfile,
            asset_name: str,
            steps: tuple[CompileStep, ...],
    ) -> tuple:
        runner = ResourceCompilerRunner(target)
        results = []
        try:
            for step in steps:
                results.append(runner.compile(
                    step.source,
                    expected_outputs=step.expected_outputs,
                    arguments=step.arguments,
                ))
        except ResourceCompilerError as exc:
            if exc.result is not None:
                log_path = _compile_log_path(asset_name)
                try:
                    stage_authoring_artifacts(
                        target,
                        (AuthoringArtifact(
                            log_path,
                            AuthoringArtifactKind.LOG,
                            exc.result.log,
                        ),),
                        overwrite=True,
                    )
                except OSError as log_error:
                    raise RuntimeError(
                        "Resource compilation failed and its diagnostic log could not be saved"
                    ) from log_error
            raise
        return tuple(results)
