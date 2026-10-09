import contextlib
import random
import re

import bpy

from ...library.utils.perf_sampler import timed
from ...library.utils.tiny_path import TinyPath


@contextlib.contextmanager
def pause_view_layer_update():
    from bpy.ops import _BPyOpsSubModOp
    view_layer_update = _BPyOpsSubModOp._view_layer_update

    def dummy_view_layer_update(context):
        pass

    _BPyOpsSubModOp._view_layer_update = dummy_view_layer_update
    try:
        yield
    finally:
        _BPyOpsSubModOp._view_layer_update = view_layer_update


@contextlib.contextmanager
def edit_armature(armature_obj: bpy.types.Object):
    """Edit mode on ``armature_obj`` for the duration of the block; yields its edit bones.

    Bones can only be created in edit mode, and only an operator enters it. Edit mode follows the
    view layer's active object (a context override is ignored) and also takes in every other
    selected armature, so the armature is made the only selected, active object meanwhile. It is
    linked to the scene collection for the duration unless it is already there. Object mode, the
    selection and the active object are restored afterwards, also when the block raises.
    """
    view_layer = bpy.context.view_layer
    scene_collection = bpy.context.scene.collection
    # Not view_layer.objects: it is only resynced lazily after a link or unlink.
    linked = armature_obj.name not in scene_collection.objects
    if linked:
        scene_collection.objects.link(armature_obj)
    active = view_layer.objects.active
    if active is not None and active.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    selected = [obj for obj in view_layer.objects if obj.select_get()]
    for obj in selected:
        obj.select_set(False)
    armature_obj.select_set(True)
    view_layer.objects.active = armature_obj
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        yield armature_obj.data.edit_bones
    finally:
        if armature_obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        armature_obj.select_set(False)
        for obj in selected:
            obj.select_set(True)
        view_layer.objects.active = active
        if linked:
            scene_collection.objects.unlink(armature_obj)


class ActionCurveFactory:
    """Creates layered (slotted) actions and their FCurves.

    Compact mode stores every animation as a separate slot of one master action.
    Otherwise each animation gets its own action, all sharing one slot name so
    swapping actions on the armature keeps the slot binding.
    """

    def __init__(self, master_name: str, armature_obj: bpy.types.Object | None, legacy_behavior: bool = False):
        self._armature = armature_obj
        self._legacy_behavior = legacy_behavior
        self.master_name = master_name
        self._adt = None
        self._channelbag = None
        self.action: bpy.types.Action | None = None
        self.slot: bpy.types.ActionSlot | None = None
        self.created: list[tuple[bpy.types.Action, bpy.types.ActionSlot]] = []

        if self._armature:
            self._adt = armature_obj.animation_data or armature_obj.animation_data_create()

        if not legacy_behavior:
            self.action = bpy.data.actions.new(master_name)
            self.action.use_fake_user = True
            self._strip = self.action.layers.new(name='Layer').strips.new(type='KEYFRAME')
            if self._adt is not None:
                self._adt.action = self.action

    def new_action(self, name: str) -> tuple[bpy.types.Action, bpy.types.ActionSlot]:
        if not self._legacy_behavior:
            slot = self.action.slots.new(id_type='OBJECT', name=name)
            self._channelbag = self._strip.channelbags.new(slot=slot)
        else:
            self.action = bpy.data.actions.new(name)
            self.action.use_fake_user = True
            strip = self.action.layers.new(name='Layer').strips.new(type='KEYFRAME')
            slot = self.action.slots.new(id_type='OBJECT', name=self.master_name)
            self._channelbag = strip.channelbags.new(slot=slot)
            if self._adt is not None:
                self._adt.action = self.action

        if self._adt is not None:
            self._adt.action_slot = slot
        self.slot = slot
        self.created.append((self.action, slot))
        return self.action, slot

    def new_group(self, name: str):
        return self._channelbag.groups.new(name=name)

    def new_fcurve(self, data_path: str, index: int = 0, group=None):
        curve = self._channelbag.fcurves.new(data_path=data_path, index=index)
        if group is not None:
            curve.group = group
        return curve

def find_layer_collection(layer_collection, name):
    if layer_collection.name == name:
        return layer_collection
    for layer in layer_collection.children:
        found = find_layer_collection(layer, name)
        if found:
            return found



def add_material(material, model_ob):
    md = model_ob.data
    for i, ob_material in enumerate(md.materials):
        if (ob_material.name == material.name and
            ob_material.get("full_path", "Not match") == material.get("full_path", "Not match too")
        ):
            return i
    else:
        md.materials.append(material)
        return len(md.materials) - 1



def get_or_create_material(name: str, full_path: str):
    full_path = full_path.lstrip('/').casefold()
    for mat in bpy.data.materials:
        #if (fp := mat.get('full_path', None)) is None:
        #    continue
        if TinyPath(mat.get('full_path', '').casefold()) == TinyPath(full_path):
            return mat
    mat = bpy.data.materials.new(name)
    mat["full_path"] = full_path
    mat.diffuse_color = [random.uniform(.4, 1) for _ in range(3)] + [1.0]
    return mat


KNOWN_COLLECTIONS_CACHE = {}


def get_or_create_collection(name, parent: bpy.types.Collection) -> bpy.types.Collection:
    if (key := KNOWN_COLLECTIONS_CACHE.get(name, None)) is not None:
        if (collection := bpy.data.collections.get(key, None)) is not None:
            return collection
        KNOWN_COLLECTIONS_CACHE.clear()

    new_collection = (bpy.data.collections.get(name, None) or bpy.data.collections.new(name))
    if new_collection.name not in parent.children:
        parent.children.link(new_collection)
    KNOWN_COLLECTIONS_CACHE[name] = new_collection.name
    return new_collection


def get_or_create_child_collection(name: str, parent: bpy.types.Collection) -> bpy.types.Collection:
    """Return ``parent``'s child collection called ``name``, creating it if there is none.

    Unlike :func:`get_or_create_collection`, a collection of that name elsewhere is not reused: the new one
    gets Blender's ``.001`` suffix instead, and is found again under that name.
    """
    pattern = re.compile(rf"{re.escape(name)}(\.\d{{3,}})?")
    for child in parent.children:
        if pattern.fullmatch(child.name):
            return child
    collection = bpy.data.collections.new(name)
    parent.children.link(collection)
    return collection


# def get_new_unique_collection(model_name, parent_collection):
#     copy_count = len([collection for collection in bpy.data.collections if model_name in collection.name])
#
#     master_collection = get_or_create_collection(model_name + (f'_{copy_count}' if copy_count > 0 else ''),
#                                                  parent_collection)
#     return master_collection

_name_next_idx = {}

def get_new_unique_collection(model_name, parent_collection):
    """Faster for repeated calls: caches the next free suffix per base name and verifies only once per new base."""
    from bpy import data as _d

    if model_name not in _name_next_idx:
        if _d.collections.get(model_name) is None:
            _name_next_idx[model_name] = 1
            return get_or_create_collection(model_name, parent_collection)
        i = 1
        while _d.collections.get(f"{model_name}_{i}") is not None:
            i += 1
        _name_next_idx[model_name] = i + 1
        return get_or_create_collection(f"{model_name}_{i}", parent_collection)

    i = _name_next_idx[model_name]
    name = f"{model_name}_{i}"
    _name_next_idx[model_name] = i + 1
    return get_or_create_collection(name, parent_collection)


def append_blend(filepath, type_name, link=False):
    with bpy.data.libraries.load(filepath, link=link) as (data_from, data_to):
        setattr(data_to, type_name, [asset for asset in getattr(data_from, type_name)])
    for o in getattr(data_to, type_name):
        o.use_fake_user = True


def new_collection(name: str, parent: bpy.types.Collection):
    collection = bpy.data.collections.new(name)
    if collection.name not in parent.children:
        parent.children.link(collection)
    collection.name = name
    return collection
