"""Build one Blender extension zip per platform, each carrying only its own native library.

Usage:  python tools/build_extension.py --blender "path/to/blender" [--output-dir dist] [--platform windows-x64]

``blender --command extension build --split-platforms`` only splits Python wheels, so every package
would otherwise ship the Windows, Linux and macOS builds of ``library/utils/pylib``. This stages the
current non-ignored worktree once per platform, drops the other platforms' libraries, narrows
``platforms`` in the manifest and runs Blender's own builder on the result.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYLIB = "library/utils/pylib"

# Blender platform tag -> folder of the native library it needs.
PLATFORM_LIBRARIES = {
    "windows-x64": "windows",
    "linux-x64": "linux",
    "macos-x64": "macos",
    "macos-arm64": "macos",
}


def source_files() -> list[str]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return [
        name for name in output.decode().split("\0")
        if name and (REPO_ROOT / name).is_file()
    ]


def stage(platform: str, files: list[str], destination: Path):
    keep = PLATFORM_LIBRARIES[platform]
    for name in files:
        parts = name.split("/")
        if name.startswith(PYLIB + "/") and len(parts) > 4 and parts[3] in set(PLATFORM_LIBRARIES.values()) - {keep}:
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / name, target)

    manifest = destination / "blender_manifest.toml"
    text = manifest.read_text(encoding="utf-8")
    text, count = re.subn(r'^platforms\s*=\s*\[[^\]]*\]', f'platforms = ["{platform}"]', text, flags=re.M)
    if count != 1:
        raise RuntimeError("could not find the platforms list in blender_manifest.toml")
    manifest.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blender", default=os.environ.get("BLENDER", "blender"))
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--platform", action="append", choices=sorted(PLATFORM_LIBRARIES),
                        help="platform to build, may be repeated (default: all)")
    args = parser.parse_args()

    files = source_files()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for platform in args.platform or sorted(PLATFORM_LIBRARIES):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / REPO_ROOT.name
            stage(platform, files, source)
            command = [args.blender, "--factory-startup", "--command", "extension", "build", "--split-platforms",
                       "--source-dir", str(source), "--output-dir", str(args.output_dir)]
            result = subprocess.run(command, capture_output=True, text=True)
            if result.returncode != 0:
                print(result.stdout, result.stderr, sep="\n")
                print(f"FAILED {platform}")
                return 1
            print(f"built {platform}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
