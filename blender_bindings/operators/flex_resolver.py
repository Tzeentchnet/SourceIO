def flex_controller_objects(obj):
    if obj is None:
        return ()
    if obj.type == 'MESH':
        return (obj,) if len(getattr(obj, "flex_controllers", ())) > 0 else ()
    if obj.type != 'ARMATURE':
        return ()
    return tuple(
        child for child in obj.children
        if child.type == 'MESH' and len(getattr(child, "flex_controllers", ())) > 0
    )


def resolve_flex_object(obj):
    candidates = flex_controller_objects(obj)
    return candidates[0] if len(candidates) == 1 else None
