from typing import Iterator, Optional

from ...shared.content_manager import ContentManager
from ..blocks.kv3_block import KVBlock, custom_type_kvblock
from ...utils import MemoryBuffer
from ..keyvalues3.types import Object
from ..utils.entity_keyvalues import EntityKeyValues
from ..compiled_resource import CompiledResource, DATA_BLOCK
from ...utils.perf_sampler import timed

from ...utils.tiny_path import TinyPath


class CompiledEntityLumpResource(CompiledResource):
    @property
    def data_block(self):
        return self.get_block(KVBlock, block_id=DATA_BLOCK)

    def get_child_lumps(self, content_manager: ContentManager):
        for child_lump in self.data_block["m_childLumps"]:
            yield self.get_child_resource(child_lump, content_manager, CompiledEntityLumpResource)


    def get_entities(self) -> Iterator[Object]:
        for entity_key_values in self.data_block["m_entityKeyValues"]:
            if "m_keyValuesData" in entity_key_values and len(entity_key_values["m_keyValuesData"]):
                buffer = MemoryBuffer(entity_key_values["m_keyValuesData"])
                yield EntityKeyValues.from_buffer(buffer)
            elif "keyValue3Data" in entity_key_values:
                yield entity_key_values["keyValue3Data"]
            elif "keyValues3Data" in entity_key_values:
                yield entity_key_values["keyValues3Data"]


class CompiledWorldNodeResource(CompiledResource):
    @property
    def data_block(self):
        return self.get_block(custom_type_kvblock("WorldNode_t"), block_id=DATA_BLOCK)

    def get_scene_objects(self) -> list[Object]:
        return self.data_block["m_sceneObjects"]

    def get_aggregate_scene_objects(self) -> list[Object]:
        return self.data_block.get("m_aggregateSceneObjects", [])


class CompiledMapResource(CompiledResource):

    def get_worldnode(self, node_group_prefix: str, content_manager: ContentManager) \
            -> Optional[CompiledWorldNodeResource]:
        world_node = self.get_child_resource(TinyPath(node_group_prefix + ".vwnod").as_posix(), content_manager,
                                             CompiledWorldNodeResource)
        if world_node is not None:
            return world_node

        buffer = content_manager.find_file(TinyPath(node_group_prefix + ".vwnod_c"))
        if not buffer:
            return None
        return CompiledWorldNodeResource.from_buffer(buffer, TinyPath(node_group_prefix + ".vwnod_c"))

    def get_resource_provenance(self, *, resolver=None, recursive: bool = False):
        from ..provenance import resource_provenance

        return resource_provenance(self, resolver=resolver, recursive=recursive, asset_kind="map")

    def get_import_provenance(self, *, resolver=None, recursive: bool = False):
        from ..provenance import import_provenance

        return import_provenance(self, asset_kind="map", resolver=resolver, recursive=recursive)

    def to_hammer_document(self, world_resource: "CompiledWorldResource", content_manager: ContentManager,
                           *, report=None):
        from ..export.map_reconstruction import hammer_document_from_compiled

        return hammer_document_from_compiled(
            self,
            world_resource,
            content_manager,
            report=report,
        )


class CompiledWorldResource(CompiledResource):
    @property
    def data_block(self):
        return self.get_block(custom_type_kvblock("World_t"), block_id=DATA_BLOCK)

    def get_worldnode_prefixes(self) -> Iterator[str]:
        for world_node_group in self.data_block['m_worldNodes']:
            yield TinyPath(world_node_group['m_worldNodePrefix']).as_posix()

    def get_resource_provenance(self, *, resolver=None, recursive: bool = False):
        from ..provenance import resource_provenance

        return resource_provenance(self, resolver=resolver, recursive=recursive, asset_kind="world")

    def get_import_provenance(self, *, resolver=None, recursive: bool = False):
        from ..provenance import import_provenance

        return import_provenance(self, asset_kind="world", resolver=resolver, recursive=recursive)

    def to_hammer_document(self, map_resource: CompiledMapResource, content_manager: ContentManager,
                           *, report=None):
        from ..export.map_reconstruction import hammer_document_from_compiled

        return hammer_document_from_compiled(
            map_resource,
            self,
            content_manager,
            report=report,
        )
