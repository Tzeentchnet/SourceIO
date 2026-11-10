"""Run inside Blender with SourceIO registered: unittest ...test_source2_blend_indices."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from SourceIO.blender_bindings.source2 import vmdl_loader


class Source2BlendIndexTests(unittest.TestCase):
    def test_signed_32_bit_indices_without_weights_decode_as_eight_uint16(self):
        decoded_indices = np.arange(8, dtype=np.uint16).reshape(1, 8)
        used_vertices = np.zeros(1, dtype=[("BLENDINDICES", np.int32, (4,))])
        used_vertices["BLENDINDICES"] = decoded_indices.view(np.int32)

        model_data = {
            "m_modelSkeleton": {"m_boneName": [f"bone_{index}" for index in range(8)]},
            "m_remappingTable": np.arange(8, dtype=np.uint32),
            "m_remappingTableStarts": [0],
        }

        class ModelResource:
            @staticmethod
            def get_block(_block_type, block_name):
                return model_data if block_name == "DATA" else {}

        class VertexBuffer:
            @staticmethod
            def has_attribute(name):
                return name == "BLENDINDICES"

            @staticmethod
            def get_attribute(_name):
                return SimpleNamespace(format=vmdl_loader.DxgiFormat.R32G32B32A32_SINT)

        mesh = SimpleNamespace(vertex_groups=SimpleNamespace(new=lambda name: name))
        with patch.object(vmdl_loader, "set_vertex_weights") as set_vertex_weights:
            vmdl_loader._add_vertex_groups(ModelResource(), VertexBuffer(), 0, used_vertices, mesh)

        groups, actual_indices, actual_weights = set_vertex_weights.call_args.args
        self.assertEqual(groups, [f"bone_{index}" for index in range(8)])
        np.testing.assert_array_equal(actual_indices, decoded_indices.astype(np.uint32))
        np.testing.assert_array_equal(actual_weights, np.ones((1, 8), dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
