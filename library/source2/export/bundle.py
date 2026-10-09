from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .diagnostics import LossReport
from .staging import stage_text_outputs


@dataclass(slots=True)
class ExportBundle:
    files: dict[str, str]
    loss_report: LossReport = field(default_factory=LossReport)

    def write(self, root: str | Path, *, overwrite: bool = False) -> tuple[Path, ...]:
        return stage_text_outputs(root, self.files, overwrite=overwrite)
