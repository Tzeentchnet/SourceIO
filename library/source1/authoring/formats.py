from __future__ import annotations

import json
import math

from ...source2.authoring import AuthoringMaterial, MaterialAlphaMode, TextureRole
from ...source2.export import ModelDocument
from ...source2.workshop import ContentRelativePath


def _float(value: float) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Source 1 authored numeric values must be finite")
    text = format(number, ".9g")
    return "0" if text == "-0" else text


def _quoted(value: str) -> str:
    if any(ord(character) < 32 for character in value):
        raise ValueError("Source 1 authored strings cannot contain control characters")
    return json.dumps(value, ensure_ascii=True)


def _texture_reference(path: ContentRelativePath) -> str:
    if not path.parts or path.parts[0].casefold() != "materialsrc":
        raise ValueError("Source texture paths must be below materialsrc")
    parts = list(path.parts[1:])
    suffix = ContentRelativePath("/".join(parts)).suffix
    if not suffix:
        raise ValueError("Source texture paths must have a file extension")
    parts[-1] = parts[-1][:-len(suffix)]
    return "/".join(parts)


def serialize_smd(
        document: ModelDocument,
        material_names: set[str] | frozenset[str],
) -> str:
    lines = [
        "version 1",
        "nodes",
        '  0 "root" -1',
        "end",
        "skeleton",
        "time 0",
        "  0 0 0 0 0 0 0",
        "end",
        "triangles",
    ]
    for mesh in sorted(document.meshes, key=lambda item: (item.name.casefold(), item.name)):
        for face in mesh.faces:
            if len(face.vertices) != 3:
                raise ValueError(
                    f"Source 1 SMD export requires triangles: {mesh.name}"
                )
            if face.material not in material_names:
                raise ValueError(
                    f"Source 1 SMD face references unknown material: {face.material}"
                )
            if any(ord(character) < 32 for character in face.material):
                raise ValueError("Source 1 SMD material names cannot contain control characters")
            lines.append(face.material)
            for vertex_index in reversed(face.vertices):
                vertex = mesh.vertices[vertex_index]
                normal = vertex.corner.normal
                if normal is None:
                    raise ValueError(
                        f"Source 1 SMD vertex has no normal: {mesh.name}"
                    )
                if not vertex.corner.texcoords:
                    raise ValueError(
                        f"Source 1 SMD vertex has no UV map: {mesh.name}"
                    )
                values = (
                    *vertex.position,
                    *normal,
                    *vertex.corner.texcoords[0],
                )
                lines.append("  0 " + " ".join(_float(value) for value in values))
    lines.extend(("end", ""))
    return "\n".join(lines)


def serialize_qc(
        model_path: ContentRelativePath,
        smd_name: str,
        material_directory: ContentRelativePath,
        *,
        surface_property: str = "default",
) -> str:
    if model_path.parts[0].casefold() != "models":
        raise ValueError("Compiled Source 1 model paths must be below models")
    if material_directory.parts[0].casefold() != "materials":
        raise ValueError("Source 1 material directories must be below materials")
    model_reference = "/".join(model_path.parts[1:])
    material_reference = "/".join(material_directory.parts[1:])
    return "\n".join((
        f"$modelname {_quoted(model_reference)}",
        f"$body {_quoted('body')} {_quoted(smd_name)}",
        "$staticprop",
        f"$surfaceprop {_quoted(surface_property)}",
        f"$cdmaterials {_quoted(material_reference)}",
        f"$sequence {_quoted('idle')} {_quoted(smd_name)} fps 1",
        "",
    ))


def serialize_vmt(
        material: AuthoringMaterial,
        base_texture: ContentRelativePath,
        *,
        normal_texture: ContentRelativePath | None = None,
) -> str:
    lines = [
        _quoted("VertexLitGeneric"),
        "{",
        f"    {_quoted('$basetexture')} {_quoted(_texture_reference(base_texture))}",
    ]
    if normal_texture is not None:
        lines.append(
            f"    {_quoted('$bumpmap')} {_quoted(_texture_reference(normal_texture))}"
        )
    tint = material.base_color[:3]
    if tint != (1.0, 1.0, 1.0):
        lines.append(
            f"    {_quoted('$color2')} {_quoted('[' + ' '.join(_float(value) for value in tint) + ']')}"
        )
    alpha = material.base_color[3]
    if alpha != 1.0:
        lines.append(f"    {_quoted('$alpha')} {_quoted(_float(alpha))}")
    if material.alpha_mode == MaterialAlphaMode.MASK:
        lines.extend((
            f"    {_quoted('$alphatest')} {_quoted('1')}",
            f"    {_quoted('$alphatestreference')} {_quoted(_float(material.alpha_cutoff))}",
        ))
    elif material.alpha_mode == MaterialAlphaMode.BLEND:
        lines.append(f"    {_quoted('$translucent')} {_quoted('1')}")
    if material.double_sided:
        lines.append(f"    {_quoted('$nocull')} {_quoted('1')}")
    lines.extend(("}", ""))
    return "\n".join(lines)


def serialize_vtex_settings(
        role: TextureRole,
        *,
        alpha_mode: MaterialAlphaMode = MaterialAlphaMode.OPAQUE,
) -> str:
    if role == TextureRole.BASE_COLOR:
        lines = [
            "numchannels 4"
            if alpha_mode != MaterialAlphaMode.OPAQUE
            else "numchannels 3"
        ]
        if alpha_mode != MaterialAlphaMode.OPAQUE:
            lines.append("dxt5 1")
    elif role == TextureRole.NORMAL:
        lines = [
            "normal 1",
            "numchannels 3",
        ]
    else:
        raise ValueError(f"Source 1 VTEX settings do not support {role.value} textures")
    return "\n".join((*lines, ""))
