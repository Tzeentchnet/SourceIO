from dataclasses import field, dataclass
from typing import Optional, Callable

from ..operators.import_settings_base import ModelOptions
from ..shared.model_container import ModelContainer
from ...library.shared.app_id import SteamAppId
from ...library.shared.content_manager import ContentManager
from ...library.utils import Buffer
from ...library.utils.tiny_path import TinyPath


@dataclass(slots=True)
class ModelImporterTag:
    ident: bytes
    version: int
    steam_id: Optional[SteamAppId] = field(default=None)


ModelImportFunction = Callable[[TinyPath, Buffer, ContentManager, ModelOptions], ModelContainer]
MODEL_HANDLERS: list[tuple[ModelImporterTag, ModelImportFunction]] = []


def register_model_importer(ident: bytes, version: int,
                            steam_id: Optional[SteamAppId] = None):
    def inner(func: Callable[[TinyPath, Buffer, ContentManager, ModelOptions], ModelContainer]) -> ModelImportFunction:
        MODEL_HANDLERS.append((ModelImporterTag(ident, version, steam_id), func))
        return func

    return inner


def choose_model_importer(ident: bytes, version: int, steam_id: Optional[int] = None) -> Optional[ModelImportFunction]:
    best_match = None
    best_score = 0  # Start with a score lower than any possible match score

    for handler_tag, handler_func in MODEL_HANDLERS:
        score = 0
        # Check ident and version match
        if handler_tag.ident == ident and handler_tag.version == version:
            score += 2  # Base score for ident and version match

            # If steam_id is provided and matches, increase the score
            if steam_id is not None and handler_tag.steam_id == steam_id:
                score += 1  # Additional score for steam_id match

        # Update best match if this handler has a higher score
        if score > best_score:
            best_score = score
            best_match = handler_func

    return best_match
