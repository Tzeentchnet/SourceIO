import os
import tempfile
from hashlib import md5, sha256
from typing import Optional

import bpy
import numpy as np

from ...library.utils.pylib.image import save_exr, save_png, encode_exr, encode_png
from ...library.utils.tiny_path import TinyPath
from ...logger import SourceLogMan

logger = SourceLogMan().get_logger("TextureUtils")

#: Folder name used for on-disk caches next to a recognized game, so frames
#: extracted once can be reused by later imports from the same game.
CACHE_DIR_NAME = "sourceio_cache"
CACHE_ID_PROPERTY = "sourceio_cache_identity"


def texture_cache_key(texture_path: TinyPath, cache_identity: str = "", *other_args: str) -> str:
    md_ = md5(texture_path.as_posix().casefold().encode("utf8"))
    for value in (cache_identity, *other_args):
        encoded = str(value).encode("utf8")
        md_.update(len(encoded).to_bytes(4, "little"))
        md_.update(encoded)
    return md_.hexdigest()


def _get_texture(texture_path: TinyPath, cache_identity: str = "", *other_args):
    key = texture_cache_key(texture_path, cache_identity, *other_args)
    cache = bpy.context.scene.get("texture_name_to_texture", {})
    if key in cache:
        return cache[key]


def _add_texture(texture_path: TinyPath, real_name: str, cache_identity: str = "", *other_args):
    key = texture_cache_key(texture_path, cache_identity, *other_args)
    cache = bpy.context.scene.get("texture_name_to_texture", {})
    cache[key] = real_name
    bpy.context.scene["texture_name_to_texture"] = cache


def _variant_path(path: TinyPath, extension: str, cache_identity: str) -> TinyPath:
    if not cache_identity:
        return path.with_suffix(f".{extension}")
    digest = sha256(cache_identity.encode("utf8")).hexdigest()[:16]
    return path.with_suffix(f".sourceio-{digest}.{extension}")


def _variant_image_name(texture_path: TinyPath, cache_identity: str) -> str:
    if not cache_identity:
        return texture_path.stem
    digest = sha256(cache_identity.encode("utf8")).hexdigest()[:12]
    return f"{texture_path.stem}__{digest}"


def check_texture_cache(
        texture_path: TinyPath,
        cache_identity: str = "",
) -> Optional[bpy.types.Image]:
    logical_path = texture_path.as_posix().casefold()
    for image_existing in bpy.data.images:
        if (fp := image_existing.get('full_path')) is None:
            continue
        if fp.casefold() == logical_path and image_existing.get(CACHE_ID_PROPERTY, "") == cache_identity:
            return image_existing

    short_name = _get_texture(texture_path, cache_identity)
    if short_name is not None:
        image = bpy.data.images.get(short_name)
        if image is not None and image.get(CACHE_ID_PROPERTY, "") == cache_identity:
            return image
    if bpy.context.scene.TextureCachePath == "":
        return None
    image = None
    base_path = TinyPath(bpy.context.scene.TextureCachePath) / texture_path
    for extension in ("png", "exr", "hdr", "tga", "jpg", "jpeg", "webp"):
        full_path = _variant_path(base_path, extension, cache_identity)
        if full_path.exists():
            image = bpy.data.images.load(full_path.as_posix(), check_existing=True)
            break
    if image is None:
        return None
    logger.info(f"Loaded {texture_path!r} texture from disc")
    image.alpha_mode = "CHANNEL_PACKED"
    image.name = _variant_image_name(texture_path, cache_identity)
    image['full_path'] = logical_path
    image[CACHE_ID_PROPERTY] = cache_identity
    _add_texture(texture_path, image.name, cache_identity)
    return image


def _is_writable_dir(path: TinyPath) -> bool:
    """True if ``path`` exists (or can be created) and we may write into it."""
    probe = path
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            return False
        probe = parent
    return probe.is_dir() and os.access(probe, os.W_OK)


def get_asset_cache_roots(content_manager, asset_path: TinyPath) -> list[TinyPath]:
    """Candidate game-local cache roots for ``asset_path``, best first.

    The provider that owns an asset is often not writable: a ``VPKContentProvider``
    reports the archive's own folder, and a game installed under Program Files or
    on a read-only mount cannot be written to at all. So offer the owning
    provider's root first, then the roots of every other mounted provider that
    contains it (a VPK's hosting game directory sorts nearest because it is the
    longest matching prefix), leaving the caller to pick the first writable one.
    """
    if content_manager is None:
        return []
    try:
        owner = content_manager.get_content_provider_from_asset_path(asset_path)
    except Exception:
        owner = None

    roots = []
    if owner is not None and owner.root is not None:
        roots.append(TinyPath(owner.root))

    # Providers whose root contains the owner's root, nearest ancestor first.
    if roots:
        owner_root = roots[0]
        ancestors = []
        for provider in getattr(content_manager, 'children', ()):
            root = getattr(provider, 'root', None)
            if root is None:
                continue
            root = TinyPath(root)
            if root in owner_root.parents:
                ancestors.append(root)
        ancestors.sort(key=lambda path: len(path.parts), reverse=True)
        roots.extend(ancestors)
    return roots


def get_frame_cache_dir(asset_roots: list[TinyPath] | TinyPath | None = None) -> TinyPath:
    """Return a writable directory for cached image-sequence frames.

    Blender cannot pack image sequences (``pack_all`` reports *"packing movies or
    image sequences not supported"``), so unlike single textures these frames must
    stay on disk for as long as the material lives. Prefer a ``sourceio_cache``
    folder inside the recognized game so a later import of a different map from
    the same game reuses the already-extracted frames, then fall back to the
    user's texture cache, then to the OS temp dir for read-only installs.
    """
    if asset_roots is None:
        asset_roots = []
    elif isinstance(asset_roots, (str, TinyPath)):
        asset_roots = [TinyPath(asset_roots)]

    candidates = [TinyPath(root) / CACHE_DIR_NAME for root in asset_roots]
    cache_path = bpy.context.scene.TextureCachePath
    if cache_path:
        candidates.append(TinyPath(cache_path) / CACHE_DIR_NAME)
    candidates.append(TinyPath(tempfile.gettempdir()) / CACHE_DIR_NAME)

    for candidate in candidates:
        if not _is_writable_dir(candidate):
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
        except OSError:
            continue
        return candidate
    raise OSError(f"No writable directory for frame cache, tried: {candidates}")


def create_and_cache_image_sequence(texture_path: TinyPath, frames: list[np.ndarray],
                                    asset_root: TinyPath | None = None) -> tuple[bpy.types.Image, int]:
    """Write ``frames`` as a numbered PNG sequence and return ``(image, count)``.

    The returned image has ``source = 'SEQUENCE'``; playback is configured per
    node via :func:`setup_image_sequence_node`, since ``ImageUser`` lives on the
    node rather than on the image datablock.

    Frames are named ``<stem>_0001.png`` upwards because that is the numbering
    Blender's sequence resolver expects. Existing files are reused as-is, which is
    what makes the game-local cache worthwhile.
    """
    if not frames:
        raise ValueError("Cannot build an image sequence with no frames")

    cache_dir = get_frame_cache_dir(asset_root) / texture_path.parent
    os.makedirs(cache_dir, exist_ok=True)

    for index, frame in enumerate(frames):
        height, width, channels = frame.shape
        frame_path = cache_dir / f"{texture_path.stem}_{index + 1:04d}.png"
        if index == 0:
            first_frame_path = frame_path
        if frame_path.exists():
            continue
        save_png((frame.ravel() * 255).astype(np.uint8).tobytes(), width, height, channels, frame_path)

    image = bpy.data.images.load(first_frame_path.as_posix(), check_existing=True)
    image.source = 'SEQUENCE'
    image.alpha_mode = 'CHANNEL_PACKED'
    image['full_path'] = texture_path.as_posix().lower()
    image['frame_count'] = len(frames)
    logger.info(f"Cached {len(frames)} frames of {texture_path.as_posix()!r} in {cache_dir}")
    return image, len(frames)


def setup_image_sequence_node(texture_node: bpy.types.Node, frame_count: int, frame_rate: float = 0.0):
    """Configure a Image Texture node to play back a cached frame sequence.

    ``ImageUser`` is per-node, not per-image, so this must be applied to every
    node that shows the sequence. Source's ``AnimatedTexture`` proxy advances with
    ``frame = int($animatedtextureframerate * time) % numFrames``; Blender's
    sequence playback is locked to one image frame per scene frame, so a rate that
    differs from the scene FPS is approximated by resampling the frame list rather
    than by anything on the node.
    """
    image_user = texture_node.image_user
    image_user.frame_duration = frame_count
    image_user.frame_start = 1
    image_user.frame_offset = 0
    image_user.use_cyclic = True
    image_user.use_auto_refresh = True
    if frame_rate:
        texture_node['source_frame_rate'] = frame_rate


def create_texture_from_encoded_image(
        texture_path: TinyPath,
        data: bytes,
        extension: str,
        cache_identity: str = "",
):
    """Create an image from an already encoded PNG/JPEG/WEBP file, without decoding it in Python."""
    if bpy.context.scene.TextureCachePath != "":
        save_path = _variant_path(
            TinyPath(bpy.context.scene.TextureCachePath) / texture_path,
            extension,
            cache_identity,
        )
        os.makedirs(save_path.parent, exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(data)
        image = bpy.data.images.load(save_path.as_posix())
        logger.info(f"Save {texture_path.as_posix()!r} texture to disc: {save_path}")
    else:
        image = bpy.data.images.new(_variant_image_name(texture_path, cache_identity), width=1, height=1)
        image.pack(data=data, data_len=len(data))
        image.source = 'FILE'
        logger.info(f"Save {texture_path.as_posix()!r} texture to memory")
    image.alpha_mode = 'CHANNEL_PACKED'
    image['full_path'] = texture_path.as_posix().lower()
    image[CACHE_ID_PROPERTY] = cache_identity
    _add_texture(texture_path, image.name, cache_identity)
    return image


def create_and_cache_texture(
        texture_path: TinyPath,
        data: np.ndarray,
        is_hdr: bool = False,
        invert_y: bool = False,
        cache_identity: str = "",
):
    if invert_y and not is_hdr:
        data = data.copy()
        data[:, :, 1] = 1 - data[:, :, 1]
    height, width, channels = data.shape
    if is_hdr and channels == 4:
        # The native EXR writer assigns the interleaved values to channels in EXR (alphabetical) order: A, B, G, R.
        data = data[..., ::-1]
    data = np.ascontiguousarray(data, dtype=np.float32).ravel()

    if bpy.context.scene.TextureCachePath != "":
        save_path = _variant_path(
            TinyPath(bpy.context.scene.TextureCachePath) / texture_path,
            "exr" if is_hdr else "png",
            cache_identity,
        )
        os.makedirs(save_path.parent, exist_ok=True)

        if is_hdr:
            save_exr(data.tobytes(), width, height, channels, save_path)
        else:
            save_png((data * 255).astype(np.uint8).tobytes(), width, height, channels, save_path)
        posix_path = save_path.as_posix()
        image = bpy.data.images.load(posix_path)
        image.alpha_mode = 'CHANNEL_PACKED'
        logger.info(f"Save {texture_path.as_posix()!r} texture to disc: {save_path}")
    else:
        if is_hdr:
            image_data = encode_exr(data.tobytes(), width, height, channels)
        else:
            image_data = encode_png((data * 255).astype(np.uint8).tobytes(), width, height, channels)
        image = bpy.data.images.new(
            _variant_image_name(texture_path, cache_identity),
            width=1,
            height=1,
            float_buffer=is_hdr,
        )
        image.pack(data=image_data, data_len=len(image_data))
        image.source = 'FILE'
        image.alpha_mode = 'CHANNEL_PACKED'
        logger.info(f"Save {texture_path.as_posix()!r} texture to memory")
    image['full_path'] = texture_path.as_posix().lower()
    image[CACHE_ID_PROPERTY] = cache_identity
    _add_texture(texture_path, image.name, cache_identity)

    return image
