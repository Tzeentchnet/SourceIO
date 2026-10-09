from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from ..exporting.source2 import hammer_document_from_blender, model_document_from_blender
from ..shared.model_container import ModelContainer
from ...library.source2.export import (
    ExportBlockedError,
    HammerMapExporter,
    LossReport,
    ModelDocExporter,
)
from ...library.utils.math_utilities import SOURCE2_HAMMER_UNIT_TO_METERS


def _selected_model_container(context) -> ModelContainer:
    selected = list(context.selected_objects)
    meshes = [obj for obj in selected if obj.type == "MESH"]
    attachments = [
        obj for obj in selected
        if obj.type == "EMPTY" and getattr(obj, "parent_type", None) == "BONE"
    ]
    armature = next((obj for obj in selected if obj.type == "ARMATURE"), None)
    if armature is None:
        armature = next(
            (
                obj.parent for obj in meshes
                if obj.parent is not None and obj.parent.type == "ARMATURE"
            ),
            None,
        )
    return ModelContainer(
        objects=meshes,
        bodygroups={},
        attachments=attachments,
        armature=armature,
        master_collection=context.collection,
    )


def _report_result(operator, paths, report: LossReport):
    warning_count = sum(diagnostic.severity_name == "warning" for diagnostic in report)
    message = f"Wrote {len(paths)} file(s)"
    if warning_count:
        message += f" with {warning_count} warning(s); see the loss report"
    operator.report({"WARNING"} if warning_count else {"INFO"}, message)


class SOURCEIO_OT_Source2ModelDocExport(bpy.types.Operator, ExportHelper):
    """Export selected static meshes as editable ModelDoc/DMX plus loss and provenance sidecars."""

    bl_idname = "sourceio.source2_modeldoc_export"
    bl_label = "Source2 ModelDoc"

    filename_ext = ".vmdl"
    filter_glob: StringProperty(default="*.vmdl", options={"HIDDEN"})
    overwrite: BoolProperty(
        name="Overwrite authored files",
        description="Replace every generated output only after all files stage successfully",
        default=False,
    )
    source_unit_scale: FloatProperty(
        name="Source unit scale",
        description="Blender units per Source 2 unit used when converting geometry back for ModelDoc",
        default=SOURCE2_HAMMER_UNIT_TO_METERS,
        min=0.000001,
        precision=6,
    )

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        target = Path(self.filepath)
        report = LossReport()
        try:
            document = model_document_from_blender(
                _selected_model_container(context),
                target.stem,
                unit_scale=self.source_unit_scale,
                report=report,
            )
            bundle = ModelDocExporter().build(document, stem=target.stem, report=report)
            bundle.loss_report.raise_for_errors()
            paths = bundle.write(target.parent, overwrite=self.overwrite)
        except (ExportBlockedError, OSError, TypeError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _report_result(self, paths, report)
        return {"FINISHED"}


class SOURCEIO_OT_Source2HammerMapExport(bpy.types.Operator, ExportHelper):
    """Export the active collection as a static editable Hammer map plus loss/provenance sidecars."""

    bl_idname = "sourceio.source2_hammer_export"
    bl_label = "Source2 Hammer map"

    filename_ext = ".vmap"
    filter_glob: StringProperty(default="*.vmap", options={"HIDDEN"})
    overwrite: BoolProperty(
        name="Overwrite authored files",
        description="Replace every generated output only after all files stage successfully",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.collection is not None

    def execute(self, context):
        target = Path(self.filepath)
        report = LossReport()
        try:
            document = hammer_document_from_blender(
                context.collection,
                name=target.stem,
                report=report,
            )
            bundle = HammerMapExporter().build(document, stem=target.stem, report=report)
            bundle.loss_report.raise_for_errors()
            paths = bundle.write(target.parent, overwrite=self.overwrite)
        except (ExportBlockedError, OSError, TypeError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _report_result(self, paths, report)
        return {"FINISHED"}


classes = (
    SOURCEIO_OT_Source2ModelDocExport,
    SOURCEIO_OT_Source2HammerMapExport,
)
