from . import nodes, sockets
from .model_tree_nodes import SourceIO_NT_ModelTree, \
    SourceIO_OP_EvaluateNodeTree
from .nodes.textures.texture_preview import SourceIO_OP_EvaluatePreview


classes = (
    SourceIO_NT_ModelTree,

    nodes.SourceIOObjectNode,
    nodes.SourceIOModelNode,
    nodes.SourceIOMaterialNode,

    nodes.SourceIOBodygroupNode,
    nodes.SourceIOSkinNode,
    nodes.SourceIOSkingroupNode,

    nodes.SourceIOVertexLitGenericNode,

    nodes.SourceIOTextureInputNode,
    nodes.SourceIOTextureToVTFNode,
    nodes.SourceIOTextureInvertChannelsNode,
    nodes.SourceIOTextureSplitChannelsNode,
    nodes.SourceIOTextureCombineChannelsNode,
    nodes.SourceIOTextureBrightnessContrastNode,
    nodes.SourceIOTexturePreviewNode,
    nodes.SourceIOTextureMathNode,
    nodes.SourceIOChannelRemapNode,
    nodes.SourceIONormalOpsNode,
    nodes.SourceIOHeightToNormalNode,
    nodes.SourceIONormalBlendNode,
    nodes.SourceIOColorSpaceNode,

    sockets.SourceIOObjectSocket,
    sockets.SourceIOBodygroupSocket,
    sockets.SourceIOMaterialSocket,
    sockets.SourceIOSkinSocket,
    sockets.SourceIOSkinGroupSocket,
    sockets.SourceIOTextureSocket,
    sockets.SourceIOTextureVtfSocket,
    sockets.SourceIOTextureChannelSocket,


    SourceIO_OP_EvaluateNodeTree,
    SourceIO_OP_EvaluatePreview
)


def register_nodes():
    print('Registered nodes')
    from bpy.utils import register_class
    for cls in classes:
        register_class(cls)


def unregister_nodes():
    from bpy.utils import unregister_class
    for cls in reversed(classes):
        unregister_class(cls)
