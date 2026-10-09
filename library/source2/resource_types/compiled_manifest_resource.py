from ..blocks.manifest import ManifestBlock
from ..compiled_resource import CompiledResource, DATA_BLOCK


class CompiledManifestResource(CompiledResource):

    @property
    def data_block(self):
        return self.get_block(ManifestBlock, block_id=DATA_BLOCK)
