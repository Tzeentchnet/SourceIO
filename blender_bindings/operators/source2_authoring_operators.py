from __future__ import annotations

import os
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ..exporting.source2 import execute_cs2_authoring, recompile_cs2_manifest
from ...library.source2.authoring import (
    AuthoringGrouping,
    AuthoringScope,
    Source2AuthoringRequest,
    suggest_asset_name,
)
from ...library.source2.export import ExportBlockedError
from ...library.source2.workshop import (
    CS2_WORKSHOP_LAYOUT,
    ContentRelativePath,
    ResourceCompilerError,
    WorkshopTargetError,
    WorkshopTargetProfile,
)


def _default_install_root() -> str:
    return os.environ.get("SOURCEIO_CS2_WORKSHOP_ROOT", "")


def _request(operator) -> Source2AuthoringRequest:
    target = WorkshopTargetProfile(
        CS2_WORKSHOP_LAYOUT,
        Path(operator.install_root),
        operator.addon_name,
    )
    return Source2AuthoringRequest(
        target,
        operator.asset_name,
        scope=operator.scope,
        grouping=operator.grouping,
        overwrite=operator.overwrite,
        compile_after_export=operator.compile_after_export,
    )


def _report_failure(operator, exc: Exception):
    message = str(exc).strip().splitlines()[-1] if str(exc).strip() else type(exc).__name__
    operator.report({"ERROR"}, message)


class SOURCEIO_OT_CS2AuthoringExport(bpy.types.Operator):
    """Author editable static CS2 Workshop files and optionally compile them."""

    bl_idname = "sourceio.cs2_authoring_export"
    bl_label = "CS2 Workshop static asset"
    bl_options = {"REGISTER"}

    install_root: StringProperty(
        name="CS2 installation",
        description="Counter-Strike Global Offensive installation root containing content and game",
        subtype="DIR_PATH",
        default=_default_install_root(),
    )
    addon_name: StringProperty(
        name="Workshop addon",
        description="Lowercase addon folder name",
        default="sourceio_export",
    )
    asset_name: StringProperty(
        name="Asset name",
        description="Lowercase output name using letters, digits, and underscores",
        default="sourceio_asset",
    )
    scope: EnumProperty(
        name="Scope",
        items=(
            (AuthoringScope.SELECTED.value, "Selected objects", ""),
            (AuthoringScope.COLLECTION.value, "Active collection", ""),
            (AuthoringScope.SCENE.value, "Whole scene", ""),
        ),
        default=AuthoringScope.SELECTED.value,
    )
    grouping: EnumProperty(
        name="Grouping",
        items=(
            (AuthoringGrouping.COMBINED.value, "One combined model", ""),
            (AuthoringGrouping.BATCH.value, "One model per object", ""),
        ),
        default=AuthoringGrouping.COMBINED.value,
    )
    overwrite: BoolProperty(
        name="Overwrite authored files",
        description="Replace all planned editable files atomically",
        default=False,
    )
    compile_after_export: BoolProperty(
        name="Compile after export",
        description="Run the validated CS2 Resource Compiler after editable files are written",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.scene.objects)

    def invoke(self, context, _event):
        if self.asset_name == "sourceio_asset" and context.active_object is not None:
            self.asset_name = suggest_asset_name(context.active_object.name)
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "install_root")
        layout.prop(self, "addon_name")
        layout.prop(self, "asset_name")
        layout.prop(self, "scope")
        layout.prop(self, "grouping")
        layout.prop(self, "overwrite")
        layout.prop(self, "compile_after_export")

    def execute(self, context):
        try:
            result = execute_cs2_authoring(context, _request(self))
        except (
                ExportBlockedError,
                ResourceCompilerError,
                WorkshopTargetError,
                OSError,
                TypeError,
                ValueError,
        ) as exc:
            _report_failure(self, exc)
            return {"CANCELLED"}
        warning_count = sum(
            diagnostic.severity_name == "warning"
            for diagnostic in result.plan.report
        )
        compiled = len(result.execution.compiler_results)
        self.report(
            {"WARNING"} if warning_count else {"INFO"},
            (
                f"Wrote {len(result.execution.written_paths)} editable file(s), "
                f"compiled {compiled} resource(s); manifest "
                f"{result.execution.manifest_path}"
            ),
        )
        return {"FINISHED"}


class SOURCEIO_OT_CS2AuthoringRecompile(bpy.types.Operator):
    """Revalidate and compile an existing SourceIO CS2 authoring manifest."""

    bl_idname = "sourceio.cs2_authoring_recompile"
    bl_label = "Recompile CS2 authoring manifest"
    bl_options = {"REGISTER"}

    install_root: StringProperty(
        name="CS2 installation",
        subtype="DIR_PATH",
        default=_default_install_root(),
    )
    addon_name: StringProperty(name="Workshop addon", default="sourceio_export")
    manifest_path: StringProperty(
        name="Manifest path",
        description="Content-relative SourceIO authoring manifest",
        default="sourceio/manifests/sourceio_asset.sourceio.json",
    )

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "install_root")
        layout.prop(self, "addon_name")
        layout.prop(self, "manifest_path")

    def execute(self, _context):
        try:
            results = recompile_cs2_manifest(
                Path(self.install_root),
                self.addon_name,
                ContentRelativePath(self.manifest_path),
            )
        except (
                ResourceCompilerError,
                WorkshopTargetError,
                OSError,
                TypeError,
                ValueError,
        ) as exc:
            _report_failure(self, exc)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Compiled {len(results)} resource(s)")
        return {"FINISHED"}


classes = (
    SOURCEIO_OT_CS2AuthoringExport,
    SOURCEIO_OT_CS2AuthoringRecompile,
)
