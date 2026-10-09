from ..compiled_resource import CompiledResource, DATA_BLOCK
from ..blocks.kv3_block import KVBlock


class CompiledMeshResource(CompiledResource):

    @property
    def data_block(self):
        return self.get_block(KVBlock, block_id=DATA_BLOCK)

    def get_name(self):
        return self.data_block['m_name']

    def get_resource_provenance(self, *, resolver=None, recursive: bool = False):
        from ..provenance import resource_provenance

        return resource_provenance(self, resolver=resolver, recursive=recursive, asset_kind="mesh")

    def get_import_provenance(self, *, resolver=None, recursive: bool = False):
        from ..provenance import import_provenance

        return import_provenance(self, asset_kind="mesh", resolver=resolver, recursive=recursive)

    def to_static_meshes(self, *, report=None):
        from ..export.render_mesh import decode_compiled_mesh

        return decode_compiled_mesh(self, report=report)
