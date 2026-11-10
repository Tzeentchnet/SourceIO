from types import SimpleNamespace

import pytest

from SourceIO.library.models.mdl.materials import get_model_material_for_slot


def test_material_slots_follow_default_skin_family():
    first = SimpleNamespace(name="first")
    second = SimpleNamespace(name="second")
    mdl = SimpleNamespace(
        materials=[first, second],
        skin_groups=[[second, second, first]],
    )

    assert get_model_material_for_slot(mdl, 0) is second
    assert get_model_material_for_slot(mdl, 1) is second
    assert get_model_material_for_slot(mdl, 2) is first


def test_material_slots_fall_back_to_texture_table_without_skins():
    material = SimpleNamespace(name="material")
    mdl = SimpleNamespace(materials=[material], skin_groups=[])

    assert get_model_material_for_slot(mdl, 0) is material


def test_material_slots_reject_invalid_skin_reference():
    mdl = SimpleNamespace(
        materials=[SimpleNamespace(name="material")],
        skin_groups=[[SimpleNamespace(name="material")]],
    )

    with pytest.raises(IndexError):
        get_model_material_for_slot(mdl, 1)
