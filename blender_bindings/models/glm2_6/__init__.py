from .import_glm import import_model
from ..model_tags import register_model_importer
from ...operators.import_settings_base import ModelOptions
from ...shared.model_container import ModelContainer
from ....library.shared.content_manager import ContentManager
from ....library.utils import TinyPath, Buffer


@register_model_importer(b"2LGM", 6)
def import_mdl4(model_path: TinyPath, buffer: Buffer,
                content_manager: ContentManager, options: ModelOptions) -> ModelContainer | None:
    return import_model(model_path.parent.stem, buffer, options, content_manager)
