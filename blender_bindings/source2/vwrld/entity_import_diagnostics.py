from __future__ import annotations

import json
from collections import Counter
from enum import Enum
from typing import Any


ENTITY_IMPORT_REPORT_PROPERTY = "sourceio_entity_import_report"
MAX_LOGGED_ENTITY_CLASSES = 20


class EntityImportStatus(str, Enum):
    HANDLED = "handled"
    REPRESENTED = "represented"
    DEFERRED = "deferred"
    UNSUPPORTED = "unsupported"


class EntityImportDiagnostics:
    def __init__(self):
        self._class_counts = {
            status: Counter()
            for status in EntityImportStatus
        }

    def record(self, classname: str, status: EntityImportStatus):
        self._class_counts[status][str(classname)] += 1

    def to_dict(self) -> dict[str, Any]:
        class_counts = {
            status.value: dict(sorted(self._class_counts[status].items()))
            for status in EntityImportStatus
        }
        totals = {
            status.value: sum(class_counts[status.value].values())
            for status in EntityImportStatus
        }
        return {
            "schema": "sourceio.entity-import-report",
            "schema_version": 1,
            "entity_count": sum(totals.values()),
            "totals": totals,
            "class_counts": class_counts,
        }

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    def attach(self, collection) -> str:
        serialized = self.serialize()
        collection[ENTITY_IMPORT_REPORT_PROPERTY] = serialized
        return serialized

    def log_summary(self, logger, max_classes: int = MAX_LOGGED_ENTITY_CLASSES):
        if max_classes < 1:
            raise ValueError("max_classes must be at least 1")
        for status in EntityImportStatus:
            counts = self._class_counts[status]
            if not counts:
                continue
            entries = sorted(counts.items())
            shown = entries[:max_classes]
            details = ", ".join(f"{classname}={count}" for classname, count in shown)
            omitted = len(entries) - len(shown)
            suffix = f"; {omitted} additional classes omitted" if omitted else ""
            message = (
                f"Entity import {status.value}: {sum(counts.values())} entities across "
                f"{len(entries)} classes ({details}){suffix}"
            )
            if status is EntityImportStatus.UNSUPPORTED:
                logger.warn(message)
            else:
                logger.info(message)


def get_entity_import_report(collection) -> dict[str, Any] | None:
    raw = collection.get(ENTITY_IMPORT_REPORT_PROPERTY) if hasattr(collection, "get") else None
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise TypeError(f"{ENTITY_IMPORT_REPORT_PROPERTY} must contain JSON text")
    report = json.loads(raw)
    if not isinstance(report, dict):
        raise ValueError(f"{ENTITY_IMPORT_REPORT_PROPERTY} must decode to an object")
    return report
