"""Load the shipped files under another package name, as Blender does for an extension
(``bl_ext.<repository>.sourceio``), and check nothing needs a top-level ``SourceIO`` module.

Usage:
    blender -b --factory-startup --python tests/blender_tests/run_renamed_smoke.py -- [--work DIR]

The files git tracks, minus what the extension build leaves out, are copied into DIR (default: a
temporary folder) as ``bl_ext_test_sourceio``. An import hook rejects ``SourceIO``. Every module is
imported, then the add-on is registered, unregistered and registered again. Exit code is 1 if any
of that fails.
"""
import argparse
import importlib
import importlib.abc
import pkgutil
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NAME = "bl_ext_test_sourceio"
NOT_SHIPPED = ("tests/", "tools/", "samples/", "dist/", "wiki/")


class BlockSourceIO(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "SourceIO" or fullname.startswith("SourceIO."):
            raise ImportError(f"absolute import of {fullname}")
        return None


def copy_shipped(work: Path) -> None:
    files = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True,
                           check=True).stdout.splitlines()
    for rel in files:
        if rel.startswith(NOT_SHIPPED):
            continue
        (work / NAME / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, work / NAME / rel)


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path)
    args = parser.parse_args(argv)
    work = args.work or Path(tempfile.mkdtemp(prefix="sourceio_renamed_"))
    shutil.rmtree(work / NAME, ignore_errors=True)
    copy_shipped(work)

    sys.meta_path.insert(0, BlockSourceIO())
    sys.path.insert(0, str(work))
    package = importlib.import_module(NAME)

    failed = {}
    modules = [info.name for info in pkgutil.walk_packages(package.__path__, NAME + ".")]
    for name in modules:
        try:
            importlib.import_module(name)
        except BaseException as e:
            failed[name] = f"{type(e).__name__}: {e}"
    print(f"imported {len(modules) - len(failed)}/{len(modules)} modules")
    for name, error in failed.items():
        print(f"  FAIL {name}: {error}")

    leaked = sorted(m for m in sys.modules if m == "SourceIO" or m.startswith("SourceIO."))
    if leaked:
        print(f"SourceIO modules loaded: {leaked[:5]}")

    registered = True
    try:
        package.register()
        package.unregister()
        package.register()
    except Exception:
        traceback.print_exc()
        registered = False
    print(f"register/unregister/register: {'OK' if registered else 'FAIL'}")
    return 0 if registered and not failed and not leaked else 1


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    sys.exit(code)
