"""Bone roles (from flags and procedural rules) and sides (from names), used for bone collections and colours."""
import os
from pathlib import Path
from types import SimpleNamespace

os.environ['NO_BPY'] = '1'

import pytest

from SourceIO.library.models.mdl.structs.bone import Bone, BoneFlags, BoneRole, ProceduralBoneType, bone_side
from SourceIO.library.models.mdl.v49.mdl_file import MdlV49
from SourceIO.library.utils import FileBuffer

SAMPLES_DIR = Path(__file__).parent.parent.parent / "samples"


@pytest.mark.parametrize("name, side", [
    ("bip_hand_L", "L"),
    ("bip_upperArm_R", "R"),
    ("Dog_Model.Leg1_L", "L"),
    ("ValveBiped.Bip01_L_Thigh", "L"),
    ("ValveBiped.Bip01_R_Finger02", "R"),
    ("foot.l", "L"),
    ("hose_3_R", "R"),
    ("mixamorig:LeftArm", "L"),
    ("Bip01RightHand", "R"),
    ("Right", "R"),
    ("bip_pelvis", None),
    ("Dog_Model.Eye_Panel_Top", None),
    ("S2chaingunL_02", None),  # L inside a word is not a side
    ("leftover_bone", None),
    ("Bright", None),
    ("weapon_bone", None),
])
def test_bone_side(name, side):
    assert bone_side(name) == side


def role(flags=0, procedural_rule_type=0):
    return Bone.role.fget(SimpleNamespace(flags=BoneFlags(flags), procedural_rule_type=procedural_rule_type))


def test_role_priority():
    vertex = BoneFlags.USED_BY_VERTEX_LOD0
    assert role(vertex | BoneFlags.USED_BY_HITBOX) == BoneRole.DEFORM
    assert role(BoneFlags.USED_BY_VERTEX_LOD3) == BoneRole.DEFORM
    # Helpers skin vertices too, but are driven by their rule
    assert role(vertex, ProceduralBoneType.QUATINTERP) == BoneRole.PROCEDURAL
    assert role(vertex | BoneFlags.ALWAYS_PROCEDURAL) == BoneRole.PROCEDURAL
    # prop_bone: an attachment hangs off it, but it exists for bone merge
    assert role(BoneFlags.USED_BY_BONE_MERGE | BoneFlags.USED_BY_ATTACHMENT) == BoneRole.BONE_MERGE
    assert role(BoneFlags.USED_BY_ATTACHMENT) == BoneRole.ATTACHMENT
    assert role(BoneFlags.USED_BY_HITBOX) == BoneRole.OTHER
    assert role() == BoneRole.OTHER


def test_dog_bones():
    path = SAMPLES_DIR / "dog.mdl"
    if not path.exists():
        pytest.skip("dog.mdl not found")
    with FileBuffer(path) as buffer:
        mdl = MdlV49.from_buffer(buffer)
    bones = {bone.name: bone for bone in mdl.bones}

    # procedural_rule_type used to hold the rule object instead of the type
    assert bones["Dog_Model.Arm_Tricep1_R"].procedural_rule_type == ProceduralBoneType.QUATINTERP
    assert bones["Dog_Model.Pelvis"].procedural_rule_type == 0

    roles = {name: bone.role for name, bone in bones.items()}
    assert roles["Dog_Model.Arm_Tricep1_R"] == BoneRole.PROCEDURAL
    assert roles["Dog_Model.Shoulder_Plate_R"] == BoneRole.PROCEDURAL
    assert roles["Dog_Model.BigPhys_Attachment"] == BoneRole.ATTACHMENT
    assert roles["Dog_Model.forward"] == BoneRole.ATTACHMENT
    assert roles["Dog_Model.Leg1_L"] == BoneRole.DEFORM
    counts = {r: list(roles.values()).count(r) for r in BoneRole}
    assert counts == {BoneRole.DEFORM: 49, BoneRole.PROCEDURAL: 7, BoneRole.BONE_MERGE: 0,
                      BoneRole.ATTACHMENT: 2, BoneRole.OTHER: 0}

    assert bones["Dog_Model.Leg1_L"].side == "L"
    assert bones["Dog_Model.Hand_R_Drill"].side == "R"
    assert bones["Dog_Model.Spine1"].side is None
