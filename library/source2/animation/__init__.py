"""Source 2 skeletal animation decoding, ported from ValveResourceFormat (MIT).

See https://github.com/ValveResourceFormat/ValveResourceFormat (``ResourceTypes/ModelAnimation``).
"""
from .animation import DecodedAnimation, SequenceAnimation, Skeleton
from .loader import load_model_animations, model_skeleton
from .segments import SUPPORTED_DECODERS
