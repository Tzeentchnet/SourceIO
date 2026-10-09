from ..blocks.manifest import ManifestBlock
from ..capabilities import READ_EXTRACT_CAPABILITIES
from ..compiled_resource import CompiledResource, DATA_BLOCK
from ..interfaces import ResourceKind


class CompiledManifestResource(CompiledResource):
    resource_kind = ResourceKind.RESOURCE_MANIFEST
    declared_capabilities = READ_EXTRACT_CAPABILITIES

    @property
    def data_block(self):
        return self.get_block(ManifestBlock, block_id=DATA_BLOCK)
