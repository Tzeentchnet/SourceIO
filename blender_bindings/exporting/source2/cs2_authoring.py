from __future__ import annotations

from dataclasses import dataclass

from ....library.source2.authoring import (
    AuthoringExecutionResult,
    AuthoringPlan,
    CS2AuthoringBackend,
    Source2AuthoringRequest,
    Source2AuthoringService,
)
from ....library.source2.workshop import (
    CS2_WORKSHOP_LAYOUT,
    ContentRelativePath,
    WorkshopTargetProfile,
)
from .authoring import extract_authoring_models


@dataclass(frozen=True, slots=True)
class BlenderAuthoringResult:
    plan: AuthoringPlan
    execution: AuthoringExecutionResult


def build_cs2_authoring_plan(context, request: Source2AuthoringRequest) -> AuthoringPlan:
    models, report = extract_authoring_models(context, request)
    backend = CS2AuthoringBackend().build(request, models, report=report)
    return Source2AuthoringService().plan(request, backend)


def execute_cs2_authoring(
        context,
        request: Source2AuthoringRequest,
) -> BlenderAuthoringResult:
    plan = build_cs2_authoring_plan(context, request)
    execution = Source2AuthoringService().execute(plan)
    return BlenderAuthoringResult(plan, execution)


def recompile_cs2_manifest(
        install_root,
        addon_name: str,
        manifest_path: str | ContentRelativePath,
):
    target = WorkshopTargetProfile(
        CS2_WORKSHOP_LAYOUT,
        install_root,
        addon_name,
    )
    return Source2AuthoringService().recompile(target, manifest_path)
