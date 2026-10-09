import bpy
import numpy as np
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy.types import Operator
from bpy_extras import anim_utils

from .shared_operators import UITools

# Adapted from REDxEYE/SourceIO#477 (hisprofile). Each entry of Object.flex_controllers drives one or two
# custom properties on the mesh data (made by create_flex_drivers); the shape key drivers read those properties.

_dragging = False
_updating_values = False


def get_frame(context):
    return context.scene.frame_float if context.scene.show_subframe else context.scene.frame_current


def has_key(context, obj, prop_name) -> bool:
    anim_data = obj.data.animation_data
    if anim_data is None or anim_data.action is None:
        return False
    channelbag = anim_utils.action_get_channelbag_for_slot(anim_data.action, anim_data.action_slot)
    if channelbag is None:
        return False
    fcurve = channelbag.fcurves.find(f'["{prop_name}"]')
    if fcurve is None:
        return False
    points = fcurve.keyframe_points
    co = np.zeros(len(points) * 2, dtype=np.float32)
    points.foreach_get('co', co)
    return get_frame(context) in co[::2]


def map_range(x, a, b, c, d):
    y = (x - a) / (b - a) * (d - c) + c
    return min(max(y, c), d)


def slider_update(self, context):
    global _dragging
    if _updating_values:  # the handler resets the additive sliders to 0
        return
    if not _dragging:
        _dragging = True
        bpy.ops.sourceio.flex_slider_handler('INVOKE_DEFAULT')


# noinspection PyPep8Naming
class SourceIO_PG_FlexController(bpy.types.PropertyGroup):
    # name: the mesh custom property (mono) or the UI controller name (stereo, which uses L and R)
    display_name: StringProperty(name='Display Name', default='')
    value: FloatProperty(name='Additive Value', default=0.0, update=slider_update, min=-1, max=1, options=set(),
                         description='Adds to the current value; resets to 0 on release')
    split: BoolProperty(name='Stereo', default=False, options=set())

    minimum: FloatProperty(options=set())
    maximum: FloatProperty(options=set())

    original_value: FloatProperty(options=set())
    original_value_r: FloatProperty(options=set())
    original_value_l: FloatProperty(options=set())

    realvalue: BoolProperty(name='Show values', default=True, options=set(),
                            description='Show the property values instead of the additive slider')

    R: StringProperty()
    L: StringProperty()

    def prop_names(self):
        """The mesh custom properties this controller sets."""
        return (self.R, self.L) if self.split else (self.name,)


def handle_slider(context, obj, slider: SourceIO_PG_FlexController):
    data = obj.data
    magnitude = max(abs(slider.maximum), abs(slider.minimum))

    def clamp(value):
        return min(max(value, slider.minimum), slider.maximum)

    if not slider.split:
        data[slider.name] = clamp(slider.original_value + slider.value * magnitude)
    else:
        balance = context.scene.sourceio_flex_lr_balance
        r_mult = map_range(balance, -1.0, 0.0, 0.0, 1.0)
        l_mult = map_range(balance, 1.0, 0.0, 0.0, 1.0)
        data[slider.R] = clamp(slider.original_value_r + slider.value * r_mult * magnitude)
        data[slider.L] = clamp(slider.original_value_l + slider.value * l_mult * magnitude)


class SOURCEIO_OT_FlexSliderHandler(Operator):
    """Applies the additive sliders while they are dragged. On release it keys them (with auto keying on)
    or, if a drag was cancelled, restores the values from before the drag; then it resets the sliders to 0."""
    bl_idname = 'sourceio.flex_slider_handler'
    bl_label = 'Flex Slider Handler'
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        self.active_sliders = None
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global _dragging, _updating_values
        obj = context.object
        if obj is None:
            _dragging = False
            return {'CANCELLED'}
        data = obj.data

        if self.active_sliders is None:
            # The first slider update has already happened; the mesh properties still hold the old values.
            self.active_sliders = []
            for slider in obj.flex_controllers:
                if slider.split:
                    slider.original_value_r = data[slider.R]
                    slider.original_value_l = data[slider.L]
                else:
                    slider.original_value = data[slider.name]
                if slider.value != 0.0:
                    self.active_sliders.append(slider)
            return {'PASS_THROUGH'}

        if event.type == 'MOUSEMOVE':
            for slider in self.active_sliders:
                handle_slider(context, obj, slider)
            data.update()
            return {'PASS_THROUGH'}

        if event.value != 'RELEASE':
            return {'PASS_THROUGH'}

        _dragging = False
        _updating_values = True
        try:
            for slider in self.active_sliders:
                handle_slider(context, obj, slider)
            if any(slider.value == 0.0 for slider in self.active_sliders):  # cancelled
                for slider in self.active_sliders:
                    if slider.split:
                        data[slider.R] = slider.original_value_r
                        data[slider.L] = slider.original_value_l
                    else:
                        data[slider.name] = slider.original_value
            elif context.scene.tool_settings.use_keyframe_insert_auto:
                frame = get_frame(context)
                for slider in self.active_sliders:
                    for prop_name in slider.prop_names():
                        data.keyframe_insert(data_path=f'["{prop_name}"]', frame=frame)
            for slider in self.active_sliders:
                slider.value = 0.0
            data.update()
        finally:
            _updating_values = False
        return {'FINISHED'}


class SOURCEIO_OT_FlexAdjustBalance(Operator):
    bl_idname = 'sourceio.flex_adjust_balance'
    bl_label = 'Adjust'
    bl_description = 'Move the left/right balance by 0.1'
    bl_options = {'INTERNAL'}
    amount: FloatProperty()

    def execute(self, context):
        scene = context.scene
        scene.sourceio_flex_lr_balance = round(scene.sourceio_flex_lr_balance + self.amount, 1)
        return {'FINISHED'}


class SOURCEIO_OT_KeyFlexController(Operator):
    bl_idname = 'sourceio.key_flex_controller'
    bl_label = 'Keyframe Flex Controller'
    bl_description = 'Insert or delete a keyframe for this flex controller on the current frame'
    bl_options = {'UNDO'}
    flex_controller: StringProperty()

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object
        slider = obj.flex_controllers.get(self.flex_controller)
        if slider is None:
            self.report({'ERROR'}, f'No flex controller "{self.flex_controller}"')
            return {'CANCELLED'}
        prop_names = slider.prop_names()
        keyed = all(has_key(context, obj, prop_name) for prop_name in prop_names)
        frame = get_frame(context)
        for prop_name in prop_names:
            if keyed:
                obj.data.keyframe_delete(data_path=f'["{prop_name}"]', frame=frame)
            else:
                obj.data.keyframe_insert(data_path=f'["{prop_name}"]', frame=frame)
        return {'FINISHED'}


class SOURCEIO_OT_KeyAllFlexControllers(Operator):
    bl_idname = 'sourceio.key_all_flex_controllers'
    bl_label = 'Key Every Flex Controller'
    bl_description = 'Insert a keyframe for every flex controller on the current frame'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object
        frame = get_frame(context)
        for slider in obj.flex_controllers:
            for prop_name in slider.prop_names():
                obj.data.keyframe_insert(data_path=f'["{prop_name}"]', frame=frame)
        return {'FINISHED'}


class SOURCEIO_OT_ResetFlexControllers(Operator):
    bl_idname = 'sourceio.reset_flex_controllers'
    bl_label = 'Reset Face'
    bl_description = 'Set every flex controller to 0 and Flex Scale to 1'
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.type == 'MESH' and 'flexmap' in context.object.data

    def execute(self, context):
        data = context.object.data
        flexmap = data['flexmap'].to_dict()
        flex_scale = flexmap.get('flex_scale')
        auto_key = context.scene.tool_settings.use_keyframe_insert_auto
        frame = get_frame(context)
        for prop_name in flexmap.values():
            default = 1.0 if prop_name == flex_scale else 0.0
            if data.get(prop_name, default) != default:
                data[prop_name] = default
                if auto_key:
                    data.keyframe_insert(data_path=f'["{prop_name}"]', frame=frame)
        data.update()
        return {'FINISHED'}


class SOURCEIO_PT_FlexControlPanel(UITools, bpy.types.Panel):
    bl_label = 'Flex controllers'
    bl_idname = 'SOURCEIO_PT_FlexControlPanel'
    bl_parent_id = "SOURCEIO_PT_Utils"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.type == 'MESH' and len(obj.flex_controllers) > 0

    def draw(self, context):
        layout = self.layout
        obj = context.object

        if obj.data.library:
            layout.label(text='Object data is linked.')
            layout.label(text='Flexes are frozen until made local!')
            return

        row = layout.row(align=True)
        col = row.column()
        col.template_list('SOURCEIO_UL_FlexControllerList', '', obj, 'flex_controllers',
                          obj, 'flex_controller_index')
        col = row.column()
        col.scale_y = 1.5
        col.prop(context.scene.tool_settings, 'use_keyframe_insert_auto', text='')
        col.separator()
        col.operator(SOURCEIO_OT_KeyAllFlexControllers.bl_idname, icon='DECORATE_KEYFRAME', text='')
        col.separator()
        col.operator(SOURCEIO_OT_ResetFlexControllers.bl_idname, icon='LOOP_BACK', text='')

        row = layout.row(align=True)
        row.operator(SOURCEIO_OT_FlexAdjustBalance.bl_idname, text='', icon='TRIA_LEFT').amount = -0.1
        row.prop(context.scene, 'sourceio_flex_lr_balance', slider=True, text='L <--> R balance')
        row.operator(SOURCEIO_OT_FlexAdjustBalance.bl_idname, text='', icon='TRIA_RIGHT').amount = 0.1


class SOURCEIO_UL_FlexControllerList(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        if self.layout_type not in {'DEFAULT', 'COMPACT'}:
            layout.alignment = 'CENTER'
            layout.label(text='')
            return
        obj = context.object
        if item.split:
            keyed = has_key(context, obj, item.R) or has_key(context, obj, item.L)
        else:
            keyed = has_key(context, obj, item.name)

        row = layout.row(align=True)
        if not item.realvalue:
            row.alert = keyed
            row.prop(item, 'value', slider=True, text=item.display_name)
            row.alert = False
        elif item.split:
            row.prop(obj.data, f'["{item.L}"]', text=f'{item.display_name} L')
            row.prop(obj.data, f'["{item.R}"]', text='R')
        else:
            row.prop(obj.data, f'["{item.name}"]', text=item.display_name)

        op = row.operator(SOURCEIO_OT_KeyFlexController.bl_idname, text='', emboss=False, depress=keyed,
                          icon='DECORATE_KEYFRAME' if keyed else 'DECORATE_ANIMATE')
        op.flex_controller = item.name
        row.prop(item, 'realvalue', text='', emboss=False,
                 icon='RESTRICT_VIEW_OFF' if item.realvalue else 'RESTRICT_VIEW_ON')


classes = (
    SourceIO_PG_FlexController,
    SOURCEIO_UL_FlexControllerList,
    SOURCEIO_PT_FlexControlPanel,
    SOURCEIO_OT_FlexAdjustBalance,
    SOURCEIO_OT_FlexSliderHandler,
    SOURCEIO_OT_KeyFlexController,
    SOURCEIO_OT_ResetFlexControllers,
    SOURCEIO_OT_KeyAllFlexControllers,
)
