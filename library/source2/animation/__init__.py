"""Source 2 skeletal animation decoding, ported from ValveResourceFormat (MIT).

See https://github.com/ValveResourceFormat/ValveResourceFormat (``ResourceTypes/ModelAnimation`` and
``ResourceTypes/ModelAnimation2``).
"""
from .animation import DecodedAnimation, DecodedDataChannel, SequenceAnimation, Skeleton
from .clip import (AnimationClip, AnimationFloatCurve, ClipAnimation, ClipEvent,
                   nm_skeleton)
from .document import (ANIMATION_CAPABILITIES, ANIMATION_DOCUMENT_CAPABILITIES,
                       AnimationArtifact, AnimationDocument, AnimationExternalSlot,
                       AnimationGraphExecutionError, AnimationGraphNode,
                       AnimationGraphState, AnimationGraphVariation,
                       AnimationImportOptions, AnimationReference,
                       UnsupportedAnimationNodeError, normalize_animation_document,
                       resource_kind_from_path)
from .loader import (ClipLoader, MissingAnimationDecodeKeyError,
                     animation_artifact_from_resource, animation_document_from_resource,
                     animation_group_animations, animation_group_decode_key,
                     animation_group_references, clip_matches, clip_names,
                     graph_clip_paths, load_animation_document, load_graph_clips,
                     load_model_animations, model_morph_set, model_skeleton,
                     parse_clip_filter, register_animation_resources,
                     standalone_animation_animations)
from .segments import (SUPPORTED_DECODERS, ChannelAttribute,
                       OpaqueAnimationSegment)
