from types import SimpleNamespace

from SourceIO.blender_bindings.operators.flex_resolver import (
    flex_controller_objects,
    resolve_flex_object,
)


def object_of_type(object_type, *, controllers=(), children=()):
    return SimpleNamespace(type=object_type, flex_controllers=controllers, children=children)


def test_active_flex_mesh_resolves_to_itself():
    mesh = object_of_type('MESH', controllers=(object(),))

    assert flex_controller_objects(mesh) == (mesh,)
    assert resolve_flex_object(mesh) is mesh


def test_armature_resolves_its_unique_flex_child():
    plain_mesh = object_of_type('MESH')
    flex_mesh = object_of_type('MESH', controllers=(object(),))
    armature = object_of_type('ARMATURE', children=(plain_mesh, flex_mesh))

    assert flex_controller_objects(armature) == (flex_mesh,)
    assert resolve_flex_object(armature) is flex_mesh


def test_armature_with_multiple_flex_children_is_ambiguous():
    first = object_of_type('MESH', controllers=(object(),))
    second = object_of_type('MESH', controllers=(object(),))
    armature = object_of_type('ARMATURE', children=(first, second))

    assert flex_controller_objects(armature) == (first, second)
    assert resolve_flex_object(armature) is None


def test_unrelated_or_missing_object_has_no_flex_target():
    assert flex_controller_objects(None) == ()
    assert resolve_flex_object(object_of_type('EMPTY')) is None
