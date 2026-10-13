from SourceIO.library.source2.compiled_resource import CompiledResource
from SourceIO.library.utils import MemoryBuffer, TinyPath


class _ParentResource(CompiledResource):
    def get_child_resource_path(self, name_or_id):
        return TinyPath("materials/sourceio/child.vtex_c")


class _ChildResource:
    @classmethod
    def from_buffer(cls, buffer, filename):
        assert not buffer.closed
        return buffer.read(), filename


class _ContentManager:
    def __init__(self):
        self.buffers = []

    def find_file(self, path):
        buffer = MemoryBuffer(b"child")
        self.buffers.append(buffer)
        return buffer


def _parent():
    return _ParentResource(
        MemoryBuffer(b""),
        TinyPath("materials/sourceio/parent.vmat_c"),
        None,
    )


def test_child_resource_probes_and_loads_close_resolver_buffers():
    content_manager = _ContentManager()
    parent = _parent()

    assert parent.has_child_resource("child", content_manager)
    assert content_manager.buffers[-1].closed
    payload, path = parent.get_child_resource(
        "child",
        content_manager,
        _ChildResource,
    )

    assert payload == b"child"
    assert path == TinyPath("materials/sourceio/child.vtex_c")
    assert content_manager.buffers[-1].closed
