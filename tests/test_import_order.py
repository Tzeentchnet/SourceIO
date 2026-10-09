"""Every library module must import on its own. A circular import only shows up when the
module is imported before the rest of its cycle, which the add-on's own import order hides."""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
# Need bpy/mathutils or an optional dev dependency
SKIPPED = {
    "SourceIO.library.source1.dmx.sfm_utils",
    "SourceIO.library.source1.fgd.generate_entity_classes",
}

# Runs in a fresh interpreter so clearing sys.modules can't affect other tests
SCRIPT = """
import importlib, json, sys, traceback
failures = {}
for name in json.loads(sys.argv[1]):
    for loaded in [m for m in sys.modules if m == 'SourceIO' or m.startswith('SourceIO.')]:
        del sys.modules[loaded]
    try:
        importlib.import_module(name)
    except Exception:
        failures[name] = traceback.format_exc(limit=-3)
print(json.dumps(failures))
"""


def library_modules():
    for path in sorted((REPO_ROOT / "library").rglob("*.py")):
        parts = ("SourceIO",) + path.relative_to(REPO_ROOT).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        name = ".".join(parts)
        if " " not in name and name not in SKIPPED:
            yield name


def test_each_library_module_imports_first():
    modules = list(library_modules())
    assert len(modules) > 300
    result = subprocess.run([sys.executable, "-c", SCRIPT, json.dumps(modules)], cwd=REPO_ROOT.parent,
                            env=dict(os.environ, NO_BPY="1"), capture_output=True, text=True, check=True)
    failures = json.loads(result.stdout.strip().splitlines()[-1])
    assert not failures, "\n".join(f"{name}:\n{tb}" for name, tb in failures.items())
