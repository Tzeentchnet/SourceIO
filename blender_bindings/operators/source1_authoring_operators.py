from __future__ import annotations

import os
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ..exporting.source1 import (
    execute_source1_authoring,
    recompile_source1_manifest,
)
from ...library.source1.authoring import (
    Source1AuthoringRequest,
    Source1AuthoringTarget,
    Source1AuthoringTargetError,
    Source1CompilerError,
)
from ...library.source2.authoring import (
    AuthoringGrouping,
    AuthoringScope,
    suggest_asset_name,
)
from ...library.source2.export import ExportBlockedError
from ...library.source2.workshop import ContentRelativePath


def _default_game_root() -> str:
    return os.environ.get("SOURCEIO_SOURCE1_GAME_ROOT", "")


def _default_content_root() -> str:
    return os.environ.get("SOURCEIO_SOURCE1_CONTENT_ROOT", "")


def _default_tool_root() -> str:
    return os.environ.get("SOURCEIO_SOURCE1_TOOL_ROOT", "")


def _tool_root(operator) -> Path | None:
    value = str(operator.tool_root).strip()
    return Path(value) if value else None


def _request(operator) -> Source1AuthoringRequest:
    target = Source1AuthoringTarget(
        Path(operator.game_root),
        Path(operator.content_root),
    )
    return Source1AuthoringRequest(
        target,
        operator.asset_name,
        surface_property=operator.surface_property,
        scope=operator.scope,
        grouping=operator.grouping,
        overwrite=operator.overwrite,
        compile_after_export=operator.compile_after_export,
    )


def _report_failure(operator, exc: Exception):
    message = (
        str(exc).strip().splitlines()[-1]
        if str(exc).strip()
        else type(exc).__name__
    )
    operator.report({"ERROR"}, message)


class SOURCEIO_OT_Source1AuthoringExport(bpy.types.Operator):
    """Author editable static Source 1 files and optionally compile them."""

    bl_idname = "sourceio.source1_authoring_export"
    bl_label = "Source 1 static asset"
    bl_options = {"REGISTER"}

    game_root: StringProperty(
        name="Game root",
        description="Source 1 mod or game directory containing gameinfo.txt",
        subtype="DIR_PATH",
        default=_default_game_root(),
    )
    content_root: StringProperty(
        name="SDK content root",
        description="Root containing modelsrc and materialsrc",
        subtype="DIR_PATH",
        default=_default_content_root(),
    )
    tool_root: StringProperty(
        name="Compiler root",
        description="Optional directory containing vtex.exe and studiomdl.exe",
        subtype="DIR_PATH",
        default=_default_tool_root(),
    )
    asset_name: StringProperty(
        name="Asset name",
        description="Lowercase output name using letters, digits, and underscores",
        default="sourceio_asset",
    )
    surface_property: StringProperty(
        name="Surface property",
        default="default",
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
        description="Run VTEX and StudioMDL after editable files are written",
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
        layout.prop(self, "game_root")
        layout.prop(self, "content_root")
        layout.prop(self, "tool_root")
        layout.prop(self, "asset_name")
        layout.prop(self, "surface_property")
        layout.prop(self, "scope")
        layout.prop(self, "grouping")
        layout.prop(self, "overwrite")
        layout.prop(self, "compile_after_export")

    def execute(self, context):
        try:
            result = execute_source1_authoring(
                context,
                _request(self),
                tool_root=_tool_root(self),
            )
        except (
                ExportBlockedError,
                Source1CompilerError,
                Source1AuthoringTargetError,
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
        self.report(
            {"WARNING"} if warning_count else {"INFO"},
            (
                f"Wrote {len(result.execution.written_paths)} editable file(s), "
                f"compiled {len(result.execution.compiler_results)} resource(s); "
                f"manifest {result.execution.manifest_path}"
            ),
        )
        return {"FINISHED"}


class SOURCEIO_OT_Source1AuthoringRecompile(bpy.types.Operator):
    """Revalidate and compile an existing SourceIO Source 1 manifest."""

    bl_idname = "sourceio.source1_authoring_recompile"
    bl_label = "Recompile Source 1 authoring manifest"
    bl_options = {"REGISTER"}

    game_root: StringProperty(
        name="Game root",
        subtype="DIR_PATH",
        default=_default_game_root(),
    )
    content_root: StringProperty(
        name="SDK content root",
        subtype="DIR_PATH",
        default=_default_content_root(),
    )
    tool_root: StringProperty(
        name="Compiler root",
        subtype="DIR_PATH",
        default=_default_tool_root(),
    )
    manifest_path: StringProperty(
        name="Manifest path",
        description="Content-relative SourceIO authoring manifest",
        default="sourceio/manifests/sourceio_asset.sourceio.json",
    )

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "game_root")
        layout.prop(self, "content_root")
        layout.prop(self, "tool_root")
        layout.prop(self, "manifest_path")

    def execute(self, _context):
        try:
            results = recompile_source1_manifest(
                Path(self.game_root),
                Path(self.content_root),
                ContentRelativePath(self.manifest_path),
                tool_root=_tool_root(self),
            )
        except (
                Source1CompilerError,
                Source1AuthoringTargetError,
                OSError,
                TypeError,
                ValueError,
        ) as exc:
            _report_failure(self, exc)
            return {"CANCELLED"}
        self.report({"INFO"}, f"Compiled {len(results)} resource(s)")
        return {"FINISHED"}


classes = (
    SOURCEIO_OT_Source1AuthoringExport,
    SOURCEIO_OT_Source1AuthoringRecompile,
)
