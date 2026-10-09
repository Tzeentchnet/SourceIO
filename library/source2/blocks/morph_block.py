from collections import defaultdict
from dataclasses import dataclass
from enum import IntEnum
from struct import pack, unpack
from typing import Any

import numpy as np
import numpy.typing as npt

from .kv3_block import KVBlock
from ..keyvalues3.enums import KV3Signature, KV3Format
from ..keyvalues3.types import AnyKVType
from ..interfaces import Diagnostic, DiagnosticSeverity
from ....logger import SourceLogMan

logger = SourceLogMan().get_logger("Source2::Morph")


@dataclass(frozen=True, slots=True)
class FlexController:
    name: str
    controller_type: str = ""
    minimum: float = 0.0
    maximum: float = 1.0


class UnsupportedFlexOperationError(ValueError):
    pass


class _FlexOp(IntEnum):
    CONST = 1
    FETCH1 = 2
    FETCH2 = 3
    ADD = 4
    SUB = 5
    MUL = 6
    DIV = 7
    NEG = 8
    EXP = 9
    OPEN = 10
    CLOSE = 11
    COMMA = 12
    MAX = 13
    MIN = 14
    TWO_WAY_0 = 15
    TWO_WAY_1 = 16
    NWAY = 17
    COMBO = 18
    DOMINATE = 19
    DME_LOWER_EYELID = 20
    DME_UPPER_EYELID = 21
    SQRT = 22
    REMAP_VAL_CLAMPED = 23
    SIN = 24
    COS = 25
    ABS = 26


_FLEX_OP_NAMES = {
    "FLEX_OP_CONST": _FlexOp.CONST,
    "FLEX_OP_FETCH1": _FlexOp.FETCH1,
    "FLEX_OP_FETCH2": _FlexOp.FETCH2,
    "FLEX_OP_ADD": _FlexOp.ADD,
    "FLEX_OP_SUB": _FlexOp.SUB,
    "FLEX_OP_MUL": _FlexOp.MUL,
    "FLEX_OP_DIV": _FlexOp.DIV,
    "FLEX_OP_NEG": _FlexOp.NEG,
    "FLEX_OP_EXP": _FlexOp.EXP,
    "FLEX_OP_OPEN": _FlexOp.OPEN,
    "FLEX_OP_CLOSE": _FlexOp.CLOSE,
    "FLEX_OP_COMMA": _FlexOp.COMMA,
    "FLEX_OP_MAX": _FlexOp.MAX,
    "FLEX_OP_MIN": _FlexOp.MIN,
    "FLEX_OP_2WAY_0": _FlexOp.TWO_WAY_0,
    "FLEX_OP_2WAY_1": _FlexOp.TWO_WAY_1,
    "FLEX_OP_NWAY": _FlexOp.NWAY,
    "FLEX_OP_COMBO": _FlexOp.COMBO,
    "FLEX_OP_DOMINATE": _FlexOp.DOMINATE,
    "FLEX_OP_DME_LOWER_EYELID": _FlexOp.DME_LOWER_EYELID,
    "FLEX_OP_DME_UPPER_EYELID": _FlexOp.DME_UPPER_EYELID,
    "FLEX_OP_SQRT": _FlexOp.SQRT,
    "FLEX_OP_REMAPVALCLAMPED": _FlexOp.REMAP_VAL_CLAMPED,
    "FLEX_OP_SIN": _FlexOp.SIN,
    "FLEX_OP_COS": _FlexOp.COS,
    "FLEX_OP_ABS": _FlexOp.ABS,
}


class MorphBlock(KVBlock):

    def __init__(self, data: dict[str, AnyKVType] = None, version: KV3Signature = KV3Signature.KV3_V3, format:KV3Format = KV3Format.generic):
        super().__init__(data or {}, version, format)
        self._morph_datas: dict[int, dict[str, npt.NDArray[np.float32]]] = defaultdict(dict)
        self._morph_name_map: dict[str, AnyKVType] = {}
        self._vmorf_texture = None

    @staticmethod
    def _struct_name():
        return 'MorphSetData_t'

    @property
    def lookup_type(self):
        return self.get("m_nLookupType", "LOOKUP_TYPE_VERTEX_ID")

    @property
    def encoding_type(self):
        return self.get("m_nEncodingType", "ENCODING_TYPE_OBJECT_SPACE")

    @property
    def bundles(self) -> list[str]:
        return self['m_bundleTypes']

    def get_bundle_id(self, *bundle_names: str):
        for name in bundle_names:
            if name in self.bundles:
                return self.bundles.index(name)
        return None

    @property
    def flex_descriptors(self) -> tuple[str, ...]:
        names = []
        for descriptor in self.get("m_FlexDesc", []) or []:
            if hasattr(descriptor, "get"):
                name = descriptor.get("m_szFacs", descriptor.get("m_name", ""))
            else:
                name = descriptor
            names.append(str(name))
        return tuple(names)

    @property
    def flex_controllers(self) -> tuple[FlexController, ...]:
        controllers = []
        for controller in self.get("m_FlexControllers", []) or []:
            if not hasattr(controller, "get"):
                continue
            controllers.append(FlexController(
                str(controller.get("m_szName", controller.get("m_name", ""))),
                str(controller.get("m_szType", controller.get("m_type", ""))),
                float(controller.get("min", controller.get("m_flMin", 0.0))),
                float(controller.get("max", controller.get("m_flMax", 1.0))),
            ))
        return tuple(controller for controller in controllers if controller.name)

    @property
    def controller_names(self) -> tuple[str, ...]:
        return tuple(controller.name for controller in self.flex_controllers)

    @property
    def flex_rules(self) -> tuple[Any, ...]:
        return tuple(self.get("m_FlexRules", []) or [])

    def evaluate_flex_rules(
            self,
            controller_values: npt.ArrayLike,
            *,
            strict: bool = False,
    ) -> tuple[tuple[str, ...], npt.NDArray[np.float32], tuple[Diagnostic, ...]]:
        """Evaluate legacy postfix flex rules for every input frame."""

        values = np.asarray(controller_values, dtype=np.float32)
        if values.ndim != 2:
            raise ValueError("controller_values must have shape (frames, controllers)")
        controllers = self.flex_controllers
        if values.shape[1] != len(controllers):
            raise ValueError(
                f"Expected {len(controllers)} flex controllers, got {values.shape[1]}"
            )

        descriptors = list(self.flex_descriptors)
        rules = self.flex_rules
        max_flex = max(
            (int(rule.get("m_nFlex", -1)) for rule in rules if hasattr(rule, "get")),
            default=-1,
        )
        while len(descriptors) <= max_flex:
            descriptors.append(f"flex_{len(descriptors)}")
        weights = np.zeros((values.shape[0], len(descriptors)), dtype=np.float32)
        diagnostics: list[Diagnostic] = []

        if not rules:
            controller_lookup = {
                controller.name.casefold(): index for index, controller in enumerate(controllers)
            }
            for flex_index, name in enumerate(descriptors):
                controller_index = controller_lookup.get(name.casefold())
                if controller_index is not None:
                    weights[:, flex_index] = values[:, controller_index]
            return tuple(descriptors), weights, ()

        for rule_index, rule in enumerate(rules):
            if not hasattr(rule, "get"):
                continue
            flex_index = int(rule.get("m_nFlex", -1))
            if not 0 <= flex_index < len(descriptors):
                diagnostics.append(Diagnostic(
                    "animation.flex_rule.target",
                    f"Flex rule {rule_index} has invalid target {flex_index}",
                    DiagnosticSeverity.ERROR,
                    details={"rule_index": rule_index, "flex_index": flex_index},
                ))
                continue
            try:
                weights[:, flex_index] = self._evaluate_flex_rule(
                    rule.get("m_FlexOps", []) or [], values, weights, controllers
                )
            except (UnsupportedFlexOperationError, ValueError, IndexError) as ex:
                diagnostic = Diagnostic(
                    "animation.flex_rule.unsupported",
                    f"Flex rule {rule_index} for {descriptors[flex_index]!r} was not evaluated: {ex}",
                    DiagnosticSeverity.ERROR,
                    details={
                        "rule_index": rule_index,
                        "flex_index": flex_index,
                        "exception": type(ex).__name__,
                        "rule": rule,
                    },
                )
                diagnostics.append(diagnostic)
                if strict:
                    raise UnsupportedFlexOperationError(diagnostic.message) from ex
        return tuple(descriptors), weights, tuple(diagnostics)

    @staticmethod
    def _evaluate_flex_rule(ops, controller_values: np.ndarray, flex_values: np.ndarray,
                            controllers: tuple[FlexController, ...]) -> np.ndarray:
        frames = controller_values.shape[0]
        zero = np.zeros(frames, dtype=np.float32)
        stack: list[np.ndarray | np.float32] = []

        def pop():
            return stack.pop() if stack else zero

        def controller(index: int):
            return controller_values[:, index] if 0 <= index < controller_values.shape[1] else zero

        def remap(value, source_min, source_max, target_min, target_max):
            denominator = np.asarray(source_max) - np.asarray(source_min)
            t = np.divide(
                value - source_min,
                denominator,
                out=np.zeros(frames, dtype=np.float32),
                where=np.abs(denominator) > 1e-20,
            )
            t = np.clip(t, 0.0, 1.0)
            return target_min + t * (target_max - target_min)

        def remapped_controller(index: int, target_min: float, target_max: float):
            value = controller(index)
            if not 0 <= index < len(controllers):
                return value
            item = controllers[index]
            return remap(value, item.minimum, item.maximum, target_min, target_max)

        def pop_index() -> int:
            value = pop()
            scalar = float(value[0] if isinstance(value, np.ndarray) else value)
            return unpack("<i", pack("<f", scalar))[0]

        for op_index, op in enumerate(ops):
            if not hasattr(op, "get"):
                raise UnsupportedFlexOperationError(f"operation {op_index} is not a mapping")
            opcode_value = op.get("m_OpCode", op.get("m_opCode"))
            try:
                opcode = (_FLEX_OP_NAMES.get(str(opcode_value))
                          if isinstance(opcode_value, str)
                          else _FlexOp(int(opcode_value)))
            except (TypeError, ValueError):
                opcode = None
            if opcode is None:
                raise UnsupportedFlexOperationError(
                    f"unknown opcode {opcode_value!r} at operation {op_index}"
                )
            raw_data = int(op.get("m_Data", op.get("m_data", 0)))
            float_data = np.float32(unpack("<f", pack("<I", raw_data & 0xFFFF_FFFF))[0])

            if opcode is _FlexOp.CONST:
                stack.append(float_data)
            elif opcode is _FlexOp.FETCH1:
                stack.append(controller(raw_data))
            elif opcode is _FlexOp.FETCH2:
                stack.append(flex_values[:, raw_data] if 0 <= raw_data < flex_values.shape[1] else zero)
            elif opcode is _FlexOp.ADD:
                right, left = pop(), pop()
                stack.append(left + right)
            elif opcode is _FlexOp.SUB:
                right, left = pop(), pop()
                stack.append(left - right)
            elif opcode is _FlexOp.MUL:
                right, left = pop(), pop()
                stack.append(left * right)
            elif opcode is _FlexOp.DIV:
                divisor, dividend = pop(), pop()
                quotient = np.zeros(frames, dtype=np.float32)
                np.divide(
                    dividend,
                    divisor,
                    out=quotient,
                    where=np.abs(divisor) > 0.0001,
                )
                stack.append(quotient)
            elif opcode is _FlexOp.NEG:
                stack.append(-pop())
            elif opcode is _FlexOp.MAX:
                right, left = pop(), pop()
                stack.append(np.maximum(left, right))
            elif opcode is _FlexOp.MIN:
                right, left = pop(), pop()
                stack.append(np.minimum(left, right))
            elif opcode is _FlexOp.TWO_WAY_0:
                stack.append(remap(controller(raw_data), -1.0, 0.0, 1.0, 0.0))
            elif opcode is _FlexOp.TWO_WAY_1:
                stack.append(remap(controller(raw_data), 0.0, 1.0, 0.0, 1.0))
            elif opcode is _FlexOp.NWAY:
                t_controller = pop_index()
                t_current = controller(t_controller)
                value = controller(raw_data)
                t4, t3, t2, t1 = pop(), pop(), pop(), pop()
                rising = remap(t_current, t1, t2, 0.0, 1.0) * value
                falling = remap(t_current, t3, t4, 1.0, 0.0) * value
                stack.append(np.where(
                    t_current < t1, 0.0,
                    np.where(t_current < t2, rising,
                             np.where(t_current < t3, value,
                                      np.where(t_current < t4, falling, 0.0))),
                ))
            elif opcode is _FlexOp.COMBO:
                product = np.ones(frames, dtype=np.float32)
                for _ in range(max(raw_data, 0)):
                    product *= pop()
                stack.append(product)
            elif opcode is _FlexOp.DOMINATE:
                dominators = np.ones(frames, dtype=np.float32)
                for _ in range(max(raw_data, 0)):
                    dominators *= pop()
                stack.append(pop() * (1.0 - dominators))
            elif opcode in (_FlexOp.DME_LOWER_EYELID, _FlexOp.DME_UPPER_EYELID):
                close_lid_value = remapped_controller(raw_data, 0.0, 1.0)
                close_lid = remapped_controller(pop_index(), 0.0, 1.0)
                blink_index = pop_index()
                eye_index = pop_index()
                blink = remapped_controller(blink_index, 0.0, 1.0) if blink_index >= 0 else zero
                eye = remapped_controller(eye_index, -1.0, 1.0) if eye_index >= 0 else zero
                closed = np.maximum(blink, close_lid)
                if opcode is _FlexOp.DME_LOWER_EYELID:
                    stack.append(closed * (1.0 - close_lid_value) * np.where(eye > 0.0, 1.0 - eye, 1.0))
                else:
                    stack.append(closed * close_lid_value * np.where(eye < 0.0, 1.0 + eye, 1.0))
            elif opcode is _FlexOp.SQRT:
                stack.append(np.sqrt(pop()))
            elif opcode is _FlexOp.REMAP_VAL_CLAMPED:
                target_max, target_min, source_max, source_min, value = pop(), pop(), pop(), pop(), pop()
                denominator = source_max - source_min
                t = np.divide(
                    value - source_min,
                    denominator,
                    out=np.zeros(frames, dtype=np.float32),
                    where=np.abs(denominator) > 1e-20,
                )
                stack.append(target_min + np.clip(t, 0.0, 1.0) * (target_max - target_min))
            elif opcode is _FlexOp.SIN:
                stack.append(np.sin(pop()))
            elif opcode is _FlexOp.COS:
                stack.append(np.cos(pop()))
            elif opcode is _FlexOp.ABS:
                stack.append(np.abs(pop()))
            elif opcode in (_FlexOp.EXP, _FlexOp.OPEN, _FlexOp.CLOSE, _FlexOp.COMMA):
                continue
            else:
                raise UnsupportedFlexOperationError(
                    f"unsupported opcode {opcode.name} at operation {op_index}"
                )

        if len(stack) != 1:
            raise ValueError(f"flex rule left {len(stack)} values on its stack")
        result = stack.pop()
        return np.broadcast_to(result, (frames,)).astype(np.float32, copy=False)

    def get_morph_data(self, flex_name: str, bundle_id: int, texture):
        bundle_data = self._morph_datas[bundle_id]
        cached = bundle_data.get(flex_name)
        if cached is not None:
            return cached

        assert self.lookup_type == 'LOOKUP_TYPE_VERTEX_ID'
        assert self.encoding_type == 'ENCODING_TYPE_OBJECT_SPACE'

        t_width, t_height = texture.shape[:2]
        width = self['m_nWidth']
        height = self['m_nHeight']

        name_map = self._morph_name_map
        if not name_map:
            name_map = {md['m_name']: md for md in self['m_morphDatas'] if md['m_name']}
            self._morph_name_map = name_map

        morph_data = name_map.get(flex_name)
        if morph_data is None:
            logger.error(f'Failed to find morph data for {flex_name!r} flex')
            return None

        out = bundle_data[flex_name] = np.zeros((height, width, 4), dtype=np.float32)

        for n, rect in enumerate(morph_data['m_morphRectDatas']):
            bundle = rect['m_bundleDatas'][bundle_id]

            rw = round(rect['m_flUWidthSrc'] * t_width)
            rh = round(rect['m_flVHeightSrc'] * t_height)
            dx = rect['m_nXLeftDst']
            dy = rect['m_nYTopDst']
            ru = round(bundle['m_flULeftSrc'] * t_width)
            rv = round(bundle['m_flVTopSrc'] * t_height)
            src = texture[rv:rv + rh, ru:ru + rw, :4].astype(np.float32, copy=False)

            dst = out[dy:(dy + rh), dx:(dx + rw), :4]

            np.multiply(src, np.asarray(bundle['m_ranges'], dtype=np.float32), out=dst, casting='unsafe')
            np.add(dst, np.asarray(bundle['m_offsets'], dtype=np.float32), out=dst, casting='unsafe')

        return out
