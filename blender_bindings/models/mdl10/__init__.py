from typing import Optional

from ..model_tags import register_model_importer
from ...operators.import_settings_base import ModelOptions
from ...shared.model_container import ModelContainer
from ....library.shared.content_manager import ContentManager
from ....library.utils import Buffer
from ....library.utils.tiny_path import TinyPath
from .import_mdl import import_model


@register_model_importer(b"IDST", 10)
def import_mdl10(model_path: TinyPath, buffer: Buffer,
                 content_manager: ContentManager, options: ModelOptions) -> ModelContainer | None:
    texture_mdl = content_manager.find_file(model_path.with_name(model_path.stem + "t.mdl"))

    return import_model(buffer, texture_mdl, options)
