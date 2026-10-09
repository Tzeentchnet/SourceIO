from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_PARENT = REPOSITORY_ROOT.parent
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "source2_generated"

if str(REPOSITORY_PARENT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_PARENT))


@pytest.fixture(scope="session")
def fixture_root() -> Path:
    return FIXTURE_ROOT


@pytest.fixture(scope="session")
def fixture_manifest(fixture_root: Path) -> dict:
    return json.loads((fixture_root / "manifest.json").read_text(encoding="ascii"))
