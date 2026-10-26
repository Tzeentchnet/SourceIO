import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatProperty, StringProperty
from bpy.types import Operator
from bpy_extras import anim_utils

from ...logger import SourceLogMan
from .flex_resolver import flex_controller_objects, resolve_flex_object
from .shared_operators import UITools

# Adapted from REDxEYE/SourceIO#477 (hisprofile). Each entry of Object.flex_controllers drives one or two
# custom properties on the mesh data (made by create_flex_drivers); the shape key drivers read those properties.
# A mono controller's slider is that property itself. A stereo controller uses a temporary additive slider:
# each drag snapshots both properties and applies a balance-weighted delta without destroying their existing offset.

logger = SourceLogMan().get_logger('SourceIO::FlexControllers')

_active_flex_drag = None
_resetting_flex_delta = False


def get_frame(context):
    return context.scene.frame_float if context.scene.show_subframe else context.scene.frame_current


def _flex_object_or_report(operator, context):
    candidates = flex_controller_objects(context.object)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        operator.report({'ERROR'}, 'Multiple flex-enabled child meshes; select a mesh directly')
    else:
        operator.report({'ERROR'}, 'No active flex-enabled mesh')
    return None


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


def balance_multipliers(balance):
    """Return SFM's left/right shares for a balance in the range -1..1."""
    balance = min(max(balance, -1.0), 1.0)
    left = 1.0 if balance <= 0.0 else 1.0 - balance
    right = 1.0 if balance >= 0.0 else 1.0 + balance
    return left, right


def stereo_delta_values(left, right, delta, balance, minimum, maximum):
    """Apply an SFM-style slider delta to a left/right pair."""
    magnitude = max(abs(maximum), abs(minimum))
    left_mult, right_mult = balance_multipliers(balance)

    def clamp(value):
        return min(max(value, minimum), maximum)

    return (
        clamp(left + delta * magnitude * left_mult),
        clamp(right + delta * magnitude * right_mult),
    )


def _flex_drag_terminal_action(event_type, event_value):
    if event_type == 'LEFTMOUSE' and event_value == 'RELEASE':
        return 'COMMIT'
    if event_type in {'RET', 'NUMPAD_ENTER'} and event_value == 'PRESS':
        return 'COMMIT'
    if event_type in {'ESC', 'RIGHTMOUSE'} and event_value == 'PRESS':
        return 'CANCEL'
    return None


def _set_flex_delta(slider, value):
    global _resetting_flex_delta
    was_resetting = _resetting_flex_delta
    _resetting_flex_delta = True
    try:
        slider.value = value
    finally:
        _resetting_flex_delta = was_resetting


class _FlexDragSession:
    def __init__(self, obj, slider, scene):
        self.obj = obj
        self.object_pointer = obj.as_pointer()
        self.data = obj.data
        self.scene = scene
        self.slider_name = slider.name
        self.left_name = slider.L
        self.right_name = slider.R
        self.minimum = slider.minimum
        self.maximum = slider.maximum
        self.original_left = float(self.data[self.left_name])
        self.original_right = float(self.data[self.right_name])
        self.delta = 0.0
        self.balance = scene.sourceio_flex_lr_balance
        self.changed_properties = ()
        # Set once the session has been committed or cancelled, possibly before its operator's next timer tick.
        self.finished = False

    def matches(self, slider):
        try:
            return slider.id_data.as_pointer() == self.object_pointer and slider.name == self.slider_name
        except ReferenceError:
            return False

    def matches_context(self, context):
        obj = resolve_flex_object(context.object)
        if obj is None:
            return False
        try:
            return obj.as_pointer() == self.object_pointer
        except ReferenceError:
            return False

    def current_slider(self):
        try:
            if self.obj.as_pointer() != self.object_pointer or self.obj.data != self.data:
                return None
            return self.obj.flex_controllers.get(self.slider_name)
        except ReferenceError:
            return None

    def data_is_available(self):
        try:
            return self.data.as_pointer() != 0
        except ReferenceError:
            return False

    def apply(self, delta, balance):
        if not self.data_is_available():
            return None
        left, right = stereo_delta_values(
            self.original_left,
            self.original_right,
            delta,
            balance,
            self.minimum,
            self.maximum,
        )
        values = ((self.left_name, left, self.original_left), (self.right_name, right, self.original_right))
        for prop_name, value, _original in values:
            if self.data.get(prop_name) != value:
                self.data[prop_name] = value
        self.data.update()
        self.delta = delta
        self.balance = balance
        self.changed_properties = tuple(
            prop_name for prop_name, value, original in values if value != original
        )
        return self.changed_properties

    def commit(self, delta, balance):
        changed = self.apply(delta, balance)
        if changed is None:
            return False
        if self.scene.tool_settings.use_keyframe_insert_auto:
            frame = self.scene.frame_float if self.scene.show_subframe else self.scene.frame_current
            for prop_name in changed:
                self.data.keyframe_insert(data_path=f'["{prop_name}"]', frame=frame)
        return True

    def cancel(self):
        if not self.data_is_available():
            return False
        self.data[self.left_name] = self.original_left
        self.data[self.right_name] = self.original_right
        self.data.update()
        self.delta = 0.0
        self.changed_properties = ()
        return True

    def reset_delta(self):
        slider = self.current_slider()
        if slider is None:
            return False
        _set_flex_delta(slider, 0.0)
        return True


def _flush_active_flex_drag():
    """Commit the active session now, so a script setting several sliders in a row applies all of them.

    Its operator sees the session finished on its next timer tick and only cleans up."""
    global _active_flex_drag
    session = _active_flex_drag
    _active_flex_drag = None
    try:
        slider = session.current_slider()
        if slider is None or not session.commit(slider.value, session.scene.sourceio_flex_lr_balance):
            logger.error(f'Could not apply flex controller "{session.slider_name}"')
            session.cancel()
    finally:
        session.finished = True
        session.reset_delta()


def slider_update(slider, context):
    global _active_flex_drag
    if _resetting_flex_delta:
        return
    if _active_flex_drag is not None:
        if _active_flex_drag.matches(slider):
            changed = _active_flex_drag.apply(slider.value, context.scene.sourceio_flex_lr_balance)
            if changed is None:
                logger.error(f'Flex controller "{slider.name}" lost its mesh data during a drag')
            return
        _flush_active_flex_drag()
    if slider.value == 0.0:
        return
    if not slider.split:
        logger.error(f'Additive flex slider "{slider.name}" is not a stereo controller')
        _set_flex_delta(slider, 0.0)
        return
    obj = slider.id_data
    if obj.data.library is not None:
        logger.error(f'Flex controller "{slider.name}" belongs to linked mesh data')
        _set_flex_delta(slider, 0.0)
        return
    if bpy.app.background or context.window is None:
        try:
            session = _FlexDragSession(obj, slider, context.scene)
        except KeyError as ex:
            logger.error(f'Flex controller property {ex} is missing from "{obj.name}"')
            _set_flex_delta(slider, 0.0)
            return
        try:
            if not session.commit(slider.value, context.scene.sourceio_flex_lr_balance):
                logger.error(f'Could not apply flex controller "{slider.name}" to "{obj.name}"')
        finally:
            session.reset_delta()
        return
    try:
        result = bpy.ops.sourceio.flex_slider_handler(
            'INVOKE_DEFAULT',
            object_name=obj.name,
            flex_controller=slider.name,
        )
    except RuntimeError as ex:
        logger.error(f'Could not start flex controller drag for "{slider.name}": {ex}')
        _set_flex_delta(slider, 0.0)
        return
    if 'CANCELLED' in result:
        _set_flex_delta(slider, 0.0)


# noinspection PyPep8Naming
class SourceIO_PG_FlexController(bpy.types.PropertyGroup):
    # name: the mesh custom property (mono) or the UI controller name (stereo, which uses L and R)
    display_name: StringProperty(name='Display Name', default='')
    split: BoolProperty(name='Stereo', default=False, options=set())

    minimum: FloatProperty(options=set())
    maximum: FloatProperty(options=set())

    value: FloatProperty(
        name='Additive Value',
        default=0.0,
        min=-1.0,
        max=1.0,
        options=set(),
        update=slider_update,
        description='Adds to the current left/right values using the L/R balance; resets to 0 after the drag',
    )

    realvalue: BoolProperty(name='Show left and right', default=False, options=set(),
                            description='Show the left and right values separately instead of one slider')

    R: StringProperty()
    L: StringProperty()

    def prop_names(self):
        """The mesh custom properties this controller sets."""
        return (self.R, self.L) if self.split else (self.name,)


class SOURCEIO_OT_FlexSliderHandler(Operator):
    bl_idname = 'sourceio.flex_slider_handler'
    bl_label = 'Adjust Flex Controller'
    bl_description = 'Apply a non-destructive left/right flex adjustment'
    bl_options = {'INTERNAL', 'UNDO'}

    object_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    flex_controller: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        global _active_flex_drag
        self._timer = None
        self._completed = False
        self._pending_action = None

        if _active_flex_drag is not None:
            self.report({'ERROR'}, 'Another flex controller drag is already active')
            return {'CANCELLED'}

        obj = bpy.data.objects.get(self.object_name)
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, f'No mesh object named "{self.object_name}"')
            return {'CANCELLED'}
        slider = obj.flex_controllers.get(self.flex_controller)
        if slider is None or not slider.split:
            self.report({'ERROR'}, f'No stereo flex controller "{self.flex_controller}"')
            return {'CANCELLED'}
        if obj.data.library is not None:
            self.report({'ERROR'}, 'Linked mesh data cannot be edited')
            return {'CANCELLED'}

        try:
            self._session = _FlexDragSession(obj, slider, context.scene)
        except KeyError as ex:
            self.report({'ERROR'}, f'Flex controller property {ex} is missing from "{obj.name}"')
            return {'CANCELLED'}

        _active_flex_drag = self._session
        if self._session.apply(slider.value, context.scene.sourceio_flex_lr_balance) is None:
            _active_flex_drag = None
            self.report({'ERROR'}, f'Mesh data for "{obj.name}" is no longer available')
            return {'CANCELLED'}

        if bpy.app.background or context.window is None:
            return self._complete(context, 'COMMIT')

        self._window_manager = context.window_manager
        self._timer = self._window_manager.event_timer_add(0.05, window=context.window)
        self._pending_action = _flex_drag_terminal_action(event.type, event.value)
        if self._pending_action is None and event.type not in {'LEFTMOUSE', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
            self._pending_action = 'COMMIT'
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _remove_timer(self):
        if self._timer is not None:
            self._window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _is_own_timer(self, event):
        if event.type != 'TIMER' or self._timer is None:
            return False
        event_timer = getattr(event, 'timer', None)
        if event_timer is None:
            return True
        return event_timer.as_pointer() == self._timer.as_pointer()

    def _complete(self, context, action):
        global _active_flex_drag
        if self._completed:
            return {'FINISHED'} if action == 'COMMIT' else {'CANCELLED'}

        self._completed = True
        success = False
        try:
            if self._session.finished:
                # Another slider changed first and committed this session early.
                action = 'COMMIT'
                success = True
            elif action == 'COMMIT':
                slider = self._session.current_slider()
                if slider is not None:
                    success = self._session.commit(
                        slider.value,
                        self._session.scene.sourceio_flex_lr_balance,
                    )
                if not success:
                    self._session.cancel()
            else:
                success = self._session.cancel()
        finally:
            # Always release the drag, even if keying raised; a stale session would block every later drag.
            if not self._session.finished:
                self._session.finished = True
                self._session.reset_delta()
            if _active_flex_drag is self._session:
                _active_flex_drag = None
            self._remove_timer()

        if not success:
            self.report({'ERROR'}, f'Could not finish flex controller "{self.flex_controller}"')
            return {'CANCELLED'}
        return {'FINISHED'} if action == 'COMMIT' else {'CANCELLED'}

    def modal(self, context, event):
        if self._is_own_timer(event):
            if self._session.finished:
                return self._complete(context, 'COMMIT')
            if self._pending_action is not None:
                return self._complete(context, self._pending_action)
            if not self._session.matches_context(context) or self._session.current_slider() is None:
                self.report({'WARNING'}, 'Flex controller drag cancelled because its object is no longer active')
                return self._complete(context, 'CANCEL')
            return {'RUNNING_MODAL'}

        action = _flex_drag_terminal_action(event.type, event.value)
        if action is not None:
            self._pending_action = action
            return {'RUNNING_MODAL', 'PASS_THROUGH'}
        if event.type == 'WINDOW_DEACTIVATE':
            self._pending_action = 'CANCEL'
            return {'RUNNING_MODAL', 'PASS_THROUGH'}
        return {'RUNNING_MODAL', 'PASS_THROUGH'}

    def cancel(self, context):
        if not self._completed:
            self._complete(context, 'CANCEL')


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
        return resolve_flex_object(context.object) is not None

    def execute(self, context):
        obj = _flex_object_or_report(self, context)
        if obj is None:
            return {'CANCELLED'}
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
        return resolve_flex_object(context.object) is not None

    def execute(self, context):
        obj = _flex_object_or_report(self, context)
        if obj is None:
            return {'CANCELLED'}
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
        obj = resolve_flex_object(context.object)
        return obj is not None and 'flexmap' in obj.data

    def execute(self, context):
        obj = _flex_object_or_report(self, context)
        if obj is None:
            return {'CANCELLED'}
        data = obj.data
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
        return bool(flex_controller_objects(context.object))

    def draw(self, context):
        layout = self.layout
        candidates = flex_controller_objects(context.object)
        if len(candidates) != 1:
            layout.label(text='Multiple flex-enabled child meshes.', icon='ERROR')
            layout.label(text='Select a mesh directly to edit its controllers.')
            return
        obj = candidates[0]

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
        obj = data
        if item.split:
            keyed = has_key(context, obj, item.R) or has_key(context, obj, item.L)
        else:
            keyed = has_key(context, obj, item.name)

        row = layout.row(align=True)
        if not item.split:
            row.prop(obj.data, f'["{item.name}"]', slider=True, text=item.display_name)
        elif item.realvalue:
            row.prop(obj.data, f'["{item.L}"]', slider=True, text=f'{item.display_name} L')
            row.prop(obj.data, f'["{item.R}"]', slider=True, text='R')
        else:
            # The temporary additive slider isn't animated itself, so show the raw properties' keyed state.
            row.alert = keyed
            row.prop(item, 'value', slider=True, text=item.display_name)
            row.alert = False

        op = row.operator(SOURCEIO_OT_KeyFlexController.bl_idname, text='', emboss=False, depress=keyed,
                          icon='DECORATE_KEYFRAME' if keyed else 'DECORATE_ANIMATE')
        op.flex_controller = item.name
        if item.split:
            row.prop(item, 'realvalue', text='', emboss=False,
                     icon='RESTRICT_VIEW_OFF' if item.realvalue else 'RESTRICT_VIEW_ON')


@persistent
def reset_flex_deltas(*_args):
    """Zero every leftover additive slider value. The slider's own undo step is pushed mid-drag, before the
    operator commits and resets it, so undoing to that step would otherwise bring back a stale delta that the
    next drag adds on top of."""
    if _active_flex_drag is not None:
        return
    for obj in bpy.data.objects:
        if obj.library is not None or obj.type != 'MESH':
            continue
        for slider in obj.flex_controllers:
            if slider.value != 0.0:
                _set_flex_delta(slider, 0.0)


@persistent
def _reset_flex_drag_on_load(*_args):
    global _active_flex_drag
    _active_flex_drag = None
    reset_flex_deltas()


_HANDLERS = (
    (bpy.app.handlers.undo_post, reset_flex_deltas),
    (bpy.app.handlers.redo_post, reset_flex_deltas),
    (bpy.app.handlers.load_post, _reset_flex_drag_on_load),
)


def register_handlers():
    for handler_list, handler in _HANDLERS:
        if handler not in handler_list:
            handler_list.append(handler)


def unregister_handlers():
    for handler_list, handler in _HANDLERS:
        if handler in handler_list:
            handler_list.remove(handler)


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
