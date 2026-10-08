"""Collects the skeletal animations a Source 2 model can play.

Ported from ValveResourceFormat (MIT, https://github.com/ValveResourceFormat/ValveResourceFormat):
``Model.GetEmbeddedAnimations``/``GetAnimationGroupAnimations``/``GetReferencedAnimations``,
``EmbeddedSequenceGroup`` and ``IO/Loaders/AnimationGroupLoader.cs``.

Sources, in order:
  * animations embedded in the ``.vmdl_c`` (``ANIM``/``AGRP``/``ASEQ`` blocks named by ``CTRL.embedded_animation``);
  * animation groups the model references (``m_refAnimGroups``, ``.vagrp_c``), either with their own
    ``ANIM`` block or listing external ``.vanim_c`` files (``m_localHAnimArray``);
  * the embedded animations and animation groups of included models (``m_refAnimIncludeModels``).

Animation graph 2 clips (``.vnmclip_c``) are not supported.
"""
from SourceIO.library.shared.content_manager import ContentManager
from SourceIO.library.source2.animation.animation import (SequenceAnimation, Skeleton, animations_from_data,
                                                          animations_from_sequence_data)
from SourceIO.library.source2.blocks.agrp_block import AgrpBlock
from SourceIO.library.source2.blocks.aseq_block import AseqBlock
from SourceIO.library.source2.blocks.kv3_block import KVBlock, custom_type_kvblock
from SourceIO.library.source2.blocks.resource_external_reference_list import ResourceExternalReferenceList
from SourceIO.library.source2.compiled_resource import CompiledResource, DATA_BLOCK
from SourceIO.library.utils.tiny_path import TinyPath
from SourceIO.logger import SourceLogMan

logger = SourceLogMan().get_logger("Source2::Animation")

AnimationDataBlock = custom_type_kvblock("AnimationResourceData_t")
ModelDataBlock = custom_type_kvblock("PermModelData_t")


def _compiled_path(path: str) -> TinyPath:
    path = str(path)
    return TinyPath(path if path.endswith("_c") else path + "_c")


def _load_resource(content_manager: ContentManager, path: str) -> CompiledResource | None:
    compiled_path = _compiled_path(path)
    buffer = content_manager.find_file(compiled_path)
    if buffer is None:
        logger.warn(f"Animation resource {compiled_path} not found")
        return None
    return CompiledResource.from_buffer(buffer, compiled_path)


def _resource_references(resource: CompiledResource, values, extension: str) -> list[str]:
    """Resource paths of a reference array.

    Introspected (NTRO) resources whose references can't be resolved through the external reference
    list read back as null; those fall back to every listed reference with the wanted extension.
    """
    values = list(values or [])
    paths = [str(value) for value in values if isinstance(value, str) and value]
    if len(paths) < len(values):
        references = resource.get_block(ResourceExternalReferenceList, block_name='RERL') or []
        for reference in references:
            name = str(reference.name)
            if name.endswith(extension) and name not in paths:
                paths.append(name)
    return paths


def model_skeleton(model_resource: CompiledResource) -> Skeleton | None:
    data = model_resource.get_block(ModelDataBlock, block_id=DATA_BLOCK)
    if data is None or not data.get('m_modelSkeleton'):
        return None
    return Skeleton.from_model_data(data)


def embedded_animations(model_resource: CompiledResource, skeleton: Skeleton) -> list[SequenceAnimation]:
    """Animations stored inside the model file (VRF EmbeddedSequenceGroup)."""
    ctrl = model_resource.get_block(KVBlock, block_name='CTRL')
    embedded = ctrl.get('embedded_animation') if ctrl else None
    if embedded:
        group = model_resource.get_block(AgrpBlock, block_id=int(embedded.get('group_data_block', -1)))
        animation_data = model_resource.get_block(AnimationDataBlock, block_id=int(embedded.get('anim_data_block', -1)))
        sequence_index = int(embedded.get('seqgroup_data_block', -1))
        # Index zero is the model's own data block.
        sequence_data = model_resource.get_block(AseqBlock, block_id=sequence_index) if sequence_index > 0 else None
    elif model_resource.has_block('ANIM') and model_resource.has_block('AGRP'):
        group = model_resource.get_block(AgrpBlock, block_name='AGRP')
        animation_data = model_resource.get_block(AnimationDataBlock, block_name='ANIM')
        sequence_data = model_resource.get_block(AseqBlock, block_name='ASEQ') if model_resource.has_block('ASEQ') else None
    else:
        return []

    if not group or not animation_data or not group.get('m_decodeKey'):
        return []
    decode_key = group['m_decodeKey']
    if sequence_data:
        return animations_from_sequence_data(sequence_data, animation_data, decode_key, skeleton)
    return animations_from_data(animation_data, decode_key, skeleton)


def animation_group_animations(group_resource: CompiledResource, content_manager: ContentManager,
                               skeleton: Skeleton) -> list[SequenceAnimation]:
    """Animations of a ``.vagrp_c`` (VRF AnimationGroupLoader)."""
    group = group_resource.get_block(AgrpBlock, block_id=DATA_BLOCK)
    if not group or not group.get('m_decodeKey'):
        return []
    decode_key = group['m_decodeKey']
    if group_resource.has_block('ANIM'):
        return animations_from_data(group_resource.get_block(AnimationDataBlock, block_name='ANIM'), decode_key,
                                    skeleton)

    animations = []
    for animation_path in _resource_references(group_resource, group.get('m_localHAnimArray'), '.vanim'):
        animation_resource = _load_resource(content_manager, animation_path)
        if animation_resource is None:
            continue
        animation_data = animation_resource.get_block(AnimationDataBlock, block_id=DATA_BLOCK)
        if animation_data:
            animations.extend(animations_from_data(animation_data, decode_key, skeleton))
    return animations


def referenced_group_animations(model_resource: CompiledResource, content_manager: ContentManager,
                                skeleton: Skeleton) -> list[SequenceAnimation]:
    data = model_resource.get_block(ModelDataBlock, block_id=DATA_BLOCK)
    animations = []
    for group_path in _resource_references(model_resource, data.get('m_refAnimGroups') if data else None, '.vagrp'):
        group_resource = _load_resource(content_manager, group_path)
        if group_resource is not None:
            animations.extend(animation_group_animations(group_resource, content_manager, skeleton))
    return animations


def load_model_animations(model_resource: CompiledResource, content_manager: ContentManager,
                          skeleton: Skeleton | None = None) -> list[SequenceAnimation]:
    """Every animation the model can play, bound to ``skeleton`` (the model's own by default)."""
    skeleton = skeleton or model_skeleton(model_resource)
    if skeleton is None or len(skeleton) == 0:
        return []
    animations = embedded_animations(model_resource, skeleton)
    animations.extend(referenced_group_animations(model_resource, content_manager, skeleton))

    data = model_resource.get_block(ModelDataBlock, block_id=DATA_BLOCK)
    for model_path in _resource_references(model_resource, data.get('m_refAnimIncludeModels') if data else None,
                                           '.vmdl'):
        included = _load_resource(content_manager, model_path)
        if included is None:
            continue
        animations.extend(embedded_animations(included, skeleton))
        animations.extend(referenced_group_animations(included, content_manager, skeleton))
    return animations
