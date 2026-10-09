from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import numpy as np

from ..keyvalues3.types import Array, BaseType, NullObject, Object, TypedArray


class FrozenKV3Object(Mapping[str, object]):
    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, object]):
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("FrozenKV3Object is immutable")

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"FrozenKV3Object({dict(self._values)!r})"


class FrozenKV3Array(Sequence[object]):
    __slots__ = ("_values", "source_type", "data_type", "data_specifier")

    def __init__(
        self,
        values: Sequence[object],
        *,
        source_type: str,
        data_type: object = None,
        data_specifier: object = None,
    ):
        object.__setattr__(self, "_values", tuple(values))
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "data_type", data_type)
        object.__setattr__(self, "data_specifier", data_specifier)

    def __setattr__(self, name: str, value: object) -> None:
        raise TypeError("FrozenKV3Array is immutable")

    def __getitem__(self, index):
        return self._values[index]

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"FrozenKV3Array({self._values!r}, source_type={self.source_type!r})"


@dataclass(frozen=True, slots=True, eq=False)
class FrozenKV3Scalar:
    value: object
    source_type: type
    specifier: object = None

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FrozenKV3Scalar):
            return (
                self.source_type is other.source_type
                and self.specifier == other.specifier
                and self.value == other.value
            )
        return self.value == other

    def __hash__(self) -> int:
        return hash((self.source_type, self.specifier, self.value))

    def __str__(self) -> str:
        return str(self.value)

    def __bool__(self) -> bool:
        return bool(self.value)

    def __int__(self) -> int:
        return int(self.value)

    def __float__(self) -> float:
        return float(self.value)


def _immutable_ndarray(value: np.ndarray) -> np.ndarray:
    contiguous = np.ascontiguousarray(value)
    immutable = np.frombuffer(contiguous.tobytes(), dtype=contiguous.dtype).reshape(contiguous.shape)
    return immutable


def freeze_kv3(value: object) -> object:
    return _freeze_kv3(value, set())


def _freeze_kv3(value: object, active: set[int]) -> object:
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in active:
            raise ValueError("Recursive KV3 objects cannot be frozen")
        active.add(identity)
        try:
            return FrozenKV3Object({str(key): _freeze_kv3(item, active) for key, item in value.items()})
        finally:
            active.remove(identity)

    if isinstance(value, (TypedArray, Array, list, tuple)):
        identity = id(value)
        if identity in active:
            raise ValueError("Recursive KV3 arrays cannot be frozen")
        active.add(identity)
        try:
            return FrozenKV3Array(
                [_freeze_kv3(item, active) for item in value],
                source_type=type(value).__name__,
                data_type=getattr(value, "data_type", None),
                data_specifier=getattr(value, "data_specifier", None),
            )
        finally:
            active.remove(identity)

    if isinstance(value, np.ndarray):
        return _immutable_ndarray(value)

    if isinstance(value, NullObject):
        return FrozenKV3Scalar(None, type(value), getattr(value, "specifier", None))

    if isinstance(value, BaseType):
        if isinstance(value, bytes):
            scalar_value: object = bytes(value)
        elif isinstance(value, str):
            scalar_value = str(value)
        elif isinstance(value, float):
            scalar_value = float(value)
        elif isinstance(value, int):
            scalar_value = int(value)
        else:
            raise TypeError(f"Unsupported KV3 scalar type: {type(value).__name__}")
        return FrozenKV3Scalar(scalar_value, type(value), getattr(value, "specifier", None))

    if value is None or isinstance(value, (str, bytes, bool, int, float)):
        return value

    raise TypeError(f"Unsupported KV3 value type: {type(value).__name__}")


def deep_clone_kv3(value: object) -> object:
    return _deep_clone_kv3(value, {})


def _deep_clone_kv3(value: object, memo: dict[int, object]) -> object:
    identity = id(value)
    if identity in memo:
        return memo[identity]

    if isinstance(value, Object):
        clone = Object({})
        memo[identity] = clone
        for key, item in value.items():
            clone[str(key)] = _deep_clone_kv3(item, memo)
        return clone

    if isinstance(value, dict):
        clone_dict: dict[str, object] = {}
        memo[identity] = clone_dict
        for key, item in value.items():
            clone_dict[str(key)] = _deep_clone_kv3(item, memo)
        return clone_dict

    if isinstance(value, TypedArray):
        clone_typed = TypedArray(value.data_type, value.data_specifier, [])
        memo[identity] = clone_typed
        for item in value:
            list.append(clone_typed, _deep_clone_kv3(item, memo))
        return clone_typed

    if isinstance(value, Array):
        clone_array = Array([])
        memo[identity] = clone_array
        for item in value:
            list.append(clone_array, _deep_clone_kv3(item, memo))
        return clone_array

    if isinstance(value, list):
        clone_list: list[object] = []
        memo[identity] = clone_list
        clone_list.extend(_deep_clone_kv3(item, memo) for item in value)
        return clone_list

    if isinstance(value, tuple):
        clone_tuple = tuple(_deep_clone_kv3(item, memo) for item in value)
        memo[identity] = clone_tuple
        return clone_tuple

    if isinstance(value, np.ndarray):
        clone_ndarray = value.copy()
        memo[identity] = clone_ndarray
        return clone_ndarray

    if isinstance(value, NullObject):
        clone_null = NullObject()
        clone_null.specifier = getattr(value, "specifier", clone_null.specifier)
        return clone_null

    if isinstance(value, BaseType):
        clone_scalar = type(value)(value)
        clone_scalar.specifier = getattr(value, "specifier", clone_scalar.specifier)
        return clone_scalar

    if value is None or isinstance(value, (str, bytes, bool, int, float)):
        return value

    raise TypeError(f"Unsupported KV3 value type: {type(value).__name__}")


def thaw_kv3(value: object) -> object:
    if isinstance(value, FrozenKV3Object):
        return Object({key: thaw_kv3(item) for key, item in value.items()})
    if isinstance(value, FrozenKV3Array):
        items = [thaw_kv3(item) for item in value]
        if value.source_type == "TypedArray":
            return TypedArray(value.data_type, value.data_specifier, items)
        if value.source_type == "Array":
            return Array(items)
        if value.source_type == "tuple":
            return tuple(items)
        return items
    if isinstance(value, FrozenKV3Scalar):
        if issubclass(value.source_type, NullObject):
            scalar = NullObject()
        else:
            scalar = value.source_type(value.value)
        scalar.specifier = value.specifier
        return scalar
    if isinstance(value, np.ndarray):
        return value.copy()
    if value is None or isinstance(value, (str, bytes, bool, int, float)):
        return value
    raise TypeError(f"Unsupported frozen KV3 value type: {type(value).__name__}")


def kv3_to_python(value: object) -> Any:
    if isinstance(value, FrozenKV3Object):
        return {key: kv3_to_python(item) for key, item in value.items()}
    if isinstance(value, FrozenKV3Array):
        return [kv3_to_python(item) for item in value]
    if isinstance(value, FrozenKV3Scalar):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): kv3_to_python(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, Array, TypedArray)):
        return [kv3_to_python(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, NullObject):
        return None
    if isinstance(value, BaseType):
        if isinstance(value, bytes):
            return bytes(value)
        if isinstance(value, str):
            return str(value)
        if isinstance(value, float):
            return float(value)
        if isinstance(value, int):
            return int(value)
    return value
