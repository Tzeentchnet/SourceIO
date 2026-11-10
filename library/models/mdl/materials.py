def get_model_material_for_slot(mdl, material_slot: int, skin_family: int = 0):
    """Resolve an MDL mesh's skin-reference slot to its material."""
    if mdl.skin_groups:
        return mdl.skin_groups[skin_family][material_slot]
    return mdl.materials[material_slot]
