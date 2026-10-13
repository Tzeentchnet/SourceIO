from __future__ import annotations

from typing import Iterable

import numpy as np

from .....library.source2.provenance import to_json_safe
from .abstract_entity_handlers import Base, get_angles, get_origin, parse_float_vector
from .base_entity_handlers import BaseEntityHandler
from ..entity_import_diagnostics import EntityImportStatus


MODEL_VISUAL_CLASSES = frozenset({
    "ambient_creatures",
    "ent_dota_fountain",
    "ent_dota_halloffame",
    "ent_dota_neutral_item_stash",
    "ent_dota_shop",
    "ent_dota_tree",
    "npc_dota_barracks",
    "npc_dota_building",
    "npc_dota_fort",
    "npc_dota_lantern",
    "npc_dota_lotus_pool",
    "npc_dota_tower",
    "npc_dota_unit_twin_gate",
    "npc_dota_watch_tower",
    "npc_dota_xp_fountain",
    "prop_dynamic_client_fadeout",
    "prop_dynamic_clientside",
})

BRUSH_TRIGGER_CLASSES = frozenset({
    "trigger_boss_attackable",
    "trigger_multiple",
    "trigger_no_wards",
    "trigger_shop",
})

PARTICLE_MARKER_CLASSES = frozenset({
    "dota_world_particle_system",
    "info_particle_system",
})

DEFERRED_ENVIRONMENT_CLASSES = frozenset({
    "dota_reflection_skybox",
    "ent_dota_lightinfo",
    "env_deferred_light_client_only",
    "env_fog_controller",
    "env_global_light",
    "env_soundscape",
    "env_tonemap_controller",
    "water_lod_control",
    "worldspawn",
})

SPAWNER_CLASSES = frozenset({
    "dota_item_rune_spawner_bounty",
    "dota_item_rune_spawner_powerup",
    "info_courier_spawn_dire",
    "info_courier_spawn_dire_1",
    "info_courier_spawn_dire_2",
    "info_courier_spawn_dire_3",
    "info_courier_spawn_dire_4",
    "info_courier_spawn_dire_5",
    "info_courier_spawn_radiant",
    "info_courier_spawn_radiant_1",
    "info_courier_spawn_radiant_2",
    "info_courier_spawn_radiant_3",
    "info_courier_spawn_radiant_4",
    "info_courier_spawn_radiant_5",
    "info_player_start",
    "info_player_start_badguys",
    "info_player_start_dota",
    "info_player_start_goodguys",
    "npc_dota_miniboss_spawner",
    "npc_dota_neutral_spawner",
    "npc_dota_roshan_spawner",
    "npc_dota_spawner_bad_bot",
    "npc_dota_spawner_bad_mid",
    "npc_dota_spawner_bad_top",
    "npc_dota_spawner_good_bot",
    "npc_dota_spawner_good_mid",
    "npc_dota_spawner_good_top",
})

FOG_OF_WAR_HELPER_CLASSES = frozenset({
    "dota_minimap_boundary",
    "ent_fow_blocker_node",
    "ent_fow_revealer",
    "npc_dota_base_blocker",
    "world_bounds",
})

WORLD_LAYER_CLASSES = frozenset({"info_world_layer"})

_DOTA_ENTITY_CLASSES = frozenset().union(
    MODEL_VISUAL_CLASSES,
    BRUSH_TRIGGER_CLASSES,
    PARTICLE_MARKER_CLASSES,
    DEFERRED_ENVIRONMENT_CLASSES,
    SPAWNER_CLASSES,
    FOG_OF_WAR_HELPER_CLASSES,
    WORLD_LAYER_CLASSES,
)

local_entity_lookup_table = BaseEntityHandler.entity_lookup_table.copy()
# Keep the typed base classes (trigger_multiple, info_particle_system, ...); Base only fills the gaps.
local_entity_lookup_table.update({classname: Base for classname in _DOTA_ENTITY_CLASSES
                                  if classname not in local_entity_lookup_table})


class Dota2EntityHandler(BaseEntityHandler):
    entity_lookup_table = local_entity_lookup_table

    def _set_entity_data(self, obj, entity_raw: dict):
        super()._set_entity_data(obj, to_json_safe(entity_raw))

    def _get_entity_name(self, entity):
        entity_data = entity._entity_data
        targetname = entity_data.get("targetname")
        if targetname:
            return str(targetname)
        classname = str(entity_data["classname"])
        unique_id = entity_data.get(
            "hammeruniqueid",
            entity_data.get("hammerUniqueId", entity_data.get("compile_source_id")),
        )
        return f"{classname}_{unique_id}" if unique_id not in (None, "") else classname

    @staticmethod
    def _entity_scale(entity_raw: dict) -> Iterable[float]:
        values = parse_float_vector(entity_raw.get("scales", "1 1 1"))
        if len(values) != 3:
            raise ValueError(f"Expected a three-component entity scale, got {values!r}")
        return np.asarray(values, dtype=np.float64)

    def _apply_entity_scale(self, obj, entity_raw: dict):
        # The entity scale only scales the object; the location is already in scene units.
        entity_scale = self._entity_scale(entity_raw)
        obj.scale = [obj.scale[index] * entity_scale[index] for index in range(3)]

    @staticmethod
    def _mark_behavior(obj, status: EntityImportStatus):
        obj["sourceio_entity_behavior"] = status.value
        obj["entity_data"]["behavior"] = status.value

    def _handle_model_visual(self, entity: Base, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._apply_entity_scale(obj, entity_raw)
        status = (
            EntityImportStatus.HANDLED
            if obj["entity_data"].get("prop_path")
            else EntityImportStatus.REPRESENTED
        )
        self._mark_behavior(obj, status)
        self._put_into_collection(entity_raw["classname"], obj, "props")
        return status

    def _handle_brush_trigger(self, entity: Base, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._apply_entity_scale(obj, entity_raw)
        self._mark_behavior(obj, EntityImportStatus.REPRESENTED)
        self._put_into_collection(entity_raw["classname"], obj, "triggers")
        return EntityImportStatus.REPRESENTED

    def _handle_marker(self, entity: Base, entity_raw: dict, group: str, status: EntityImportStatus,
                       subgroup: str | None = None):
        obj = self._create_empty(self._get_entity_name(entity))
        self._set_location_and_scale(obj, get_origin(entity_raw))
        self._apply_entity_scale(obj, entity_raw)
        self._set_rotation(obj, get_angles(entity_raw))
        self._set_entity_data(obj, {
            "type": entity_raw["classname"],
            "scale": self.scale,
            "entity": entity_raw,
            "behavior": status.value,
        })
        obj["sourceio_entity_behavior"] = status.value
        self._put_into_collection(subgroup or entity_raw["classname"], obj, group)
        return obj

    def _handle_particle_marker(self, entity: Base, entity_raw: dict):
        self._handle_marker(entity, entity_raw, "particles", EntityImportStatus.REPRESENTED)
        return EntityImportStatus.REPRESENTED

    def _handle_deferred_environment(self, entity: Base, entity_raw: dict):
        self._handle_marker(entity, entity_raw, "environment", EntityImportStatus.DEFERRED)
        return EntityImportStatus.DEFERRED

    def _handle_spawner(self, entity: Base, entity_raw: dict):
        self._handle_marker(entity, entity_raw, "spawners", EntityImportStatus.REPRESENTED)
        return EntityImportStatus.REPRESENTED

    def _handle_fog_of_war_helper(self, entity: Base, entity_raw: dict):
        self._handle_marker(entity, entity_raw, "fog_of_war", EntityImportStatus.REPRESENTED)
        return EntityImportStatus.REPRESENTED

    def _handle_world_layer(self, entity: Base, entity_raw: dict):
        layer_name = str(entity_raw.get("layerName") or self._get_entity_name(entity))
        obj = self._handle_marker(
            entity,
            entity_raw,
            "world_layers",
            EntityImportStatus.REPRESENTED,
            layer_name,
        )
        obj.users_collection[0]["sourceio_world_layer"] = layer_name
        return EntityImportStatus.REPRESENTED

    handle_ambient_creatures = _handle_model_visual
    handle_ent_dota_fountain = _handle_model_visual
    handle_ent_dota_halloffame = _handle_model_visual
    handle_ent_dota_neutral_item_stash = _handle_model_visual
    handle_ent_dota_shop = _handle_model_visual
    handle_ent_dota_tree = _handle_model_visual
    handle_npc_dota_barracks = _handle_model_visual
    handle_npc_dota_building = _handle_model_visual
    handle_npc_dota_fort = _handle_model_visual
    handle_npc_dota_lantern = _handle_model_visual
    handle_npc_dota_lotus_pool = _handle_model_visual
    handle_npc_dota_tower = _handle_model_visual
    handle_npc_dota_unit_twin_gate = _handle_model_visual
    handle_npc_dota_watch_tower = _handle_model_visual
    handle_npc_dota_xp_fountain = _handle_model_visual
    handle_prop_dynamic_client_fadeout = _handle_model_visual
    handle_prop_dynamic_clientside = _handle_model_visual

    handle_trigger_boss_attackable = _handle_brush_trigger
    handle_trigger_multiple = _handle_brush_trigger
    handle_trigger_no_wards = _handle_brush_trigger
    handle_trigger_shop = _handle_brush_trigger

    handle_dota_world_particle_system = _handle_particle_marker
    handle_info_particle_system = _handle_particle_marker

    handle_dota_reflection_skybox = _handle_deferred_environment
    handle_ent_dota_lightinfo = _handle_deferred_environment
    handle_env_deferred_light_client_only = _handle_deferred_environment
    handle_env_fog_controller = _handle_deferred_environment
    handle_env_global_light = _handle_deferred_environment
    handle_env_soundscape = _handle_deferred_environment
    handle_env_tonemap_controller = _handle_deferred_environment
    handle_water_lod_control = _handle_deferred_environment
    handle_worldspawn = _handle_deferred_environment

    handle_dota_item_rune_spawner_bounty = _handle_spawner
    handle_dota_item_rune_spawner_powerup = _handle_spawner
    handle_info_courier_spawn_dire = _handle_spawner
    handle_info_courier_spawn_dire_1 = _handle_spawner
    handle_info_courier_spawn_dire_2 = _handle_spawner
    handle_info_courier_spawn_dire_3 = _handle_spawner
    handle_info_courier_spawn_dire_4 = _handle_spawner
    handle_info_courier_spawn_dire_5 = _handle_spawner
    handle_info_courier_spawn_radiant = _handle_spawner
    handle_info_courier_spawn_radiant_1 = _handle_spawner
    handle_info_courier_spawn_radiant_2 = _handle_spawner
    handle_info_courier_spawn_radiant_3 = _handle_spawner
    handle_info_courier_spawn_radiant_4 = _handle_spawner
    handle_info_courier_spawn_radiant_5 = _handle_spawner
    handle_info_player_start = _handle_spawner
    handle_info_player_start_badguys = _handle_spawner
    handle_info_player_start_dota = _handle_spawner
    handle_info_player_start_goodguys = _handle_spawner
    handle_npc_dota_miniboss_spawner = _handle_spawner
    handle_npc_dota_neutral_spawner = _handle_spawner
    handle_npc_dota_roshan_spawner = _handle_spawner
    handle_npc_dota_spawner_bad_bot = _handle_spawner
    handle_npc_dota_spawner_bad_mid = _handle_spawner
    handle_npc_dota_spawner_bad_top = _handle_spawner
    handle_npc_dota_spawner_good_bot = _handle_spawner
    handle_npc_dota_spawner_good_mid = _handle_spawner
    handle_npc_dota_spawner_good_top = _handle_spawner

    handle_dota_minimap_boundary = _handle_fog_of_war_helper
    handle_ent_fow_blocker_node = _handle_fog_of_war_helper
    handle_ent_fow_revealer = _handle_fog_of_war_helper
    handle_npc_dota_base_blocker = _handle_fog_of_war_helper
    handle_world_bounds = _handle_fog_of_war_helper

    handle_info_world_layer = _handle_world_layer
