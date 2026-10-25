"""Fingerprints of Blender ID collections, for name indexes that must notice changes made elsewhere."""
import bpy
import numpy as np


def id_collection_state(collection) -> tuple[int, int, int]:
    """A cheap fingerprint of an ID collection (``bpy.data.materials``...) that changes whenever an ID is added or
    removed: session UIDs only grow, so a new ID raises the largest one and a removal lowers the count."""
    count = len(collection)
    if count == 0:
        return id(bpy.data), 0, 0
    uids = np.empty(count, np.int32)
    collection.foreach_get('session_uid', uids)
    return id(bpy.data), count, int(uids.max())


def id_collection_state_after_add(state: tuple[int, int, int] | None, collection, new_id) -> tuple | None:
    """``state`` moved on by ``new_id`` alone, or None when something else changed the collection as well."""
    if state is None or state[:2] != (id(bpy.data), len(collection) - 1) or new_id.session_uid <= state[2]:
        return None
    return state[0], state[1] + 1, new_id.session_uid
