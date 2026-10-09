"""Direct Source 2 VANIM/VAGRP import operators.

These operators decode authored animation resources onto compatible armatures.
AnimGraph resources are intentionally not accepted or evaluated.
"""
from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty

from .operator_helper import ImportOperatorHelper
from ..source2.animation_loader import (armature_skeleton, import_sequence_animations)
from ..utils.resource_utils import (deserialize_mounted_content,
                                    serialize_mounted_content)
from ...library.shared.content_manager import ContentManager
from ...library.source2.animation import (AnimationImportOptions,
                                         MissingAnimationDecodeKeyError,
                                         animation_group_animations,
                                         animation_group_decode_key,
                                         animation_group_references,
                                         standalone_animation_animations)
from ...library.source2.animation.segments import AnimationSegment
from ...library.source2.compiled_resource import CompiledResource
from ...library.source2.exceptions import Source2Error
from ...library.utils import FileBuffer
from ...library.utils.math_utilities import SOURCE2_HAMMER_UNIT_TO_METERS
from ...library.utils.tiny_path import TinyPath
from ...logger import SourceLogMan

logger = SourceLogMan().get_logger('Source2::DirectAnimImport')


class AmbiguousAnimationGroupError(ValueError):
    pass


# noinspection PyPep8Naming
class SOURCEIO_OT_Source2AnimationImport(ImportOperatorHelper):
    """Import VANIM/VAGRP sequences onto every compatible selected armature."""

    bl_idname = "sourceio.source2_animation"
    bl_label = "Import Source2 animation"
    bl_options = {'UNDO'}

    discover_resources: BoolProperty(name="Mount discovered content", default=True)
    scale: FloatProperty(
        name="World scale",
        default=SOURCE2_HAMMER_UNIT_TO_METERS,
        min=0.000001,
        precision=6,
        description="Scale the selected armatures were imported with",
    )
    apply_root_motion: BoolProperty(name="Apply root motion", default=True)
    include_hidden: BoolProperty(name="Include hidden animations", default=False)

    filter_glob: StringProperty(default="*.vanim_c;*.vagrp_c", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return any(obj.type == 'ARMATURE' for obj in context.selected_objects)

    def invoke(self, context, event):
        active = context.active_object
        if active is not None and active.type == 'ARMATURE' and 'import_scale' in active:
            self.scale = active['import_scale']
        return super().invoke(context, event)

    def execute(self, context):
        armatures = [obj for obj in context.selected_objects if obj.type == 'ARMATURE']
        if not armatures:
            self.report({'ERROR'}, "Select at least one armature")
            return {'CANCELLED'}

        directory = self.get_directory()
        content_manager = ContentManager()
        if self.discover_resources:
            content_manager.scan_for_content(directory)
            serialize_mounted_content(content_manager)
        else:
            deserialize_mounted_content(content_manager)

        selected_paths = _selected_paths(directory, self.filepath, self.files)
        invalid = [path.name for path in selected_paths if not _is_animation_path(path)]
        if invalid:
            self.report({'ERROR'}, f"Unsupported animation file(s): {', '.join(invalid)}")
            return {'CANCELLED'}

        resources = []
        for path in selected_paths:
            try:
                resources.append((path, _read_resource(path)))
            except (OSError, ValueError, IndexError, KeyError, Source2Error) as ex:
                logger.exception(f"Failed to read animation resource {path}", ex)
                self.report({'ERROR'}, f"Could not read {path.name}: {ex}")

        selected_groups = [(path, resource) for path, resource in resources if _is_group_path(path)]
        vanims = [(path, resource) for path, resource in resources if _is_vanim_path(path)]
        groups = selected_groups
        if vanims and not selected_groups:
            groups = _discover_companion_groups(selected_paths[0].parent, vanims)

        options = AnimationImportOptions(
            scale=self.scale,
            apply_root_motion=self.apply_root_motion,
            include_hidden=self.include_hidden,
        )
        created_count = 0
        imported_sources: set[str] = set()
        missing_decode_keys: list[str] = []
        ambiguous_decode_keys: list[str] = []

        for armature in armatures:
            skeleton = armature_skeleton(armature, options.scale)
            flex_names = _armature_flex_names(armature)
            animations = []

            selected_group_references: set[str] = set()
            for group_path, group_resource in selected_groups:
                try:
                    group_animations = animation_group_animations(
                        group_resource, content_manager, skeleton, flex_names
                    )
                    references = animation_group_references(group_resource)
                except Source2Error as ex:
                    logger.exception(f"Failed to decode animation group {group_path}", ex)
                    self.report({'ERROR'}, f"Could not decode {group_path.name}: {ex}")
                    continue
                animations.extend(group_animations)
                selected_group_references.update(
                    _reference_keys(reference)
                    for reference in references
                )
                if group_animations:
                    imported_sources.add(str(group_path))

            for vanim_path, vanim_resource in vanims:
                if _path_keys(vanim_path).intersection(selected_group_references):
                    continue
                try:
                    companion = _matching_group(vanim_path, groups)
                    decode_key = animation_group_decode_key(companion[1]) if companion else None
                    if decode_key is None:
                        missing_decode_keys.append(vanim_path.name)
                        logger.error(
                            f"{vanim_path} has no owning VAGRP decode key; select its .vagrp_c "
                            "or place that group beside the VANIM"
                        )
                        continue
                    decoded = standalone_animation_animations(
                        vanim_resource, decode_key, skeleton, flex_names=flex_names
                    )
                except AmbiguousAnimationGroupError as ex:
                    ambiguous_decode_keys.append(vanim_path.name)
                    logger.error(str(ex))
                    continue
                except MissingAnimationDecodeKeyError as ex:
                    missing_decode_keys.append(vanim_path.name)
                    logger.error(str(ex))
                    continue
                except Source2Error as ex:
                    logger.exception(f"Failed to decode animation resource {vanim_path}", ex)
                    self.report({'ERROR'}, f"Could not decode {vanim_path.name}: {ex}")
                    continue
                animations.extend(decoded)
                if decoded:
                    imported_sources.add(str(vanim_path))

            animations = [
                animation for animation in animations
                if (options.include_hidden or not animation.hidden) and _is_compatible(animation)
            ]
            created = import_sequence_animations(
                animations,
                armature,
                options.scale,
                options.apply_root_motion,
                selected_paths[0].stem if selected_paths else armature.name,
            )
            created_count += len(created)

        if created_count == 0:
            detail = (
                f" Missing VAGRP decode key for: {', '.join(sorted(set(missing_decode_keys)))}."
                if missing_decode_keys else ""
            )
            if ambiguous_decode_keys:
                detail += (
                    " Multiple VAGRP resources reference: "
                    f"{', '.join(sorted(set(ambiguous_decode_keys)))}."
                )
            self.report(
                {'ERROR'},
                "No compatible animation channels were found for the selected armature(s)." + detail,
            )
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"Imported {created_count} action(s) from {len(imported_sources)} resource(s) "
            f"onto {len(armatures)} armature(s)",
        )
        return {'FINISHED'}


def _selected_paths(directory: TinyPath, filepath: str, files) -> list[Path]:
    if files:
        return [Path(str(directory)) / file.name for file in files]
    return [Path(filepath)] if filepath else []


def _read_resource(path: Path) -> CompiledResource:
    with FileBuffer(TinyPath(path)) as buffer:
        return CompiledResource.from_buffer(buffer, TinyPath(path))


def _is_animation_path(path: Path) -> bool:
    name = path.name.casefold()
    return name.endswith('.vanim_c') or name.endswith('.vagrp_c')


def _is_group_path(path: Path) -> bool:
    return path.name.casefold().endswith('.vagrp_c')


def _is_vanim_path(path: Path) -> bool:
    return path.name.casefold().endswith('.vanim_c')


def _path_keys(path: Path) -> set[str]:
    normalized = path.as_posix().casefold().removesuffix('_c')
    name = path.name.casefold().removesuffix('_c')
    return {normalized, name}


def _reference_keys(reference: str) -> str:
    return str(reference).replace('\\', '/').casefold().removesuffix('_c').rsplit('/', 1)[-1]


def _matching_group(vanim_path: Path, groups):
    matches = []
    for group_path, group_resource in groups:
        if _group_references_vanim(group_resource, vanim_path):
            matches.append((group_path, group_resource))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = ', '.join(str(group_path) for group_path, _resource in matches)
        raise AmbiguousAnimationGroupError(
            f"{vanim_path} is referenced by multiple VAGRP resources: {names}"
        )
    return None


def _group_references_vanim(group_resource: CompiledResource, vanim_path: Path) -> bool:
    keys = _path_keys(vanim_path)
    return any(
        _reference_keys(reference) in keys
        for reference in animation_group_references(group_resource)
    )


def _discover_companion_groups(directory: Path, vanims):
    discovered = []
    for path in directory.glob('*.vagrp_c'):
        try:
            resource = _read_resource(path)
        except (OSError, ValueError, IndexError, KeyError, Source2Error) as ex:
            logger.exception(f"Failed to inspect companion animation group {path}", ex)
            continue
        try:
            matches = any(
                _group_references_vanim(resource, vanim_path)
                for vanim_path, _resource in vanims
            )
        except Source2Error as ex:
            logger.exception(f"Failed to decode companion animation group {path}", ex)
            continue
        if matches:
            discovered.append((path, resource))
    return discovered


def _armature_flex_names(armature: bpy.types.Object) -> tuple[str, ...]:
    names = []
    present = set()
    for obj in bpy.data.objects:
        if obj.type != 'MESH' or obj.data.shape_keys is None:
            continue
        uses_armature = obj.parent == armature or any(
            modifier.type == 'ARMATURE' and modifier.object == armature
            for modifier in obj.modifiers
        )
        if not uses_armature:
            continue
        for key in obj.data.shape_keys.key_blocks:
            if key == obj.data.shape_keys.reference_key:
                continue
            folded = key.name.casefold()
            if folded not in present:
                names.append(key.name)
                present.add(folded)
    return tuple(names)


def _is_compatible(animation) -> bool:
    if animation.has_movement:
        return True
    return any(
        isinstance(segment, AnimationSegment) and len(segment.targets)
        for segment in animation.segments
    )
