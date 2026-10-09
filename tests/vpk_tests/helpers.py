from __future__ import annotations

import struct
from collections.abc import Mapping
from pathlib import Path

VPK_SIGNATURE = 0x55AA1234
VPK_EMBEDDED_ARCHIVE = 0x7FFF


def build_vpk(
    files: Mapping[str, bytes],
    *,
    split: bool = False,
) -> tuple[bytes, dict[int, bytes]]:
    tree = bytearray()
    embedded_data = bytearray()
    chunks: dict[int, bytearray] = {}
    grouped: dict[str, dict[str, list[tuple[str, bytes]]]] = {}

    for path, content in files.items():
        directory, _, filename = path.replace("\\", "/").rpartition("/")
        stem, separator, extension = filename.rpartition(".")
        if not separator:
            stem, extension = filename, " "
        grouped.setdefault(extension, {}).setdefault(directory or " ", []).append((stem, content))

    for extension, directories in grouped.items():
        tree += extension.encode("utf8") + b"\0"
        for directory, entries in directories.items():
            tree += directory.encode("utf8") + b"\0"
            for stem, content in entries:
                tree += stem.encode("utf8") + b"\0"
                if split:
                    archive_index = 0
                    archive_data = chunks.setdefault(archive_index, bytearray())
                else:
                    archive_index = VPK_EMBEDDED_ARCHIVE
                    archive_data = embedded_data
                offset = len(archive_data)
                archive_data += content
                tree += struct.pack(
                    "<IHHIIH",
                    0,
                    0,
                    archive_index,
                    offset,
                    len(content),
                    0xFFFF,
                )
            tree += b"\0"
        tree += b"\0"
    tree += b"\0"

    directory = struct.pack("<III", VPK_SIGNATURE, 1, len(tree)) + tree + embedded_data
    return directory, {index: bytes(data) for index, data in chunks.items()}


def write_vpk(path: Path, files: Mapping[str, bytes], *, split: bool = False) -> Path:
    directory, chunks = build_vpk(files, split=split)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(directory)
    if split:
        name = path.name
        base = name[:-8] if name.casefold().endswith("_dir.vpk") else path.stem
        for archive_index, data in chunks.items():
            path.with_name(f"{base}_{archive_index:03d}.vpk").write_bytes(data)
    return path
