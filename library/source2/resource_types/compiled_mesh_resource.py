from ..compiled_resource import CompiledResource, DATA_BLOCK
from ..blocks.kv3_block import KVBlock


class CompiledMeshResource(CompiledResource):

    @property
    def data_block(self):
        return self.get_block(KVBlock, block_id=DATA_BLOCK)

    def get_name(self):
        return self.data_block['m_name']
