from typing import Optional

from ..model_tags import register_model_importer
from ...operators.import_settings_base import ModelOptions
from ...shared.model_container import ModelContainer
from ....library.shared.content_manager.provider import ContentProvider
from ....library.utils import Buffer
from ....library.utils.tiny_path import TinyPath
from .import_mdl import import_model


@register_model_importer(b"IDST", 4)
def import_mdl4(model_path: TinyPath, buffer: Buffer,
                content_manager: ContentProvider, options: ModelOptions) -> ModelContainer | None:
    return import_model(model_path.stem, buffer, options)
