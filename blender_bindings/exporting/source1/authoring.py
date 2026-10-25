from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ....library.source1.authoring import (
    Source1AuthoringBackend,
    Source1AuthoringExecutionResult,
    Source1AuthoringMaterial,
    Source1AuthoringPlan,
    Source1AuthoringRequest,
    Source1AuthoringService,
    Source1AuthoringTarget,
    Source1PhongParameters,
)
from ....library.source2.authoring import AuthoringMaterial
from ....library.source2.export import (
    DiagnosticSeverity,
    LossKind,
    LossReport,
)
from ....library.source2.workshop import ContentRelativePath
from ....library.utils.math_utilities import SOURCE1_HAMMER_UNIT_TO_METERS
from ..source2.authoring import (
    _extract_principled_material,
    extract_blender_authoring_models,
)


_PHONG_PROPERTIES = {
    "exponent": "sourceio_source1_phong_exponent",
    "boost": "sourceio_source1_phong_boost",
    "tint": "sourceio_source1_phong_tint",
    "fresnel_ranges": "sourceio_source1_phong_fresnel_ranges",
}


def _source1_material_path(
        material,
        report: LossReport,
) -> ContentRelativePath | None:
    explicit = (
        material.get("sourceio_export_source1_material_path")
        or material.get("sourceio_export_material_path")
    )
    value = explicit or material.get("full_path") or material.get("source_path")
    if not value:
        return None
    raw = str(value).split("?", 1)[0].replace("\\", "/").lstrip("/")
    if raw.casefold().startswith("materials/"):
        raw = raw[len("materials/"):]
    suffix = Path(raw).suffix.casefold()
    if not suffix:
        raw += ".vmt"
    try:
        path = ContentRelativePath(raw)
    except ValueError:
        path = None
    if path is None or path.suffix.casefold() != ".vmt":
        if explicit is not None:
            report.record(
                "authoring.source1.material.override.invalid",
                "The explicit SourceIO Source 1 material path must be a safe, "
                "material-relative editable .vmt path.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
                path=("materials", material.name),
                details={"value": str(explicit)},
            )
        return None
    return path


def _phong_parameters(
        material,
        report: LossReport,
) -> Source1PhongParameters | None:
    enabled = bool(material.get("sourceio_source1_phong", False))
    values = {
        field_name: material.get(property_name)
        for field_name, property_name in _PHONG_PROPERTIES.items()
    }
    if not enabled:
        if any(value is not None for value in values.values()):
            report.record(
                "authoring.source1.material.phong.disabled",
                "Explicit Source 1 phong values require "
                "sourceio_source1_phong to be enabled.",
                severity=DiagnosticSeverity.ERROR,
                loss_kind=LossKind.SAFETY,
                path=("materials", material.name),
            )
        return None
    try:
        return Source1PhongParameters(**values)
    except (TypeError, ValueError) as exc:
        report.record(
            "authoring.source1.material.phong.invalid",
            str(exc),
            severity=DiagnosticSeverity.ERROR,
            loss_kind=LossKind.SAFETY,
            path=("materials", material.name),
        )
        return None


def _extract_source1_material(
        material,
        name: str,
        report: LossReport,
) -> AuthoringMaterial | None:
    resource_path = _source1_material_path(material, report)
    if resource_path is not None:
        return Source1AuthoringMaterial(name=name, existing_vmt=resource_path)
    extracted = _extract_principled_material(material, name, report)
    if extracted is None:
        return None
    phong = _phong_parameters(material, report)
    return Source1AuthoringMaterial(
        name=extracted.name,
        base_color=extracted.base_color,
        metallic=extracted.metallic,
        roughness=extracted.roughness,
        alpha_mode=extracted.alpha_mode,
        alpha_cutoff=extracted.alpha_cutoff,
        double_sided=extracted.double_sided,
        textures=extracted.textures,
        phong=phong,
    )


def extract_source1_authoring_models(
        context,
        request: Source1AuthoringRequest,
        *,
        report: LossReport | None = None,
):
    return extract_blender_authoring_models(
        context,
        request,
        material_extractor=_extract_source1_material,
        unit_scale=SOURCE1_HAMMER_UNIT_TO_METERS,
        fbx_exporter=None,
        report=report,
    )


@dataclass(frozen=True, slots=True)
class BlenderSource1AuthoringResult:
    plan: Source1AuthoringPlan
    execution: Source1AuthoringExecutionResult


def build_source1_authoring_plan(
        context,
        request: Source1AuthoringRequest,
) -> Source1AuthoringPlan:
    models, report = extract_source1_authoring_models(context, request)
    backend = Source1AuthoringBackend().build(request, models, report=report)
    return Source1AuthoringService().plan(request, backend)


def execute_source1_authoring(
        context,
        request: Source1AuthoringRequest,
        *,
        tool_root=None,
) -> BlenderSource1AuthoringResult:
    plan = build_source1_authoring_plan(context, request)
    execution = Source1AuthoringService().execute(
        plan,
        tool_root=tool_root,
    )
    return BlenderSource1AuthoringResult(plan, execution)


def recompile_source1_manifest(
        game_root,
        content_root,
        manifest_path: str | ContentRelativePath,
        *,
        tool_root=None,
):
    target = Source1AuthoringTarget(game_root, content_root)
    return Source1AuthoringService().recompile(
        target,
        manifest_path,
        tool_root=tool_root,
    )
