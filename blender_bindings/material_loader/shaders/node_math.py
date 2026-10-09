"""Shader node arithmetic written as Python expressions.

``NodeMath(tree)`` wraps sockets and constants in ``Scalar`` and ``Vector`` values whose operators add Math and
Vector Math nodes to the tree, so a shader formula can be written as it reads:
``m.saturate(0.5 + difference / (2.0 * softness))``. Operations on constants fold to constants, so a parameter
that is known when the material is built adds no node. ``node_group`` builds a cached node group from such a
formula.
"""
import math
from typing import Callable

import bpy


class Scalar:
    def __init__(self, m: "NodeMath", socket=None, const: float = 0.0):
        self.m, self.socket, self.const = m, socket, float(const)

    @property
    def is_const(self):
        return self.socket is None

    def _op(self, operation, other, reverse=False):
        if isinstance(other, Vector) or (isinstance(other, tuple) and len(other) == 3):
            vector = self.m.vector(other)
            return self.m.vector(self)._op(operation, vector, reverse)
        a, b = (other, self) if reverse else (self, other)
        return self.m.math(operation, a, b)

    def __add__(self, other): return self._op('ADD', other)
    def __radd__(self, other): return self._op('ADD', other, True)
    def __sub__(self, other): return self._op('SUBTRACT', other)
    def __rsub__(self, other): return self._op('SUBTRACT', other, True)
    def __mul__(self, other): return self._op('MULTIPLY', other)
    def __rmul__(self, other): return self._op('MULTIPLY', other, True)
    def __truediv__(self, other): return self._op('DIVIDE', other)
    def __rtruediv__(self, other): return self._op('DIVIDE', other, True)
    def __neg__(self): return self.m.math('MULTIPLY', self, -1.0)


class Vector:
    def __init__(self, m: "NodeMath", socket=None, const=(0.0, 0.0, 0.0)):
        self.m, self.socket, self.const = m, socket, tuple(float(c) for c in const)
        self._split = None

    @property
    def is_const(self):
        return self.socket is None

    def _op(self, operation, other, reverse=False):
        if isinstance(other, Scalar) or isinstance(other, (int, float)):
            other = self.m.scalar(other)
            if operation == 'MULTIPLY':
                return self.m.vmath('SCALE', self, other)
            if operation == 'DIVIDE' and not reverse:
                return self.m.vmath('SCALE', self, 1.0 / other)
            other = self.m.vector(other)
        a, b = (other, self) if reverse else (self, other)
        return self.m.vmath(operation, a, b)

    def __add__(self, other): return self._op('ADD', other)
    def __radd__(self, other): return self._op('ADD', other, True)
    def __sub__(self, other): return self._op('SUBTRACT', other)
    def __rsub__(self, other): return self._op('SUBTRACT', other, True)
    def __mul__(self, other): return self._op('MULTIPLY', other)
    def __rmul__(self, other): return self._op('MULTIPLY', other, True)
    def __truediv__(self, other): return self._op('DIVIDE', other)
    def __rtruediv__(self, other): return self._op('DIVIDE', other, True)
    def __neg__(self): return self._op('MULTIPLY', -1.0)

    def _component(self, index):
        if self.is_const:
            return Scalar(self.m, const=self.const[index])
        if self._split is None:
            self._split = self.m.node('ShaderNodeSeparateXYZ')
            self.m.link(self.socket, self._split.inputs[0])
        return Scalar(self.m, self._split.outputs[index])

    @property
    def x(self): return self._component(0)

    @property
    def y(self): return self._component(1)

    @property
    def z(self): return self._component(2)


_FOLD = {
    'ADD': lambda a, b: a + b, 'SUBTRACT': lambda a, b: a - b, 'MULTIPLY': lambda a, b: a * b,
    'DIVIDE': lambda a, b: a / b if b != 0 else 0.0, 'MINIMUM': min, 'MAXIMUM': max,
    'POWER': lambda a, b: a ** b if a > 0 or float(b).is_integer() else 0.0, 'ABSOLUTE': lambda a: abs(a),
    'GREATER_THAN': lambda a, b: float(a > b), 'LESS_THAN': lambda a, b: float(a < b),
    'SINE': math.sin, 'COSINE': math.cos, 'SQRT': lambda a: math.sqrt(a) if a > 0 else 0.0,
    'MULTIPLY_ADD': lambda a, b, c: a * b + c,
}


class NodeMath:
    def __init__(self, tree: bpy.types.NodeTree, new_node: Callable[[str], bpy.types.Node] = None):
        self.tree = tree
        self._new_node = new_node or tree.nodes.new

    def node(self, idname: str) -> bpy.types.Node:
        return self._new_node(idname)

    def link(self, output, input_socket):
        self.tree.links.new(output, input_socket)

    def scalar(self, value) -> Scalar:
        if isinstance(value, Scalar):
            return value
        if isinstance(value, Vector):
            raise TypeError("a vector where a scalar was expected")
        if isinstance(value, bpy.types.NodeSocket):
            return Scalar(self, value)
        return Scalar(self, const=value)

    def vector(self, value) -> Vector:
        if isinstance(value, Vector):
            return value
        if isinstance(value, Scalar):
            if value.is_const:
                return Vector(self, const=(value.const,) * 3)
            return self.combine(value, value, value)
        if isinstance(value, bpy.types.NodeSocket):
            return Vector(self, value)
        if isinstance(value, (int, float)):
            return Vector(self, const=(float(value),) * 3)
        return Vector(self, const=tuple(value)[:3])

    def set(self, input_socket, value):
        """Link a value into an input socket, or set its default for a constant."""
        if isinstance(value, bpy.types.NodeSocket):
            self.link(value, input_socket)
            return
        if isinstance(value, (Scalar, Vector)) and not value.is_const:
            self.link(value.socket, input_socket)
            return
        if isinstance(value, Vector) or (isinstance(value, tuple) and len(value) >= 3):
            const = value.const if isinstance(value, Vector) else tuple(value)
            size = len(input_socket.default_value)
            input_socket.default_value = (tuple(const[:3]) + (1.0,))[:size] if size >= 3 else const[:size]
        else:
            input_socket.default_value = self.scalar(value).const

    def math(self, operation: str, *operands, clamp: bool = False) -> Scalar:
        values = [self.scalar(operand) for operand in operands]
        if all(value.is_const for value in values) and operation in _FOLD:
            result = _FOLD[operation](*(value.const for value in values))
            return Scalar(self, const=min(max(result, 0.0), 1.0) if clamp else result)
        if not clamp and len(values) == 2:
            a, b = values
            if operation == 'MULTIPLY':
                for one, other in ((a, b), (b, a)):
                    if one.is_const and one.const == 1.0:
                        return other
                    if one.is_const and one.const == 0.0:
                        return Scalar(self, const=0.0)
            if operation == 'ADD' and a.is_const and a.const == 0.0:
                return b
            if operation in ('ADD', 'SUBTRACT') and b.is_const and b.const == 0.0:
                return a
            if operation == 'DIVIDE' and b.is_const and b.const == 1.0:
                return a
        node = self.node('ShaderNodeMath')
        node.operation = operation
        node.use_clamp = clamp
        for index, value in enumerate(values):
            self.set(node.inputs[index], value)
        return Scalar(self, node.outputs[0])

    def vmath(self, operation: str, *operands):
        """Vector Math; DOT_PRODUCT and LENGTH give a Scalar. SCALE takes a vector and a scalar."""
        if operation == 'SCALE':
            vector, scale = self.vector(operands[0]), self.scalar(operands[1])
            if scale.is_const and scale.const == 1.0:
                return vector
            if scale.is_const and vector.is_const:
                return Vector(self, const=tuple(c * scale.const for c in vector.const))
        elif operation in ('ADD', 'SUBTRACT', 'MULTIPLY') and len(operands) == 2:
            a, b = self.vector(operands[0]), self.vector(operands[1])
            if a.is_const and b.is_const:
                fold = {'ADD': lambda p, q: p + q, 'SUBTRACT': lambda p, q: p - q, 'MULTIPLY': lambda p, q: p * q}
                return Vector(self, const=tuple(fold[operation](p, q) for p, q in zip(a.const, b.const)))
            neutral = (1.0, 1.0, 1.0) if operation == 'MULTIPLY' else (0.0, 0.0, 0.0)
            if b.is_const and b.const == neutral:
                return a
            if a.is_const and a.const == neutral and operation != 'SUBTRACT':
                return b
        node = self.node('ShaderNodeVectorMath')
        node.operation = operation
        if operation == 'SCALE':
            self.set(node.inputs[0], self.vector(operands[0]))
            self.set(node.inputs['Scale'], self.scalar(operands[1]))
        else:
            for index, operand in enumerate(operands):
                self.set(node.inputs[index], self.vector(operand))
        if operation in ('DOT_PRODUCT', 'LENGTH', 'DISTANCE'):
            return Scalar(self, node.outputs['Value'])
        return Vector(self, node.outputs['Vector'])

    def combine(self, x, y, z) -> Vector:
        values = [self.scalar(v) for v in (x, y, z)]
        if all(value.is_const for value in values):
            return Vector(self, const=tuple(value.const for value in values))
        node = self.node('ShaderNodeCombineXYZ')
        for index, value in enumerate(values):
            self.set(node.inputs[index], value)
        return Vector(self, node.outputs[0])

    # Scalar functions
    def min(self, a, b): return self.math('MINIMUM', a, b)
    def max(self, a, b): return self.math('MAXIMUM', a, b)
    def abs(self, a): return self.math('ABSOLUTE', a)
    def pow(self, a, b): return self.math('POWER', a, b)
    def sin(self, a): return self.math('SINE', a)
    def cos(self, a): return self.math('COSINE', a)
    def greater(self, a, b): return self.math('GREATER_THAN', a, b)
    def less(self, a, b): return self.math('LESS_THAN', a, b)

    def saturate(self, a) -> Scalar:
        value = self.scalar(a)
        if value.is_const:
            return Scalar(self, const=min(max(value.const, 0.0), 1.0))
        node = self.node('ShaderNodeClamp')
        self.set(node.inputs['Value'], value)
        return Scalar(self, node.outputs[0])

    def clamp(self, a, low, high) -> Scalar:
        return self.min(self.max(a, low), high)

    def lerp(self, a, b, factor):
        """a + (b - a)·factor for scalars or vectors (a scalar factor)."""
        factor = self.scalar(factor)
        if factor.is_const and factor.const in (0.0, 1.0):
            chosen = a if factor.const == 0.0 else b
            if any(isinstance(v, Vector) or (isinstance(v, tuple) and len(v) == 3) for v in (a, b)):
                return self.vector(chosen)
            return self.scalar(chosen)
        if any(isinstance(v, Vector) or (isinstance(v, tuple) and len(v) == 3) for v in (a, b)):
            a, b = self.vector(a), self.vector(b)
            return a + (b - a) * self.scalar(factor)
        a, b = self.scalar(a), self.scalar(b)
        return a + (b - a) * factor

    def smoothstep(self, edge0, edge1, x) -> Scalar:
        """GLSL smoothstep; a zero-width range gives 0 below the edge and 1 above it (Math divides by zero to 0)."""
        t = self.saturate((self.scalar(x) - edge0) / (self.scalar(edge1) - edge0))
        return t * t * (3.0 - 2.0 * t)

    def select(self, condition, if_true, if_false):
        """if_true where condition is 1, if_false where it is 0."""
        return self.lerp(if_false, if_true, condition)

    # Vector functions
    def dot(self, a, b) -> Scalar: return self.vmath('DOT_PRODUCT', a, b)
    def normalize(self, a) -> Vector: return self.vmath('NORMALIZE', a)
    def vmax(self, a, b) -> Vector: return self.vmath('MAXIMUM', a, b)
    def vmin(self, a, b) -> Vector: return self.vmath('MINIMUM', a, b)

    def vsaturate(self, a) -> Vector:
        return self.vmin(self.vmax(a, (0.0, 0.0, 0.0)), (1.0, 1.0, 1.0))

    def max3(self, a) -> Scalar:
        a = self.vector(a)
        return self.max(self.max(a.x, a.y), a.z)

    def min3(self, a) -> Scalar:
        a = self.vector(a)
        return self.min(self.min(a.x, a.y), a.z)


SOCKET_TYPES = {'float': 'NodeSocketFloat', 'vector': 'NodeSocketVector', 'color': 'NodeSocketColor'}


def node_group(name: str, inputs: dict[str, tuple], outputs: dict[str, str],
               build: Callable[[NodeMath, dict], dict]) -> bpy.types.ShaderNodeTree:
    """A shader node group built once per file: inputs {name: (type, default)}, outputs {name: type}, with types
    'float', 'vector' or 'color'. build(m, inputs) gets the group inputs as Scalar or Vector values and returns
    {output name: value}."""
    group = bpy.data.node_groups.get(name)
    if group is not None:
        return group
    group = bpy.data.node_groups.new(name, 'ShaderNodeTree')
    for output_name, socket_type in outputs.items():
        group.interface.new_socket(output_name, in_out='OUTPUT', socket_type=SOCKET_TYPES[socket_type])
    for input_name, (socket_type, default) in inputs.items():
        socket = group.interface.new_socket(input_name, in_out='INPUT', socket_type=SOCKET_TYPES[socket_type])
        if socket_type == 'color':
            socket.default_value = (*default[:3], 1.0)
        elif socket_type == 'vector':
            socket.default_value = default
        else:
            socket.default_value = default
    m = NodeMath(group)
    group_input = m.node('NodeGroupInput')
    group_output = m.node('NodeGroupOutput')
    values = {}
    for input_name, (socket_type, _) in inputs.items():
        socket = group_input.outputs[input_name]
        values[input_name] = m.scalar(socket) if socket_type == 'float' else m.vector(socket)
    for output_name, value in build(m, values).items():
        m.set(group_output.inputs[output_name], value)
    return group


def group_call(m: NodeMath, group: bpy.types.ShaderNodeTree, inputs: dict) -> dict:
    """Add a group node to m's tree with these inputs {name: value}; its outputs as values by name."""
    node = m.node('ShaderNodeGroup')
    node.node_tree = group
    for key, value in inputs.items():
        m.set(node.inputs[key], value)
    return {output.name: (m.scalar(output) if output.type == 'VALUE' else m.vector(output))
            for output in node.outputs}
