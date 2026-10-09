"""Generated Source 2 animation containers and metadata-only AnimGraph documents."""
import os
from dataclasses import replace
from struct import pack, unpack

os.environ['NO_BPY'] = '1'

import numpy as np
import pytest

from SourceIO.library.source2.animation import (
    AnimationClip,
    ClipAnimation,
    ClipLoader,
    AnimationGraphExecutionError,
    AnimationImportOptions,
    ChannelAttribute,
    OpaqueAnimationSegment,
    Skeleton,
    UnsupportedAnimationNodeError,
    load_animation_document,
    normalize_animation_document,
    register_animation_resources,
)
from SourceIO.library.source2.animation import loader as animation_loader_module
from SourceIO.library.source2.animation.animation import animations_from_data
from SourceIO.library.source2.blocks.morph_block import (
    MorphBlock,
    UnsupportedFlexOperationError,
)
from SourceIO.library.source2.interfaces import Maturity, ResourceKind, ResourceRef
from SourceIO.library.source2.resource_registry import ResourceRegistry


def skeleton(position=(0, 0, 0)):
    return Skeleton(
        ['root'],
        np.array([-1], dtype=np.int32),
        np.array([position], dtype=np.float32),
        np.array([(0, 0, 0, 1)], dtype=np.float32),
        np.zeros(1, dtype=np.int64),
    )


def segment(decoder_index, element_ids, payload):
    header = np.array(
        [decoder_index, 1, len(element_ids), 8 + len(element_ids) * 2 + len(payload)],
        dtype='<i2',
    )
    return header.tobytes() + np.asarray(element_ids, dtype='<i2').tobytes() + payload


def animation_data(decoders, segments, frames=3, flags=None):
    return {
        'm_decoderArray': [{'m_szName': decoder} for decoder in decoders],
        'm_segmentArray': [{'m_nLocalChannel': channel, 'm_container': container}
                           for channel, container in segments],
        'm_animArray': [{
            'm_name': 'generated',
            'fps': 30.0,
            'm_flags': flags or {},
            'm_pData': {
                'm_nFrames': frames,
                'm_frameblockArray': [{
                    'm_nStartFrame': 0,
                    'm_nEndFrame': frames - 1,
                    'm_segmentIndexArray': np.arange(len(segments), dtype=np.int32),
                }],
            },
        }],
    }


def test_skeletal_morph_user_and_unknown_channels_decode_together():
    positions = np.array([(1, 2, 3), (4, 5, 6), (7, 8, 9)], dtype='<f4')
    morph = np.array([0.25], dtype='<f4')
    user = np.array([0.1, 0.5, 0.9], dtype='<f4')
    unknown = bytes((1, 0, 1))
    data = animation_data(
        [
            'CCompressedFullVector3',
            'CCompressedStaticFloat',
            'CCompressedFullFloat',
            'CCompressedFullBool',
        ],
        [
            (0, segment(0, [10], positions.tobytes())),
            (1, segment(1, [20], morph.tobytes())),
            (2, segment(2, [30], user.tobytes())),
            (3, segment(3, [40], unknown)),
        ],
    )
    decode_key = {
        'm_userArray': [{'m_name': 'MATERIAL_ATTRIBUTE:glow'}],
        'm_dataChannelArray': [
            {
                'm_szChannelClass': 'BoneChannel',
                'm_szVariableName': 'Position',
                'm_szElementNameArray': ['root'],
                'm_nElementIndexArray': [10],
            },
            {
                'm_szChannelClass': 'MorphChannel',
                'm_szVariableName': 'data',
                'm_szElementNameArray': ['smile'],
                'm_nElementIndexArray': [20],
            },
            {
                'm_szChannelClass': 'UserChannel',
                'm_szVariableName': 'data',
                'm_szElementNameArray': ['MATERIAL_ATTRIBUTE:glow'],
                'm_nElementIndexArray': [30],
            },
            {
                'm_szChannelClass': 'FutureChannel',
                'm_szVariableName': 'FutureValue',
                'm_szElementNameArray': ['opaque_flag'],
                'm_nElementIndexArray': [40],
            },
        ],
    }

    decoded = animations_from_data(data, decode_key, skeleton(), ['smile'])[0].decode(skeleton())
    assert np.array_equal(decoded.positions[:, 0], positions)
    assert decoded.animated_position.tolist() == [True]

    morph_channel = decoded.channels(ChannelAttribute.DATA)[0]
    assert morph_channel.names == ('smile',)
    assert np.allclose(morph_channel.values[:, 0, 0], 0.25)

    user_channel = decoded.channels(ChannelAttribute.USER)[0]
    assert user_channel.names == ('MATERIAL_ATTRIBUTE:glow',)
    assert np.allclose(user_channel.values[:, 0, 0], user)

    unknown_channel = decoded.channels(ChannelAttribute.UNKNOWN)[0]
    assert unknown_channel.names == ('opaque_flag',)
    assert unknown_channel.values[:, 0, 0].tolist() == [1.0, 0.0, 1.0]
    assert any(diagnostic.code == 'animation.channel.unknown' for diagnostic in decoded.diagnostics)


def test_additive_skeletal_frames_compose_over_bind_pose_exactly():
    deltas = np.array([(1, 0, 0), (2, 0, 0), (3, 0, 0)], dtype='<f4')
    data = animation_data(
        ['CCompressedFullVector3'],
        [(0, segment(0, [1], deltas.tobytes()))],
        flags={'m_bDelta': True},
    )
    decode_key = {'m_dataChannelArray': [{
        'm_szChannelClass': 'BoneChannel',
        'm_szVariableName': 'Position',
        'm_szElementNameArray': ['root'],
        'm_nElementIndexArray': [1],
    }]}
    target = skeleton((10, 20, 30))
    decoded = animations_from_data(data, decode_key, target)[0].decode(target)
    assert np.array_equal(
        decoded.positions[:, 0],
        np.array([(11, 20, 30), (12, 20, 30), (13, 20, 30)], dtype=np.float32),
    )


def test_unsupported_decoder_is_preserved_with_diagnostic():
    data = animation_data(
        ['CFutureCompressedFloat'],
        [(0, segment(0, [7], b'\x01\x02\x03\x04'))],
        frames=1,
    )
    decode_key = {'m_dataChannelArray': [{
        'm_szChannelClass': 'MorphChannel',
        'm_szVariableName': 'data',
        'm_szElementNameArray': ['future'],
        'm_nElementIndexArray': [7],
    }]}
    animation = animations_from_data(data, decode_key, skeleton(), ['future'])[0]
    assert isinstance(animation.segments[0], OpaqueAnimationSegment)
    assert animation.segments[0].data == b'\x01\x02\x03\x04'
    assert animation.diagnostics[0].code == 'animation.decoder.unsupported'


def _float_bits(value):
    return unpack('<i', pack('<f', value))[0]


def test_legacy_flex_rules_turn_controller_channels_into_morph_weights():
    block = MorphBlock({
        'm_FlexDesc': [{'m_szFacs': 'smile'}, {'m_szFacs': 'half_smile'}],
        'm_FlexControllers': [
            {'m_szName': 'smile', 'm_szType': 'phoneme', 'min': 0.0, 'max': 1.0},
        ],
        'm_FlexRules': [
            {
                'm_nFlex': 0,
                'm_FlexOps': [{'m_OpCode': 'FLEX_OP_FETCH1', 'm_Data': 0}],
            },
            {
                'm_nFlex': 1,
                'm_FlexOps': [
                    {'m_OpCode': 2, 'm_Data': 0},
                    {'m_OpCode': 1, 'm_Data': _float_bits(0.5)},
                    {'m_OpCode': 6, 'm_Data': 0},
                ],
            },
        ],
    })
    names, weights, diagnostics = block.evaluate_flex_rules(
        np.array([[0.0], [0.5], [1.0]], dtype=np.float32)
    )
    assert names == ('smile', 'half_smile')
    assert np.allclose(weights[:, 0], [0.0, 0.5, 1.0])
    assert np.allclose(weights[:, 1], [0.0, 0.25, 0.5])
    assert diagnostics == ()


def test_legacy_flex_rules_support_remap_combo_fetch2_and_nway():
    block = MorphBlock({
        'm_FlexDesc': [
            {'m_szFacs': 'remapped'},
            {'m_szFacs': 'combined'},
            {'m_szFacs': 'nway'},
        ],
        'm_FlexControllers': [
            {'m_szName': 'selector', 'min': 0.0, 'max': 1.0},
            {'m_szName': 'value', 'min': 0.0, 'max': 1.0},
        ],
        'm_FlexRules': [{
            'm_nFlex': 0,
            'm_FlexOps': [
                {'m_OpCode': 'FLEX_OP_FETCH1', 'm_Data': 0},
                {'m_OpCode': 'FLEX_OP_CONST', 'm_Data': _float_bits(0.0)},
                {'m_OpCode': 'FLEX_OP_CONST', 'm_Data': _float_bits(1.0)},
                {'m_OpCode': 'FLEX_OP_CONST', 'm_Data': _float_bits(10.0)},
                {'m_OpCode': 'FLEX_OP_CONST', 'm_Data': _float_bits(20.0)},
                {'m_OpCode': 'FLEX_OP_REMAPVALCLAMPED', 'm_Data': 0},
            ],
        }, {
            'm_nFlex': 1,
            'm_FlexOps': [
                {'m_OpCode': 'FLEX_OP_FETCH2', 'm_Data': 0},
                {'m_OpCode': 'FLEX_OP_FETCH1', 'm_Data': 1},
                {'m_OpCode': 'FLEX_OP_COMBO', 'm_Data': 2},
            ],
        }, {
            'm_nFlex': 2,
            'm_FlexOps': [
                {'m_OpCode': 'FLEX_OP_CONST', 'm_Data': _float_bits(0.0)},
                {'m_OpCode': 'FLEX_OP_CONST', 'm_Data': _float_bits(0.25)},
                {'m_OpCode': 'FLEX_OP_CONST', 'm_Data': _float_bits(0.75)},
                {'m_OpCode': 'FLEX_OP_CONST', 'm_Data': _float_bits(1.0)},
                {'m_OpCode': 'FLEX_OP_CONST', 'm_Data': 0},
                {'m_OpCode': 'FLEX_OP_NWAY', 'm_Data': 1},
            ],
        }],
    })
    names, weights, diagnostics = block.evaluate_flex_rules(
        np.array([[0.0, 0.2], [0.5, 0.4], [1.0, 0.6]], dtype=np.float32)
    )
    assert names == ('remapped', 'combined', 'nway')
    assert np.allclose(weights[:, 0], [10.0, 15.0, 20.0])
    assert np.allclose(weights[:, 1], [2.0, 6.0, 12.0])
    assert np.allclose(weights[:, 2], [0.0, 0.4, 0.0])
    assert diagnostics == ()


def test_unknown_flex_op_is_diagnostic_and_strict_mode_fails():
    block = MorphBlock({
        'm_FlexDesc': [{'m_szFacs': 'future'}],
        'm_FlexControllers': [{'m_szName': 'c', 'min': 0.0, 'max': 1.0}],
        'm_FlexRules': [{
            'm_nFlex': 0,
            'm_FlexOps': [{'m_OpCode': 'FLEX_OP_FUTURE', 'm_Data': 0}],
        }],
    })
    _names, weights, diagnostics = block.evaluate_flex_rules(
        np.ones((1, 1), dtype=np.float32)
    )
    assert weights[0, 0] == 0.0
    assert diagnostics[0].code == 'animation.flex_rule.unsupported'
    with pytest.raises(UnsupportedFlexOperationError):
        block.evaluate_flex_rules(np.ones((1, 1), dtype=np.float32), strict=True)


def test_nm_float_curves_and_full_event_payloads_are_retained():
    samples = np.array([
        0, 65535,
        32768, 32768,
        65535, 0,
    ], dtype=np.uint16)
    clip = AnimationClip.from_kv({
        'm_skeleton': 'test.vnmskel',
        'm_nNumFrames': 3,
        'm_flDuration': 2.0,
        'm_bIsAdditive': False,
        'm_trackCompressionSettings': [],
        'm_compressedPoseData': b'',
        'm_compressedPoseOffsets': [0, 0, 0],
        'm_floatCurveIDs': ['static', 'speed', 'weight'],
        'm_floatCurveDefs': [
            {'m_bIsStatic': True, 'm_range': {'m_flRangeStart': 2.0, 'm_flRangeLength': 0.0}},
            {'m_bIsStatic': False, 'm_range': {'m_flRangeStart': -1.0, 'm_flRangeLength': 2.0}},
            {'m_bIsStatic': False, 'm_range': {'m_flRangeStart': 10.0, 'm_flRangeLength': 10.0}},
        ],
        'm_compressedFloatCurveData': samples,
        'm_compressedFloatCurveOffsets': [0, 2, 4],
        'm_events': [{
            '_class': 'CNmSoundEvent',
            'm_flStartTime': {'m_flValue': 0.25},
            'm_flDuration': {'m_flValue': 0.5},
            'm_syncID': 'reload_sync',
            'm_name': 'Weapon.Reload',
            'm_position': 'EntityPos',
            'm_attachmentName': 'weapon_hand',
            'm_customFutureField': {'value': 17},
        }, {
            '_class': 'CNmIDEvent',
            'm_flStartTime': {'m_flValue': 0.5},
            'm_flDuration': {'m_flValue': 0.0},
            'm_ID': 'Plant',
            'm_secondaryID': 'Left',
        }, {
            '_class': 'CNmMaterialAttributeEvent',
            'm_flStartTime': {'m_flValue': 0.0},
            'm_flDuration': {'m_flValue': 1.0},
            'm_attributeName': 'emissive',
            'm_curve': {'m_flStartValue': 0.0, 'm_flEndValue': 1.0},
        }, {
            '_class': 'CNmFloatCurveEvent',
            'm_flStartTime': {'m_flValue': 0.25},
            'm_flDuration': {'m_flValue': 0.5},
            'm_ID': 'reload_weight',
            'm_curve': {'m_flStartValue': 0.2, 'm_flEndValue': 0.8},
        }],
    }, 'generated')

    assert [curve.name for curve in clip.float_curves] == ['static', 'speed', 'weight']
    assert np.array_equal(clip.float_curves[0].values, np.full(3, 2.0, dtype=np.float32))
    assert np.allclose(clip.float_curves[1].values, [-1.0, 0.0, 1.0], atol=2e-5)
    assert np.allclose(clip.float_curves[2].values, [20.0, 15.0, 10.0], atol=2e-4)

    sound = clip.events[0]
    assert sound.attachment == 'weapon_hand'
    assert sound.sound_position == 'EntityPos'
    assert sound.sync_id == 'reload_sync'
    assert sound.start_seconds == pytest.approx(0.5)
    assert sound.duration_seconds == pytest.approx(1.0)
    assert sound.payload['m_customFutureField'] == {'value': 17}
    assert clip.events[1].primary_id == 'Plant'
    assert clip.events[1].secondary_id == 'Left'
    assert clip.events[2].material_attribute == 'emissive'
    assert clip.events[2].payload['m_curve']['m_flEndValue'] == 1.0
    assert clip.events[3].curve_id == 'reload_weight'
    assert clip.events[3].payload['m_curve']['m_flStartValue'] == 0.2
    decoded = ClipAnimation(clip, skeleton()).decode(skeleton())
    curve_channel = next(
        channel for channel in decoded.data_channels
        if channel.channel_class == 'NmFloatCurve'
    )
    assert curve_channel.names == ('static', 'speed', 'weight')
    assert np.allclose(curve_channel.values[:, 1, 0], clip.float_curves[1].values)

    secondary = replace(
        clip,
        skeleton_name='secondary.vnmskel',
        secondary=[],
        events=[],
        float_curves=[],
        diagnostics=(),
    )
    main = replace(clip, skeleton_name='primary.vnmskel', secondary=[secondary])
    incompatible = Skeleton(
        ['other'],
        np.array([-1], dtype=np.int32),
        np.zeros((1, 3), dtype=np.float32),
        np.array([(0, 0, 0, 1)], dtype=np.float32),
        np.zeros(1, dtype=np.int64),
    )
    loader = ClipLoader(None)
    loader.skeleton = lambda path: (
        incompatible if path == 'primary.vnmskel' else skeleton()
    )
    rebound = loader.bind(main, skeleton())
    assert rebound is not None
    assert rebound.clip is secondary
    assert rebound.float_curves is main.float_curves
    assert rebound.events is main.events


def test_animgraph2_document_preserves_references_variations_states_nodes_and_slots():
    data = {
        'm_variationID': 'Default',
        'm_resources': ['animations/run.vnmclip', 'graphs/weapon.vnmgraph'],
        'm_nodes': [{
            '_class': 'CNmClipNode',
            'm_ID': 'clip-node',
            'm_bRequired': True,
            'm_nDataSlotIdx': 0,
            'm_futureOptionalField': {'x': 1},
        }],
        'm_stateMachine': {
            'm_states': [{
                'm_ID': 'locomotion',
                'm_name': 'Locomotion',
                'm_rootNode': 'clip-node',
                'm_transitions': [{'m_condition': 'moving'}],
            }],
        },
        'm_variations': [{
            'm_ID': 'Heavy',
            'm_parentID': 'Default',
            'm_skeleton': 'heavy.vnmskel',
        }],
        'm_externalGraphSlots': [{
            'm_slotID': 'weapon',
            'm_nNodeIdx': 0,
            'm_dataSlotIdx': 1,
        }],
        'm_externalPoseSlots': [{
            'm_slotID': 'look_pose',
            'm_nNodeIdx': 0,
        }],
    }
    document = normalize_animation_document(data, 'graphs/player.vnmgraph_c')
    assert document.graph_version == 2
    assert [reference.path for reference in document.references[:2]] == [
        'animations/run.vnmclip', 'graphs/weapon.vnmgraph',
    ]
    assert document.references[2].path == 'heavy.vnmskel'
    assert document.nodes[0].metadata['m_futureOptionalField'] == {'x': 1}
    assert document.states[0].node_ids == ('clip-node',)
    assert [variation.identifier for variation in document.variations] == ['Default', 'Heavy']
    assert [(slot.identifier, slot.slot_type) for slot in document.external_slots] == [
        ('weapon', 'graph'), ('look_pose', 'pose'),
    ]
    assert document.external_slots[0].resource == 'graphs/weapon.vnmgraph'
    assert not document.execution_supported and not document.ik_solving_supported
    with pytest.raises(AnimationGraphExecutionError):
        document.execute()

    with pytest.raises(UnsupportedAnimationNodeError):
        normalize_animation_document(
            data,
            'graphs/player.vnmgraph_c',
            options=AnimationImportOptions(supported_node_classes=frozenset()),
        )
    supported = normalize_animation_document(
        data,
        'graphs/player.vnmgraph_c',
        options=AnimationImportOptions(supported_node_classes=frozenset({'CNmClipNode'})),
    )
    assert supported.nodes[0].class_name == 'CNmClipNode'


def test_animgraph1_document_is_normalized_without_execution_claims():
    document = normalize_animation_document({
        'm_pSharedData': {
            'm_nodes': [{
                '_class': 'CSequenceUpdateNode',
                'm_ID': 7,
                'm_sequenceName': 'idle',
            }],
        },
        'm_stateMachine': {
            'm_states': [{'m_sName': 'Idle', 'm_nodeIDs': [7]}],
        },
        'm_animationGroups': ['characters/player.vagrp'],
    }, 'graphs/player.vanmgrph_c', kind=ResourceKind.UNKNOWN)
    assert document.kind is ResourceKind.ANIMATION_GRAPH
    assert document.graph_version == 1
    assert document.nodes[0].class_name == 'CSequenceUpdateNode'
    assert document.states[0].name == 'Idle'
    assert document.references[0].path == 'characters/player.vagrp'


def test_resolver_resource_kind_is_honored_without_a_path(monkeypatch):
    class Resolver:
        @staticmethod
        def resolve(_reference):
            return b'compiled resource'

    class GraphResource:
        _filepath = None

        @staticmethod
        def get_block(*_args, **_kwargs):
            return {'m_variationID': 'Default', 'm_nodes': []}

    monkeypatch.setattr(
        animation_loader_module.CompiledResource,
        'from_buffer',
        lambda *_args, **_kwargs: GraphResource(),
    )
    document = load_animation_document(
        ResourceRef(resource_id=7, kind=ResourceKind.ANIMATION_GRAPH),
        Resolver(),
    )
    assert document.kind is ResourceKind.ANIMATION_GRAPH
    assert document.graph_version == 2


def test_animation_capabilities_register_idempotently():
    registry = ResourceRegistry(include_builtins=False)
    registrations = register_animation_resources(registry)
    assert {registration.kind for registration in registrations} == {
        ResourceKind.ANIMATION,
        ResourceKind.ANIMATION_GROUP,
        ResourceKind.ANIMATION_SEQUENCE,
        ResourceKind.ANIMATION_GRAPH,
        ResourceKind.ANIMATION_CLIP,
    }
    graph = next(
        registration for registration in registrations
        if registration.kind is ResourceKind.ANIMATION_GRAPH
    )
    assert graph.capabilities.read is Maturity.STABLE
    assert graph.capabilities.extract is Maturity.STABLE
    assert graph.capabilities.render is Maturity.UNSUPPORTED
    count = len(registry.registrations)
    register_animation_resources(registry)
    assert len(registry.registrations) == count
