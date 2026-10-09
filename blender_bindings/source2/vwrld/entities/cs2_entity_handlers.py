import math

import bpy
from mathutils import Euler, Matrix, Vector

from .....library.source2.keyvalues3.types import NullObject
from .abstract_entity_handlers import Base
from .hlvr_entity_classes import point_viewcontrol
from .hlvr_entity_handlers import HLVREntityHandler, get_origin, get_angles
from .cs2_entity_classes import *

local_entity_lookup_table = HLVREntityHandler.entity_lookup_table.copy()
local_entity_lookup_table.update(entity_class_handle)


def replace_null_object(data):
    if isinstance(data, dict):
        # If it's a dictionary, recurse over key-value pairs
        return {key: replace_null_object(value) for key, value in data.items()}
    elif isinstance(data, list):
        # If it's a list, recurse over list elements
        return [replace_null_object(item) for item in data]
    elif isinstance(data, tuple):
        # If it's a tuple, recurse over tuple elements and return a new tuple
        return tuple(replace_null_object(item) for item in data)
    elif isinstance(data, set):
        # If it's a set, recurse over set elements and return a new set
        return {replace_null_object(item) for item in data}
    elif isinstance(data, NullObject):
        # If it's a NullObject, replace it with None
        return None
    else:
        # Otherwise, return the data as-is
        return data


def light2_brightness(entity_raw: dict) -> float:
    """Linear brightness of a CS2 light_omni2, light_rect or light_barn.

    Their ``brightness`` is in stops (EV): ``brightness_legacy``, the linear value, is 2 ** brightness (to within
    its 1/256 steps) on every light of 11 maps. ``brightness_lumens`` depends on the light's shape and size.
    """
    return 2.0 ** float(entity_raw.get("brightness", 0.0)) * float(entity_raw.get("brightnessscale", 1.0))


# A light2 reaches its brightness this many units in front of its luminaire (radiance of a white surface facing it),
# falling off with the squared distance.
LIGHT2_REFERENCE_DISTANCE = 100.0


def point_light_watts(brightness: float, scale: float, distance: float = LIGHT2_REFERENCE_DISTANCE) -> float:
    """Power of a Blender point or spot light that gives a white diffuse surface ``distance`` units away a radiance
    of ``brightness``: P watts give it P / (4 pi^2 d^2) at d meters."""
    return 4 * math.pi ** 2 * (distance * scale) ** 2 * brightness


def entity_rotation(angles) -> Matrix:
    """The entity's axes as columns: forward (+X), left (+Y), up (+Z)."""
    return Euler((math.radians(angles[2]), math.radians(angles[0]), math.radians(angles[1]))).to_matrix()


def light_basis(axes: Matrix, direction: Vector) -> Matrix:
    """Orientation of a Blender light (shining along local -Z) along ``direction``, with local X near the entity's up
    axis and local Y near its left axis."""
    basis = axes @ Matrix.Rotation(-math.pi / 2, 3, 'Y')
    return axes.col[0].rotation_difference(direction).to_matrix() @ basis


def spot_blend(inner_angle: float, outer_angle: float) -> float:
    """Blender's spot blend for a light fully bright inside ``inner_angle`` and dark outside ``outer_angle`` (half
    angles, degrees): Cycles fades between cos(outer) and cos(outer) + (1 - cos(outer)) x blend."""
    cos_outer = math.cos(math.radians(outer_angle))
    cos_inner = math.cos(math.radians(min(inner_angle, outer_angle)))
    return float(np.clip((cos_inner - cos_outer) / max(1.0 - cos_outer, 1e-6), 0.0, 1.0))


class CS2EntityHandler(HLVREntityHandler):
    entity_lookup_table = local_entity_lookup_table
    entity_lookup_table["point_script"] = Base

    def load_entities(self):
        for entity in self._entities:
            if "values" not in entity:
                self.handle_entity(replace_null_object(entity))
            else:
                self.handle_entity(replace_null_object(entity["values"]))

    def handle_env_cs_place(self, entity: env_cs_place, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._put_into_collection("env_cs_place", obj, 'environment')

    def handle_env_soundscape(self, entity: env_soundscape, entity_raw: dict):
        obj = bpy.data.objects.new(self._get_entity_name(entity), None)
        obj.empty_display_size = entity_raw["radius"] * self.scale
        obj.empty_display_type = 'SPHERE'
        self._set_location_and_scale(obj, get_origin(entity_raw))
        self._set_rotation(obj, get_angles(entity_raw))
        self._set_icon_if_present(obj, entity)
        self._set_entity_data(obj, {'entity': entity_raw})
        self._put_into_collection('env_soundscape', obj, 'environment')

    def handle_env_wind(self, entity: env_wind, entity_raw: dict):
        obj = bpy.data.objects.new(self._get_entity_name(entity), None)
        self._set_location_and_scale(obj, get_origin(entity_raw),
                                     additional_scale=parse_source_value(entity_raw.get("scales")))
        self._set_rotation(obj, get_angles(entity_raw))
        self._set_icon_if_present(obj, entity)
        self._set_entity_data(obj, {'entity': entity_raw})
        self._put_into_collection('env_wind', obj, 'environment')

    def handle_info_player_counterterrorist(self, entity: info_player_counterterrorist, entity_raw: dict):
        obj = bpy.data.objects.new(self._get_entity_name(entity), None)
        self._set_location_and_scale(obj, get_origin(entity_raw))
        self._set_rotation(obj, get_angles(entity_raw))
        self._set_icon_if_present(obj, entity)
        self._set_entity_data(obj, {'entity': entity_raw})
        self._put_into_collection('info_player_counterterrorist', obj, 'info')

    def handle_info_player_terrorist(self, entity: info_player_terrorist, entity_raw: dict):
        obj = bpy.data.objects.new(self._get_entity_name(entity), None)
        self._set_location_and_scale(obj, get_origin(entity_raw))
        self._set_rotation(obj, get_angles(entity_raw))
        self._set_icon_if_present(obj, entity)
        self._set_entity_data(obj, {'entity': entity_raw})
        self._put_into_collection('info_player_terrorist', obj, 'info')

    def handle_point_viewcontrol(self, entity: point_viewcontrol, entity_raw: dict):
        obj = bpy.data.objects.new(self._get_entity_name(entity), None)
        self._set_location_and_scale(obj, get_origin(entity_raw))
        self._set_rotation(obj, get_angles(entity_raw))
        self._set_icon_if_present(obj, entity)
        self._set_entity_data(obj, {'entity': entity_raw})
        self._put_into_collection('point_viewcontrol', obj, 'environment')

    def handle_point_devshot_camera(self, entity: point_devshot_camera, entity_raw: dict):
        obj = bpy.data.objects.new(self._get_entity_name(entity), None)
        self._set_location_and_scale(obj, get_origin(entity_raw))
        self._set_rotation(obj, get_angles(entity_raw))
        self._set_icon_if_present(obj, entity)
        self._set_entity_data(obj, {'entity': entity_raw})
        self._put_into_collection('point_devshot_camera', obj, 'environment')

    def handle_point_script(self, entity: object, entity_raw: dict):
        obj = bpy.data.objects.new(self._get_entity_name(entity), None)
        self._set_location_and_scale(obj, get_origin(entity_raw))
        self._set_rotation(obj, get_angles(entity_raw))
        self._set_icon_if_present(obj, entity)
        self._set_entity_data(obj, {'entity': entity_raw})
        self._put_into_collection('point_script', obj, 'logic')

    def handle_point_camera(self, entity: point_camera, entity_raw: dict):
        obj = bpy.data.objects.new(self._get_entity_name(entity), None)
        self._set_location_and_scale(obj, get_origin(entity_raw))
        self._set_rotation(obj, get_angles(entity_raw))
        self._set_icon_if_present(obj, entity)
        self._set_entity_data(obj, {'entity': entity_raw})
        self._put_into_collection('point_camera', obj, 'logic')

    def handle_func_bomb_target(self, entity: func_bomb_target, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._put_into_collection("func_bomb_target", obj, 'func')

    def handle_func_water(self, entity: func_water, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._put_into_collection("func_water", obj, 'func')

    def handle_func_button(self, entity: func_button, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._put_into_collection("func_button", obj, 'func')

    def handle_func_breakable(self, entity: func_breakable, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._put_into_collection("func_breakable", obj, 'func')

    def handle_func_nav_blocker(self, entity: func_nav_blocker, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._put_into_collection("func_nav_blocker", obj, 'func')

    def handle_func_buyzone(self, entity: func_buyzone, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._put_into_collection("func_buyzone", obj, 'func')

    def handle_prop_physics_multiplayer(self, entity: prop_physics_multiplayer, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._put_into_collection("prop_physics_multiplayer", obj, 'props')

    def handle_prop_door_rotating(self, entity: prop_door_rotating, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._put_into_collection("prop_door_rotating", obj, 'props')

    def handle_func_clip_vphysics(self, entity: func_clip_vphysics, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._put_into_collection("func_clip_vphysics", obj, 'func')

    def handle_skybox_reference(self, entity: skybox_reference, entity_raw: dict):
        obj = self._handle_entity_with_model(entity, entity_raw)
        self._put_into_collection("skybox_reference", obj, 'func')

    def handle_path_particle_rope_clientside(self, entity: path_particle_rope_clientside, entity_raw: dict):
        return

    def handle_snd_event_path_corner(self, entity: snd_event_path_corner, entity_raw: dict):
        return

    def handle_cs_minimap_boundary(self, entity: cs_minimap_boundary, entity_raw: dict):
        return

    def handle_team_select(self, entity: team_select, entity_raw: dict):
        return

    def handle_light_barn(self, entity: light_barn, entity_raw: dict):
        """A barn light lights a frustum whose apex is 1 / size_params.z units behind the entity, where it is
        2 x size_params.x wide (along the entity's left axis) and 2 x size_params.y high (up axis), as VRF draws it.
        ``shear`` moves the frustum's far end, ``range`` units ahead, sideways. It becomes a spot at the apex,
        made elliptical by its object scale; size_params.z = 0 (orthographic) becomes an area light.

        Not kept: the rectangular shape (``shape`` 0; a spot is elliptical), the near and far skirts, ``range`` and
        light cookies. ``soft_x``/``soft_y`` set the spot blend, and ``luminaire_size`` the radius (unverified).
        """
        name = self._get_entity_name(entity)
        half_width, half_height, widening = (float(v) for v in entity_raw.get("size_params", (16.0, 16.0, 0.0625)))
        origin = Vector(get_origin(entity_raw))
        axes = entity_rotation(get_angles(entity_raw))
        brightness = light2_brightness(entity_raw)

        if widening <= 0:
            lamp_data = bpy.data.lights.new(name + "_DATA", 'AREA')
            lamp_data.shape = 'RECTANGLE'
            lamp_data.size = 2 * half_height * self.scale
            lamp_data.size_y = 2 * half_width * self.scale
            # A parallel beam: a white surface it lights has radiance `brightness` at any distance, matched
            # near the light by an area light of irradiance P / area.
            lamp_data.energy = math.pi * brightness * lamp_data.size * lamp_data.size_y
            lamp = bpy.data.objects.new(name, lamp_data)
            lamp.matrix_world = Matrix.LocRotScale(origin * self.scale, light_basis(axes, axes.col[0]), None)
        else:
            near = 1.0 / widening
            light_range = float(entity_raw.get("range", 0.0))
            shear_x, shear_y = (float(v) for v in entity_raw.get("shear", (0.0, 0.0)))
            skew_x, skew_y = (shear_x / light_range, shear_y / light_range) if light_range > 0 else (0.0, 0.0)
            # The frustum's centerline runs from the apex through the entity origin; sheared, the frustum is
            # off-axis around it. The spot aims at the middle of the frustum's angles along each axis, so its edges
            # meet a surface facing the light where the frustum's do.
            apex = origin - near * (axes.col[0] + skew_x * axes.col[1] + skew_y * axes.col[2])
            left_edges = math.atan(skew_x - half_width * widening), math.atan(skew_x + half_width * widening)
            up_edges = math.atan(skew_y - half_height * widening), math.atan(skew_y + half_height * widening)
            aim_left, aim_up = sum(left_edges) / 2, sum(up_edges) / 2
            direction = axes.col[0] + math.tan(aim_left) * axes.col[1] + math.tan(aim_up) * axes.col[2]

            # Half-angle tangents along the light's local X (entity up) and Y (entity left).
            tan_x = math.tan((up_edges[1] - up_edges[0]) / 2) * math.cos(aim_left)
            tan_y = math.tan((left_edges[1] - left_edges[0]) / 2) * math.cos(aim_up)
            tan_cone = max(tan_x, tan_y)
            lamp_data = bpy.data.lights.new(name + "_DATA", 'SPOT')
            lamp_data.spot_size = 2 * math.atan(tan_cone)
            lamp_data.spot_blend = float(np.clip((float(entity_raw.get("soft_x", 0.25)) +
                                                  float(entity_raw.get("soft_y", 0.25))) / 2, 0.0, 1.0))
            lamp_data.shadow_soft_size = float(entity_raw.get("luminaire_size", 4.0)) / 2 * self.scale
            # The brightness is reached 100 units ahead of the entity, the falloff counted from the apex.
            reference = near * math.sqrt(1 + skew_x ** 2 + skew_y ** 2) + LIGHT2_REFERENCE_DISTANCE
            lamp_data.energy = point_light_watts(brightness, self.scale, reference)
            lamp = bpy.data.objects.new(name, lamp_data)
            # Cycles scales a spot's cone along local X and Y by the object's scale over its Z scale.
            lamp.matrix_world = Matrix.LocRotScale(apex * self.scale, light_basis(axes, direction),
                                                   Vector((tan_x / tan_cone, tan_y / tan_cone, 1.0)))

        lamp_data.color = np.divide(entity_raw["color"], 255.0)[:3]
        self._set_entity_data(lamp, {'entity': entity_raw})
        self._put_into_collection('light_barn', lamp, 'lights')

    def handle_light_rect(self, entity: light_rect, entity_raw: dict):
        """A rectangle (``shape`` 0) or disc (1) of 2 x size_params.x by 2 x size_params.y units facing the entity's
        forward axis. The game lights the hemisphere in front of it like a point light (a white surface 100 units
        ahead has radiance `brightness`); an area light matches that on its axis at a quarter of the point's watts.
        """
        name = self._get_entity_name(entity)
        half_width, half_height, _ = (float(v) for v in entity_raw.get("size_params", (16.0, 16.0, 0.0)))
        axes = entity_rotation(get_angles(entity_raw))

        lamp_data = bpy.data.lights.new(name + "_DATA", 'AREA')
        lamp_data.shape = 'ELLIPSE' if int(float(entity_raw.get("shape", 0))) == 1 else 'RECTANGLE'
        lamp_data.size = 2 * half_height * self.scale
        lamp_data.size_y = 2 * half_width * self.scale
        lamp_data.energy = point_light_watts(light2_brightness(entity_raw), self.scale) / 4
        lamp_data.color = np.divide(entity_raw["color"], 255.0)[:3]
        lamp = bpy.data.objects.new(name, lamp_data)
        lamp.matrix_world = Matrix.LocRotScale(Vector(get_origin(entity_raw)) * self.scale,
                                               light_basis(axes, axes.col[0]), None)

        self._set_entity_data(lamp, {'entity': entity_raw})
        self._put_into_collection('light_rect', lamp, 'lights')

    def handle_light_omni2(self, entity: light_omni2, entity_raw: dict):
        name = self._get_entity_name(entity)

        # Blender's spot cone ends at 180° (an outer half-angle of 90°); wider ones stay point lights.
        is_spot = entity.outer_angle <= 90

        lamp_data = None
        lamp = None

        # TODO: This should probably take in all axes into account
        light_source_radius = float(entity.size_params[0]) * self.scale

        if is_spot:
            lamp_data = bpy.data.lights.new(name + "_DATA", 'SPOT')
            lamp = bpy.data.objects.new(name, lamp_data)
            lamp_data.spot_size = math.radians(entity.outer_angle * 2)
            lamp_data.spot_blend = spot_blend(float(entity_raw.get("inner_angle", 0.0)), entity.outer_angle)
        else:
            lamp_data = bpy.data.lights.new(name + "_DATA", 'POINT')
            lamp = bpy.data.objects.new(name, lamp_data)

        self._set_location_and_scale(lamp, get_origin(entity_raw))
        if is_spot:
            self._set_light_rotation(lamp, get_angles(entity_raw))
        else:
            self._set_rotation(lamp, get_angles(entity_raw))

        color = np.divide(entity.color, 255.0)
        lamp_data.energy = point_light_watts(light2_brightness(entity_raw), self.scale)
        lamp_data.color = color[:3]
        lamp_data.shadow_soft_size = light_source_radius

        self._set_entity_data(lamp, {'entity': entity_raw})
        self._put_into_collection('light_omni2', lamp, 'lights')
