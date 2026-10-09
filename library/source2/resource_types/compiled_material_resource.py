from ..compiled_resource import CompiledResource, DATA_BLOCK
from ..blocks.kv3_block import KVBlock
from ..blocks.texture_data import TextureImportSettings
from ..compiled_shader import CompiledShaderMetadata
from ..interfaces import Maturity, ResourceCapabilities, ResourceKind
from ..materials import MaterialSemantics


class CompiledMaterialResource(CompiledResource):
    resource_kind = ResourceKind.MATERIAL
    declared_capabilities = ResourceCapabilities(
        read=Maturity.STABLE,
        extract=Maturity.STABLE,
        render=Maturity.PARTIAL,
    )

    @property
    def data_block(self):
        return self.get_block(KVBlock, block_name="DATA")

    def get_used_textures(self):
        data = self.data_block
        used_textures = {}
        for texture in data['m_textureParams']:
            used_textures[texture['m_name']] = texture['m_pValue']
        return used_textures

    @property
    def texture_import_settings(self) -> TextureImportSettings:
        return getattr(self, "_texture_import_settings", TextureImportSettings())

    @texture_import_settings.setter
    def texture_import_settings(self, settings: TextureImportSettings):
        if not isinstance(settings, TextureImportSettings):
            raise TypeError(f"Expected TextureImportSettings, got {type(settings).__name__}")
        self._texture_import_settings = settings

    @property
    def shader_metadata(self) -> CompiledShaderMetadata | None:
        return getattr(self, "_shader_metadata", None)

    @shader_metadata.setter
    def shader_metadata(self, metadata: CompiledShaderMetadata | None):
        if metadata is not None and not isinstance(metadata, CompiledShaderMetadata):
            raise TypeError(f"Expected CompiledShaderMetadata, got {type(metadata).__name__}")
        self._shader_metadata = metadata

    def get_material_semantics(
            self,
            shader_metadata: CompiledShaderMetadata | None = None,
    ) -> MaterialSemantics:
        return MaterialSemantics.from_resource(self, shader_metadata or self.shader_metadata)

    def get_int_property(self, prop_name, default=None):
        data = self.get_block(KVBlock, block_name='DATA')
        return self._get_prop(prop_name, data['m_intParams'], 'm_nValue', default)

    def get_float_property(self, prop_name, default=None):
        data = self.get_block(KVBlock, block_name='DATA')
        return self._get_prop(prop_name, data['m_floatParams'], 'm_flValue', default)

    def get_vector_property(self, prop_name, default=None):
        data = self.get_block(KVBlock, block_name='DATA')
        return self._get_prop(prop_name, data['m_vectorParams'], 'm_value', default)

    def get_texture_property(self, prop_name, default=None):
        data = self.get_block(KVBlock, block_name='DATA')
        return self._get_prop(prop_name, data['m_textureParams'], 'm_pValue', default)

    def get_dynamic_property(self, prop_name, default=None):
        data = self.get_block(KVBlock, block_name='DATA')
        return self._get_prop(prop_name, data['m_dynamicParams'], 'error', default)

    def get_dynamic_texture(self, prop_name, default=None):
        data = self.get_block(KVBlock, block_name='DATA')
        return self._get_prop(prop_name, data['m_dynamicTextureParams'], 'error', default)

    @staticmethod
    def _get_prop(prop_name: str, prop_array: list[dict], prop_value_name, default=None):
        for prop in prop_array:
            if prop['m_name'] == prop_name:
                return prop[prop_value_name]
        return default
