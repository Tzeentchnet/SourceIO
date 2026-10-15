"""Write a VTF out as files an image editor can open, plus what Valve's tools need to rebuild it.

For ``name`` and an output folder this writes:

* ``name.tga`` (or ``.png``): the full image; ``name_frameNNN`` per frame for an animated VTF. HDR textures are
  written as ``.exr`` whatever format was asked for, since 8-bit files would clip them.
* for a texture with a sprite sheet:

  * ``name.sht``, the sheet resource exactly as stored in the VTF. vtex attaches a ``.sht`` lying next to its
    input verbatim, so editing ``name.tga`` in place and running ``vtex name.tga`` rebuilds the texture with its
    original layout.
  * ``name_NNN.tga``, one per distinct sheet rectangle, and ``name.mks``, which lists them per sequence. To change
    frame sizes or timing, edit these and run ``mksheet name.mks`` first: it repacks the frames into a new
    ``name.tga`` and ``name.sht``, replacing both, so edit either the frames or the full image, not both.

* ``name.vtf.json``: the header, every resource and the decoded sheet, with each frame's pixel rectangle and
  frame file.

A sheet packed with ``packmode rgb+a`` has overlapping frames (RGB and alpha are packed independently) and does not
record which sequence uses which channels; the export flags it and recommends the ``name.sht`` route.

SourceIO's own VTF export cannot write a sheet resource, so a sheet texture is rebuilt with vtex. Checked with
TF2's mksheet and vtex on all 273 TF2 sheet textures: the rebuilt sheet has the same sequences, timing and frame
pixels.
"""
from __future__ import annotations

import base64
import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from ...shared.sprite_sheet import SpriteSheetImage, SpriteSheetMetadata
from ...utils.pylib.image import encode_exr, encode_png
from ...utils.pylib.vtf import load_vtf_texture_frames
from .metadata import VTFMetadata, read_vtf_metadata

IMAGE_FORMATS = ("tga", "png")
#: Treats UVs within this many texels of a pixel edge as on the edge. Valve inset sheet UVs by half a texel.
_EDGE_TOLERANCE = 1e-3


@dataclass(frozen=True, slots=True)
class EditableExport:
    images: tuple[Path, ...]
    frame_images: tuple[Path, ...]
    mks: Path | None
    sht: Path | None
    sidecar: Path
    warnings: tuple[str, ...]

    @property
    def written(self) -> tuple[Path, ...]:
        extra = tuple(path for path in (self.mks, self.sht) if path is not None)
        return self.images + self.frame_images + extra + (self.sidecar,)


def export_vtf_for_editing(
        vtf_data: bytes,
        output_dir: Path,
        name: str,
        image_format: str = "tga",
        overwrite: bool = False,
) -> EditableExport:
    """Decode a .vtf and write it with :func:`write_editable_texture`."""
    metadata = read_vtf_metadata(vtf_data)
    frames, width, height, is_float = load_vtf_texture_frames(vtf_data)
    dtype = np.float32 if is_float else np.uint8
    images = [np.frombuffer(frame, dtype=dtype).reshape(height, width, 4) for frame in frames]
    warnings = []
    if metadata.header.flags & 0x4000:  # TEXTUREFLAGS_ENVMAP
        warnings.append("cubemap: only the first face is written")
    if metadata.header.depth > 1:
        warnings.append("volume texture: only the first slice is written")
    return write_editable_texture(
        images, output_dir, name,
        metadata=metadata,
        image_format=image_format,
        overwrite=overwrite,
        hdr=bool(is_float),
        warnings=warnings,
    )


def write_editable_texture(
        images: Sequence[np.ndarray],
        output_dir: Path,
        name: str,
        *,
        metadata: VTFMetadata | dict[str, Any] | None = None,
        sheet: SpriteSheetMetadata | None = None,
        image_format: str = "tga",
        overwrite: bool = False,
        hdr: bool | None = None,
        warnings: Sequence[str] = (),
) -> EditableExport:
    """Write top-down RGBA ``images`` (one per animation frame) and their sheet for editing.

    ``uint8`` images are written as they are and ``float32`` images in [0, 1] are rounded to 8 bits. HDR images
    go to EXR; ``hdr=None`` treats float images with values outside [0, 1] as HDR. ``metadata`` may be a parsed
    :class:`VTFMetadata` or its ``to_dict()`` form; ``sheet`` defaults to the one it holds, and the ``.sht`` is
    written from its raw sheet resource.
    """
    if image_format not in IMAGE_FORMATS:
        raise ValueError(f"Unsupported image format {image_format!r}; expected one of {IMAGE_FORMATS}")
    if not images:
        raise ValueError("No image data to write")
    metadata_dict = metadata.to_dict() if isinstance(metadata, VTFMetadata) else dict(metadata or {})
    if sheet is None:
        if isinstance(metadata, VTFMetadata):
            sheet = metadata.sprite_sheet
        elif "sprite_sheet" in metadata_dict:
            sheet = SpriteSheetMetadata.from_dict(metadata_dict["sprite_sheet"])
    sheet_resource = sheet.raw_data if sheet is not None else b""
    if not sheet_resource and "sprite_sheet_resource" in metadata_dict:
        sheet_resource = base64.b64decode(metadata_dict["sprite_sheet_resource"])

    if hdr is None:
        hdr = any(_is_hdr(image) for image in images)
    extension = "exr" if hdr else image_format
    warnings = list(warnings)
    if hdr:
        warnings.append(f"HDR texture: wrote .exr instead of .{image_format}")
    height, width = images[0].shape[:2]
    output_dir = Path(output_dir)

    files: dict[Path, bytes] = {}
    image_paths = []
    for index, image in enumerate(images):
        suffix = "" if len(images) == 1 else f"_frame{index:03d}"
        path = output_dir / f"{name}{suffix}.{extension}"
        files[path] = _encode(image, extension)
        image_paths.append(path)

    frame_paths: list[Path] = []
    mks_path = None
    sht_path = None
    if sheet_resource:
        sht_path = output_dir / f"{name}.sht"
        files[sht_path] = sheet_resource
    if sheet is not None and sheet.sequences:
        if len(images) > 1:
            warnings.append("sheet frames are cut from the first animation frame")
        slices, frame_files = _slice_sheet(images[0], sheet, output_dir, name, extension, warnings)
        for path, pixels in slices.items():
            files[path] = _encode(pixels, extension)
            frame_paths.append(path)
        split_channels = _has_overlapping_frames(sheet, width, height)
        if split_channels:
            metadata_dict["sprite_sheet_split_channels"] = True
            warnings.append(
                "sheet frames overlap, so it was packed with 'packmode rgb+a': each frame image mixes the RGB of "
                f"one sequence with the alpha of another. Edit {name}.{extension} and keep {name}.sht instead of "
                "rebuilding with mksheet"
            )
        mks_path = output_dir / f"{name}.mks"
        files[mks_path] = _mks_text(sheet, frame_files, name, split_channels).encode("utf8")
        metadata_dict["sprite_sheet"] = _sheet_with_rects(sheet, frame_files, width, height)

    sidecar = output_dir / f"{name}.vtf.json"
    metadata_dict["editable_files"] = {
        "images": [path.name for path in image_paths],
        "frames": [path.name for path in frame_paths],
        "mks": mks_path.name if mks_path is not None else None,
        "sht": sht_path.name if sht_path is not None else None,
    }
    if warnings:
        metadata_dict["export_warnings"] = warnings
    files[sidecar] = json.dumps(metadata_dict, indent=2).encode("utf8")

    if not overwrite:
        existing = [path for path in files if path.exists()]
        if existing:
            raise FileExistsError(f"{len(existing)} output file(s) already exist, first: {existing[0]}")
    output_dir.mkdir(parents=True, exist_ok=True)
    for path, data in files.items():
        path.write_bytes(data)
    return EditableExport(tuple(image_paths), tuple(frame_paths), mks_path, sht_path, sidecar, tuple(warnings))


def sheet_image_pixel_rect(image: SpriteSheetImage, width: int, height: int) -> tuple[int, int, int, int]:
    """Pixel bounds ``(x0, y0, x1, y1)``, end exclusive, of a sheet rectangle.

    Valve's tools inset each rectangle by half a texel (a 128-pixel cell at the origin is stored as 0.5..127.5),
    while other writers store the cell edges; flooring the start and ceiling the end covers both.
    """
    (u0, v0), (u1, v1) = image.uncropped_min, image.uncropped_max
    x0 = max(0, math.floor(u0 * width + _EDGE_TOLERANCE))
    y0 = max(0, math.floor(v0 * height + _EDGE_TOLERANCE))
    x1 = min(width, math.ceil(u1 * width - _EDGE_TOLERANCE))
    y1 = min(height, math.ceil(v1 * height - _EDGE_TOLERANCE))
    return x0, y0, max(x0, x1), max(y0, y1)


def _slice_sheet(image, sheet, output_dir, name, extension, warnings):
    height, width = image.shape[:2]
    slices: dict[Path, np.ndarray] = {}
    by_rect: dict[tuple[int, int, int, int], Path] = {}
    frame_files: list[list[list[Path]]] = []
    for sequence in sheet.sequences:
        sequence_files = []
        for frame_index, frame in enumerate(sequence.frames):
            paths = []
            for sheet_image in frame.images:
                rect = sheet_image_pixel_rect(sheet_image, width, height)
                x0, y0, x1, y1 = rect
                if x1 <= x0 or y1 <= y0:
                    warnings.append(f"sequence {sequence.sequence_id} frame {frame_index}: empty rectangle skipped")
                    continue
                path = by_rect.get(rect)
                if path is None:
                    path = output_dir / f"{name}_{len(by_rect):03d}.{extension}"
                    by_rect[rect] = path
                    slices[path] = image[y0:y1, x0:x1]
                paths.append(path)
            sequence_files.append(paths)
        frame_files.append(sequence_files)
    return slices, frame_files


def _has_overlapping_frames(sheet: SpriteSheetMetadata, width: int, height: int) -> bool:
    """Whether two different frame rectangles share pixels.

    mksheet only does that under ``packmode rgb+a``, which packs the ``sequence-rgb`` frames into the color channels
    and the ``sequence-a`` frames into alpha independently. The sheet itself does not record which sequence is which.
    """
    rects = sorted({
        sheet_image_pixel_rect(image, width, height)
        for sequence in sheet.sequences
        for frame in sequence.frames
        for image in frame.images
    })
    for index, (x0, y0, x1, y1) in enumerate(rects):
        for other_x0, other_y0, other_x1, other_y1 in rects[index + 1:]:
            if other_x0 >= x1:
                break
            if other_x0 < x1 and x0 < other_x1 and other_y0 < y1 and y0 < other_y1:
                return True
    return False


def _mks_text(sheet: SpriteSheetMetadata, frame_files, name: str, split_channels: bool = False) -> str:
    lines = [
        f"// Sprite sheet of {name}.vtf, written by SourceIO.",
        f"// 'mksheet {name}.mks' packs these frames into {name}.tga and {name}.sht for vtex,",
        f"// replacing both. To keep the original layout, edit {name}.tga instead and skip mksheet.",
    ]
    if split_channels:
        lines += [
            "//",
            "// WARNING: the original was packed with 'packmode rgb+a' (its frames overlap). Each sequence below",
            "// was either 'sequence-rgb' or 'sequence-a', but the sheet does not record which, so these frame",
            "// images mix two layers. A rebuild from them keeps each sequence's frames but packs them",
            f"// without overlap, so the sheet grows; editing {name}.tga with {name}.sht keeps the original.",
        ]
    for sequence, sequence_files in zip(sheet.sequences, frame_files):
        lines.append("")
        lines.append(f"sequence {sequence.sequence_id}")
        if not sequence.clamp:
            lines.append("loop")
        for frame, paths in zip(sequence.frames, sequence_files):
            if paths:
                lines.append(f"frame {' '.join(path.name for path in paths)} {frame.display_time:g}")
    return "\n".join(lines) + "\n"


def _sheet_with_rects(sheet: SpriteSheetMetadata, frame_files, width: int, height: int) -> dict[str, Any]:
    data = sheet.to_dict()
    for sequence, sequence_data, sequence_files in zip(sheet.sequences, data["sequences"], frame_files):
        for frame, frame_data, paths in zip(sequence.frames, sequence_data["frames"], sequence_files):
            frame_data["files"] = [path.name for path in paths]
            for image, image_data in zip(frame.images, frame_data["images"]):
                image_data["pixel_rect"] = list(sheet_image_pixel_rect(image, width, height))
    return data


def _is_hdr(image: np.ndarray) -> bool:
    return image.dtype != np.uint8 and bool(np.any(image > 1.0) or np.any(image < 0.0))


def _to_uint8(image: np.ndarray) -> np.ndarray:
    if image.dtype == np.uint8:
        return image
    return np.rint(np.clip(image, 0.0, 1.0) * 255.0).astype(np.uint8)


def _encode(image: np.ndarray, extension: str) -> bytes:
    height, width, channels = image.shape
    if extension == "exr":
        # The native EXR writer assigns interleaved values in EXR channel order: A, B, G, R.
        data = np.ascontiguousarray(image.astype(np.float32)[..., ::-1])
        return bytes(encode_exr(data.tobytes(), width, height, channels))
    pixels = np.ascontiguousarray(_to_uint8(image))
    if extension == "png":
        return bytes(encode_png(pixels.tobytes(), width, height, channels))
    return _encode_tga(pixels)


def _encode_tga(pixels: np.ndarray) -> bytes:
    """Uncompressed 32-bit TGA, stored bottom-up, the layout every TGA reader including vtex accepts."""
    height, width = pixels.shape[:2]
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, width, height, 32, 0x08)
    bgra = pixels[::-1, :, [2, 1, 0, 3]]
    return header + np.ascontiguousarray(bgra).tobytes()
