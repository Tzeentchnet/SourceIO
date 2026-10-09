"""Collects the skeletal animations a Source 2 model can play.

Ported from ValveResourceFormat (MIT, https://github.com/ValveResourceFormat/ValveResourceFormat):
``Model.GetEmbeddedAnimations``/``GetAnimationGroupAnimations``/``GetReferencedAnimations``,
``EmbeddedSequenceGroup`` and ``IO/Loaders/AnimationGroupLoader.cs``.

Sources, in order:
  * animations embedded in the ``.vmdl_c`` (``ANIM``/``AGRP``/``ASEQ`` blocks named by ``CTRL.embedded_animation``);
  * animation groups the model references (``m_refAnimGroups``, ``.vagrp_c``), either with their own
    ``ANIM`` block or listing external ``.vanim_c`` files (``m_localHAnimArray``);
  * the embedded animations and animation groups of included models (``m_refAnimIncludeModels``).

Animation graph 2 clips (``.vnmclip_c``) are found through the model's graphs (``m_animGraph2Refs``,
VRF ``AnimationGraphLoader``) and loaded separately by :func:`load_graph_clips`, since a character's
graphs reach thousands of them.
"""
from fnmatch import fnmatchcase

from ...shared.content_manager import ContentManager
from .animation import (SequenceAnimation, Skeleton, animations_from_data,
                                                          animations_from_sequence_data)
from .clip import AnimationClip, ClipAnimation, nm_skeleton
from ..blocks.agrp_block import AgrpBlock
from ..blocks.aseq_block import AseqBlock
from ..blocks.kv3_block import KVBlock, custom_type_kvblock
from ..blocks.resource_external_reference_list import ResourceExternalReferenceList
from ..compiled_resource import CompiledResource, DATA_BLOCK
from ...utils.tiny_path import TinyPath
from ....logger import SourceLogMan

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


def graph_clip_paths(model_resource: CompiledResource, content_manager: ContentManager) -> list[str]:
    """Clips (``.vnmclip``) the model's animation graphs reference, nested graphs included, each once and
    in first-seen order (VRF ``AnimationGraphLoader.GetClipNames``)."""
    data = model_resource.get_block(ModelDataBlock, block_id=DATA_BLOCK)
    visited: set[str] = set()
    clips: list[str] = []

    def collect(graph_path: str):
        if graph_path.casefold() in visited:
            return
        visited.add(graph_path.casefold())
        graph_resource = _load_resource(content_manager, graph_path)
        graph = graph_resource.get_block(KVBlock, block_id=DATA_BLOCK) if graph_resource else None
        if not graph:
            return
        for resource in graph.get('m_resources', []) or []:
            resource = str(resource)
            if resource.casefold().endswith('.vnmclip'):
                if resource.casefold() not in visited:
                    visited.add(resource.casefold())
                    clips.append(resource)
            elif resource.casefold().endswith('.vnmgraph'):
                collect(resource)

    for reference in (data.get('m_animGraph2Refs', []) or []) if data else []:
        if reference.get('m_hGraph'):
            collect(str(reference['m_hGraph']))
    return clips


def clip_matches(path: str, patterns: list[str]) -> bool:
    """Whether a clip path matches any of the patterns, as a whole path (with or without the extension) or by
    its file name, ignoring case."""
    path = path.casefold().replace('\\', '/')
    bare = path.removesuffix('.vnmclip')
    stem = bare.rsplit('/', 1)[-1]
    return any(fnmatchcase(path, pattern) or fnmatchcase(bare, pattern) or fnmatchcase(stem, pattern)
               for pattern in (p.casefold() for p in patterns))


def parse_clip_filter(text: str) -> list[str]:
    """Patterns of a filter string, separated by commas or whitespace."""
    return [part for part in text.replace(',', ' ').split() if part]


def clip_names(paths: list[str]) -> dict[str, str]:
    """Display names for clip paths: the file name, with as many parent folders as it takes to be unique."""
    parts = {path: path.replace('\\', '/').removesuffix('.vnmclip_c').removesuffix('.vnmclip').split('/') for path in paths}
    names: dict[str, str] = {}
    pending = list(parts)
    depth = 1
    while pending:
        candidates = {path: '/'.join(parts[path][-depth:]) for path in pending}
        counts: dict[str, int] = {}
        for candidate in candidates.values():
            counts[candidate.casefold()] = counts.get(candidate.casefold(), 0) + 1
        taken = {name.casefold() for name in names.values()}
        still_pending = []
        for path, candidate in candidates.items():
            if (counts[candidate.casefold()] == 1 and candidate.casefold() not in taken) or depth >= len(parts[path]):
                names[path] = candidate
            else:
                still_pending.append(path)
        pending = still_pending
        depth += 1
    return names


class ClipLoader:
    """Loads clips and the NM skeletons they are authored on, caching skeletons."""

    def __init__(self, content_manager: ContentManager):
        self.content_manager = content_manager
        self._skeletons: dict[str, Skeleton | None] = {}

    def skeleton(self, path: str) -> Skeleton | None:
        key = path.casefold()
        if key not in self._skeletons:
            resource = _load_resource(self.content_manager, path) if path else None
            data = resource.get_block(KVBlock, block_id=DATA_BLOCK) if resource else None
            self._skeletons[key] = nm_skeleton(data) if data and data.get('m_boneIDs') else None
        return self._skeletons[key]

    def clip(self, path: str) -> AnimationClip | None:
        resource = _load_resource(self.content_manager, path)
        data = resource.get_block(KVBlock, block_id=DATA_BLOCK) if resource else None
        if not data:
            return None
        return clip_from_resource(data, path)

    def bind(self, clip: AnimationClip, target: Skeleton, path: str = '') -> ClipAnimation | None:
        """The clip, or the secondary animation of it, that drives the most bones of ``target``."""
        best, best_count = None, 0
        for candidate in [clip, *clip.secondary]:
            source = self.skeleton(candidate.skeleton_name)
            if source is None:
                continue
            animation = ClipAnimation(candidate, source, path)
            count = animation.mapped_bones(target)
            if count > best_count:
                best, best_count = animation, count
        return best


def clip_from_resource(data, path: str) -> AnimationClip:
    name = str(path).replace('\\', '/').rsplit('/', 1)[-1]
    for suffix in ('_c', '.vnmclip'):
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return AnimationClip.from_kv(data, name)


def load_graph_clips(model_resource: CompiledResource, content_manager: ContentManager, patterns: list[str],
                     skeleton: Skeleton | None = None) -> list[ClipAnimation]:
    """The model's graph clips that match ``patterns``, bound to ``skeleton`` (the model's own by default).

    Clips that drive none of the skeleton's bones are skipped.
    """
    skeleton = skeleton or model_skeleton(model_resource)
    if skeleton is None or len(skeleton) == 0 or not patterns:
        return []
    loader = ClipLoader(content_manager)
    animations = []
    for path in graph_clip_paths(model_resource, content_manager):
        if not clip_matches(path, patterns):
            continue
        clip = loader.clip(path)
        if clip is None:
            continue
        animation = loader.bind(clip, skeleton, path)
        if animation is None:
            logger.warn(f"Clip {path} drives no bone of {model_resource.name}")
            continue
        animations.append(animation)
    return animations
