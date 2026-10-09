from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from ....library.source2.provenance import import_provenance, to_json_safe

PROVENANCE_PROPERTY = "sourceio_import_provenance"


def build_resource_import_payload(resource, *, asset_kind: str, metadata: Mapping[str, Any] | None = None):
    return import_provenance(
        resource,
        asset_kind=asset_kind,
        metadata=to_json_safe(metadata or {}),
    )


def provenance_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(to_json_safe(payload), sort_keys=True, separators=(",", ":"), allow_nan=False)


def attach_import_provenance(owner, payload: Mapping[str, Any] | str):
    serialized = payload if isinstance(payload, str) else provenance_json(payload)
    owner[PROVENANCE_PROPERTY] = serialized
    return serialized


def attach_import_provenance_many(owners: Iterable[Any], payload: Mapping[str, Any] | str):
    serialized = payload if isinstance(payload, str) else provenance_json(payload)
    for owner in owners:
        if owner is not None:
            attach_import_provenance(owner, serialized)
    return serialized


def read_import_provenance(owner) -> dict[str, Any] | None:
    value = owner.get(PROVENANCE_PROPERTY) if hasattr(owner, "get") else None
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{PROVENANCE_PROPERTY} must contain JSON text")
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError(f"{PROVENANCE_PROPERTY} must decode to an object")
    return result
