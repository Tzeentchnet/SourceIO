from __future__ import annotations

import hashlib
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
from .compiler import (
    Source1CompilerError,
    Source1CompilerRunner,
)
from .manifest import (
    Source1AuthoringManifest,
    Source1AuthoringManifestError,
)
from .models import (
    Source1ArtifactKind,
    Source1ArtifactRoot,
    Source1AuthoringArtifact,
    Source1AuthoringRequest,
    Source1BackendOutput,
    Source1CompileStep,
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


def _compile_log_path(asset_name: str) -> ContentRelativePath:
    return ContentRelativePath(f"sourceio/logs/{asset_name}.compile.log")


def _artifact_path(
        request: Source1AuthoringRequest,
        artifact: Source1AuthoringArtifact,
) -> Path:
    if artifact.root == Source1ArtifactRoot.CONTENT:
        return request.target.content_path(artifact.path)
    return request.target.game_path(artifact.path)


def _rooted_path(
        target,
        root: Source1ArtifactRoot,
        path: ContentRelativePath,
) -> Path:
    if root == Source1ArtifactRoot.CONTENT:
        return target.content_path(path)
    return target.game_path(path)


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

    artifact_keys = {
        (artifact.root, artifact.path)
        for artifact in artifacts
    }
    output_keys = set()
    for step in backend.compile_steps:
        source_key = (step.source_root, step.source)
        if source_key not in artifact_keys:
            report.record(
                "authoring.compile.source_missing",
                "Every compiler input must be an artifact in the same authoring plan.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
                details={
                    "root": step.source_root.value,
                    "path": step.source.as_posix(),
                },
            )
        for output in step.expected_outputs:
            output_key = (output.root, output.path)
            if output_key in artifact_keys:
                report.record(
                    "authoring.compile.output_collision",
                    "A compiler output collides with an editable artifact.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={
                        "root": output.root.value,
                        "path": output.path.as_posix(),
                    },
                )
            if output_key in output_keys:
                report.record(
                    "authoring.compile.output_duplicate",
                    "Two compiler steps expect the same output.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={
                        "root": output.root.value,
                        "path": output.path.as_posix(),
                    },
                )
            output_keys.add(output_key)
            output_path = _rooted_path(
                request.target,
                output.root,
                output.path,
            )
            if (
                    request.compile_after_export
                    and not request.overwrite
                    and os.path.lexists(output_path)
            ):
                report.record(
                    "authoring.output.exists",
                    "A compiled output already exists and overwrite is disabled.",
                    severity=DiagnosticSeverity.ERROR,
                    loss_kind=LossKind.SAFETY,
                    details={"path": str(output_path)},
                )


@dataclass(slots=True)
class Source1AuthoringPlan:
    request: Source1AuthoringRequest
    artifacts: tuple[Source1AuthoringArtifact, ...]
    compile_steps: tuple[Source1CompileStep, ...]
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
    compiler_results: tuple
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
            backend.compile_steps,
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
            backend.compile_steps,
            manifest,
            manifest_path,
            report,
        )

    def execute(
            self,
            plan: Source1AuthoringPlan,
            *,
            tool_root: str | os.PathLike[str] | None = None,
    ) -> Source1AuthoringExecutionResult:
        written = plan.write()
        results = ()
        if plan.request.compile_after_export:
            results = self.compile(plan, tool_root=tool_root)
        return Source1AuthoringExecutionResult(
            written,
            results,
            plan.manifest_path,
        )

    def compile(
            self,
            plan: Source1AuthoringPlan,
            *,
            tool_root: str | os.PathLike[str] | None = None,
    ) -> tuple:
        plan.raise_for_errors()
        return self._compile_steps(
            plan.request.target,
            plan.request.asset_name,
            plan.compile_steps,
            tool_root=tool_root,
        )

    def recompile(
            self,
            target,
            manifest_path: str | ContentRelativePath,
            *,
            tool_root: str | os.PathLike[str] | None = None,
    ) -> tuple:
        path = ContentRelativePath(manifest_path)
        manifest_file = target.content_path(path)
        if not manifest_file.is_file():
            raise FileNotFoundError(
                f"Source 1 authoring manifest is unavailable: {manifest_file}"
            )
        manifest = Source1AuthoringManifest.from_json(
            manifest_file.read_text(encoding="utf-8")
        )
        manifest.validate_target(target)
        for artifact in manifest.artifacts:
            if artifact.root == Source1ArtifactRoot.CONTENT:
                artifact_path = target.content_path(artifact.path)
            else:
                artifact_path = target.game_path(artifact.path)
            if not artifact_path.is_file():
                raise Source1AuthoringManifestError(
                    f"Manifest artifact is unavailable: {artifact.path}"
                )
            payload = artifact_path.read_bytes()
            if (
                    len(payload) != artifact.size
                    or hashlib.sha256(payload).hexdigest() != artifact.sha256
            ):
                raise Source1AuthoringManifestError(
                    f"Manifest artifact changed since export: {artifact.path}"
                )
        return self._compile_steps(
            target,
            manifest.asset_name,
            manifest.compile_steps,
            tool_root=tool_root,
        )

    @staticmethod
    def _compile_steps(
            target,
            asset_name: str,
            steps: tuple[Source1CompileStep, ...],
            *,
            tool_root: str | os.PathLike[str] | None = None,
    ) -> tuple:
        results = []
        try:
            runner = Source1CompilerRunner(target, tool_root=tool_root)
            for step in steps:
                results.append(runner.compile(step))
        except Source1CompilerError as exc:
            log = (
                exc.result.log
                if exc.result is not None
                else f"{type(exc).__name__}: {exc}\n"
            )
            try:
                stage_source1_artifacts(
                    target,
                    (Source1AuthoringArtifact(
                        Source1ArtifactRoot.CONTENT,
                        _compile_log_path(asset_name),
                        Source1ArtifactKind.LOG,
                        log,
                    ),),
                    overwrite=True,
                )
            except OSError as log_error:
                raise RuntimeError(
                    "Source 1 compilation failed and its diagnostic log "
                    "could not be saved"
                ) from log_error
            raise
        return tuple(results)
