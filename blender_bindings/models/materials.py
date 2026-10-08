"""Material path resolution shared by Source 1 material and mesh import."""
import posixpath

from SourceIO.library.utils.tiny_path import TinyPath
from SourceIO.library.shared.content_manager import ContentManager


def resolve_model_material(content_manager: ContentManager, mdl, material_name: str) -> TinyPath | None:
    """Resolve with existence checks; cache paths and misses for this import."""
    paths = getattr(mdl, '_resolved_material_paths', None)
    if paths is None:
        paths = mdl._resolved_material_paths = {}
    if material_name in paths:
        return paths[material_name]

    # The bare name may already contain its full path. Deduplicate candidates
    # because MDLs can also contain empty or repeated material search paths.
    candidates = [_normalize(material_name)]
    for directory in mdl.materials_paths:
        if not directory:
            continue
        if not TinyPath(directory).is_absolute():
            # Like the engine, concatenate and then resolve "..": TF2's spy uses "/../../effects/invulnfx_red"
            candidates.append(_normalize(directory + "/" + material_name))
    seen = set()
    for path in filter(None, candidates):
        candidate = 'materials' / TinyPath(path.as_posix() + '.vmt')
        key = candidate.as_posix().casefold()
        if key in seen:
            continue
        seen.add(key)
        if content_manager.check(candidate):
            paths[material_name] = path
            return path
    paths[material_name] = None
    return None


def _normalize(path: str) -> TinyPath | None:
    path = posixpath.normpath(path.replace("\\", "/")).lstrip("/")
    if path in ("", ".") or path.startswith("../"):
        return None
    return TinyPath(path)


def get_model_material_names(content_manager: ContentManager, mdl):
    """Reuse material import's resolution results without reopening any files."""
    names = {}
    for material in mdl.materials:
        paths = getattr(mdl, '_resolved_material_paths', {})
        if material.name not in paths:
            resolve_model_material(content_manager, mdl, material.name)
        path = mdl._resolved_material_paths[material.name]
        names[material.name] = path.as_posix().lstrip('/') if path is not None else material.name
    return names
