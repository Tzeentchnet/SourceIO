"""Animation graph 2 clips (``.vnmclip_c``): decoding, retargeting by bone name, filters and names.

Clips are built here with an encoder written from the format description (VRF ``AnimationClip``):
per frame and track, three words of rotation (the largest component dropped, its index in the top
bits of the first two words), three of translation and one of scale, each left out when the track
marks it static. The VRF fixtures from ``tests/fetch_samples.py`` are used when present.
"""
import os
from pathlib import Path

os.environ['NO_BPY'] = '1'

import numpy as np
import pytest

from SourceIO.library.source2.animation import (AnimationClip, ClipAnimation, ClipEvent, ClipLoader, Skeleton,
                                                clip_matches, clip_names, parse_clip_filter)
from SourceIO.library.source2.animation.clip import _decode_quaternions, world_matrices, _matrices

RANGE_MIN = -1 / np.sqrt(2)
RANGE_LENGTH = 2 / np.sqrt(2)
SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "source2" / "clips"


def quat(axis, angle):  # x y z w
    axis = np.asarray(axis, np.float64) / np.linalg.norm(axis)
    return np.array([*(axis * np.sin(angle / 2)), np.cos(angle / 2)])


def encode_quaternion(q) -> list[int]:
    q = np.asarray(q, np.float64)
    index = int(np.argmax(np.abs(q)))
    if q[index] < 0:
        q = -q
    a, b, c = (int(round((q[i] - RANGE_MIN) / RANGE_LENGTH * 0x7FFF)) for i in range(4) if i != index)
    return [a | ((index >> 1) << 15), b | ((index & 1) << 15), c]


def encode_unorm(value, start, length) -> int:
    return int(round((value - start) / length * 65535))


def track(rotation=None, translation=None, scale=None, constant_rotation=(0, 0, 0, 1),
          translation_start=(0, 0, 0), scale_start=1.0):
    def rng(start):
        return {'m_flRangeStart': float(start), 'm_flRangeLength': 0.0 if start is None else 20.0}

    setting = {'m_bIsRotationStatic': int(rotation is None), 'm_bIsTranslationStatic': int(translation is None),
               'm_bIsScaleStatic': int(scale is None), 'm_constantRotation': np.array(constant_rotation, float),
               'm_scaleRange': {'m_flRangeStart': scale_start, 'm_flRangeLength': 2.0}}
    for axis, start in zip('XYZ', translation_start):
        setting[f'm_translationRange{axis}'] = {'m_flRangeStart': float(start), 'm_flRangeLength': 20.0}
    return setting, rotation, translation, scale


def build_clip(tracks, frames, skeleton='test.vnmskel', additive=False, offsets_order=None, root_motion=None):
    """``tracks``: (setting, per-frame rotations, translations, scales) from :func:`track`."""
    frame_words = []
    for f in range(frames):
        words = []
        for setting, rotations, translations, scales in tracks:
            if rotations is not None:
                words += encode_quaternion(rotations[f])
            if translations is not None:
                words += [encode_unorm(translations[f][i], setting[f'm_translationRange{axis}']['m_flRangeStart'], 20.0)
                          for i, axis in enumerate('XYZ')]
            if scales is not None:
                words.append(encode_unorm(scales[f], setting['m_scaleRange']['m_flRangeStart'], 2.0))
        frame_words.append(words)
    order = offsets_order or list(range(frames))
    data, offsets = [], [0] * frames
    for f in order:  # frames may be stored in any order; offsets point at each
        offsets[f] = len(data)
        data += frame_words[f]
    kv = {'m_skeleton': skeleton, 'm_nNumFrames': frames, 'm_flDuration': (frames - 1) / 30.0,
          'm_bIsAdditive': int(additive), 'm_trackCompressionSettings': [t[0] for t in tracks],
          'm_compressedPoseData': np.array(data, dtype='<u2').tobytes(),
          'm_compressedPoseOffsets': np.array(offsets, dtype=np.uint32)}
    if root_motion is not None:
        kv['m_rootMotion'] = {'m_transforms': root_motion}
    return AnimationClip.from_kv(kv, 'test')


def skeleton(names, parents, positions, rotations=None) -> Skeleton:
    count = len(names)
    rotations = np.array(rotations if rotations is not None else [(0, 0, 0, 1)] * count, np.float32)
    return Skeleton(list(names), np.array(parents, np.int32), np.array(positions, np.float32), rotations,
                    np.zeros(count, np.int64))


def same_rotation(a, b, atol=2e-4):
    return np.allclose(a, b, atol=atol) or np.allclose(a, -np.asarray(b), atol=atol)


@pytest.mark.parametrize("q", [quat((1, 0, 0), 2.5), quat((0, 1, 0), -2.9), quat((0, 0, 1), 3.0),
                               quat((1, 1, 1), 0.3), quat((-1, 2, -0.5), 1.7), quat((0.2, -1, 0.4), -1.1)])
def test_quaternion_round_trip(q):
    decoded = _decode_quaternions(np.array([encode_quaternion(q)], dtype=np.uint16))[0]
    assert same_rotation(decoded, q)


def test_each_dropped_component_slot():
    # One quaternion per dropped index: the largest component must come back in its own slot.
    for index in range(4):
        q = np.full(4, 0.2)
        q[index] = 0.9
        q /= np.linalg.norm(q)
        decoded = _decode_quaternions(np.array([encode_quaternion(q)], dtype=np.uint16))[0]
        assert np.argmax(np.abs(decoded)) == index
        assert same_rotation(decoded, q)


def test_track_layout_mixed_static_channels():
    frames = 4
    rot = [quat((0, 0, 1), 0.2 * f) for f in range(frames)]
    pos = [(1.0 + f, -2.0, 3.5 - f) for f in range(frames)]
    scl = [1.0 + 0.1 * f for f in range(frames)]
    tracks = [
        track(constant_rotation=quat((1, 0, 0), 0.5), translation_start=(4, 5, 6), scale_start=1.0),  # all static
        track(rotation=rot, scale=scl, translation_start=(7, 8, 9), scale_start=0.5),
        track(translation=pos, translation_start=(-10, -10, -10)),
    ]
    clip = build_clip(tracks, frames, offsets_order=[2, 0, 3, 1])
    positions, rotations, scales = clip.decode_tracks()
    assert positions.shape == (frames, 3, 3) and rotations.shape == (frames, 3, 4) and scales.shape == (frames, 3)
    for f in range(frames):
        assert same_rotation(rotations[f, 0], quat((1, 0, 0), 0.5), 1e-6)
        assert np.allclose(positions[f, 0], (4, 5, 6)) and scales[f, 0] == pytest.approx(1.0)
        assert same_rotation(rotations[f, 1], rot[f])
        assert np.allclose(positions[f, 1], (7, 8, 9)) and scales[f, 1] == pytest.approx(scl[f], abs=1e-4)
        assert np.allclose(positions[f, 2], pos[f], atol=1e-3)
        assert same_rotation(rotations[f, 2], (0, 0, 0, 1), 1e-6)


def test_short_pose_data_raises():
    clip = build_clip([track(rotation=[quat((0, 0, 1), 0.1)] * 2)], 2)
    clip.pose_data = clip.pose_data[:4]
    with pytest.raises(ValueError):
        clip.decode_tracks()


def test_fps_counts_intervals():
    clip = build_clip([track()], 31)
    assert clip.fps == pytest.approx(30.0)
    assert build_clip([track()], 1).fps == 1.0


def test_retarget_matches_world_pose_by_name():
    # Clip skeleton: root -> arm -> hand. The model inserts a twist bone between arm and hand, has a
    # different bind pose, and an extra finger the clip doesn't know about.
    source = skeleton(["root", "arm", "hand"], [-1, 0, 1], [(0, 0, 0), (0, 0, 10), (5, 0, 0)])
    target = skeleton(["Root", "arm", "twist", "hand", "finger"], [-1, 0, 1, 2, 3],
                      [(0, 0, 1), (0, 0, 9), (2, 0, 0), (3, 1, 0), (1, 0, 0)],
                      [(0, 0, 0, 1), quat((0, 1, 0), 0.3), quat((1, 0, 0), 0.2), (0, 0, 0, 1), quat((0, 0, 1), 0.4)])
    frames = 3
    tracks = [track(),
              track(rotation=[quat((0, 1, 0), 0.4 * f) for f in range(frames)], translation_start=(0, 0, 10)),
              track(rotation=[quat((0, 0, 1), -0.3 * f) for f in range(frames)], translation_start=(5, 0, 0))]
    animation = ClipAnimation(build_clip(tracks, frames), source)
    assert animation.mapped_bones(target) == 3
    decoded = animation.decode(target)

    rotations_xyzw = decoded.rotations[..., [1, 2, 3, 0]]
    target_world = world_matrices(target.parents, _matrices(decoded.positions.astype(np.float64),
                                                            rotations_xyzw.astype(np.float64),
                                                            decoded.scales.astype(np.float64)))
    source_world = world_matrices(source.parents, animation.source_pose())
    for target_index, source_index in ((0, 0), (1, 1), (3, 2)):
        assert np.allclose(target_world[:, target_index], source_world[:, source_index], atol=2e-3)
    bind = _matrices(target.positions.astype(np.float64), target.rotations.astype(np.float64), np.ones(5))
    assert np.allclose(target_world[:, 2], target_world[:, 1] @ bind[2], atol=1e-4)  # twist follows arm
    assert np.allclose(target_world[:, 4], target_world[:, 3] @ bind[4], atol=1e-4)  # finger follows hand
    assert list(decoded.animated_rotation) == [True, True, False, True, False]
    assert not decoded.animated_scale.any()


def test_additive_clip_composes_over_bind_pose():
    source = skeleton(["root", "bone"], [-1, 0], [(0, 0, 0), (3, 0, 0)], [(0, 0, 0, 1), quat((0, 0, 1), 0.5)])
    delta = quat((1, 0, 0), 0.25)
    tracks = [track(scale_start=0.0),
              track(rotation=[delta], translation_start=(1, 2, 0), scale_start=0.0)]
    animation = ClipAnimation(build_clip(tracks, 1, additive=True), source)
    assert animation.delta
    decoded = animation.decode(source)
    assert np.allclose(decoded.positions[0, 1], (4, 2, 0), atol=1e-5)
    expected = _quat_mul(quat((0, 0, 1), 0.5), delta)
    assert same_rotation(decoded.rotations[0, 1][[1, 2, 3, 0]], expected)
    assert np.allclose(decoded.scales, 1.0, atol=1e-5)


def _quat_mul(a, b):  # x y z w
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz])


def test_root_motion_yaw_unwraps_across_180():
    def transform(yaw_degrees, x):
        q = quat((0, 0, 1), np.radians(yaw_degrees))
        return [x, 0.0, 0.5 * x, 1.0, *q]

    yaws = [10, 170, -170, -10]  # a turn past 180 degrees
    clip = build_clip([track()], 4, root_motion=[transform(y, i) for i, y in enumerate(yaws)])
    positions, angles = clip.root_motion_movement()
    assert np.allclose(angles, [10, 170, 190, 350], atol=1e-3)
    assert np.allclose(positions[:, 2], [0, 0.5, 1.0, 1.5])  # clips keep vertical travel

    # A static clip stores a single identity transform, which is no root motion.
    assert build_clip([track()], 2, root_motion=[transform(0, 0)]).root_motion_movement() is None


def test_filters():
    assert parse_clip_filter("idle*, run_n_* ,, walk") == ["idle*", "run_n_*", "walk"]
    path = "animation/anims/world/knife/_default_knife/Idle_Knife.vnmclip"
    assert clip_matches(path, ["idle_knife"])
    assert clip_matches(path, ["*/_default_knife/*"])
    assert clip_matches(path, ["animation/anims/world/knife/_default_knife/idle_knife"])
    assert not clip_matches(path, ["idle"])
    assert not clip_matches(path, [])


def test_clip_names_take_parent_folders_only_when_needed():
    paths = ["a/world/chicken/chick_idle01.vnmclip", "a/viewmodel/chicken/chick_idle01.vnmclip",
             "a/world/chicken/chick_run.vnmclip", "b/x/same.vnmclip", "c/x/same.vnmclip_c"]
    assert clip_names(paths) == {
        "a/world/chicken/chick_idle01.vnmclip": "world/chicken/chick_idle01",
        "a/viewmodel/chicken/chick_idle01.vnmclip": "viewmodel/chicken/chick_idle01",
        "a/world/chicken/chick_run.vnmclip": "chick_run",
        "b/x/same.vnmclip": "b/x/same",
        "c/x/same.vnmclip_c": "c/x/same",
    }


def event(cls, start, duration=0.0, **fields):
    return {'_class': cls, 'm_flStartTime': {'m_flValue': start}, 'm_flDuration': {'m_flValue': duration},
            'm_syncID': '', **fields}


def test_event_labels():
    events = [ClipEvent.from_kv(e) for e in [
        event('CNmSoundEvent', 0.25, m_name='Weapon_Nova.Pump_Q', m_attachmentName=''),
        event('CNmParticleEvent', 0.0, m_hParticleSystem='particles/weapons/cs_weapon_fx/shell_9mm.vpcf'),
        event('CNmIDEvent', 0.4, 0.5, m_ID='RinFront', m_secondaryID=''),
        event('CNmIDEvent', 0.4, 0.5, m_ID='Plant', m_secondaryID='Left'),
        event('CNmLegacyEvent', 0.0, m_animEventClassName='AE_WEAPON_PERFORM_ATTACK', m_KV=None),
        event('CNmMaterialAttributeEvent', 0.0, 1.0, m_attributeName='c4_light'),
        event('CNmFloatCurveEvent', 0.2, 0.4, m_ID='Reload'),
        event('CNmOrientationWarpEvent', 0.1, 0.75),
        event('CNmSomethingNewEvent', 1.0),
    ]]
    assert [e.marker_name for e in events] == [
        'Sound: Weapon_Nova.Pump_Q', 'Particle: shell_9mm', 'ID: RinFront', 'ID: Plant/Left',
        'Legacy: AE_WEAPON_PERFORM_ATTACK', 'MaterialAttribute: c4_light', 'FloatCurve: Reload',
        'OrientationWarp', 'SomethingNew']
    assert events[2].start == pytest.approx(0.4) and events[2].duration == pytest.approx(0.5)


def test_event_frames_are_fractions_of_the_clip():
    # 25 frames span 24 intervals; times are fractions of the clip, not seconds
    assert ClipEvent('Sound', 7 / 24, 0.0).frame(25) == 7
    assert ClipEvent('Sound', 0.0, 0.0).frame(25) == 0
    assert ClipEvent('Sound', 1.0, 0.0).frame(25) == 24
    assert ClipEvent('Sound', 1.0000001, 0.0).frame(25) == 24
    assert ClipEvent('Sound', 0.5, 0.0).frame(1) == 0


def test_secondary_animation_keeps_main_clip_events():
    main = build_clip([track()], 3, skeleton='body.vnmskel')
    weapon = build_clip([track(), track()], 3, skeleton='weapon.vnmskel')
    main.secondary = [weapon]
    main.events = [ClipEvent('Sound', 0.5, 0.0, 'Fire')]
    loader = ClipLoader(None)
    loader._skeletons = {'body.vnmskel': skeleton(['pelvis'], [-1], [(0, 0, 0)]),
                         'weapon.vnmskel': skeleton(['weapon', 'mag'], [-1, 0], [(0, 0, 0), (0, 0, 1)])}
    animation = loader.bind(main, skeleton(['weapon', 'mag'], [-1, 0], [(0, 0, 0), (0, 0, 1)]), 'a/fire.vnmclip')
    assert animation.clip is weapon
    assert [e.marker_name for e in animation.events] == ['Sound: Fire']


def _load_kv(name):
    from SourceIO.library.source2.blocks.kv3_block import KVBlock
    from SourceIO.library.source2.compiled_resource import CompiledResource, DATA_BLOCK
    from SourceIO.library.utils import FileBuffer
    from SourceIO.library.utils.tiny_path import TinyPath
    path = SAMPLES / name
    if not path.is_file():
        pytest.skip(f"{name} not fetched (tests/fetch_samples.py)")
    with FileBuffer(path) as buffer:
        return CompiledResource.from_buffer(buffer, TinyPath(path)).get_block(KVBlock, block_id=DATA_BLOCK)


def test_fixture_secondary_animation_binds_to_weapon_skeleton():
    from SourceIO.library.source2.animation import nm_skeleton
    from SourceIO.library.source2.animation.loader import clip_from_resource
    ak47 = nm_skeleton(_load_kv("ak47.vnmskel_c"))
    clip = clip_from_resource(_load_kv("idle_ak.vnmclip_c"), "animation/idle_ak.vnmclip_c")
    assert clip.name == "idle_ak"
    weapon_tracks = [c for c in [clip, *clip.secondary] if c.skeleton_name.endswith("ak47.vnmskel")]
    assert len(weapon_tracks) == 1
    weapon_clip = weapon_tracks[0]
    assert weapon_clip.frame_count == 1 and weapon_clip.fps == 1.0 and not weapon_clip.additive
    assert weapon_clip.track_count == len(ak47)
    decoded = ClipAnimation(weapon_clip, ak47).decode(ak47)
    assert decoded.positions.shape == (1, len(ak47), 3)
    assert np.allclose(np.linalg.norm(decoded.rotations, axis=-1), 1, atol=1e-3)
    assert decoded.movement_positions is None


@pytest.mark.parametrize("name", ["shoot1_nova.vnmclip_c", "shoot_cz75.vnmclip_c"])
def test_fixture_clips_decode(name):
    from SourceIO.library.source2.animation.loader import clip_from_resource
    clip = clip_from_resource(_load_kv(name), name)
    for part in [clip, *clip.secondary]:
        positions, rotations, scales = part.decode_tracks()
        assert positions.shape[:2] == rotations.shape[:2] == (part.frame_count, part.track_count)
        assert np.all(np.isfinite(positions)) and np.all(np.isfinite(scales))
        assert np.allclose(np.linalg.norm(rotations, axis=-1), 1, atol=1e-3)
    if name == "shoot1_nova.vnmclip_c":
        assert clip.duration == pytest.approx(0.8, abs=1e-4)
        assert [(e.marker_name, e.frame(clip.frame_count)) for e in clip.events] == [
            ("ID: WPN_BLOCK_INSPECT", 0), ("Particle: uweapon_muzflsh_shot_fps", 0),
            ("Sound: Weapon_Nova.Pump_Q", 7), ("Particle: weapon_shell_casing_shotgun_nova", 10)]
    else:
        assert [e.marker_name for e in clip.events] == [
            "Particle: weapon_shell_casing_9mm", "Particle: uweapon_muzzleflash_pist"]


def test_fixture_chicken_skeleton():
    from SourceIO.library.source2.animation import nm_skeleton
    chicken = nm_skeleton(_load_kv("chicken.vnmskel_c"))
    assert len(chicken) == len(chicken.parents) == len(chicken.positions)
    assert chicken.parents[0] == -1 and np.all(chicken.parents < np.arange(len(chicken)))
    assert np.allclose(np.linalg.norm(chicken.rotations, axis=-1), 1, atol=1e-4)
