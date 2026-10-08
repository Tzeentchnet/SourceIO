"""Source 2 skeletal animation decoding, ported from ValveResourceFormat (MIT).

See https://github.com/ValveResourceFormat/ValveResourceFormat (``ResourceTypes/ModelAnimation`` and
``ResourceTypes/ModelAnimation2``).
"""
from .animation import DecodedAnimation, SequenceAnimation, Skeleton
from .clip import AnimationClip, ClipAnimation, nm_skeleton
from .loader import (ClipLoader, clip_matches, clip_names, graph_clip_paths, load_graph_clips, load_model_animations,
                     model_skeleton, parse_clip_filter)
from .segments import SUPPORTED_DECODERS
