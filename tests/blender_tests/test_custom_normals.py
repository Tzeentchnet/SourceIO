"""Run with a Python environment providing bpy: python -m unittest ...test_custom_normals.

Custom normals must follow deformation. Model meshes are stored Y-up and turned upright by their armature;
normals written as a free `custom_normal` attribute stayed in mesh space and pointed sideways after that.
"""
import unittest

import bpy
import numpy as np

from SourceIO.blender_bindings.utils.fast_mesh import FastMesh


def evaluated_corner_normals(obj):
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    normals = np.zeros(len(mesh.loops) * 3, np.float32)
    mesh.corner_normals.foreach_get('vector', normals)
    evaluated.to_mesh_clear()
    return normals.reshape(-1, 3)


class CustomNormalTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_homefile(use_empty=True)

    def quad(self, domain):
        """A quad in the XZ plane with custom normals along its face normal (the Y axis), and a shape key that
        lays it flat (facing along Z)."""
        mesh = FastMesh.new('quad')
        vertices = np.array([(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)], np.float32)
        mesh.from_pydata(vertices, [], np.array([(0, 1, 2, 3)], np.uint32), shade_flat=False)
        count = 4 if domain == 'POINT' else len(mesh.loops)
        mesh.set_custom_normals(np.tile(tuple(mesh.polygons[0].normal), (count, 1)), domain)
        obj = bpy.data.objects.new('quad', mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.shape_key_add(name='Basis')
        flat = obj.shape_key_add(name='flat')
        for point, co in zip(flat.data, [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]):
            point.co = co
        flat.value = 0.0  # new shape keys start at 1
        return obj, flat

    def check_follows_deformation(self, domain):
        obj, flat = self.quad(domain)
        self.assertTrue(obj.data.has_custom_normals)
        before = evaluated_corner_normals(obj)
        face_normal = np.array(obj.data.polygons[0].normal)
        np.testing.assert_allclose(before, np.tile(face_normal, (len(before), 1)), atol=1e-4)

        flat.value = 1.0
        bpy.context.view_layer.update()
        after = evaluated_corner_normals(obj)
        # Laid flat, the quad faces down the Z axis; free normals would still point along Y.
        np.testing.assert_allclose(np.abs(after[:, 2]), 1.0, atol=1e-4)
        np.testing.assert_allclose(after[:, 1], 0.0, atol=1e-4)

    def test_point_normals_follow_deformation(self):
        self.check_follows_deformation('POINT')

    def test_corner_normals_follow_deformation(self):
        self.check_follows_deformation('CORNER')

    def test_no_free_normal_attribute(self):
        obj, _ = self.quad('POINT')
        attribute = obj.data.attributes.get('custom_normal')
        self.assertTrue(attribute is None or attribute.data_type != 'FLOAT_VECTOR')


if __name__ == '__main__':
    unittest.main()
